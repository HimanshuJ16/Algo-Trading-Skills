"""
feature-selection-stability-across-folds: measures how reproducible a feature
selection procedure's output is across resampled folds, using the chance-corrected
stability estimator of Nogueira, Sechidis and Brown.

Estimator (Definition 4 of the source paper), with ``d`` = total candidate features
and ``M`` = number of feature sets (folds):

    Phi = 1 - [ (1/d) * sum_f s_f^2 ] / [ (k_bar/d) * (1 - k_bar/d) ]

    s_f^2 = M / (M - 1) * p_f * (1 - p_f)   unbiased sample variance of the selection
                                            indicator of feature f
    p_f   = fraction of the M folds that selected feature f
    k_bar = (1/M) * sum_i |S_i|             average number of features per fold

Properties, taken from the source paper rather than assumed:

- Phi = 1 if and only if all M feature sets are identical.
- E[Phi] = 0 under the null model of random selection, so Phi is corrected for
  chance and is comparable across different values of k_bar and d.
- Phi is bounded below by -1 / (M - 1): with 5 folds the minimum is -0.25. A
  negative Phi is not a bug, it means the folds agree *less* than random chance.
- Phi is UNDEFINED when k_bar = 0 or k_bar = d, because the denominator is zero.
  Those are the cases where the selector chose nothing, or everything, in every
  fold. This module reports DEGENERATE_SELECTION instead of inventing a value:
  returning 1.0 ("perfectly stable") for a selector that collapsed to an empty
  set would pass a stability gate on a broken pipeline.
- Phi is asymptotically normal (Theorem 7), which yields the variance estimate,
  the confidence interval (Corollary 8) and the one-sided test against a fixed
  threshold (Section 4.2.4) implemented below.

Source: Sarah Nogueira, Konstantinos Sechidis and Gavin Brown, "On the Stability of
Feature Selection Algorithms", Journal of Machine Learning Research, vol. 18 (2018),
pp. 1-54, https://jmlr.org/papers/v18/17-514.html. Authors' reference implementation:
https://github.com/nogueirs/JMLR2018 (``python/stability``).

Limitations (documented, deliberate):

- **Independence assumption.** Theorem 7 assumes each fold's selection is an
  independent sample; the paper measures stability over bootstrap resamples.
  Walk-forward and purged k-fold splits of a financial series share training data
  and are serially dependent, so Phi is optimistic and the confidence interval is
  narrower than the truth. Read both as an upper bound on the evidence.
- **Stability is not predictive utility.** A selector returning the same arbitrary
  subset every fold scores Phi = 1. Stability must be read alongside out-of-sample
  performance, never instead of it.
- **d must be the full candidate pool.** Phi is defined relative to the number of
  features the selector could have chosen. Passing only the union of the selected
  features changes the value, and the pool must be identical across folds.
- **The asymptotics are in M (folds), not in rows per fold.** With the small fold
  counts typical of walk-forward validation the interval is approximate; the paper
  reports acceptable coverage for moderate M but it is not exact.
- **No numpy/scipy dependency.** The normal CDF uses ``math.erfc`` and its inverse
  is obtained by bisection, so results are deterministic and stdlib-only.
"""
import logging
import math
from collections import Counter
from collections.abc import Iterable as IterableABC
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Feature selection is reproducible enough to promote the consensus set.
STATUS_STABLE = "STABLE_FEATURE_SET"

#: Selection varies across folds beyond the configured tolerance.
STATUS_UNSTABLE = "UNSTABLE_OVERFITTED_FEATURE_SET"

#: k_bar = 0 or k_bar = d, so Phi is mathematically undefined (zero denominator).
STATUS_DEGENERATE = "DEGENERATE_SELECTION"

#: House minimum fold count. Not a published standard — the estimator only requires
#: M > 1, but its confidence interval is asymptotic in M and is untrustworthy below
#: this. Falling short raises a warning, not an error.
MIN_RECOMMENDED_FOLDS = 5

#: Default two-sided coverage for the reported confidence interval (Corollary 8).
DEFAULT_CONFIDENCE_LEVEL = 0.95


def normal_cdf(x: float) -> float:
    """Standard normal CDF, exact to double precision via ``math.erfc``."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def normal_quantile(p: float) -> float:
    """
    Inverse standard normal CDF by bisection on :func:`normal_cdf`.

    Avoids a scipy dependency for the handful of quantiles this module needs.
    Converges to ~1e-13 absolute; ``normal_quantile(0.975)`` reproduces 1.959964.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"Quantile probability must lie strictly in (0, 1); got {p}.")
    low, high = -40.0, 40.0
    for _ in range(200):
        mid = 0.5 * (low + high)
        if normal_cdf(mid) < p:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


@dataclass
class FeatureInclusionDetail:
    feature_name: str
    selection_count_folds: int
    inclusion_probability: float        # p_f = selection_count_folds / K, unrounded
    is_consensus_feature: bool          # selection_count_folds >= min_folds_for_consensus


@dataclass
class StabilityStatistics:
    """Nogueira stability estimate with the paper's uncertainty quantification."""
    nogueira_stability_index_phi: Optional[float]   # None when the estimator is undefined
    average_features_per_fold: float
    variance: Optional[float]
    confidence_level: float
    ci_lower: Optional[float]
    ci_upper: Optional[float]
    phi_theoretical_lower_bound: float              # -1 / (K - 1)
    is_degenerate: bool
    degenerate_reason: str
    inclusion_details: List[FeatureInclusionDetail] = field(default_factory=list)


@dataclass
class FeatureStabilityAuditReport:
    total_candidate_features_M: int
    total_folds_K: int
    nogueira_stability_index_phi: Optional[float]
    average_features_per_fold: float
    consensus_features_count: int
    pruned_unstable_features_count: int
    consensus_feature_names: List[str]
    pruned_feature_names: List[str]
    inclusion_details: List[FeatureInclusionDetail]
    status: str                         # STATUS_STABLE / STATUS_UNSTABLE / STATUS_DEGENERATE
    audit_notes: str
    # --- uncertainty quantification (Nogueira et al. Theorem 7 / Corollary 8) ---
    stability_variance: Optional[float] = None
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL
    stability_ci_lower: Optional[float] = None
    stability_ci_upper: Optional[float] = None
    stability_test_p_value: Optional[float] = None
    stability_significantly_above_threshold: bool = False
    phi_theoretical_lower_bound: Optional[float] = None
    # --- audit context ---
    min_folds_for_consensus: int = 0
    folds_below_recommended_minimum: bool = False
    is_degenerate: bool = False


class FeatureStabilityAnalyzerEngine:
    """
    Measures feature selection stability across cross-validation folds using the
    chance-corrected Nogueira estimator, reports its confidence interval and
    significance against the configured threshold, and separates consensus
    features from fold-specific noise.

    Thresholds are engineering defaults, not published standards: the source paper
    defines the estimator and its sampling distribution but prescribes no cut-off.
    Calibrate both against your own validation record before relying on them.
    """

    def __init__(
        self,
        min_nogueira_stability_threshold: float = 0.70,
        min_inclusion_threshold: float = 0.80,
        confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    ) -> None:
        if not 0.0 <= min_nogueira_stability_threshold <= 1.0:
            raise ValueError(
                "min_nogueira_stability_threshold must lie in [0.0, 1.0]; got "
                f"{min_nogueira_stability_threshold}."
            )
        if not 0.0 < min_inclusion_threshold <= 1.0:
            raise ValueError(
                f"min_inclusion_threshold must lie in (0.0, 1.0]; got {min_inclusion_threshold}."
            )
        if not 0.0 < confidence_level < 1.0:
            raise ValueError(
                f"confidence_level must lie strictly in (0.0, 1.0); got {confidence_level}."
            )
        self.min_nogueira_stability_threshold = min_nogueira_stability_threshold
        self.min_inclusion_threshold = min_inclusion_threshold
        self.confidence_level = confidence_level

    # ------------------------------------------------------------------ inputs

    @staticmethod
    def _validate_inputs(
        candidate_features: Sequence[str],
        fold_selections: Sequence[Iterable[str]],
    ) -> Tuple[List[str], List[FrozenSet[str]]]:
        """
        Normalises and validates the selection matrix.

        Rejects the input conditions that silently corrupt Phi rather than raising:
        an unknown feature inflates k_bar without contributing a p_f, and a repeated
        candidate name inflates d. Both shift the estimate with no visible error.
        """
        if not candidate_features:
            raise ValueError("candidate_features must contain at least one feature.")
        features = list(candidate_features)
        if any(not isinstance(f, str) for f in features):
            raise ValueError("candidate_features must contain only feature-name strings.")
        duplicates = sorted(name for name, n in Counter(features).items() if n > 1)
        if duplicates:
            raise ValueError(
                f"candidate_features contains duplicate names, which inflates d: {duplicates}."
            )

        folds = list(fold_selections)
        if len(folds) <= 1:
            raise ValueError(
                f"At least 2 folds are required to measure stability; got {len(folds)}."
            )

        candidate_set = set(features)
        normalised: List[FrozenSet[str]] = []
        for idx, fold in enumerate(folds):
            if isinstance(fold, str) or not isinstance(fold, IterableABC):
                raise ValueError(f"Fold {idx} must be an iterable of feature names.")
            selected = frozenset(fold)
            unknown = sorted(selected - candidate_set)
            if unknown:
                raise ValueError(
                    f"Fold {idx} selected features absent from candidate_features: {unknown}. "
                    "The candidate pool must be identical across folds, otherwise k_bar and "
                    "the inclusion frequencies are computed over different feature sets."
                )
            normalised.append(selected)

        return features, normalised

    def _min_folds_for_consensus(self, total_folds: int) -> int:
        """
        Fold count a feature must reach to be a consensus feature.

        Compared on integer counts rather than on ``p_f >= threshold`` so the
        boundary is exact: p_f is a ratio of small integers and float comparison at
        the threshold is not reliable across fold counts.
        """
        return max(1, math.ceil(self.min_inclusion_threshold * total_folds - 1e-9))

    # -------------------------------------------------------------- estimation

    def compute_stability_statistics(
        self,
        candidate_features: Sequence[str],
        fold_selections: Sequence[Iterable[str]],
    ) -> StabilityStatistics:
        """
        Computes Phi, its estimated variance, and the confidence interval.

        Variance follows Theorem 7 of the source paper:

            v(Phi) = (4 / M^2) * sum_i (phi_i - mean(phi))^2
            phi_i  = (1/denom) * [ (1/d) * sum_f z_if * p_f
                                   - k_i * k_bar / d^2
                                   + (Phi/2) * (2*k_i*k_bar/d^2 - k_i/d - k_bar/d + 1) ]

        with ``denom = (k_bar/d) * (1 - k_bar/d)``.
        """
        features, folds = self._validate_inputs(candidate_features, fold_selections)
        d = len(features)
        K = len(folds)

        selection_counts = [sum(1 for fold in folds if f in fold) for f in features]
        p_list = [count / K for count in selection_counts]
        min_folds = self._min_folds_for_consensus(K)

        inclusion_details = [
            FeatureInclusionDetail(
                feature_name=name,
                selection_count_folds=count,
                inclusion_probability=p,
                is_consensus_feature=count >= min_folds,
            )
            for name, count, p in zip(features, selection_counts, p_list)
        ]

        fold_sizes = [len(fold) for fold in folds]
        total_selected = sum(fold_sizes)
        k_bar = total_selected / K
        phi_lower_bound = -1.0 / (K - 1)

        # Degenerate cases are detected on integer totals so the test is exact:
        # nothing selected anywhere, or everything selected everywhere. The paper's
        # denominator is zero here and Phi is undefined, NOT equal to 1.0.
        if total_selected == 0 or total_selected == d * K:
            reason = (
                "no features were selected in any fold"
                if total_selected == 0
                else "every candidate feature was selected in every fold"
            )
            logger.warning(
                "Nogueira stability is undefined (k_bar=%.4f, d=%d): %s.", k_bar, d, reason
            )
            return StabilityStatistics(
                nogueira_stability_index_phi=None,
                average_features_per_fold=k_bar,
                variance=None,
                confidence_level=self.confidence_level,
                ci_lower=None,
                ci_upper=None,
                phi_theoretical_lower_bound=phi_lower_bound,
                is_degenerate=True,
                degenerate_reason=reason,
                inclusion_details=inclusion_details,
            )

        denominator = (k_bar / d) * (1.0 - k_bar / d)
        mean_sample_variance = (K / (K - 1)) * sum(p * (1.0 - p) for p in p_list) / d
        phi = 1.0 - mean_sample_variance / denominator

        per_fold_phi: List[float] = []
        for fold, k_i in zip(folds, fold_sizes):
            selected_p_sum = sum(p_list[i] for i, name in enumerate(features) if name in fold)
            per_fold_phi.append(
                (
                    selected_p_sum / d
                    - (k_i * k_bar) / (d ** 2)
                    + (phi / 2.0)
                    * ((2.0 * k_i * k_bar) / (d ** 2) - k_i / d - k_bar / d + 1.0)
                )
                / denominator
            )
        mean_per_fold_phi = sum(per_fold_phi) / K
        variance = (4.0 / (K ** 2)) * sum((v - mean_per_fold_phi) ** 2 for v in per_fold_phi)
        # Floating-point cancellation can drive a mathematically zero variance
        # slightly negative; clamp rather than propagate a NaN through sqrt.
        variance = max(variance, 0.0)

        z = normal_quantile(1.0 - (1.0 - self.confidence_level) / 2.0)
        half_width = z * math.sqrt(variance)

        return StabilityStatistics(
            nogueira_stability_index_phi=phi,
            average_features_per_fold=k_bar,
            variance=variance,
            confidence_level=self.confidence_level,
            ci_lower=phi - half_width,
            ci_upper=phi + half_width,
            phi_theoretical_lower_bound=phi_lower_bound,
            is_degenerate=False,
            degenerate_reason="",
            inclusion_details=inclusion_details,
        )

    def calculate_nogueira_index(
        self,
        candidate_features: Sequence[str],
        fold_selections: Sequence[Iterable[str]],
    ) -> Tuple[Optional[float], float, List[FeatureInclusionDetail]]:
        """
        Returns ``(phi, k_bar, inclusion_details)``.

        ``phi`` is ``None`` when the estimator is undefined (k_bar = 0 or k_bar = d);
        callers must handle that case rather than comparing it to a threshold.
        """
        stats = self.compute_stability_statistics(candidate_features, fold_selections)
        return (
            stats.nogueira_stability_index_phi,
            stats.average_features_per_fold,
            stats.inclusion_details,
        )

    def test_stability_against_threshold(
        self,
        phi: float,
        variance: float,
        threshold: Optional[float] = None,
    ) -> Tuple[float, bool]:
        """
        One-sided test of H0: Phi = threshold against H1: Phi > threshold
        (source paper, Section 4.2.4). Returns ``(p_value, is_significant)`` at
        significance level ``1 - confidence_level``.

        A zero variance estimate means every fold contributed the same influence —
        which includes the perfectly reproducible case — so the comparison is made
        directly rather than dividing by zero.
        """
        threshold = self.min_nogueira_stability_threshold if threshold is None else threshold
        alpha = 1.0 - self.confidence_level
        if variance <= 0.0:
            return (0.0, True) if phi > threshold else (1.0, False)
        v_statistic = (phi - threshold) / math.sqrt(variance)
        p_value = 1.0 - normal_cdf(v_statistic)
        return p_value, p_value <= alpha

    # ------------------------------------------------------------------- audit

    def audit_feature_stability(
        self,
        candidate_features: Sequence[str],
        fold_selections: Sequence[Iterable[str]],
    ) -> FeatureStabilityAuditReport:
        """
        Audits selection stability across K folds, extracting the consensus feature
        set and pruning features that only survive in a minority of folds.

        The returned ``status`` is driven by the Phi point estimate, preserving the
        original gate. ``stability_significantly_above_threshold`` reports whether
        that verdict survives the sampling uncertainty — a point estimate marginally
        above the threshold on five folds routinely does not.
        """
        folds = list(fold_selections)
        stats = self.compute_stability_statistics(candidate_features, folds)
        details = stats.inclusion_details
        M = len(details)
        K = len(folds)
        min_folds = self._min_folds_for_consensus(K)
        below_min_folds = K < MIN_RECOMMENDED_FOLDS
        if below_min_folds:
            logger.warning(
                "Stability measured over K=%d folds, below the recommended minimum of %d. "
                "The confidence interval is asymptotic in the number of folds and is "
                "unreliable at this size.",
                K,
                MIN_RECOMMENDED_FOLDS,
            )

        consensus_features = [d.feature_name for d in details if d.is_consensus_feature]
        pruned_features = [d.feature_name for d in details if not d.is_consensus_feature]
        phi = stats.nogueira_stability_index_phi

        if stats.is_degenerate:
            status = STATUS_DEGENERATE
            p_value: Optional[float] = None
            significant = False
            notes = (
                f"FEATURE SELECTION DEGENERATE (K={K} folds, M={M} candidate features): "
                f"Nogueira Index is undefined because {stats.degenerate_reason} "
                f"(k_bar={stats.average_features_per_fold:.4f}). The selector is not "
                "discriminating between features, so stability carries no information — "
                "fix the selection step before reading this audit as a pass or a fail."
            )
            logger.error(notes)
        else:
            p_value, significant = self.test_stability_against_threshold(
                phi, stats.variance or 0.0
            )
            if phi >= self.min_nogueira_stability_threshold:
                status = STATUS_STABLE
                notes = (
                    f"FEATURE SELECTION STABLE (K={K} folds, M={M} candidate features): "
                    f"Nogueira Index Phi = {phi:.4f} >= "
                    f"{self.min_nogueira_stability_threshold:.2f} "
                    f"[{self.confidence_level * 100:.0f}% CI {stats.ci_lower:.4f} to "
                    f"{stats.ci_upper:.4f}, one-sided p={p_value:.4f}"
                    f"{'' if significant else ', NOT significant'}]. "
                    f"Retained {len(consensus_features)} consensus features "
                    f"(selected in >= {min_folds}/{K} folds). "
                    f"Pruned {len(pruned_features)} unstable features."
                )
                logger.info(notes)
            else:
                status = STATUS_UNSTABLE
                notes = (
                    f"FEATURE SELECTION UNSTABLE (K={K} folds, M={M} candidate features): "
                    f"Nogueira Index Phi = {phi:.4f} < "
                    f"{self.min_nogueira_stability_threshold:.2f} threshold "
                    f"[{self.confidence_level * 100:.0f}% CI {stats.ci_lower:.4f} to "
                    f"{stats.ci_upper:.4f}]. Selection varies across CV folds; "
                    f"recommend pruning {len(pruned_features)} unstable features and "
                    "re-validating before the consensus set is trained on."
                )
                logger.warning(notes)

        return FeatureStabilityAuditReport(
            total_candidate_features_M=M,
            total_folds_K=K,
            nogueira_stability_index_phi=phi,
            average_features_per_fold=stats.average_features_per_fold,
            consensus_features_count=len(consensus_features),
            pruned_unstable_features_count=len(pruned_features),
            consensus_feature_names=consensus_features,
            pruned_feature_names=pruned_features,
            inclusion_details=details,
            status=status,
            audit_notes=notes,
            stability_variance=stats.variance,
            confidence_level=stats.confidence_level,
            stability_ci_lower=stats.ci_lower,
            stability_ci_upper=stats.ci_upper,
            stability_test_p_value=p_value,
            stability_significantly_above_threshold=significant,
            phi_theoretical_lower_bound=stats.phi_theoretical_lower_bound,
            min_folds_for_consensus=min_folds,
            folds_below_recommended_minimum=below_min_folds,
            is_degenerate=stats.is_degenerate,
        )
