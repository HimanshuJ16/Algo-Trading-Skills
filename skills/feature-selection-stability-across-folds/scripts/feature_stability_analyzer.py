import math
import statistics
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass
class FeatureInclusionDetail:
    feature_name: str
    selection_count_folds: int
    inclusion_probability: float
    is_consensus_feature: bool         # inclusion_probability >= min_inclusion_threshold

@dataclass
class FeatureStabilityAuditReport:
    total_candidate_features_M: int
    total_folds_K: int
    nogueira_stability_index_phi: float
    average_features_per_fold: float
    consensus_features_count: int
    pruned_unstable_features_count: int
    consensus_feature_names: List[str]
    pruned_feature_names: List[str]
    inclusion_details: List[FeatureInclusionDetail]
    status: str                         # 'STABLE_FEATURE_SET', 'UNSTABLE_OVERFITTED_FEATURE_SET'
    audit_notes: str

class FeatureStabilityAnalyzerEngine:
    """
    Quantitative ML engine for measuring feature selection stability across cross-validation folds using Nogueira's Index,
    computing inclusion frequencies, and filtering unstable overfitted features.
    """
    def __init__(
        self,
        min_nogueira_stability_threshold: float = 0.70,
        min_inclusion_threshold: float = 0.80
    ):
        self.min_nogueira_stability_threshold = min_nogueira_stability_threshold
        self.min_inclusion_threshold = min_inclusion_threshold

    def calculate_nogueira_index(
        self,
        candidate_features: List[str],
        fold_selections: List[Set[str]]
    ) -> Tuple[float, float, List[FeatureInclusionDetail]]:
        """
        Calculates Nogueira Stability Index (Phi) and feature inclusion probabilities across K folds.
        Nogueira (2017) formula: Phi = 1 - sum(s_i^2) / ( k_bar * (1 - k_bar / M) )
        """
        M = len(candidate_features)
        K = len(fold_selections)

        if M == 0 or K <= 1:
            raise ValueError("Candidate features M must be > 0 and folds K must be > 1.")

        inclusion_details: List[FeatureInclusionDetail] = []
        p_i_list: List[float] = []

        for f in candidate_features:
            sel_cnt = sum(1 for fold in fold_selections if f in fold)
            p_i = sel_cnt / float(K)
            p_i_list.append(p_i)

            is_consensus = (p_i >= self.min_inclusion_threshold)
            inclusion_details.append(FeatureInclusionDetail(
                feature_name=f,
                selection_count_folds=sel_cnt,
                inclusion_probability=round(p_i, 4),
                is_consensus_feature=is_consensus
            ))

        # k_bar = average number of features selected per fold
        k_bar = statistics.mean([len(fold) for fold in fold_selections])

        if k_bar == 0 or k_bar == M:
            # Degenerate cases (no features selected or all features selected in all folds)
            return 1.0, k_bar, inclusion_details

        # Variance component sum: sum(s_i^2) where s_i^2 = (K / (K - 1)) * p_i * (1 - p_i)
        sample_variance_sum = sum((K / float(K - 1)) * p * (1.0 - p) for p in p_i_list)

        # Nogueira Index Phi Formula: Phi = 1 - sum(s_i^2) / ( k_bar * (1 - k_bar / M) )
        denominator = k_bar * (1.0 - (k_bar / float(M)))

        phi = 1.0 - (sample_variance_sum / denominator) if denominator > 0 else 1.0
        phi = round(phi, 4)

        return phi, round(k_bar, 2), inclusion_details

    def audit_feature_stability(
        self,
        candidate_features: List[str],
        fold_selections: List[Set[str]]
    ) -> FeatureStabilityAuditReport:
        """
        Audits feature selection stability across K folds, extracting consensus features and pruning unstable ones.
        """
        M = len(candidate_features)
        K = len(fold_selections)

        phi, k_bar, details = self.calculate_nogueira_index(candidate_features, fold_selections)

        consensus_features = [d.feature_name for d in details if d.is_consensus_feature]
        pruned_features = [d.feature_name for d in details if not d.is_consensus_feature]

        is_stable = (phi >= self.min_nogueira_stability_threshold)

        if is_stable:
            status = "STABLE_FEATURE_SET"
            notes = (
                f"FEATURE SELECTION STABLE (K={K} folds, M={M} candidate features): "
                f"Nogueira Index Phi = {phi:.4f} >= {self.min_nogueira_stability_threshold:.2f}. "
                f"Retained {len(consensus_features)} consensus features (Inclusion >= {self.min_inclusion_threshold*100:.0f}%). "
                f"Pruned {len(pruned_features)} unstable features."
            )
            logger.info(notes)
        else:
            status = "UNSTABLE_OVERFITTED_FEATURE_SET"
            notes = (
                f"FEATURE SELECTION UNSTABLE (K={K} folds, M={M} candidate features): "
                f"Nogueira Index Phi = {phi:.4f} < {self.min_nogueira_stability_threshold:.2f} threshold. "
                f"Feature selection varies wildly across CV folds! Recommend pruning {len(pruned_features)} unstable features."
            )
            logger.warning(notes)

        return FeatureStabilityAuditReport(
            total_candidate_features_M=M,
            total_folds_K=K,
            nogueira_stability_index_phi=phi,
            average_features_per_fold=k_bar,
            consensus_features_count=len(consensus_features),
            pruned_unstable_features_count=len(pruned_features),
            consensus_feature_names=consensus_features,
            pruned_feature_names=pruned_features,
            inclusion_details=details,
            status=status,
            audit_notes=notes
        )
