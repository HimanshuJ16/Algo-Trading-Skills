"""
transaction-cost-analysis-tca-integration: Implementation shortfall breakdown engine,
sqrt market impact model, and backtest slippage calibration module.
"""
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TCATradeBreakdown:
    symbol: str
    action: str  # BUY or SELL
    order_size: float
    adv: float
    p_decision: float
    p_arrival: float
    p_fill: float
    spread: float
    delay_cost_bps: float
    spread_cross_bps: float
    market_impact_bps: float
    commission_bps: float
    total_shortfall_bps: float


@dataclass
class TCAPortfolioSummary:
    total_trades_analyzed: int
    avg_implementation_shortfall_bps: float
    total_market_impact_cost_usd: float
    total_commission_cost_usd: float
    gross_return_pct: float
    net_tca_return_pct: float
    is_strategy_viable: bool


class TCABacktestIntegrator:
    """
    Decomposes implementation shortfall into delay cost, spread cross, market impact,
    and commissions to calibrate backtest slippage.
    """

    def __init__(
        self,
        market_impact_gamma: float = 15.0,  # Constant for sqrt market impact model
        fixed_commission_bps: float = 2.5,
        max_acceptable_shortfall_bps: float = 50.0,
    ):
        self.gamma = market_impact_gamma
        self.fixed_commission_bps = fixed_commission_bps
        self.max_acceptable_shortfall_bps = max_acceptable_shortfall_bps

    def analyze_trade(
        self,
        symbol: str,
        action: str,
        order_size: float,
        adv: float,
        p_decision: float,
        p_arrival: float,
        p_fill: float,
        spread: float,
    ) -> TCATradeBreakdown:
        """
        Decomposes trade execution costs into 4 sub-components.
        """
        is_buy = (action.upper() == "BUY")
        direction = 1.0 if is_buy else -1.0

        # 1. Delay Cost (signal decision -> order arrival at exchange)
        delay_cost_bps = direction * ((p_arrival - p_decision) / p_decision) * 10000.0

        # 2. Half Spread Cross Cost
        spread_cross_bps = (0.5 * spread / p_decision) * 10000.0

        # 3. Square Root Market Impact Model: gamma * sqrt(Size / ADV)
        volume_pct = min(1.0, order_size / max(1.0, adv))
        market_impact_bps = self.gamma * math.sqrt(volume_pct)

        # 4. Total Implementation Shortfall
        total_shortfall_bps = delay_cost_bps + spread_cross_bps + market_impact_bps + self.fixed_commission_bps

        return TCATradeBreakdown(
            symbol=symbol.upper(),
            action=action.upper(),
            order_size=order_size,
            adv=adv,
            p_decision=p_decision,
            p_arrival=p_arrival,
            p_fill=p_fill,
            spread=spread,
            delay_cost_bps=round(delay_cost_bps, 2),
            spread_cross_bps=round(spread_cross_bps, 2),
            market_impact_bps=round(market_impact_bps, 2),
            commission_bps=round(self.fixed_commission_bps, 2),
            total_shortfall_bps=round(total_shortfall_bps, 2),
        )

    def evaluate_portfolio_tca(
        self,
        trades: List[TCATradeBreakdown],
        gross_return_pct: float,
    ) -> TCAPortfolioSummary:
        """
        Aggregates TCA trade breakdowns to calibrate net-of-TCA backtest returns.
        """
        if not trades:
            return TCAPortfolioSummary(0, 0.0, 0.0, 0.0, gross_return_pct, gross_return_pct, True)

        total_shortfall = sum(t.total_shortfall_bps for t in trades)
        avg_shortfall = total_shortfall / len(trades)

        total_impact_usd = sum((t.market_impact_bps / 10000.0) * (t.order_size * t.p_decision) for t in trades)
        total_comm_usd = sum((t.commission_bps / 10000.0) * (t.order_size * t.p_decision) for t in trades)

        # Convert avg shortfall bps to percentage drag
        friction_drag_pct = (avg_shortfall * len(trades)) / 100.0
        net_return_pct = gross_return_pct - friction_drag_pct

        is_viable = (avg_shortfall <= self.max_acceptable_shortfall_bps) and (net_return_pct > 0.0)

        msg = (
            f"TCA Portfolio Analysis ({len(trades)} trades): Avg Shortfall={avg_shortfall:.1f} bps | "
            f"Gross Ret={gross_return_pct:.2f}% -> Net Ret={net_return_pct:.2f}%."
        )
        logger.info(msg)

        return TCAPortfolioSummary(
            total_trades_analyzed=len(trades),
            avg_implementation_shortfall_bps=round(avg_shortfall, 2),
            total_market_impact_cost_usd=round(total_impact_usd, 2),
            total_commission_cost_usd=round(total_comm_usd, 2),
            gross_return_pct=round(gross_return_pct, 2),
            net_tca_return_pct=round(net_return_pct, 2),
            is_strategy_viable=is_viable,
        )
