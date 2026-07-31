import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Feed Tiers & Default Monthly Costs (USD)
TIER_COSTS: Dict[str, float] = {
    "TIER1_DIRECT_L3": 1000.0, # Co-location / Direct L3 Feed
    "TIER2_SIP_L1": 150.0,      # Filtered SIP L1/L2 Feed
    "TIER3_DELAYED_EOD": 5.0,   # Delayed / End-Of-Day Feed
}

@dataclass
class SymbolSubscriptionSpec:
    symbol: str
    current_tier: str                   # 'TIER1_DIRECT_L3', 'TIER2_SIP_L1', 'TIER3_DELAYED_EOD'
    has_active_position: bool
    has_active_signal: bool
    days_since_last_trade: int

@dataclass
class TierOptimizationDecision:
    symbol: str
    previous_tier: str
    recommended_tier: str
    action: str                         # 'MAINTAIN', 'PROMOTE', 'DEMOTE'
    previous_cost_usd: float
    recommended_cost_usd: float
    monthly_savings_usd: float

@dataclass
class MarketDataCostReport:
    total_symbols_audited: int
    promotions_count: int
    demotions_count: int
    baseline_monthly_spend_usd: float
    optimized_monthly_spend_usd: float
    total_monthly_savings_usd: float
    savings_percentage: float
    decisions: List[TierOptimizationDecision]
    status: str                         # 'COST_OPTIMIZATION_SUCCESS', 'NO_SAVINGS_FOUND'
    audit_notes: str

class MarketDataCostOptimizerEngine:
    """
    Quantitative market data feed cost optimization engine,
    dynamically promoting active trading symbols to high-frequency L3 direct feeds while demoting inactive universe symbols to low-cost EOD/SIP tiers.
    """
    def __init__(self, demotion_inactivity_days_threshold: int = 30):
        self.demotion_inactivity_days_threshold = demotion_inactivity_days_threshold

    def determine_optimal_tier(self, spec: SymbolSubscriptionSpec) -> str:
        """
        Determines optimal data tier based on position status, trading signals, and trade recency.
        """
        if spec.has_active_position and spec.has_active_signal:
            return "TIER1_DIRECT_L3"
        elif spec.has_active_position or spec.days_since_last_trade <= self.demotion_inactivity_days_threshold:
            return "TIER2_SIP_L1"
        else:
            return "TIER3_DELAYED_EOD"

    def optimize_market_data_costs(
        self,
        subscriptions: List[SymbolSubscriptionSpec]
    ) -> MarketDataCostReport:
        """
        Audits active subscriptions, determines promotions/demotions, and calculates monthly cost savings.
        """
        if not subscriptions:
            raise ValueError("Subscription list cannot be empty.")

        decisions: List[TierOptimizationDecision] = []
        baseline_spend = 0.0
        optimized_spend = 0.0
        promotions = 0
        demotions = 0

        tier_rank = {"TIER3_DELAYED_EOD": 1, "TIER2_SIP_L1": 2, "TIER1_DIRECT_L3": 3}

        for spec in subscriptions:
            curr_tier = spec.current_tier.strip().upper()
            opt_tier = self.determine_optimal_tier(spec)

            prev_cost = TIER_COSTS.get(curr_tier, 1000.0)
            opt_cost = TIER_COSTS.get(opt_tier, 5.0)

            curr_rank = tier_rank.get(curr_tier, 3)
            opt_rank = tier_rank.get(opt_tier, 1)

            if opt_rank > curr_rank:
                action = "PROMOTE"
                promotions += 1
            elif opt_rank < curr_rank:
                action = "DEMOTE"
                demotions += 1
            else:
                action = "MAINTAIN"

            savings = prev_cost - opt_cost

            baseline_spend += prev_cost
            optimized_spend += opt_cost

            decisions.append(
                TierOptimizationDecision(
                    symbol=spec.symbol,
                    previous_tier=curr_tier,
                    recommended_tier=opt_tier,
                    action=action,
                    previous_cost_usd=prev_cost,
                    recommended_cost_usd=opt_cost,
                    monthly_savings_usd=savings
                )
            )

        total_savings = round(baseline_spend - optimized_spend, 2)
        savings_pct = round((total_savings / baseline_spend) * 100.0, 1) if baseline_spend > 0 else 0.0

        if total_savings > 0:
            status = "COST_OPTIMIZATION_SUCCESS"
            notes = (
                f"MARKET DATA COST OPTIMIZATION SUCCESS: Reduced monthly spend from ${baseline_spend:,.2f} "
                f"to ${optimized_spend:,.2f} USD (Saved ${total_savings:,.2f}/mo, {savings_pct:.1f}% reduction). "
                f"Promotions = {promotions}, Demotions = {demotions}."
            )
            logger.info(notes)
        else:
            status = "NO_SAVINGS_FOUND"
            notes = f"MARKET DATA COST AUDIT: Current subscriptions already optimal (${baseline_spend:,.2f}/mo)."
            logger.info(notes)

        return MarketDataCostReport(
            total_symbols_audited=len(subscriptions),
            promotions_count=promotions,
            demotions_count=demotions,
            baseline_monthly_spend_usd=round(baseline_spend, 2),
            optimized_monthly_spend_usd=round(optimized_spend, 2),
            total_monthly_savings_usd=total_savings,
            savings_percentage=savings_pct,
            decisions=decisions,
            status=status,
            audit_notes=notes
        )
