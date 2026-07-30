import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class VenueFeeTier:
    tier_name: str
    min_volume_shares: int
    taker_rate_per_share: float         # Positive for fee
    maker_rate_per_share: float         # Negative for rebate

@dataclass
class ExecutionVenueSpec:
    venue_id: str
    fill_probability_rating: float      # 0.0 to 1.0
    tiers: List[VenueFeeTier]

@dataclass
class VenueAllocationStrategy:
    strategy_name: str
    volume_allocations_shares: Dict[str, int] # venue_id -> assigned volume
    total_net_cost_usd: float
    total_maker_rebates_usd: float
    total_taker_fees_usd: float
    weighted_fill_probability: float

@dataclass
class VenueFeeOptimizationReport:
    total_monthly_volume_target_shares: int
    maker_volume_ratio: float
    taker_volume_ratio: float
    optimal_strategy: VenueAllocationStrategy
    all_strategies_evaluated: List[VenueAllocationStrategy]
    monthly_cost_savings_vs_default_usd: float
    audit_notes: str

class ExecutionVenueFeeTierOptimizerEngine:
    """
    Quantitative Smart Order Router (SOR) engine for optimizing order flow volume distribution across multiple execution venues,
    unlocking volume fee tiers, and minimizing net transaction costs.
    """
    def __init__(self, min_fill_probability_threshold: float = 0.80):
        self.min_fill_probability_threshold = min_fill_probability_threshold

    def calculate_venue_net_cost(
        self,
        venue: ExecutionVenueSpec,
        allocated_volume_shares: int,
        maker_ratio: float
    ) -> Tuple[float, float, float]:
        """
        Calculates gross taker fees, gross maker rebates, and net cost for a venue given allocated volume.
        Returns: (net_cost_usd, gross_taker_fees_usd, gross_maker_rebates_usd)
        """
        if allocated_volume_shares <= 0:
            return 0.0, 0.0, 0.0

        # Determine active fee tier
        sorted_tiers = sorted(venue.tiers, key=lambda t: t.min_volume_shares)
        active_tier = sorted_tiers[0]
        for t in sorted_tiers:
            if allocated_volume_shares >= t.min_volume_shares:
                active_tier = t

        maker_vol = int(allocated_volume_shares * maker_ratio)
        taker_vol = allocated_volume_shares - maker_vol

        taker_fees = round(taker_vol * active_tier.taker_rate_per_share, 2)
        maker_rebates = round(maker_vol * abs(active_tier.maker_rate_per_share), 2) if active_tier.maker_rate_per_share < 0 else 0.0

        if active_tier.maker_rate_per_share >= 0:
            net_cost = round(taker_fees + (maker_vol * active_tier.maker_rate_per_share), 2)
        else:
            net_cost = round(taker_fees - maker_rebates, 2)

        return net_cost, taker_fees, maker_rebates

    def optimize_venue_fee_allocation(
        self,
        venues: List[ExecutionVenueSpec],
        total_volume_shares: int,
        maker_ratio: float = 0.70           # 70% Maker / 30% Taker order flow mix
    ) -> VenueFeeOptimizationReport:
        """
        Evaluates candidate volume allocation strategies across venues to minimize total net transaction costs.
        """
        if not venues or total_volume_shares <= 0:
            raise ValueError("Venues list cannot be empty and total volume must be > 0.")

        strategies: List[VenueAllocationStrategy] = []

        # Strategy 1: Single-Venue Concentrated (Max VIP Tier)
        for v in venues:
            alloc = {v.venue_id: total_volume_shares}
            net_c, taker_f, maker_r = self.calculate_venue_net_cost(v, total_volume_shares, maker_ratio)
            strategies.append(VenueAllocationStrategy(
                strategy_name=f"CONCENTRATED_{v.venue_id}",
                volume_allocations_shares=alloc,
                total_net_cost_usd=net_c,
                total_maker_rebates_usd=maker_r,
                total_taker_fees_usd=taker_f,
                weighted_fill_probability=v.fill_probability_rating
            ))

        # Strategy 2: Equal Split across all venues
        split_vol = total_volume_shares // len(venues)
        alloc_split: Dict[str, int] = {}
        tot_net = 0.0
        tot_taker = 0.0
        tot_maker = 0.0
        tot_fill = 0.0

        for v in venues:
            alloc_split[v.venue_id] = split_vol
            nc, tf, mr = self.calculate_venue_net_cost(v, split_vol, maker_ratio)
            tot_net += nc
            tot_taker += tf
            tot_maker += mr
            tot_fill += v.fill_probability_rating * (split_vol / float(total_volume_shares))

        strategies.append(VenueAllocationStrategy(
            strategy_name="EQUAL_SPLIT_BALANCED",
            volume_allocations_shares=alloc_split,
            total_net_cost_usd=round(tot_net, 2),
            total_maker_rebates_usd=round(tot_maker, 2),
            total_taker_fees_usd=round(tot_taker, 2),
            weighted_fill_probability=round(tot_fill, 2)
        ))

        # Strategy 3: Threshold-Optimal Split (Allocate to reach Tier 2 on top 2 venues)
        if len(venues) >= 2:
            v1, v2 = venues[0], venues[1]
            t2_vol1 = v1.tiers[-1].min_volume_shares if len(v1.tiers) > 1 else split_vol
            v1_alloc = min(total_volume_shares, t2_vol1)
            v2_alloc = total_volume_shares - v1_alloc

            alloc_thresh = {v1.venue_id: v1_alloc, v2.venue_id: v2_alloc}
            nc1, tf1, mr1 = self.calculate_venue_net_cost(v1, v1_alloc, maker_ratio)
            nc2, tf2, mr2 = self.calculate_venue_net_cost(v2, v2_alloc, maker_ratio)

            tot_fill_thresh = (v1.fill_probability_rating * (v1_alloc / float(total_volume_shares))) + (v2.fill_probability_rating * (v2_alloc / float(total_volume_shares)))

            strategies.append(VenueAllocationStrategy(
                strategy_name="FEE_THRESHOLD_OPTIMAL",
                volume_allocations_shares=alloc_thresh,
                total_net_cost_usd=round(nc1 + nc2, 2),
                total_maker_rebates_usd=round(mr1 + mr2, 2),
                total_taker_fees_usd=round(tf1 + tf2, 2),
                weighted_fill_probability=round(tot_fill_thresh, 2)
            ))

        # Filter strategies by fill probability threshold and pick lowest net cost
        valid_strats = [s for s in strategies if s.weighted_fill_probability >= self.min_fill_probability_threshold]
        if not valid_strats:
            valid_strats = strategies

        sorted_strats = sorted(valid_strats, key=lambda s: s.total_net_cost_usd)
        optimal = sorted_strats[0]
        worst_default = sorted_strats[-1]
        savings = round(worst_default.total_net_cost_usd - optimal.total_net_cost_usd, 2)

        notes = (
            f"VENUE FEE TIER OPTIMIZATION COMPLETE: Target Volume = {total_volume_shares:,} sh ({maker_ratio*100:.0f}% Maker). "
            f"Optimal Strategy: '{optimal.strategy_name}' with Net Cost = ${optimal.total_net_cost_usd:,.2f} "
            f"(Gross Rebates = ${optimal.total_maker_rebates_usd:,.2f}). Monthly Savings = ${savings:,.2f} vs default."
        )
        logger.info(notes)

        return VenueFeeOptimizationReport(
            total_monthly_volume_target_shares=total_volume_shares,
            maker_volume_ratio=maker_ratio,
            taker_volume_ratio=round(1.0 - maker_ratio, 2),
            optimal_strategy=optimal,
            all_strategies_evaluated=sorted_strats,
            monthly_cost_savings_vs_default_usd=max(0.0, savings),
            audit_notes=notes
        )
