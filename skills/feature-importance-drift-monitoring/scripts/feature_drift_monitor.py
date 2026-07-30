import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class FeatureRankDetail:
    feature_name: str
    baseline_importance: float
    live_importance: float
    baseline_rank: int
    live_rank: int
    rank_difference: int                # live_rank - baseline_rank
    importance_ratio_live_to_base: float # live / max(1e-4, base)

@dataclass
class FeatureDriftAuditReport:
    model_id: str
    monitoring_window: str
    spearman_rank_correlation: float
    is_retrain_triggered: bool
    degraded_features: List[str]
    rank_details: List[FeatureRankDetail]
    status: str                         # 'FEATURE_STABILITY_NORMAL', 'FEATURE_DRIFT_ALERT_TRIGGERED'
    audit_notes: str

class FeatureImportanceDriftMonitorEngine:
    """
    Quantitative MLOps engine for tracking feature importance drift,
    computing Spearman rank correlations between training baselines and live inference windows,
    and triggering model retraining alerts.
    """
    def __init__(
        self,
        min_spearman_rank_threshold: float = 0.70,
        max_degradation_drop_pct: float = 0.80 # > 80% drop triggers alert
    ):
        self.min_spearman_rank_threshold = min_spearman_rank_threshold
        self.max_degradation_drop_pct = max_degradation_drop_pct

    def compute_spearman_rank_correlation(self, base_ranks: List[int], live_ranks: List[int]) -> float:
        """
        Computes Spearman Rank Correlation: rho = 1 - (6 * sum(d^2)) / (M * (M^2 - 1))
        """
        M = len(base_ranks)
        if M <= 1:
            return 1.0

        d_sq_sum = sum((r_live - r_base) ** 2 for r_base, r_live in zip(base_ranks, live_ranks))
        rho = 1.0 - (6.0 * d_sq_sum) / float(M * (M ** 2 - 1))
        return round(rho, 4)

    def audit_feature_importance_drift(
        self,
        model_id: str,
        monitoring_window: str,
        baseline_importance_map: Dict[str, float],
        live_importance_map: Dict[str, float]
    ) -> FeatureDriftAuditReport:
        """
        Audits feature importance drift between baseline training and live production window.
        """
        common_features = sorted(list(set(baseline_importance_map.keys()).intersection(set(live_importance_map.keys()))))
        if not common_features:
            raise ValueError("No common features found between baseline and live maps.")

        M = len(common_features)

        # Compute Ranks for Baseline (1 = highest importance)
        sorted_base = sorted(common_features, key=lambda f: baseline_importance_map[f], reverse=True)
        base_rank_dict = {f: idx + 1 for idx, f in enumerate(sorted_base)}

        # Compute Ranks for Live (1 = highest importance)
        sorted_live = sorted(common_features, key=lambda f: live_importance_map[f], reverse=True)
        live_rank_dict = {f: idx + 1 for idx, f in enumerate(sorted_live)}

        base_ranks: List[int] = []
        live_ranks: List[int] = []
        details: List[FeatureRankDetail] = []
        degraded: List[str] = []

        for f in common_features:
            r_base = base_rank_dict[f]
            r_live = live_rank_dict[f]
            base_ranks.append(r_base)
            live_ranks.append(r_live)

            val_base = baseline_importance_map[f]
            val_live = live_importance_map[f]
            ratio = round(val_live / max(1e-4, val_base), 4)

            # Check if top baseline feature suffered severe drop (> 80% drop)
            if r_base <= 3 and ratio < (1.0 - self.max_degradation_drop_pct):
                degraded.append(f)

            details.append(FeatureRankDetail(
                feature_name=f,
                baseline_importance=val_base,
                live_importance=val_live,
                baseline_rank=r_base,
                live_rank=r_live,
                rank_difference=r_live - r_base,
                importance_ratio_live_to_base=ratio
            ))

        rho = self.compute_spearman_rank_correlation(base_ranks, live_ranks)

        is_triggered = (rho < self.min_spearman_rank_threshold) or (len(degraded) > 0)

        if is_triggered:
            status = "FEATURE_DRIFT_ALERT_TRIGGERED"
            notes = (
                f"FEATURE IMPORTANCE DRIFT ALERT [{model_id} - {monitoring_window}]: Spearman Rank Correlation = {rho:.4f} "
                f"(Threshold = {self.min_spearman_rank_threshold:.2f}). Degraded top features: {degraded}. Automated MODEL RETRAIN TRIGGERED."
            )
            logger.warning(notes)
        else:
            status = "FEATURE_STABILITY_NORMAL"
            notes = f"FEATURE IMPORTANCE STABLE [{model_id} - {monitoring_window}]: Spearman Rank Correlation = {rho:.4f} >= {self.min_spearman_rank_threshold:.2f}."
            logger.info(notes)

        return FeatureDriftAuditReport(
            model_id=model_id,
            monitoring_window=monitoring_window,
            spearman_rank_correlation=rho,
            is_retrain_triggered=is_triggered,
            degraded_features=degraded,
            rank_details=details,
            status=status,
            audit_notes=notes
        )
