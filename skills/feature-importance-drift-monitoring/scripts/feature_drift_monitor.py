"""
feature-importance-drift-monitoring: compares a model's *baseline* (training-time)
feature-importance profile against a *live* (production-window) profile and decides
whether the ranking has drifted far enough to warrant revalidation or retraining.

Two independent detectors run on every audit:

1. **Rank agreement** - Spearman's rank correlation between the two importance
   rankings over the features common to both profiles. Spearman's rho is defined as
   the Pearson correlation of the *rank variables*; the familiar shortcut

       rho = 1 - 6 * sum(d_i^2) / (M * (M^2 - 1))

   is valid **only when every rank is a distinct integer**, i.e. when there are no
   ties. Feature-importance vectors tie constantly (unused features share an
   importance of exactly 0.0), so this module computes the Pearson correlation of
   mid-ranks - tied features receive the average of the positions they span - which
   reduces to the shortcut when no ties are present and is correct when they are.
   See ``compute_spearman_rank_correlation``.

2. **Top-feature degradation** - the share of total importance carried by each of
   the top-N baseline features, compared like-for-like against its live share. A
   drop beyond ``max_degradation_drop_pct`` is flagged even when the overall ranking
   still correlates well.

Importances are normalised to **shares of the total importance in their own
profile** before any magnitude comparison. Gain-based importances from an XGBoost
booster and mean |SHAP| values from an explainer are on entirely different scales;
comparing them raw makes every feature look like it lost ~100% of its importance.
Normalising makes the comparison scale-invariant. It does *not* make the two
metrics interchangeable - see the limitations below.

Limitations (documented, deliberate):

- **Same metric on both sides.** Baseline and live importances must be produced by
  the same method on the same kind of data (e.g. permutation importance on a
  held-out window, both times). Impurity/gain-based importance is strongly biased
  toward high-cardinality features and is not comparable with permutation or SHAP
  importance; normalisation hides the scale difference, not the definitional one.
- **Correlated features swap ranks for free.** When two features are correlated,
  permutation importance splits the credit between them and the split is unstable
  across samples. Rank churn between ``rsi_14`` and ``rsi_21`` is estimator noise,
  not regime change. Cluster correlated features and monitor one representative, or
  expect a permanently depressed rho.
- **Importance drift is not performance decay.** A model whose importance ranking is
  perfectly stable can still be losing money, and a model whose ranking has been
  reshuffled can still be profitable. This is a leading *indicator*, not a
  performance measure - pair it with live PnL / accuracy monitoring.
- **No statistical significance test.** With few features the coefficient is coarse
  and noisy: at M = 4 the attainable values of rho are the multiples of 0.2, and a
  uniformly random reordering of 4 features clears a 0.70 threshold 16.7% of the
  time (4 of 24 permutations). Thresholds are only meaningful once M is reasonably
  large; ``min_common_features`` enforces a floor of 3 but does not make M = 3
  informative.
- **Stateless and single-window.** Each call audits one window in isolation. There
  is no de-bouncing, so a single noisy window can trip the alert; require K
  consecutive breaches before acting on a retrain if window-to-window noise is
  material. ``monitoring_window`` is an opaque caller-supplied label - this module
  does not parse it, order windows, or detect stale inputs.
- **Triggering a retrain is not deploying one.** The report recommends revalidation;
  promoting a retrained model is a change to a live trading algorithm and belongs to
  the firm's change-control process (see ``references/standards.md``).
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Sequence

logger = logging.getLogger(__name__)

#: Default rank-agreement floor. A library default, not an industry or regulatory
#: standard - calibrate against the model's own window-to-window rho distribution.
DEFAULT_MIN_SPEARMAN_RANK_THRESHOLD = 0.70

#: Default single-feature degradation trigger: a top-N baseline feature that loses
#: more than this fraction of its importance *share*.
DEFAULT_MAX_DEGRADATION_DROP_PCT = 0.80

#: Number of top baseline features subject to the degradation check.
DEFAULT_TOP_N_MONITORED_FEATURES = 3

#: Hard floor on the number of common features. Below 3 the coefficient is
#: degenerate: at M = 2 the only attainable values are +1 and -1.
ABSOLUTE_MIN_COMMON_FEATURES = 3

STATUS_NORMAL = "FEATURE_STABILITY_NORMAL"
STATUS_ALERT = "FEATURE_DRIFT_ALERT_TRIGGERED"


@dataclass
class FeatureRankDetail:
    feature_name: str
    baseline_importance: float            # As supplied, un-normalised
    live_importance: float                # As supplied, un-normalised
    baseline_share: float                 # baseline_importance / sum(baseline profile)
    live_share: float                     # live_importance / sum(live profile)
    baseline_rank: float                  # Mid-rank, 1.0 = most important; ties share the average
    live_rank: float
    rank_difference: float                # live_rank - baseline_rank
    importance_ratio_live_to_base: float  # live_share / baseline_share (inf if base share is 0)
    is_top_n_baseline: bool               # Subject to the degradation check
    is_degraded: bool                     # Top-N and share dropped beyond the threshold


@dataclass
class FeatureDriftAuditReport:
    model_id: str
    monitoring_window: str
    spearman_rank_correlation: float      # Tie-corrected; over common features only
    is_retrain_triggered: bool
    degraded_features: List[str]
    rank_details: List[FeatureRankDetail]
    status: str                           # STATUS_NORMAL | STATUS_ALERT
    audit_notes: str
    common_feature_count: int = 0
    baseline_only_features: List[str] = field(default_factory=list)  # Dropped from live
    live_only_features: List[str] = field(default_factory=list)      # New in live
    feature_set_overlap_ratio: float = 1.0  # |common| / |union|
    top_n_rank_churn: int = 0             # Baseline top-N features no longer in the live top-N
    trigger_reasons: List[str] = field(default_factory=list)


def _validate_importance_map(label: str, importance_map: Mapping[str, float]) -> None:
    """
    Rejects an importance profile that cannot produce a meaningful ranking.

    Fails closed on every unusable input rather than ranking it anyway: a NaN sorts
    unpredictably, and an all-zero profile has no ordering at all. Either one
    silently yields a plausible-looking coefficient built on nothing.
    """
    if not isinstance(importance_map, Mapping):
        raise TypeError(
            f"{label} importance map must be a mapping, got {type(importance_map).__name__}."
        )
    if not importance_map:
        raise ValueError(f"{label} importance map is empty.")

    positive_seen = False
    for name, value in importance_map.items():
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"{label} importance map has a non-string or empty feature name: {name!r}."
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(
                f"{label} importance for '{name}' must be numeric, got {type(value).__name__}."
            )
        if not math.isfinite(value):
            raise ValueError(
                f"{label} importance for '{name}' is {value!r}. Non-finite importances cannot be "
                "ranked; fix the explainer output before monitoring."
            )
        if value < 0.0:
            raise ValueError(
                f"{label} importance for '{name}' is negative ({value}). A negative permutation "
                "importance means the feature scored no better than noise; clip it to 0.0 before "
                "monitoring rather than ranking a negative contribution."
            )
        if value > 0.0:
            positive_seen = True

    if not positive_seen:
        raise ValueError(
            f"{label} importance map is entirely zero - there is no ranking to compare. This is a "
            "broken explainer or an untrained model, not a stable one."
        )


def _compute_mid_ranks(features: Sequence[str], values: Mapping[str, float]) -> Dict[str, float]:
    """
    Assigns mid-ranks in descending importance order: 1.0 is the most important
    feature, and tied features all receive the average of the positions they span
    (three features tied across positions 3, 4 and 5 each receive 4.0).

    Mid-ranking is what makes the Pearson-on-ranks definition of Spearman's rho
    correct in the presence of ties, and it removes the dependency on dictionary or
    alphabetical insertion order that naive positional ranking has.
    """
    ordered = sorted(features, key=lambda f: values[f], reverse=True)
    ranks: Dict[str, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and values[ordered[j + 1]] == values[ordered[i]]:
            j += 1
        mid_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[ordered[k]] = mid_rank
        i = j + 1
    return ranks


def _importance_shares(importance_map: Mapping[str, float]) -> Dict[str, float]:
    """
    Normalises a profile to shares of its own total importance, making magnitude
    comparisons across differently-scaled importance metrics meaningful. The total is
    strictly positive because ``_validate_importance_map`` rejects all-zero and
    negative profiles.
    """
    total = float(sum(importance_map.values()))
    return {name: float(value) / total for name, value in importance_map.items()}


class FeatureImportanceDriftMonitorEngine:
    """
    Quantitative MLOps engine for tracking feature importance drift, computing
    tie-corrected Spearman rank correlations between training baselines and live
    inference windows, and recommending model revalidation.
    """

    def __init__(
        self,
        min_spearman_rank_threshold: float = DEFAULT_MIN_SPEARMAN_RANK_THRESHOLD,
        max_degradation_drop_pct: float = DEFAULT_MAX_DEGRADATION_DROP_PCT,
        top_n_monitored_features: int = DEFAULT_TOP_N_MONITORED_FEATURES,
        min_common_features: int = ABSOLUTE_MIN_COMMON_FEATURES,
        min_feature_set_overlap_ratio: float = 1.0,
    ) -> None:
        if not -1.0 <= min_spearman_rank_threshold <= 1.0:
            raise ValueError(
                "min_spearman_rank_threshold must lie in [-1, 1], got "
                f"{min_spearman_rank_threshold}."
            )
        if not 0.0 < max_degradation_drop_pct < 1.0:
            raise ValueError(
                "max_degradation_drop_pct is the fraction of importance share a top feature may "
                f"lose and must lie in (0, 1); got {max_degradation_drop_pct}. A value of 1.0 can "
                "never trigger, because no positive share drops by a full 100%."
            )
        if top_n_monitored_features < 1:
            raise ValueError(
                f"top_n_monitored_features must be >= 1, got {top_n_monitored_features}."
            )
        if min_common_features < ABSOLUTE_MIN_COMMON_FEATURES:
            raise ValueError(
                f"min_common_features must be >= {ABSOLUTE_MIN_COMMON_FEATURES}; a rank correlation "
                "over fewer features is degenerate (at M = 2 only +1 and -1 are attainable). Got "
                f"{min_common_features}."
            )
        if not 0.0 < min_feature_set_overlap_ratio <= 1.0:
            raise ValueError(
                "min_feature_set_overlap_ratio must lie in (0, 1], got "
                f"{min_feature_set_overlap_ratio}."
            )

        self.min_spearman_rank_threshold = float(min_spearman_rank_threshold)
        self.max_degradation_drop_pct = float(max_degradation_drop_pct)
        self.top_n_monitored_features = int(top_n_monitored_features)
        self.min_common_features = int(min_common_features)
        self.min_feature_set_overlap_ratio = float(min_feature_set_overlap_ratio)

    def compute_spearman_rank_correlation(
        self,
        base_ranks: Sequence[float],
        live_ranks: Sequence[float],
    ) -> float:
        """
        Spearman's rho, computed by its general definition: the Pearson correlation of
        the two rank vectors.

        Accepts mid-ranks (floats). For distinct integer ranks this is numerically
        equivalent to the shortcut ``1 - 6*sum(d^2)/(M*(M^2-1))``; when ranks are tied
        the shortcut is simply wrong, so it is not used here.

        Raises if either rank vector is constant - a correlation with a zero-variance
        variable is undefined, and returning 1.0 there would report "perfectly stable"
        for a profile that carries no ordering at all.
        """
        m = len(base_ranks)
        if m != len(live_ranks):
            raise ValueError(
                f"Rank vectors must be the same length, got {m} and {len(live_ranks)}."
            )
        if m < 2:
            raise ValueError(f"Rank correlation requires at least 2 observations, got {m}.")

        mean_base = sum(base_ranks) / m
        mean_live = sum(live_ranks) / m
        cov = 0.0
        var_base = 0.0
        var_live = 0.0
        for r_base, r_live in zip(base_ranks, live_ranks):
            d_base = r_base - mean_base
            d_live = r_live - mean_live
            cov += d_base * d_live
            var_base += d_base * d_base
            var_live += d_live * d_live

        if var_base <= 0.0 or var_live <= 0.0:
            raise ValueError(
                "Rank correlation is undefined: every feature shares the same rank on at least one "
                "side, so that ranking carries no ordering information."
            )

        rho = cov / math.sqrt(var_base * var_live)
        # Guard against floating-point overshoot on perfectly (anti-)correlated ranks.
        return max(-1.0, min(1.0, rho))

    def audit_feature_importance_drift(
        self,
        model_id: str,
        monitoring_window: str,
        baseline_importance_map: Mapping[str, float],
        live_importance_map: Mapping[str, float],
    ) -> FeatureDriftAuditReport:
        """
        Audits feature importance drift between a baseline training profile and a live
        production window.

        ``monitoring_window`` is an opaque label recorded on the report; it is not
        parsed and carries no ordering or staleness semantics.

        Raises ``ValueError``/``TypeError`` on unusable input rather than returning a
        report. A raised exception is a *monitoring failure* that must be escalated -
        it is never evidence of stability.
        """
        _validate_importance_map("Baseline", baseline_importance_map)
        _validate_importance_map("Live", live_importance_map)

        baseline_names = set(baseline_importance_map)
        live_names = set(live_importance_map)
        common_features = sorted(baseline_names & live_names)
        baseline_only = sorted(baseline_names - live_names)
        live_only = sorted(live_names - baseline_names)
        union_size = len(baseline_names | live_names)

        if len(common_features) < self.min_common_features:
            raise ValueError(
                f"Only {len(common_features)} feature(s) are common to the baseline and live "
                f"profiles; at least {self.min_common_features} are required for a meaningful rank "
                "correlation. Supply explicit zero importances for features the live explainer "
                "omits rather than dropping them from the map."
            )

        overlap_ratio = len(common_features) / float(union_size)

        base_shares = _importance_shares(baseline_importance_map)
        live_shares = _importance_shares(live_importance_map)

        # Baseline ranks for the degradation check are taken over the *whole* baseline
        # profile, so a feature stays "top-3 of the model" even if the live profile
        # dropped it. Ranks fed to the correlation are recomputed over the common set,
        # which is the only set on which both sides are defined.
        full_base_rank_of = _compute_mid_ranks(sorted(baseline_names), base_shares)
        base_rank_of = _compute_mid_ranks(common_features, base_shares)
        live_rank_of = _compute_mid_ranks(common_features, live_shares)

        base_ranks = [base_rank_of[f] for f in common_features]
        live_ranks = [live_rank_of[f] for f in common_features]
        rho = self.compute_spearman_rank_correlation(base_ranks, live_ranks)

        top_n_cutoff = float(self.top_n_monitored_features)
        degradation_ratio_floor = 1.0 - self.max_degradation_drop_pct

        details: List[FeatureRankDetail] = []
        degraded: List[str] = []
        top_n_churn = 0

        for feature in common_features:
            base_share = base_shares[feature]
            live_share = live_shares[feature]
            r_base = base_rank_of[feature]
            r_live = live_rank_of[feature]
            is_top_n = full_base_rank_of[feature] <= top_n_cutoff
            ratio = (live_share / base_share) if base_share > 0.0 else math.inf
            is_degraded = is_top_n and base_share > 0.0 and ratio < degradation_ratio_floor

            if is_degraded:
                degraded.append(feature)
            if is_top_n and r_live > top_n_cutoff:
                top_n_churn += 1

            details.append(FeatureRankDetail(
                feature_name=feature,
                baseline_importance=float(baseline_importance_map[feature]),
                live_importance=float(live_importance_map[feature]),
                baseline_share=base_share,
                live_share=live_share,
                baseline_rank=r_base,
                live_rank=r_live,
                rank_difference=r_live - r_base,
                importance_ratio_live_to_base=ratio,
                is_top_n_baseline=is_top_n,
                is_degraded=is_degraded,
            ))

        # A top-N baseline feature the live profile does not report at all never
        # reaches the loop above. Absence is not evidence of stability: treat it as a
        # degradation of the most severe kind.
        dropped_top_n = [f for f in baseline_only if full_base_rank_of[f] <= top_n_cutoff]
        degraded.extend(dropped_top_n)
        top_n_churn += len(dropped_top_n)

        trigger_reasons: List[str] = []
        if rho < self.min_spearman_rank_threshold:
            trigger_reasons.append(
                f"rank agreement {rho:.4f} < {self.min_spearman_rank_threshold:.2f} over "
                f"{len(common_features)} common features"
            )
        if degraded:
            trigger_reasons.append(
                f"top-{self.top_n_monitored_features} baseline feature(s) lost more than "
                f"{self.max_degradation_drop_pct:.0%} of importance share or vanished: "
                f"{sorted(degraded)}"
            )
        if overlap_ratio < self.min_feature_set_overlap_ratio:
            trigger_reasons.append(
                f"feature-set overlap {overlap_ratio:.2%} < "
                f"{self.min_feature_set_overlap_ratio:.2%} (dropped from live: {baseline_only}, "
                f"new in live: {live_only})"
            )

        is_triggered = bool(trigger_reasons)
        if is_triggered:
            status = STATUS_ALERT
            notes = (
                f"FEATURE IMPORTANCE DRIFT ALERT [{model_id} - {monitoring_window}]: "
                + "; ".join(trigger_reasons)
                + ". Model REVALIDATION/RETRAIN RECOMMENDED - promoting any retrained model "
                  "remains subject to the firm's change-control and testing process."
            )
            logger.warning(notes)
        else:
            status = STATUS_NORMAL
            notes = (
                f"FEATURE IMPORTANCE STABLE [{model_id} - {monitoring_window}]: rank agreement "
                f"{rho:.4f} >= {self.min_spearman_rank_threshold:.2f} over "
                f"{len(common_features)} common features; no top-"
                f"{self.top_n_monitored_features} degradation."
            )
            logger.info(notes)

        return FeatureDriftAuditReport(
            model_id=model_id,
            monitoring_window=monitoring_window,
            spearman_rank_correlation=rho,
            is_retrain_triggered=is_triggered,
            degraded_features=sorted(degraded),
            rank_details=details,
            status=status,
            audit_notes=notes,
            common_feature_count=len(common_features),
            baseline_only_features=baseline_only,
            live_only_features=live_only,
            feature_set_overlap_ratio=overlap_ratio,
            top_n_rank_churn=top_n_churn,
            trigger_reasons=trigger_reasons,
        )
