"""
feature-engineering-cost-benefit-tracking: feature ROI calculator, latency-aware
cost auditor, and correlation-group-aware feature pruning advisor.

Design notes
------------
* **Units.** `importance_score` is a *fraction* of the model's score, on the same
  scale as `min_importance_threshold` (0.05 = a 5 percentage-point score drop when
  the feature is permuted). Passing 5.0 for "5%" against a 0.01 threshold silently
  keeps every feature, so a unit mismatch is warned about explicitly rather than
  left to be discovered in production.

* **Negative importance is legal.** Permuting a pure-noise feature can *improve*
  the held-out score, which scikit-learn reports as a negative importance. That is
  a strong prune signal, not an input error, so it is accepted and evaluated.

* **ROI is a ranking statistic, not the decision rule.** `roi_ratio` is
  `importance * 100 / max(roi_cost_floor_usd, monthly_cost_usd)` -- importance
  percentage points per USD/month, with the denominator floored so that near-zero-
  cost features do not produce unbounded ROI. Because the floor makes ROI flat
  below it, ROI is used to *triage* REVIEW features, while KEEP/REVIEW/PRUNE is
  decided by explicit thresholds on importance, cost, and latency.

* **Correlation dilution.** When two features are correlated and one is permuted,
  the model still reads the signal through the other, so *both* report a lower
  importance than they deserve (scikit-learn, "Permutation feature importance";
  Strobl et al. 2008). Pruning each in isolation is therefore unsafe. Features may
  declare a shared `group`; `audit_pipeline` refuses to prune a group member while
  the group's *aggregate* importance clears the threshold. Summing permutation
  importances is a conservative heuristic -- the sum is only exactly meaningful for
  Shapley-based attributions, which are additive by construction (Lundberg & Lee
  2017, local accuracy).

* **Shared licence cost.** `group` (statistical) and `cost_pool` (economic) are
  deliberately separate: one vendor feed can supply two uncorrelated features, and
  two correlated features can come from different feeds. For a feature in a
  `cost_pool`, `cost_usd` is its attributed *share* of one shared feed/licence.
  Dropping one member of a shared licence saves nothing, so a pooled feature's cost
  counts toward `potential_monthly_savings_usd` only when every member of its pool
  is pruned.

* **Latency is a cost.** `compute_latency_ms` is load-bearing when `max_latency_ms`
  is configured: a feature that blows the per-feature inference latency budget is
  prunable on latency alone, regardless of dollar cost.

* **Estimation noise.** Permutation importance is an average over `n_repeats`
  shuffles and carries a standard deviation. When `importance_std` is supplied, a
  feature is only pruned if it sits confidently below the threshold
  (`importance + prune_confidence_sigma * std < min_importance`) -- a one-sided
  normal approximation used as a guard, not a formal hypothesis test.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

logger = logging.getLogger(__name__)

_REQUIRED_KEYS = ("name", "importance", "cost_usd")
_OPTIONAL_KEYS = ("latency_ms", "group", "cost_pool", "importance_std")


class FeatureCostBenefitError(ValueError):
    """Raised on malformed feature records or infeasible tracker configuration."""


class Recommendation(str, Enum):
    """Pruning verdict. Subclasses str so `rec.recommendation == "PRUNE"` holds."""

    KEEP = "KEEP"
    REVIEW = "REVIEW"
    PRUNE = "PRUNE"


@dataclass
class FeatureCostBenefitRecord:
    feature_name: str
    importance_score: float           # fraction of model score, e.g. 0.05 = 5 pp
    monthly_cost_usd: float           # attributed USD/month (share of a shared licence)
    compute_latency_ms: float         # per-inference compute cost, milliseconds
    roi_ratio: float                  # importance pp per USD/month (cost floored)
    recommendation: str               # KEEP, REVIEW, PRUNE
    rationale: str
    group: Optional[str] = None       # correlated feature cluster (importance dilution)
    cost_pool: Optional[str] = None   # shared feed/licence (savings realization)
    importance_std: Optional[float] = None   # std of importance across shuffles


@dataclass
class FeatureCostBenefitReport:
    total_features_analyzed: int
    total_monthly_cost_usd: float
    kept_features_count: int          # KEEP only -- REVIEW is counted separately
    pruned_features_count: int
    potential_monthly_savings_usd: float
    feature_records: List[FeatureCostBenefitRecord]
    message: str
    review_features_count: int = 0
    total_compute_latency_ms: float = 0.0
    pruned_latency_ms: float = 0.0


def _require_finite(value: Any, label: str) -> float:
    """Rejects NaN/Inf/bool/non-numeric before it can reach a threshold comparison.

    A NaN importance is the dangerous case: every `<` comparison against NaN is
    False, so an unvalidated NaN falls through every prune branch and is reported
    as KEEP.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeatureCostBenefitError(f"{label}: expected a number, got {value!r}.")
    out = float(value)
    if not math.isfinite(out):
        raise FeatureCostBenefitError(f"{label}: non-finite value {value!r}.")
    return out


class FeatureCostBenefitTracker:
    """
    Evaluates each feature's marginal predictive contribution against its monthly
    licensing/compute cost and its inference latency, and recommends KEEP, REVIEW,
    or PRUNE.

    Threshold defaults are *illustrative starting points*, not industry standards.
    Calibrate `max_acceptable_cost_usd` and `high_cost_review_usd` against the
    strategy's actual P&L per unit of model score, and `min_importance_threshold`
    against the noise floor of the importance estimator.
    """

    def __init__(
        self,
        min_importance_threshold: float = 0.01,   # 1 pp score contribution minimum
        max_acceptable_cost_usd: float = 50.0,    # above this, low importance -> PRUNE
        high_cost_review_usd: float = 500.0,      # above this, even useful features are re-checked
        max_latency_ms: Optional[float] = None,   # per-feature inference budget; None disables
        roi_cost_floor_usd: float = 1.0,          # ROI denominator floor
        prune_confidence_sigma: float = 1.0,      # sigma margin required before pruning
    ):
        self.min_importance = _require_finite(min_importance_threshold, "min_importance_threshold")
        self.max_cost = _require_finite(max_acceptable_cost_usd, "max_acceptable_cost_usd")
        self.high_cost_review_usd = _require_finite(high_cost_review_usd, "high_cost_review_usd")
        self.roi_cost_floor_usd = _require_finite(roi_cost_floor_usd, "roi_cost_floor_usd")
        self.prune_confidence_sigma = _require_finite(
            prune_confidence_sigma, "prune_confidence_sigma"
        )

        if self.max_cost < 0.0:
            raise FeatureCostBenefitError("max_acceptable_cost_usd must be >= 0.")
        if self.high_cost_review_usd < self.max_cost:
            raise FeatureCostBenefitError(
                "high_cost_review_usd must be >= max_acceptable_cost_usd; otherwise the "
                "high-cost review band is empty and the rules are contradictory."
            )
        if self.roi_cost_floor_usd <= 0.0:
            raise FeatureCostBenefitError(
                "roi_cost_floor_usd must be > 0 to keep the ROI denominator bounded away "
                "from zero."
            )
        if self.prune_confidence_sigma < 0.0:
            raise FeatureCostBenefitError("prune_confidence_sigma must be >= 0.")

        if max_latency_ms is None:
            self.max_latency_ms: Optional[float] = None
        else:
            self.max_latency_ms = _require_finite(max_latency_ms, "max_latency_ms")
            if self.max_latency_ms <= 0.0:
                raise FeatureCostBenefitError("max_latency_ms must be > 0 when set.")

    # ------------------------------------------------------------------ #
    # Single-feature evaluation
    # ------------------------------------------------------------------ #
    def evaluate_feature(
        self,
        feature_name: str,
        importance_score: float,
        monthly_cost_usd: float,
        compute_latency_ms: float = 0.0,
        importance_std: Optional[float] = None,
        group: Optional[str] = None,
        cost_pool: Optional[str] = None,
    ) -> FeatureCostBenefitRecord:
        """Evaluates one feature in isolation.

        This is a *marginal* view. It cannot see correlation between features, so a
        PRUNE returned here for a member of a correlated cluster is provisional --
        `audit_pipeline` re-checks it against the cluster's aggregate importance
        before the verdict is final.
        """
        if not isinstance(feature_name, str) or not feature_name.strip():
            raise FeatureCostBenefitError("feature_name must be a non-empty string.")

        importance = _require_finite(importance_score, f"{feature_name}.importance_score")
        cost = _require_finite(monthly_cost_usd, f"{feature_name}.monthly_cost_usd")
        latency = _require_finite(compute_latency_ms, f"{feature_name}.compute_latency_ms")

        if cost < 0.0:
            raise FeatureCostBenefitError(
                f"{feature_name}.monthly_cost_usd must be >= 0, got {cost}."
            )
        if latency < 0.0:
            raise FeatureCostBenefitError(
                f"{feature_name}.compute_latency_ms must be >= 0, got {latency}."
            )

        std: Optional[float] = None
        if importance_std is not None:
            std = _require_finite(importance_std, f"{feature_name}.importance_std")
            if std < 0.0:
                raise FeatureCostBenefitError(
                    f"{feature_name}.importance_std must be >= 0, got {std}."
                )

        for label, tag in (("group", group), ("cost_pool", cost_pool)):
            if tag is not None and (not isinstance(tag, str) or not tag.strip()):
                raise FeatureCostBenefitError(
                    f"{feature_name}.{label} must be a non-empty string when provided."
                )

        # Unit mismatch guard: a threshold on the fraction scale compared against a
        # score on the percent scale keeps everything, silently.
        if self.min_importance < 1.0 <= importance:
            logger.warning(
                "Feature '%s': importance_score=%.4f exceeds 1.0 while "
                "min_importance_threshold=%.4f is on the fraction scale. Likely a "
                "percent/fraction unit mismatch (use 0.05 for 5 percent, not 5.0).",
                feature_name,
                importance,
                self.min_importance,
            )

        roi = (importance * 100.0) / max(self.roi_cost_floor_usd, cost)

        over_latency_budget = self.max_latency_ms is not None and latency > self.max_latency_ms
        below_threshold = importance < self.min_importance
        # With no std supplied the confidence gate is a no-op and the rules reduce
        # to the plain threshold comparison.
        confidently_below = below_threshold and (
            std is None or (importance + self.prune_confidence_sigma * std) < self.min_importance
        )

        if below_threshold and not confidently_below:
            rec = Recommendation.REVIEW
            rat = (
                f"Importance {importance:.6g} is below the {self.min_importance:.6g} "
                f"threshold but not by {self.prune_confidence_sigma:g} sigma "
                f"(std={std:.4f}); the estimate is not distinguishable from the "
                f"threshold. Re-measure with more shuffles before pruning."
            )
        elif confidently_below and (cost > self.max_cost or over_latency_budget):
            rec = Recommendation.PRUNE
            driver = (
                f"cost (${cost:,.2f}/mo > ${self.max_cost:,.2f}/mo)"
                if cost > self.max_cost
                else f"latency ({latency:.2f} ms > {self.max_latency_ms:.2f} ms budget)"
            )
            rat = f"Low importance ({importance:.6g}) and prohibitive {driver}."
            logger.warning("PRUNE recommended for '%s': %s", feature_name, rat)
        elif below_threshold:
            rec = Recommendation.REVIEW
            rat = (
                f"Low importance ({importance:.6g}) but cost ${cost:,.2f}/mo is within "
                f"the ${self.max_cost:,.2f}/mo budget. Prune only if the slot is needed."
            )
        elif over_latency_budget:
            rec = Recommendation.REVIEW
            rat = (
                f"Useful importance ({importance:.6g}) but latency {latency:.2f} ms "
                f"exceeds the {self.max_latency_ms:.2f} ms per-feature budget. Optimize "
                f"the computation or accept the latency explicitly."
            )
            logger.warning("Latency budget breach for '%s': %s", feature_name, rat)
        elif cost > self.high_cost_review_usd:
            rec = Recommendation.REVIEW
            rat = (
                f"Useful importance ({importance:.6g}) but very expensive "
                f"(${cost:,.2f}/mo > ${self.high_cost_review_usd:,.2f}/mo). Confirm the "
                f"marginal P&L covers the licence before renewing."
            )
        else:
            rec = Recommendation.KEEP
            rat = (
                f"Good ROI: importance={importance:.6g}, cost=${cost:,.2f}/mo, "
                f"latency={latency:.2f} ms."
            )
            logger.info("KEEP recommended for '%s': %s", feature_name, rat)

        return FeatureCostBenefitRecord(
            feature_name=feature_name,
            importance_score=round(importance, 6),
            monthly_cost_usd=round(cost, 2),
            compute_latency_ms=round(latency, 3),
            roi_ratio=round(roi, 4),
            recommendation=rec.value,
            rationale=rat,
            group=group,
            cost_pool=cost_pool,
            importance_std=None if std is None else round(std, 6),
        )

    # ------------------------------------------------------------------ #
    # Pipeline audit
    # ------------------------------------------------------------------ #
    def audit_pipeline(
        self,
        features: Sequence[Mapping[str, Any]],
    ) -> FeatureCostBenefitReport:
        """Audits a whole feature set, applying correlation-group and shared-cost rules.

        Each entry requires `name`, `importance`, `cost_usd` and may carry
        `latency_ms`, `group`, `importance_std`.
        """
        if isinstance(features, (str, bytes)) or not isinstance(features, Sequence):
            raise FeatureCostBenefitError("features must be a sequence of mappings.")

        records: List[FeatureCostBenefitRecord] = []
        seen_names: Set[str] = set()

        for idx, f in enumerate(features):
            if not isinstance(f, Mapping):
                raise FeatureCostBenefitError(
                    f"features[{idx}]: expected a mapping, got {type(f).__name__}."
                )
            missing = [k for k in _REQUIRED_KEYS if k not in f]
            if missing:
                raise FeatureCostBenefitError(
                    f"features[{idx}]: missing required key(s) {missing}. "
                    f"Required {list(_REQUIRED_KEYS)}, optional {list(_OPTIONAL_KEYS)}."
                )

            name = f["name"]
            if not isinstance(name, str) or not name.strip():
                raise FeatureCostBenefitError(
                    f"features[{idx}]: name must be a non-empty string."
                )
            if name in seen_names:
                # Duplicates would double-count cost and corrupt the savings figure.
                raise FeatureCostBenefitError(
                    f"features[{idx}]: duplicate feature name {name!r}; feature names "
                    f"must be unique within an audit."
                )
            seen_names.add(name)

            records.append(
                self.evaluate_feature(
                    name,
                    f["importance"],
                    f["cost_usd"],
                    f.get("latency_ms", 0.0),
                    importance_std=f.get("importance_std"),
                    group=f.get("group"),
                    cost_pool=f.get("cost_pool"),
                )
            )

        self._apply_group_dilution_guard(records)
        return self._build_report(records)

    def _apply_group_dilution_guard(self, records: List[FeatureCostBenefitRecord]) -> None:
        """Downgrades PRUNE to REVIEW where a correlated cluster is still valuable.

        Permuting one of two correlated features leaves the model reading the signal
        through the other, so both report a diluted importance. A member is only safe
        to prune once the *cluster* fails the threshold.
        """
        groups: Dict[str, List[FeatureCostBenefitRecord]] = {}
        for rec in records:
            if rec.group:
                groups.setdefault(rec.group, []).append(rec)

        for group_name, members in groups.items():
            if len(members) < 2:
                continue
            group_importance = sum(m.importance_score for m in members)
            if group_importance < self.min_importance:
                continue
            for m in members:
                if m.recommendation == Recommendation.PRUNE.value:
                    m.recommendation = Recommendation.REVIEW.value
                    m.rationale = (
                        f"Individually low importance ({m.importance_score:.6g}), but "
                        f"correlated group '{group_name}' has aggregate importance "
                        f"{group_importance:.6g} >= {self.min_importance:.6g}. Permutation "
                        f"importance is diluted across correlated features -- evaluate the "
                        f"group jointly (drop every member, or keep one representative) "
                        f"before pruning."
                    )
                    logger.warning(
                        "Group '%s' blocked the prune of '%s': aggregate importance %.4f.",
                        group_name,
                        m.feature_name,
                        group_importance,
                    )

    def _build_report(
        self, records: List[FeatureCostBenefitRecord]
    ) -> FeatureCostBenefitReport:
        """Aggregates records, realizing shared-licence savings only for fully pruned groups."""
        pruned_by_pool: Dict[str, int] = {}
        size_by_pool: Dict[str, int] = {}
        for rec in records:
            if rec.cost_pool:
                size_by_pool[rec.cost_pool] = size_by_pool.get(rec.cost_pool, 0) + 1
                if rec.recommendation == Recommendation.PRUNE.value:
                    pruned_by_pool[rec.cost_pool] = pruned_by_pool.get(rec.cost_pool, 0) + 1

        fully_pruned_pools = {
            pool for pool, n in size_by_pool.items() if pruned_by_pool.get(pool, 0) == n
        }

        total_cost = 0.0
        total_latency = 0.0
        savings = 0.0
        pruned_latency = 0.0
        unrealized = 0.0
        kept_cnt = 0
        review_cnt = 0
        pruned_cnt = 0

        for rec in records:
            total_cost += rec.monthly_cost_usd
            total_latency += rec.compute_latency_ms

            if rec.recommendation == Recommendation.PRUNE.value:
                pruned_cnt += 1
                # Latency is reclaimed per feature; a shared licence is not.
                pruned_latency += rec.compute_latency_ms
                if rec.cost_pool is None or rec.cost_pool in fully_pruned_pools:
                    savings += rec.monthly_cost_usd
                else:
                    unrealized += rec.monthly_cost_usd
            elif rec.recommendation == Recommendation.KEEP.value:
                kept_cnt += 1
            else:
                review_cnt += 1

        msg = (
            f"Feature cost-benefit audit ({len(records)} features): "
            f"KEEP {kept_cnt}, REVIEW {review_cnt}, PRUNE {pruned_cnt}. "
            f"Realizable savings ${savings:,.2f}/mo of ${total_cost:,.2f}/mo total; "
            f"{pruned_latency:.2f} ms of {total_latency:.2f} ms inference latency reclaimed."
        )
        if unrealized > 0.0:
            msg += (
                f" ${unrealized:,.2f}/mo is attributed to partially-pruned shared licences "
                f"and is not realizable until every member of those cost pools is dropped."
            )
        logger.info(msg)

        return FeatureCostBenefitReport(
            total_features_analyzed=len(records),
            total_monthly_cost_usd=round(total_cost, 2),
            kept_features_count=kept_cnt,
            pruned_features_count=pruned_cnt,
            potential_monthly_savings_usd=round(savings, 2),
            feature_records=records,
            message=msg,
            review_features_count=review_cnt,
            total_compute_latency_ms=round(total_latency, 3),
            pruned_latency_ms=round(pruned_latency, 3),
        )
