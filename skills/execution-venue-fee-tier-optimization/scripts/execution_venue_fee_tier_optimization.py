"""Venue fee-tier allocation optimizer for multi-venue Smart Order Routing.

Evaluates candidate distributions of a period's order flow across execution venues
and selects the allocation with the lowest *expected* net transaction cost, where
"expected" accounts for the fact that passive (maker) volume routed to a venue only
earns a rebate if it actually fills.

Sign convention (identical to `exchange-fee-tier-and-rebate-structure-analysis`):

    rate < 0  ->  rebate: the venue CREDITS the member
    rate > 0  ->  fee:    the venue CHARGES the member

Every USD figure returned follows the same convention: positive is money out.

Jurisdictional note: on US NMS stocks, Reg NMS Rule 610(d) (compliance date the
first business day of February 2026) requires that a fee or rebate be determinable
at the time of execution, and exchanges implemented it by deriving tier volumes from
the *prior* month. Volume routed during the current period therefore cannot change
the rate applied to that period's fills. Model this with
`TierQualificationBasis.PRIOR_PERIOD`; the basis is required, not defaulted.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "VenueOptimizationError",
    "TierQualificationBasis",
    "UnfilledPassivePolicy",
    "TierBenefitPeriod",
    "VenueFeeTier",
    "ExecutionVenueSpec",
    "VenueCostBreakdown",
    "VenueAllocationStrategy",
    "RejectedStrategy",
    "VenueFeeOptimizationReport",
    "ExecutionVenueFeeTierOptimizerEngine",
]

logger = logging.getLogger(__name__)

_USD_DP = 2
_RATIO_TOLERANCE = 1e-12


class VenueOptimizationError(ValueError):
    """Raised on an invalid venue schedule, invalid inputs, or an infeasible allocation."""


class TierQualificationBasis(Enum):
    """Which volume determines the fee tier applied to the flow being priced."""

    #: Tier fixed by a completed prior period. Mandatory for US NMS stocks
    #: (Reg NMS Rule 610(d)). Current-period routing cannot reprice current fills.
    PRIOR_PERIOD = "PRIOR_PERIOD"
    #: Tier fixed by a rolling window that includes the volume being priced
    #: (crypto venues' rolling 30-day volume; some non-US venues).
    ROLLING_CURRENT = "ROLLING_CURRENT"


class UnfilledPassivePolicy(Enum):
    """What the desk does with passive shares that do not fill."""

    #: Sweep the residual aggressively at the venue's taker rate. This is the
    #: realistic default for a desk that must acquire the exposure regardless.
    CONVERT_TO_TAKER = "CONVERT_TO_TAKER"
    #: Leave the residual unexecuted and charge an explicit opportunity cost per share.
    ABANDON = "ABANDON"


class TierBenefitPeriod(Enum):
    """When an improvement in tier standing is actually billed."""

    CURRENT_PERIOD = "CURRENT_PERIOD"
    NEXT_PERIOD = "NEXT_PERIOD"


@dataclass(frozen=True)
class VenueFeeTier:
    """One row of a venue's volume-tiered fee schedule.

    `min_volume_shares` is an absolute share threshold on the qualifying volume.
    Real US equity schedules frequently qualify on ADAV/ADV or on a percentage of
    Total Consolidated Volume; converting those to an absolute share count is the
    caller's responsibility (see `references/standards.md`).
    """

    tier_name: str
    min_volume_shares: int
    taker_rate_per_share: float
    maker_rate_per_share: float

    def __post_init__(self) -> None:
        if not str(self.tier_name).strip():
            raise VenueOptimizationError("tier_name must be a non-empty string.")
        if not isinstance(self.min_volume_shares, int) or isinstance(self.min_volume_shares, bool):
            raise VenueOptimizationError(
                f"{self.tier_name}: min_volume_shares must be an int, got "
                f"{type(self.min_volume_shares).__name__}."
            )
        if self.min_volume_shares < 0:
            raise VenueOptimizationError(f"{self.tier_name}: min_volume_shares must be >= 0.")
        for label, rate in (
            ("taker_rate_per_share", self.taker_rate_per_share),
            ("maker_rate_per_share", self.maker_rate_per_share),
        ):
            if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not math.isfinite(rate):
                raise VenueOptimizationError(
                    f"{self.tier_name}: {label} must be a finite number, got {rate!r}."
                )


@dataclass(frozen=True)
class ExecutionVenueSpec:
    """A candidate execution venue.

    Attributes:
        venue_id: Unique venue identifier.
        passive_fill_probability: Probability that a passive share posted at this
            venue fills within the execution horizon. This enters the cost model
            directly -- it is not a cosmetic rating.
        tiers: The venue's fee schedule. Must include a tier at threshold 0.
        qualifying_volume_shares: Prior-period volume that fixes this venue's tier.
            Required under `TierQualificationBasis.PRIOR_PERIOD`; ignored under
            `ROLLING_CURRENT`, where executed volume qualifies.
        max_allocatable_shares: Optional capacity cap. Allocations above it are
            rejected rather than silently assumed to be executable.
    """

    venue_id: str
    passive_fill_probability: float
    tiers: Sequence[VenueFeeTier]
    qualifying_volume_shares: Optional[int] = None
    max_allocatable_shares: Optional[int] = None

    def __post_init__(self) -> None:
        if not str(self.venue_id).strip():
            raise VenueOptimizationError("venue_id must be a non-empty string.")
        p = self.passive_fill_probability
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not math.isfinite(p) or not (0.0 <= p <= 1.0):
            raise VenueOptimizationError(
                f"{self.venue_id}: passive_fill_probability must be a finite value in [0.0, 1.0], got {p!r}."
            )
        if not self.tiers:
            raise VenueOptimizationError(f"{self.venue_id}: fee schedule must contain at least one tier.")

        thresholds = [t.min_volume_shares for t in self.tiers]
        if len(set(thresholds)) != len(thresholds):
            raise VenueOptimizationError(
                f"{self.venue_id}: duplicate tier thresholds make tier assignment ambiguous."
            )
        if 0 not in thresholds:
            raise VenueOptimizationError(
                f"{self.venue_id}: schedule must define a tier at threshold 0, otherwise volume "
                f"below the lowest threshold would be priced at a tier it does not qualify for."
            )
        if self.qualifying_volume_shares is not None and self.qualifying_volume_shares < 0:
            raise VenueOptimizationError(f"{self.venue_id}: qualifying_volume_shares must be >= 0.")
        if self.max_allocatable_shares is not None and self.max_allocatable_shares < 0:
            raise VenueOptimizationError(f"{self.venue_id}: max_allocatable_shares must be >= 0.")

        object.__setattr__(self, "tiers", tuple(sorted(self.tiers, key=lambda t: t.min_volume_shares)))

    def tier_for_volume(self, volume_shares: int) -> VenueFeeTier:
        """Return the highest tier whose threshold is met by `volume_shares` (inclusive)."""
        active = self.tiers[0]
        for tier in self.tiers:
            if volume_shares >= tier.min_volume_shares:
                active = tier
            else:
                break
        return active


@dataclass(frozen=True)
class VenueCostBreakdown:
    """Per-venue expected economics for one allocation."""

    venue_id: str
    allocated_shares: int
    posted_passive_shares: int
    expected_passive_fills: float
    unfilled_passive_shares: float
    aggressive_shares: int
    swept_shares: float
    executed_shares: float
    active_tier_name: str
    qualifying_volume_shares: int
    maker_side_cost_usd: float
    taker_side_cost_usd: float
    opportunity_cost_usd: float
    net_cost_usd: float
    gross_maker_rebates_usd: float
    gross_taker_fees_usd: float
    projected_next_period_tier_name: Optional[str] = None


@dataclass(frozen=True)
class VenueAllocationStrategy:
    """One evaluated volume distribution across venues."""

    strategy_name: str
    volume_allocations_shares: Dict[str, int]
    total_net_cost_usd: float
    total_maker_rebates_usd: float
    total_taker_fees_usd: float
    total_opportunity_cost_usd: float
    weighted_passive_fill_probability: float
    expected_executed_shares: float
    per_venue: Tuple[VenueCostBreakdown, ...]


@dataclass(frozen=True)
class RejectedStrategy:
    """A candidate excluded by a hard constraint, retained so exclusions stay auditable."""

    strategy_name: str
    volume_allocations_shares: Dict[str, int]
    reason: str


@dataclass(frozen=True)
class VenueFeeOptimizationReport:
    """Structured output of one optimization run."""

    total_volume_target_shares: int
    maker_volume_ratio: float
    taker_volume_ratio: float
    qualification_basis: TierQualificationBasis
    tier_benefit_period: TierBenefitPeriod
    unfilled_passive_policy: UnfilledPassivePolicy
    optimal_strategy: VenueAllocationStrategy
    baseline_strategy: Optional[VenueAllocationStrategy]
    net_savings_vs_baseline_usd: Optional[float]
    all_strategies_evaluated: Tuple[VenueAllocationStrategy, ...]
    rejected_strategies: Tuple[RejectedStrategy, ...]
    warnings: Tuple[str, ...]
    audit_notes: str


def _largest_remainder_split(total: int, weights: Sequence[float], keys: Sequence[str]) -> Dict[str, int]:
    """Distribute `total` integer shares across `keys` in proportion to `weights`.

    Uses the largest-remainder method so the allocation sums to `total` exactly --
    integer floor division silently drops shares, which corrupts both the cost total
    and the volume-weighted fill probability.
    """
    weight_sum = sum(weights)
    if weight_sum <= 0:
        weights = [1.0] * len(keys)
        weight_sum = float(len(keys))

    exact = [total * w / weight_sum for w in weights]
    floors = [int(math.floor(v)) for v in exact]
    remainder = total - sum(floors)
    order = sorted(range(len(keys)), key=lambda i: (-(exact[i] - floors[i]), keys[i]))
    for i in order[:remainder]:
        floors[i] += 1
    return {keys[i]: floors[i] for i in range(len(keys))}


class ExecutionVenueFeeTierOptimizerEngine:
    """Selects the lowest expected-net-cost distribution of order flow across venues.

    The engine prices *expected* outcomes, not nominal ones: passive volume routed to
    a venue earns that venue's maker rate only on the fraction that fills. The residual
    is either swept aggressively (paying the taker rate) or abandoned at an explicit
    opportunity cost. Without this, the optimizer would always concentrate passive flow
    on whichever venue posts the largest rebate regardless of whether it ever fills.
    """

    def __init__(
        self,
        qualification_basis: TierQualificationBasis,
        min_weighted_passive_fill_probability: float = 0.80,
        unfilled_passive_policy: UnfilledPassivePolicy = UnfilledPassivePolicy.CONVERT_TO_TAKER,
        unfilled_passive_opportunity_cost_per_share: float = 0.0,
    ) -> None:
        """
        Args:
            qualification_basis: Required. See `TierQualificationBasis`. There is no
                safe default: guessing wrong misstates every tier in the run.
            min_weighted_passive_fill_probability: Desk routing policy, not a
                regulatory standard. Candidates whose passive-volume-weighted fill
                probability falls below it are rejected, never silently accepted.
            unfilled_passive_policy: Disposition of passive shares that do not fill.
            unfilled_passive_opportunity_cost_per_share: Cost charged per abandoned
                share under `ABANDON`. A value of 0.0 asserts that missing the trade
                is free, which is almost never true; the engine warns when it is used.
        """
        if not isinstance(qualification_basis, TierQualificationBasis):
            raise VenueOptimizationError(
                "qualification_basis must be a TierQualificationBasis; it is required because "
                "assuming the wrong basis misprices every tier in the run."
            )
        if not isinstance(unfilled_passive_policy, UnfilledPassivePolicy):
            raise VenueOptimizationError("unfilled_passive_policy must be an UnfilledPassivePolicy.")
        threshold = min_weighted_passive_fill_probability
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) \
                or not math.isfinite(threshold) or not (0.0 <= threshold <= 1.0):
            raise VenueOptimizationError(
                f"min_weighted_passive_fill_probability must be a finite value in [0.0, 1.0], "
                f"got {threshold!r}."
            )
        opp = unfilled_passive_opportunity_cost_per_share
        if not isinstance(opp, (int, float)) or isinstance(opp, bool) or not math.isfinite(opp) or opp < 0.0:
            raise VenueOptimizationError(
                f"unfilled_passive_opportunity_cost_per_share must be finite and >= 0, got {opp!r}."
            )

        self.qualification_basis = qualification_basis
        self.min_weighted_passive_fill_probability = float(threshold)
        self.unfilled_passive_policy = unfilled_passive_policy
        self.unfilled_passive_opportunity_cost_per_share = float(opp)

    # ------------------------------------------------------------------ costing

    def calculate_venue_net_cost(
        self,
        venue: ExecutionVenueSpec,
        allocated_volume_shares: int,
        maker_ratio: float,
    ) -> VenueCostBreakdown:
        """Expected economics of routing `allocated_volume_shares` to one venue.

        Passive shares are posted, not filled. Only `posted * passive_fill_probability`
        earns the maker rate; the residual is swept at the taker rate or abandoned at
        the configured opportunity cost.
        """
        self._validate_maker_ratio(maker_ratio)
        if not isinstance(allocated_volume_shares, int) or isinstance(allocated_volume_shares, bool):
            raise VenueOptimizationError(
                f"{venue.venue_id}: allocated_volume_shares must be an int, got "
                f"{type(allocated_volume_shares).__name__}."
            )
        if allocated_volume_shares < 0:
            raise VenueOptimizationError(
                f"{venue.venue_id}: allocated_volume_shares must be >= 0, got {allocated_volume_shares}."
            )

        posted_passive = min(int(round(allocated_volume_shares * maker_ratio)), allocated_volume_shares)
        aggressive = allocated_volume_shares - posted_passive

        expected_fills = posted_passive * venue.passive_fill_probability
        unfilled = posted_passive - expected_fills

        if self.unfilled_passive_policy is UnfilledPassivePolicy.CONVERT_TO_TAKER:
            swept = unfilled
            opportunity_shares = 0.0
        else:
            swept = 0.0
            opportunity_shares = unfilled

        executed = expected_fills + aggressive + swept
        qualifying = self._qualifying_volume(venue, executed)
        tier = venue.tier_for_volume(qualifying)

        maker_side = expected_fills * tier.maker_rate_per_share
        taker_side = (aggressive + swept) * tier.taker_rate_per_share
        opportunity = opportunity_shares * self.unfilled_passive_opportunity_cost_per_share
        net = maker_side + taker_side + opportunity

        projected_tier: Optional[str] = None
        if self.qualification_basis is TierQualificationBasis.PRIOR_PERIOD:
            projected_tier = venue.tier_for_volume(int(math.floor(executed))).tier_name

        return VenueCostBreakdown(
            venue_id=venue.venue_id,
            allocated_shares=allocated_volume_shares,
            posted_passive_shares=posted_passive,
            expected_passive_fills=round(expected_fills, 4),
            unfilled_passive_shares=round(unfilled, 4),
            aggressive_shares=aggressive,
            swept_shares=round(swept, 4),
            executed_shares=round(executed, 4),
            active_tier_name=tier.tier_name,
            qualifying_volume_shares=qualifying,
            maker_side_cost_usd=round(maker_side, _USD_DP),
            taker_side_cost_usd=round(taker_side, _USD_DP),
            opportunity_cost_usd=round(opportunity, _USD_DP),
            net_cost_usd=round(net, _USD_DP),
            gross_maker_rebates_usd=round(-maker_side, _USD_DP) if tier.maker_rate_per_share < 0 else 0.0,
            gross_taker_fees_usd=round(taker_side, _USD_DP) if tier.taker_rate_per_share > 0 else 0.0,
            projected_next_period_tier_name=projected_tier,
        )

    # ------------------------------------------------------------- optimization

    def optimize_venue_fee_allocation(
        self,
        venues: Sequence[ExecutionVenueSpec],
        total_volume_shares: int,
        maker_ratio: float = 0.70,
        baseline_allocation: Optional[Dict[str, int]] = None,
    ) -> VenueFeeOptimizationReport:
        """Evaluate candidate allocations and return the lowest expected-net-cost one.

        Args:
            venues: Candidate venues. Venue ids must be unique.
            total_volume_shares: Period order-flow budget, in shares. Every candidate
                allocates exactly this many shares.
            maker_ratio: Fraction of the budget posted passively, in [0.0, 1.0].
            baseline_allocation: The desk's incumbent routing table, summing to
                `total_volume_shares`. Savings are reported against this and only this.
                Without it, savings are `None` -- the engine will not manufacture a
                reference by comparing the winner to the worst candidate it generated
                itself.

        Raises:
            VenueOptimizationError: on invalid inputs, or when no candidate satisfies
                the fill-probability and capacity constraints.
        """
        self._validate_venues(venues)
        self._validate_maker_ratio(maker_ratio)
        if not isinstance(total_volume_shares, int) or isinstance(total_volume_shares, bool):
            raise VenueOptimizationError("total_volume_shares must be an int.")
        if total_volume_shares <= 0:
            raise VenueOptimizationError(f"total_volume_shares must be > 0, got {total_volume_shares}.")

        by_id = {v.venue_id: v for v in venues}
        warnings: List[str] = []
        accepted: List[VenueAllocationStrategy] = []
        rejected: List[RejectedStrategy] = []

        for name, allocation in self._candidate_allocations(venues, total_volume_shares):
            self._admit(name, allocation, by_id, maker_ratio, accepted, rejected)

        if not accepted:
            raise VenueOptimizationError(
                f"No candidate allocation satisfies the constraints (min weighted passive fill "
                f"probability = {self.min_weighted_passive_fill_probability:.2f}, plus per-venue "
                f"capacity caps). Rejected {len(rejected)} candidate(s): "
                + "; ".join(f"{r.strategy_name}: {r.reason}" for r in rejected[:5])
                + ". Relax the constraint deliberately or add a venue -- routing passive flow to a "
                "venue that does not fill is the failure this check exists to prevent."
            )

        ranked = tuple(sorted(accepted, key=lambda s: (s.total_net_cost_usd, s.strategy_name)))
        optimal = ranked[0]

        baseline: Optional[VenueAllocationStrategy] = None
        savings: Optional[float] = None
        if baseline_allocation is not None:
            baseline = self._evaluate(
                "BASELINE_INCUMBENT",
                self._normalize_allocation(baseline_allocation, by_id, total_volume_shares),
                by_id,
                maker_ratio,
            )
            savings = round(baseline.total_net_cost_usd - optimal.total_net_cost_usd, _USD_DP)
            if savings <= 0.0:
                warnings.append(
                    f"No improvement over the incumbent allocation: savings = ${savings:,.2f}. "
                    "Do not re-route."
                )
        else:
            warnings.append(
                "No baseline_allocation supplied, so net_savings_vs_baseline_usd is None. "
                "Savings are only meaningful against the routing table actually in use."
            )

        benefit_period = (
            TierBenefitPeriod.NEXT_PERIOD
            if self.qualification_basis is TierQualificationBasis.PRIOR_PERIOD
            else TierBenefitPeriod.CURRENT_PERIOD
        )
        if self.qualification_basis is TierQualificationBasis.PRIOR_PERIOD:
            warnings.append(
                "PRIOR_PERIOD basis: tiers applied to this period's fills are fixed by prior-period "
                "volume, so this allocation cannot improve this period's rates. Any tier improvement "
                "is billed NEXT_PERIOD -- see per-venue projected_next_period_tier_name."
            )
        if (
            self.unfilled_passive_policy is UnfilledPassivePolicy.ABANDON
            and self.unfilled_passive_opportunity_cost_per_share == 0.0
        ):
            warnings.append(
                "ABANDON policy with a zero opportunity cost prices unexecuted passive volume as free. "
                "That systematically favours low-fill, high-rebate venues; set a non-zero cost."
            )
        if rejected:
            warnings.append(
                f"{len(rejected)} candidate allocation(s) rejected by hard constraints; "
                "see rejected_strategies."
            )

        notes = (
            f"VENUE FEE TIER OPTIMIZATION COMPLETE: target = {total_volume_shares:,} sh "
            f"({maker_ratio * 100:.0f}% posted passively), basis = {self.qualification_basis.value}, "
            f"unfilled passive = {self.unfilled_passive_policy.value}. "
            f"Optimal: '{optimal.strategy_name}' at expected net cost ${optimal.total_net_cost_usd:,.2f} "
            f"(weighted passive fill probability {optimal.weighted_passive_fill_probability:.4f}). "
            + (
                f"Savings vs incumbent = ${savings:,.2f}."
                if savings is not None
                else "Savings vs incumbent = n/a (no baseline supplied)."
            )
        )
        logger.info(notes)

        return VenueFeeOptimizationReport(
            total_volume_target_shares=total_volume_shares,
            maker_volume_ratio=maker_ratio,
            taker_volume_ratio=round(1.0 - maker_ratio, 6),
            qualification_basis=self.qualification_basis,
            tier_benefit_period=benefit_period,
            unfilled_passive_policy=self.unfilled_passive_policy,
            optimal_strategy=optimal,
            baseline_strategy=baseline,
            net_savings_vs_baseline_usd=savings,
            all_strategies_evaluated=ranked,
            rejected_strategies=tuple(rejected),
            warnings=tuple(warnings),
            audit_notes=notes,
        )

    # ------------------------------------------------------------------ helpers

    def _candidate_allocations(
        self, venues: Sequence[ExecutionVenueSpec], total: int
    ) -> List[Tuple[str, Dict[str, int]]]:
        """Generate the candidate allocation profiles, deterministically.

        Every candidate allocates exactly `total` shares.
        """
        ids = [v.venue_id for v in venues]
        candidates: List[Tuple[str, Dict[str, int]]] = []

        # CONCENTRATED_<venue>: all volume to one venue, to reach its highest tier.
        for v in venues:
            candidates.append((f"CONCENTRATED_{v.venue_id}", {v.venue_id: total}))

        # EQUAL_SPLIT_BALANCED: even split, largest-remainder so nothing is dropped.
        candidates.append(("EQUAL_SPLIT_BALANCED", _largest_remainder_split(total, [1.0] * len(ids), ids)))

        # LIQUIDITY_WEIGHTED: proportional to passive fill probability.
        candidates.append(
            (
                "LIQUIDITY_WEIGHTED",
                _largest_remainder_split(total, [v.passive_fill_probability for v in venues], ids),
            )
        )

        # THRESHOLD_SEEK_<venue>_<tier>: allocate exactly enough to clear one venue's
        # tier threshold, and place the remainder on each other venue in turn. This
        # replaces a "top two venues" heuristic that read venues[0]/venues[1] and
        # tiers[-1] from unsorted input, so its output depended on list order rather
        # than on the fee schedules.
        for v in venues:
            for tier in v.tiers:
                if tier.min_volume_shares <= 0 or tier.min_volume_shares > total:
                    continue
                head = tier.min_volume_shares
                remainder = total - head
                slug = tier.tier_name.strip().replace(" ", "_").upper()
                if remainder == 0:
                    candidates.append((f"THRESHOLD_SEEK_{v.venue_id}_{slug}", {v.venue_id: head}))
                    continue
                for w in venues:
                    if w.venue_id == v.venue_id:
                        continue
                    candidates.append(
                        (
                            f"THRESHOLD_SEEK_{v.venue_id}_{slug}_REM_{w.venue_id}",
                            {v.venue_id: head, w.venue_id: remainder},
                        )
                    )

        seen = set()
        unique: List[Tuple[str, Dict[str, int]]] = []
        for name, alloc in candidates:
            signature = tuple(sorted((k, q) for k, q in alloc.items() if q > 0))
            if signature in seen:
                continue
            seen.add(signature)
            unique.append((name, alloc))
        return unique

    def _admit(
        self,
        name: str,
        allocation: Dict[str, int],
        by_id: Dict[str, ExecutionVenueSpec],
        maker_ratio: float,
        accepted: List[VenueAllocationStrategy],
        rejected: List[RejectedStrategy],
    ) -> None:
        """Evaluate one candidate and file it as accepted or rejected with a stated reason."""
        capacity_breach = [
            f"{vid} allocated {qty:,} > cap {by_id[vid].max_allocatable_shares:,}"
            for vid, qty in allocation.items()
            if by_id[vid].max_allocatable_shares is not None
            and qty > by_id[vid].max_allocatable_shares
        ]
        if capacity_breach:
            rejected.append(RejectedStrategy(name, dict(allocation), "; ".join(capacity_breach)))
            return

        strategy = self._evaluate(name, allocation, by_id, maker_ratio)
        if strategy.weighted_passive_fill_probability < self.min_weighted_passive_fill_probability:
            rejected.append(
                RejectedStrategy(
                    name,
                    dict(allocation),
                    f"weighted passive fill probability "
                    f"{strategy.weighted_passive_fill_probability:.4f} < "
                    f"{self.min_weighted_passive_fill_probability:.4f}",
                )
            )
            return
        accepted.append(strategy)

    def _evaluate(
        self,
        name: str,
        allocation: Dict[str, int],
        by_id: Dict[str, ExecutionVenueSpec],
        maker_ratio: float,
    ) -> VenueAllocationStrategy:
        """Price one allocation."""
        breakdowns: List[VenueCostBreakdown] = []
        net = maker_rebates = taker_fees = opportunity = 0.0
        executed = 0.0
        passive_weight = 0.0
        passive_total = 0.0

        for venue_id, qty in allocation.items():
            venue = by_id[venue_id]
            bd = self.calculate_venue_net_cost(venue, qty, maker_ratio)
            breakdowns.append(bd)
            net += bd.net_cost_usd
            maker_rebates += bd.gross_maker_rebates_usd
            taker_fees += bd.gross_taker_fees_usd
            opportunity += bd.opportunity_cost_usd
            executed += bd.executed_shares
            passive_total += bd.posted_passive_shares
            passive_weight += bd.posted_passive_shares * venue.passive_fill_probability

        # Weighted over posted passive shares: fill probability is a property of passive
        # orders, so an all-aggressive allocation is not constrained by it.
        weighted_fill = (passive_weight / passive_total) if passive_total > 0 else 1.0

        return VenueAllocationStrategy(
            strategy_name=name,
            volume_allocations_shares=dict(allocation),
            total_net_cost_usd=round(net, _USD_DP),
            total_maker_rebates_usd=round(maker_rebates, _USD_DP),
            total_taker_fees_usd=round(taker_fees, _USD_DP),
            total_opportunity_cost_usd=round(opportunity, _USD_DP),
            weighted_passive_fill_probability=round(weighted_fill, 6),
            expected_executed_shares=round(executed, 4),
            per_venue=tuple(sorted(breakdowns, key=lambda b: b.venue_id)),
        )

    def _qualifying_volume(self, venue: ExecutionVenueSpec, executed_shares: float) -> int:
        """Volume that fixes the venue's tier, per the configured basis."""
        if self.qualification_basis is TierQualificationBasis.PRIOR_PERIOD:
            if venue.qualifying_volume_shares is None:
                raise VenueOptimizationError(
                    f"{venue.venue_id}: PRIOR_PERIOD basis requires qualifying_volume_shares "
                    f"(the completed prior period's volume). The volume being routed is precisely "
                    f"the volume that may not determine its own rate -- Reg NMS Rule 610(d)."
                )
            return venue.qualifying_volume_shares
        return int(math.floor(executed_shares))

    def _normalize_allocation(
        self, allocation: Dict[str, int], by_id: Dict[str, ExecutionVenueSpec], total: int
    ) -> Dict[str, int]:
        """Validate a caller-supplied allocation against the venue set and the budget."""
        if not allocation:
            raise VenueOptimizationError("baseline_allocation must not be empty.")
        cleaned: Dict[str, int] = {}
        for venue_id, qty in allocation.items():
            if venue_id not in by_id:
                raise VenueOptimizationError(
                    f"baseline_allocation references unknown venue '{venue_id}'."
                )
            if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0:
                raise VenueOptimizationError(
                    f"baseline_allocation['{venue_id}'] must be a non-negative int, got {qty!r}."
                )
            cleaned[venue_id] = qty
        allocated = sum(cleaned.values())
        if allocated != total:
            raise VenueOptimizationError(
                f"baseline_allocation sums to {allocated:,} shares but the budget is {total:,}. "
                "A baseline priced over a different volume is not comparable to the candidates."
            )
        return cleaned

    @staticmethod
    def _validate_maker_ratio(maker_ratio: float) -> None:
        if not isinstance(maker_ratio, (int, float)) or isinstance(maker_ratio, bool):
            raise VenueOptimizationError(f"maker_ratio must be numeric, got {type(maker_ratio).__name__}.")
        if not math.isfinite(maker_ratio) or not (-_RATIO_TOLERANCE <= maker_ratio <= 1.0 + _RATIO_TOLERANCE):
            raise VenueOptimizationError(
                f"maker_ratio must be a finite value in [0.0, 1.0], got {maker_ratio!r}. "
                "A ratio outside this range produces negative aggressive volume and fabricated rebates."
            )

    @staticmethod
    def _validate_venues(venues: Sequence[ExecutionVenueSpec]) -> None:
        if not venues:
            raise VenueOptimizationError("venues must contain at least one ExecutionVenueSpec.")
        for v in venues:
            if not isinstance(v, ExecutionVenueSpec):
                raise VenueOptimizationError(
                    f"venues must contain ExecutionVenueSpec instances, got {type(v).__name__}."
                )
        ids = [v.venue_id for v in venues]
        duplicates = {vid for vid in ids if ids.count(vid) > 1}
        if duplicates:
            raise VenueOptimizationError(
                f"duplicate venue_id(s) {sorted(duplicates)}: allocations are keyed by venue_id and "
                "would silently collide."
            )
