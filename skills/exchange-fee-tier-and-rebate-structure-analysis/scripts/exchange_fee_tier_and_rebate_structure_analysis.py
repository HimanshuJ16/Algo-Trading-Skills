import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class FeeTierDefinition:
    tier_name: str                      # e.g. 'Tier 1', 'Tier 2', 'VIP Tier'
    min_monthly_volume_shares: int      # e.g. 0, 10_000_000, 50_000_000
    maker_rate_per_share: float         # Negative for rebate (e.g. -0.0020), Positive for fee
    taker_rate_per_share: float         # Positive for fee (e.g. +0.0030)

@dataclass
class VenueVolumeSummary:
    venue_id: str
    pricing_model: str                  # 'MAKER_TAKER' or 'TAKER_MAKER'
    rolling_30d_maker_volume_shares: int
    rolling_30d_taker_volume_shares: int

@dataclass
class FeeTierAnalysisReport:
    venue_id: str
    active_tier_name: str
    pricing_model: str
    total_volume_shares: int
    gross_taker_fees_usd: float
    gross_maker_rebates_usd: float
    net_transaction_cost_usd: float
    effective_cost_per_share: float
    next_tier_name: Optional[str]
    volume_needed_for_next_tier_shares: int
    estimated_monthly_savings_at_next_tier_usd: float
    audit_notes: str

class ExchangeFeeTierAnalyzerEngine:
    """
    Quantitative market microstructure engine for analyzing exchange maker-taker vs inverted fee schedules,
    calculating rolling volume-tiered rebates, and evaluating next-tier volume jump opportunity costs.
    """
    def __init__(self, venue_id: str, pricing_model: str, tiers: List[FeeTierDefinition]):
        self.venue_id = venue_id
        self.pricing_model = pricing_model.upper()
        # Sort tiers by volume threshold ascending
        self.tiers = sorted(tiers, key=lambda t: t.min_monthly_volume_shares)

    def analyze_fee_tier_and_rebates(self, summary: VenueVolumeSummary) -> FeeTierAnalysisReport:
        """
        Determines active volume tier, computes net transaction costs, and evaluates tier jump savings.
        """
        total_vol = summary.rolling_30d_maker_volume_shares + summary.rolling_30d_taker_volume_shares

        # 1. Classify Active Tier
        active_tier = self.tiers[0]
        next_tier = None
        for idx, tier in enumerate(self.tiers):
            if total_vol >= tier.min_monthly_volume_shares:
                active_tier = tier
                next_tier = self.tiers[idx + 1] if idx + 1 < len(self.tiers) else None

        # 2. Calculate Gross & Net Transaction Costs
        # Gross Taker Fees
        gross_taker_fees = round(summary.rolling_30d_taker_volume_shares * active_tier.taker_rate_per_share, 2)

        # Gross Maker Rebates (if maker rate is negative e.g. -0.0020, rebate is positive money received)
        if active_tier.maker_rate_per_share < 0:
            gross_maker_rebates = round(summary.rolling_30d_maker_volume_shares * abs(active_tier.maker_rate_per_share), 2)
        else:
            gross_maker_rebates = 0.0

        # Net Cost = Taker Fees + (Maker Fee if positive) - (Maker Rebate if negative)
        if active_tier.maker_rate_per_share >= 0:
            gross_maker_fees = round(summary.rolling_30d_maker_volume_shares * active_tier.maker_rate_per_share, 2)
            net_cost = round(gross_taker_fees + gross_maker_fees, 2)
        else:
            net_cost = round(gross_taker_fees - gross_maker_rebates, 2)

        effective_cps = round(net_cost / float(total_vol), 6) if total_vol > 0 else 0.0

        # 3. Calculate Tier Jump Opportunity Cost
        vol_needed = 0
        est_savings = 0.0
        if next_tier:
            vol_needed = max(0, next_tier.min_monthly_volume_shares - total_vol)
            # Re-estimate cost at next tier rates
            next_taker_fees = summary.rolling_30d_taker_volume_shares * next_tier.taker_rate_per_share
            if next_tier.maker_rate_per_share < 0:
                next_maker_rebates = summary.rolling_30d_maker_volume_shares * abs(next_tier.maker_rate_per_share)
                next_net_cost = next_taker_fees - next_maker_rebates
            else:
                next_net_cost = next_taker_fees + (summary.rolling_30d_maker_volume_shares * next_tier.maker_rate_per_share)
            est_savings = round(net_cost - next_net_cost, 2)

        notes = (
            f"FEE TIER ANALYSIS [{self.venue_id}]: Active Tier = '{active_tier.tier_name}' (Total Vol: {total_vol:,} sh). "
            f"Net Cost = ${net_cost:,.2f} (${effective_cps:.5f}/sh). Taker Fees = ${gross_taker_fees:,.2f}, Maker Rebates = ${gross_maker_rebates:,.2f}."
        )
        logger.info(notes)

        return FeeTierAnalysisReport(
            venue_id=self.venue_id,
            active_tier_name=active_tier.tier_name,
            pricing_model=self.pricing_model,
            total_volume_shares=total_vol,
            gross_taker_fees_usd=gross_taker_fees,
            gross_maker_rebates_usd=gross_maker_rebates,
            net_transaction_cost_usd=net_cost,
            effective_cost_per_share=effective_cps,
            next_tier_name=next_tier.tier_name if next_tier else None,
            volume_needed_for_next_tier_shares=vol_needed,
            estimated_monthly_savings_at_next_tier_usd=max(0.0, est_savings),
            audit_notes=notes
        )
