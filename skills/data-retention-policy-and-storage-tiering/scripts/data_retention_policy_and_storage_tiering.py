"""
data-retention-policy-and-storage-tiering: lifecycle and cost engine for tiering
market-data and trade-record datasets across HOT / WARM / COLD / DEEP ARCHIVE.

What this module is and is not
------------------------------
It is a **recommendation engine**. It reads a described dataset (age, size,
current tier, applicable retention period) and returns an auditable target tier
plus a gross monthly cost delta. It does **not** execute S3 lifecycle rules, it
does **not** delete anything, and it is **not** a legal determination of which
retention period applies to a given record -- that is a compliance decision.

Two safety invariants are enforced by construction, and they are the reason this
module exists as code rather than as a hand-written S3 lifecycle rule:

1. **Never recommend deletion of a record whose retention period has not
   expired.** ``PURGE`` is only reachable when the dataset's retention period
   has elapsed *and* the operator has explicitly opted in by constructing the
   engine with ``purge_expired_records=True``. The default is to keep expired
   data in DEEP ARCHIVE and let a human decide.

2. **Never recommend an archive tier that is not "easily accessible" for a
   record younger than two years.** SEC Rule 17a-4(a) and 17a-4(b)(1) both
   require the first two years of the retention period to be kept "in an easily
   accessible place", and 17a-4(j) requires records to be furnished *promptly*
   to Commission staff. S3 Glacier Deep Archive restores take hours, so this
   engine will not route a dataset there before ``min_instant_access_days``
   (default 730), whatever the age thresholds are configured to.

Cost model -- read this before quoting a savings number
-------------------------------------------------------
``monthly_cost_savings_usd`` is a **gross steady-state storage** figure:
``size_gb * (price_current - price_target)``, plus the per-object Glacier
metadata overhead when ``object_count`` is supplied. It deliberately does not
include:

* per-GB **retrieval** fees (all IA and Glacier classes charge them),
* **data transfer** costs,
* the volume tiering of S3 Standard list pricing (the first 50 TB/month band is
  used as a flat rate here, so large-fleet savings are slightly overstated),
* the 128 KB **minimum billable object size** for S3 Standard-IA, One Zone-IA
  and Glacier Instant Retrieval.

Minimum-storage-duration early-deletion fees and per-request transition charges
are *warned* about rather than charged. Supply ``days_in_current_tier`` and
``transition_price_per_1000_requests_usd`` to surface them as notes and a
payback estimate.

Prices in ``TIER_PRICING_USD_PER_GB`` are **illustrative us-east-1 list prices**
captured for this module, not a live price feed. Region, negotiated pricing and
AWS price changes all move them. Pass your own ``pricing_map``.

References
----------
See ``references/standards.md`` for the AWS storage-class constraints and the
SEC Rule 17a-4 citations relied on above.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional

logger = logging.getLogger(__name__)

TIER_HOT = "HOT_NVME"
TIER_WARM = "WARM_PARQUET_S3"
TIER_COLD = "COLD_GLACIER_INSTANT"
TIER_DEEP_ARCHIVE = "DEEP_ARCHIVE"
TIER_PURGE = "PURGE"

#: Illustrative us-east-1 list prices, USD per GB-month. See the module
#: docstring: these are defaults for worked examples, not a live price feed.
#: HOT is a self-managed NVMe/block-store placeholder; the S3 numbers track the
#: published S3 Standard (first 50 TB band), S3 Glacier Instant Retrieval and
#: S3 Glacier Deep Archive rates.
TIER_PRICING_USD_PER_GB: Dict[str, float] = {
    TIER_HOT: 0.20,
    TIER_WARM: 0.023,
    TIER_COLD: 0.004,
    TIER_DEEP_ARCHIVE: 0.00099,
    TIER_PURGE: 0.00,
}

#: AWS minimum storage duration per storage class, in days. Deleting or
#: transitioning an object out before this elapses incurs a pro-rated
#: early-deletion charge for the remainder.
MIN_STORAGE_DURATION_DAYS: Dict[str, int] = {
    TIER_HOT: 0,
    TIER_WARM: 0,            # S3 Standard has no minimum storage duration
    TIER_COLD: 90,           # S3 Glacier Instant Retrieval
    TIER_DEEP_ARCHIVE: 180,  # S3 Glacier Deep Archive
    TIER_PURGE: 0,
}

#: S3 Lifecycle will not transition objects smaller than this to any storage
#: class by default (behaviour changed September 2024), and Standard-IA /
#: One Zone-IA / Glacier Instant Retrieval bill a minimum of this size.
MIN_TRANSITIONABLE_OBJECT_KB = 128.0

#: Per-object metadata AWS adds for Glacier Flexible Retrieval and Deep Archive:
#: 8 KB billed at S3 Standard rates plus 32 KB billed at the archive rate.
GLACIER_METADATA_STANDARD_KB = 8.0
GLACIER_METADATA_ARCHIVE_KB = 32.0
_ARCHIVE_TIERS_WITH_METADATA_OVERHEAD: FrozenSet[str] = frozenset({TIER_DEEP_ARCHIVE})

#: SEC Rule 17a-4(a)/(b)(1): the first two years of the retention period must be
#: kept "in an easily accessible place".
EASILY_ACCESSIBLE_DAYS = 730

#: Tiers that return data in milliseconds and can therefore satisfy an
#: "easily accessible" / "furnish promptly" obligation without a restore job.
INSTANT_ACCESS_TIERS: FrozenSet[str] = frozenset({TIER_HOT, TIER_WARM, TIER_COLD})

#: Default record types treated as books-and-records: never purged automatically.
DEFAULT_REGULATED_DATA_TYPES: FrozenSet[str] = frozenset(
    {"TRADE_AUDIT_LOG", "ORDER_MEMORANDUM", "COMMUNICATIONS"})

_KB_PER_GB = 1024.0 * 1024.0
_DAYS_PER_YEAR = 365.25

ACTION_NO_CHANGE = "NO_CHANGE"
ACTION_PURGE = "PURGE"
_ACTION_BY_TIER: Dict[str, str] = {
    TIER_HOT: "TRANSITION_TO_HOT",
    TIER_WARM: "TRANSITION_TO_WARM",
    TIER_COLD: "TRANSITION_TO_COLD",
    TIER_DEEP_ARCHIVE: "TRANSITION_TO_DEEP_ARCHIVE",
    TIER_PURGE: ACTION_PURGE,
}


class DataRetentionPolicyError(ValueError):
    """Raised when a dataset description or engine configuration is invalid.

    A retention engine must fail loudly. Silently defaulting an unrecognised
    tier to some price, or accepting a negative size, produces an
    authoritative-looking savings number and -- far worse -- an
    authoritative-looking deletion recommendation built on a data-entry error.
    """


@dataclass
class MarketDatasetInfo:
    """One tierable dataset.

    Attributes:
        dataset_id: Stable identifier used in the audit trail.
        data_type: e.g. ``'L3_TICK'``, ``'L2_ORDER_BOOK'``,
            ``'TRADE_AUDIT_LOG'``, ``'FEATURE_MATRIX'``. Datasets whose type is
            in the engine's ``regulated_data_types`` are treated as
            books-and-records and are never recommended for purge, regardless of
            ``purge_expired_records``.
        size_gb: Logical size in GB (decimal GB, matching cloud billing).
        age_days: Age of the *newest* record in the dataset. Using the newest
            record is the conservative choice: a partition dated by its oldest
            record would be archived while it still holds recent data.
        current_tier: One of the keys of the engine's pricing map.
        regulatory_retention_years: Applicable retention period. This is an
            input, not a lookup -- the applicable period depends on record type
            and jurisdiction (see ``references/standards.md``). Use 0.0 only for
            data subject to no retention obligation.
        object_count: Optional number of stored objects. Enables small-object
            detection and Glacier per-object metadata overhead costing.
        days_in_current_tier: Optional days already spent in ``current_tier``.
            Enables minimum-storage-duration / early-deletion warnings.
    """

    dataset_id: str
    data_type: str
    size_gb: float
    age_days: int
    current_tier: str
    regulatory_retention_years: float
    object_count: Optional[int] = None
    days_in_current_tier: Optional[int] = None


@dataclass
class TierTransitionRecommendation:
    dataset_id: str
    current_tier: str
    recommended_tier: str
    size_gb: float
    current_monthly_cost_usd: float
    recommended_monthly_cost_usd: float
    monthly_cost_savings_usd: float
    action_required: str
    retention_expired: bool
    #: Non-fatal caveats that materially affect whether the transition is a good
    #: idea (early-deletion exposure, small objects, retention state).
    notes: List[str] = field(default_factory=list)
    #: One-off transition request charge; only set when a request price and an
    #: object count are both supplied.
    one_off_transition_cost_usd: Optional[float] = None
    #: Months of savings needed to repay ``one_off_transition_cost_usd``.
    transition_payback_months: Optional[float] = None


@dataclass
class DataRetentionAuditReport:
    total_datasets_audited: int
    total_size_tb: float
    total_current_monthly_cost_usd: float
    total_recommended_monthly_cost_usd: float
    total_monthly_savings_usd: float
    recommendations: List[TierTransitionRecommendation]


class DataRetentionPolicyEngine:
    """Age-and-retention based storage tiering recommender.

    The tier ladder is purely age-driven. The retention period is a *gate* laid
    over that ladder, never a ladder threshold -- conflating the two is exactly
    what makes naive lifecycle rules delete unexpired records.

    Args:
        pricing_map: USD per GB-month by tier. Defaults to
            ``TIER_PRICING_USD_PER_GB``. Must contain every tier the engine can
            recommend.
        hot_max_days: Upper age bound for HOT.
        warm_max_days: Upper age bound for WARM.
        deep_archive_after_days: Age beyond which DEEP ARCHIVE is considered.
        min_instant_access_days: Floor below which no non-instant-retrieval tier
            is recommended. Defaults to ``EASILY_ACCESSIBLE_DAYS``.
        purge_expired_records: Opt-in. When False (default) an expired,
            unregulated dataset is recommended for DEEP ARCHIVE and flagged as
            purge-eligible in ``notes`` rather than recommended for deletion.
        regulated_data_types: Data types never recommended for purge. Defaults
            to ``DEFAULT_REGULATED_DATA_TYPES``.
        transition_price_per_1000_requests_usd: Optional per-1000-request price
            for the target class, used to cost the one-off transition and a
            payback period. No default is assumed -- these prices vary by
            storage class and region.

    Raises:
        DataRetentionPolicyError: on invalid configuration.
    """

    def __init__(
        self,
        pricing_map: Optional[Dict[str, float]] = None,
        *,
        hot_max_days: int = 30,
        warm_max_days: int = 365,
        deep_archive_after_days: int = 2555,
        min_instant_access_days: int = EASILY_ACCESSIBLE_DAYS,
        purge_expired_records: bool = False,
        regulated_data_types: Optional[FrozenSet[str]] = None,
        transition_price_per_1000_requests_usd: Optional[float] = None,
    ) -> None:
        self.pricing_map: Dict[str, float] = (
            dict(pricing_map) if pricing_map else dict(TIER_PRICING_USD_PER_GB))

        missing = [t for t in _ACTION_BY_TIER if t not in self.pricing_map]
        if missing:
            raise DataRetentionPolicyError(
                f"pricing_map is missing required tiers: {sorted(missing)}")
        for tier, price in self.pricing_map.items():
            if not math.isfinite(price) or price < 0.0:
                raise DataRetentionPolicyError(
                    f"pricing_map[{tier!r}] must be a finite non-negative price, got {price!r}")

        if not 0 < hot_max_days < warm_max_days < deep_archive_after_days:
            raise DataRetentionPolicyError(
                "age thresholds must satisfy 0 < hot_max_days < warm_max_days < "
                f"deep_archive_after_days, got {hot_max_days}, {warm_max_days}, "
                f"{deep_archive_after_days}")
        if min_instant_access_days < 0:
            raise DataRetentionPolicyError("min_instant_access_days must be >= 0")
        if (transition_price_per_1000_requests_usd is not None
                and (not math.isfinite(transition_price_per_1000_requests_usd)
                     or transition_price_per_1000_requests_usd < 0.0)):
            raise DataRetentionPolicyError(
                "transition_price_per_1000_requests_usd must be finite and non-negative")

        self.hot_max_days = hot_max_days
        self.warm_max_days = warm_max_days
        self.deep_archive_after_days = deep_archive_after_days
        self.min_instant_access_days = min_instant_access_days
        self.purge_expired_records = purge_expired_records
        self.regulated_data_types: FrozenSet[str] = (
            regulated_data_types if regulated_data_types is not None
            else DEFAULT_REGULATED_DATA_TYPES)
        self.transition_price_per_1000_requests_usd = transition_price_per_1000_requests_usd

    # -- validation ---------------------------------------------------------

    def _validate(self, dataset: MarketDatasetInfo) -> None:
        if not dataset.dataset_id:
            raise DataRetentionPolicyError("dataset_id must be a non-empty string")
        if not math.isfinite(dataset.size_gb) or dataset.size_gb < 0.0:
            raise DataRetentionPolicyError(
                f"{dataset.dataset_id}: size_gb must be finite and >= 0, got {dataset.size_gb!r}")
        # A NaN age silently compares False against every threshold, so it would
        # fall through the ladder rather than raise. Reject non-finite ages.
        if not math.isfinite(dataset.age_days) or dataset.age_days < 0:
            raise DataRetentionPolicyError(
                f"{dataset.dataset_id}: age_days must be finite and >= 0, "
                f"got {dataset.age_days!r}")
        if (not math.isfinite(dataset.regulatory_retention_years)
                or dataset.regulatory_retention_years < 0.0):
            raise DataRetentionPolicyError(
                f"{dataset.dataset_id}: regulatory_retention_years must be finite and >= 0, "
                f"got {dataset.regulatory_retention_years!r}")
        if dataset.current_tier not in self.pricing_map:
            raise DataRetentionPolicyError(
                f"{dataset.dataset_id}: unknown current_tier {dataset.current_tier!r}; "
                f"known tiers are {sorted(self.pricing_map)}")
        if dataset.object_count is not None and dataset.object_count < 0:
            raise DataRetentionPolicyError(
                f"{dataset.dataset_id}: object_count must be >= 0, got {dataset.object_count!r}")
        if dataset.days_in_current_tier is not None and dataset.days_in_current_tier < 0:
            raise DataRetentionPolicyError(
                f"{dataset.dataset_id}: days_in_current_tier must be >= 0, "
                f"got {dataset.days_in_current_tier!r}")

    # -- tiering ------------------------------------------------------------

    def retention_days(self, dataset: MarketDatasetInfo) -> int:
        """Retention period in whole days, rounded up.

        Uses 365.25 days/year and ``ceil`` so the engine never treats a record
        as expired a day or two early because of leap-year truncation.
        """
        return math.ceil(dataset.regulatory_retention_years * _DAYS_PER_YEAR)

    def _target_tier(self, dataset: MarketDatasetInfo, expired: bool,
                     notes: List[str]) -> str:
        age = dataset.age_days

        if age <= self.hot_max_days:
            return TIER_HOT
        if age <= self.warm_max_days:
            return TIER_WARM
        if age <= self.deep_archive_after_days:
            return TIER_COLD

        # Beyond the deep-archive age. Deletion is only reachable here, and only
        # for expired, unregulated data with an explicit operator opt-in.
        if expired:
            if dataset.data_type in self.regulated_data_types:
                notes.append(
                    f"Retention expired, but data_type {dataset.data_type!r} is a regulated "
                    "record type; purge withheld pending compliance sign-off.")
            elif self.purge_expired_records:
                return TIER_PURGE
            else:
                notes.append(
                    "Retention expired: dataset is purge-eligible, but the engine was built "
                    "with purge_expired_records=False, so DEEP ARCHIVE is recommended "
                    "instead of deletion.")
        else:
            notes.append(
                f"Retention period not yet expired ({age} of {self.retention_days(dataset)} "
                "days elapsed); deletion is prohibited.")

        return TIER_DEEP_ARCHIVE

    def _apply_accessibility_floor(self, target: str, dataset: MarketDatasetInfo,
                                   notes: List[str]) -> str:
        """Clamp non-instant tiers for records inside the easily-accessible window.

        ``PURGE`` is exempt because this floor is about *retrieval latency* for
        records that must remain producible, not about deletion. Deletion is
        already gated four ways in ``_target_tier`` (past the deep-archive age,
        retention expired, non-regulated data type, explicit operator opt-in).
        """
        if target in INSTANT_ACCESS_TIERS or target == TIER_PURGE:
            return target
        if dataset.age_days >= self.min_instant_access_days:
            return target
        notes.append(
            f"Clamped {target} -> {TIER_COLD}: record is {dataset.age_days} days old, inside "
            f"the {self.min_instant_access_days}-day easily-accessible window (SEC Rule "
            "17a-4(a)/(b)(1)); archive tiers needing a restore job are not eligible.")
        return TIER_COLD

    # -- costing ------------------------------------------------------------

    def _archive_metadata_gb(self, dataset: MarketDatasetInfo, tier: str,
                             kb_per_object: float) -> float:
        if tier not in _ARCHIVE_TIERS_WITH_METADATA_OVERHEAD or not dataset.object_count:
            return 0.0
        return dataset.object_count * kb_per_object / _KB_PER_GB

    def _tier_monthly_cost(self, dataset: MarketDatasetInfo, tier: str) -> float:
        """Monthly storage cost at ``tier``, including Glacier metadata overhead.

        AWS bills 32 KB/object at the archive rate and 8 KB/object at S3
        Standard rates for objects in Deep Archive, which dominates the bill for
        fleets of small objects.
        """
        cost = dataset.size_gb * self.pricing_map[tier]
        cost += (self._archive_metadata_gb(dataset, tier, GLACIER_METADATA_ARCHIVE_KB)
                 * self.pricing_map[tier])
        cost += (self._archive_metadata_gb(dataset, tier, GLACIER_METADATA_STANDARD_KB)
                 * self.pricing_map[TIER_WARM])
        return cost

    def _cost_notes(self, dataset: MarketDatasetInfo, target: str,
                    notes: List[str]) -> None:
        if target == dataset.current_tier:
            return

        min_duration = MIN_STORAGE_DURATION_DAYS.get(dataset.current_tier, 0)
        if min_duration and dataset.days_in_current_tier is not None:
            shortfall = min_duration - dataset.days_in_current_tier
            if shortfall > 0:
                notes.append(
                    f"Early-deletion exposure: {dataset.current_tier} has a {min_duration}-day "
                    f"minimum storage duration and this data has been there "
                    f"{dataset.days_in_current_tier} days; transitioning now is still billed "
                    f"for the remaining {shortfall} days.")
        elif min_duration:
            notes.append(
                f"{dataset.current_tier} has a {min_duration}-day minimum storage duration; "
                "supply days_in_current_tier to check early-deletion exposure.")

        if dataset.object_count:
            avg_kb = dataset.size_gb * _KB_PER_GB / dataset.object_count
            if avg_kb < MIN_TRANSITIONABLE_OBJECT_KB:
                notes.append(
                    f"Average object size {avg_kb:.1f} KB is below the "
                    f"{MIN_TRANSITIONABLE_OBJECT_KB:.0f} KB S3 Lifecycle default: these objects "
                    "will not transition at all unless an object-size filter is set, and "
                    "IA / Glacier Instant Retrieval bill a 128 KB minimum per object. Compact "
                    "before transitioning.")

    # -- public API ---------------------------------------------------------

    def evaluate_dataset_lifecycle(
        self, dataset: MarketDatasetInfo
    ) -> TierTransitionRecommendation:
        """Recommend a target tier and quantify the gross monthly cost delta.

        Raises:
            DataRetentionPolicyError: if the dataset description is invalid.
        """
        self._validate(dataset)

        notes: List[str] = []
        expired = dataset.age_days > self.retention_days(dataset)

        target = self._target_tier(dataset, expired, notes)
        target = self._apply_accessibility_floor(target, dataset, notes)

        curr_cost = self._tier_monthly_cost(dataset, dataset.current_tier)
        target_cost = self._tier_monthly_cost(dataset, target)
        savings = curr_cost - target_cost

        self._cost_notes(dataset, target, notes)

        action = (ACTION_NO_CHANGE if target == dataset.current_tier
                  else _ACTION_BY_TIER[target])

        one_off: Optional[float] = None
        payback: Optional[float] = None
        if (self.transition_price_per_1000_requests_usd is not None
                and dataset.object_count and action != ACTION_NO_CHANGE):
            one_off = round(
                dataset.object_count / 1000.0
                * self.transition_price_per_1000_requests_usd, 2)
            if savings > 0:
                payback = round(one_off / savings, 2)
                if payback > 12.0:
                    notes.append(
                        f"Transition pays back in {payback:.1f} months; confirm the dataset "
                        "will still be retained that long before paying the request charge.")

        if savings > 0:
            logger.info(
                "STORAGE TIER OPTIMIZATION [%s]: %s -> %s. Monthly cost: $%s -> $%s "
                "(gross saving $%s/mo)",
                dataset.dataset_id, dataset.current_tier, target,
                f"{curr_cost:,.2f}", f"{target_cost:,.2f}", f"{savings:,.2f}")
        if action == ACTION_PURGE:
            logger.warning(
                "PURGE RECOMMENDED [%s]: retention of %s days elapsed (age %s days). "
                "Verify against the applicable recordkeeping rule before deleting.",
                dataset.dataset_id, self.retention_days(dataset), dataset.age_days)

        return TierTransitionRecommendation(
            dataset_id=dataset.dataset_id,
            current_tier=dataset.current_tier,
            recommended_tier=target,
            size_gb=dataset.size_gb,
            current_monthly_cost_usd=round(curr_cost, 2),
            recommended_monthly_cost_usd=round(target_cost, 2),
            monthly_cost_savings_usd=round(savings, 2),
            action_required=action,
            retention_expired=expired,
            notes=notes,
            one_off_transition_cost_usd=one_off,
            transition_payback_months=payback,
        )

    def audit_storage_fleet(
        self, datasets: List[MarketDatasetInfo]
    ) -> DataRetentionAuditReport:
        """Evaluate a fleet and aggregate unrounded costs.

        Totals are summed from unrounded per-dataset costs and rounded once, so
        a fleet of thousands of datasets does not accumulate per-row rounding
        error into the headline savings figure.
        """
        recs: List[TierTransitionRecommendation] = []
        total_curr = 0.0
        total_target = 0.0
        for dataset in datasets:
            rec = self.evaluate_dataset_lifecycle(dataset)
            recs.append(rec)
            total_curr += self._tier_monthly_cost(dataset, dataset.current_tier)
            total_target += self._tier_monthly_cost(dataset, rec.recommended_tier)

        total_gb = sum(d.size_gb for d in datasets)

        return DataRetentionAuditReport(
            total_datasets_audited=len(datasets),
            total_size_tb=round(total_gb / 1000.0, 2),  # decimal TB, matching cloud billing
            total_current_monthly_cost_usd=round(total_curr, 2),
            total_recommended_monthly_cost_usd=round(total_target, 2),
            total_monthly_savings_usd=round(total_curr - total_target, 2),
            recommendations=recs,
        )
