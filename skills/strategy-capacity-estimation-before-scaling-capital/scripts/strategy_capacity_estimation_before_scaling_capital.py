import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class StrategyCapacityEstimationBeforeScalingCapitalConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True

class StrategyCapacityEstimationBeforeScalingCapital:
    """Legacy class retained for 100% backward compatibility."""
    def __init__(self, config: StrategyCapacityEstimationBeforeScalingCapitalConfig):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled

@dataclass
class StrategyParameters:
    strategy_id: str
    annual_gross_return_pct: float         # Frictionless gross return e.g. 0.25 (25%)
    annual_volatility_pct: float           # Annual volatility e.g. 0.15 (15%)
    daily_turnover_pct: float              # Daily turnover e.g. 0.50 (50% of AUM traded daily)
    avg_daily_volume_usd: float            # Universe ADV in USD e.g. $50,000,000
    avg_daily_volatility_pct: float        # Daily volatility e.g. 0.015 (1.5%)
    half_spread_bps: float = 2.0           # Half bid-ask spread (2 bps)
    max_participation_rate_pct: float = 5.0# Max 5% of ADV
    min_acceptable_sharpe: float = 1.0     # Minimum acceptable Net Sharpe ratio

@dataclass
class AUMCapacityPoint:
    aum_usd: float
    gross_return_pct: float
    spread_cost_usd: float
    market_impact_cost_usd: float
    net_return_pct: float
    net_sharpe_ratio: float
    adv_participation_pct: float
    is_capacity_exceeded: bool

@dataclass
class StrategyCapacityReport:
    strategy_id: str
    frictionless_sharpe_ratio: float
    max_capacity_aum_usd: float            # Max AUM before Sharpe drops below min threshold
    optimal_sharpe_capacity_aum_usd: float  # AUM that maximizes net dollar PnL
    capacity_curve: List[AUMCapacityPoint]
    limiting_factor: str                   # 'MARKET_IMPACT_DECAY', 'ADV_PARTICIPATION_LIMIT', 'MIN_SHARPE_BREACH'
    audit_notes: str

class StrategyCapacityEstimatorEngine:
    """
    Production-grade Strategy Capacity Estimation Engine incorporating Almgren-Chriss square-root market impact,
    turnover drag, ADV participation limits, and Sharpe ratio decay curve modeling.
    """
    def __init__(self, impact_gamma: float = 0.5):
        self.impact_gamma = impact_gamma   # Almgren-Chriss impact scaling factor

    def _estimate_daily_market_impact_pct(
        self,
        daily_trade_size_usd: float,
        adv_usd: float,
        daily_vol_pct: float
    ) -> float:
        """
        Calculates square-root market impact percentage:
        Impact % = gamma * daily_vol * sqrt(Trade_Size / ADV)
        """
        if adv_usd <= 0 or daily_trade_size_usd <= 0:
            return 0.0
        participation = daily_trade_size_usd / adv_usd
        impact_pct = self.impact_gamma * daily_vol_pct * math.sqrt(participation)
        return impact_pct

    def estimate_capacity(
        self,
        params: StrategyParameters,
        aum_step_usd: float = 1_000_000.0,
        max_search_aum_usd: float = 200_000_000.0
    ) -> StrategyCapacityReport:
        """
        Simulates strategy returns across scaling AUM steps to construct the Sharpe decay curve
        and determine maximum capital capacity.
        """
        frictionless_sharpe = round(params.annual_gross_return_pct / params.annual_volatility_pct, 2)
        capacity_curve: List[AUMCapacityPoint] = []

        max_capacity_aum = 0.0
        optimal_pnl_aum = 0.0
        max_net_pnl_usd = -1e9
        primary_limiting_factor = "UNLIMITED"

        aum = aum_step_usd
        while aum <= max_search_aum_usd:
            # 1. Daily Trading Notional
            daily_turnover_usd = aum * params.daily_turnover_pct
            adv_participation = (daily_turnover_usd / params.avg_daily_volume_usd) * 100.0

            # 2. Annual Spread Cost
            annual_spread_cost_usd = daily_turnover_usd * 252 * (params.half_spread_bps / 10000.0)

            # 3. Almgren-Chriss Daily Market Impact Cost
            daily_impact_pct = self._estimate_daily_market_impact_pct(
                daily_turnover_usd, params.avg_daily_volume_usd, params.avg_daily_volatility_pct
            )
            annual_impact_cost_usd = daily_turnover_usd * 252 * daily_impact_pct

            # 4. Total Costs & Net Return
            total_annual_cost_usd = annual_spread_cost_usd + annual_impact_cost_usd
            gross_pnl_usd = aum * params.annual_gross_return_pct
            net_pnl_usd = gross_pnl_usd - total_annual_cost_usd

            net_return_pct = net_pnl_usd / aum
            net_sharpe = net_return_pct / params.annual_volatility_pct

            is_exceeded = (
                net_sharpe < params.min_acceptable_sharpe or
                adv_participation > params.max_participation_rate_pct
            )

            point = AUMCapacityPoint(
                aum_usd=aum,
                gross_return_pct=params.annual_gross_return_pct,
                spread_cost_usd=round(annual_spread_cost_usd, 2),
                market_impact_cost_usd=round(annual_impact_cost_usd, 2),
                net_return_pct=round(net_return_pct, 4),
                net_sharpe_ratio=round(net_sharpe, 2),
                adv_participation_pct=round(adv_participation, 2),
                is_capacity_exceeded=is_exceeded
            )
            capacity_curve.append(point)

            # Track optimal AUM maximizing net dollar PnL
            if net_pnl_usd > max_net_pnl_usd:
                max_net_pnl_usd = net_pnl_usd
                optimal_pnl_aum = aum

            # Track maximum capacity AUM before breach
            if not is_exceeded:
                max_capacity_aum = aum
            elif primary_limiting_factor == "UNLIMITED":
                if adv_participation > params.max_participation_rate_pct:
                    primary_limiting_factor = "ADV_PARTICIPATION_LIMIT"
                else:
                    primary_limiting_factor = "MIN_SHARPE_BREACH"

            aum += aum_step_usd

        notes = (
            f"STRATEGY CAPACITY REPORT [{params.strategy_id}]: Frictionless Sharpe = {frictionless_sharpe}, "
            f"Max Capacity AUM = ${max_capacity_aum:,.0f}, Optimal PnL AUM = ${optimal_pnl_aum:,.0f}, "
            f"Limiting Factor = {primary_limiting_factor}."
        )

        logger.info(notes)

        return StrategyCapacityReport(
            strategy_id=params.strategy_id,
            frictionless_sharpe_ratio=frictionless_sharpe,
            max_capacity_aum_usd=max_capacity_aum,
            optimal_sharpe_capacity_aum_usd=optimal_pnl_aum,
            capacity_curve=capacity_curve,
            limiting_factor=primary_limiting_factor,
            audit_notes=notes
        )
