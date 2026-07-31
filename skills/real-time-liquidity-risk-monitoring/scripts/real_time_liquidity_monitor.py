import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    param: float = 1.0                  # Retained for legacy backward compatibility
    max_dtl_threshold_days: float = 2.0  # Max allowable Days to Liquidate
    max_participation_pct: float = 0.10  # 10% daily volume participation cap
    spread_spike_threshold_ratio: float = 2.0 # Spread > 2.0x normal is a spike
    depth_drop_threshold_pct: float = 0.50     # 50% drop in L2 depth is a collapse

@dataclass
class PortfolioPositionLiquidity:
    symbol: str
    position_size: float
    current_price: float
    adv: float                           # Average Daily Volume
    bid_ask_spread: float
    l2_depth_top3: float
    normal_spread: float
    normal_l2_depth: float

@dataclass
class PositionLiquidityMetrics:
    symbol: str
    notional_usd: float
    days_to_liquidate: float
    spread_ratio: float
    depth_drop_pct: float
    dtl_breached: bool
    spread_spike_flag: bool
    depth_collapse_flag: bool
    l_var_contribution_usd: float

@dataclass
class RealTimeLiquidityReport:
    total_portfolio_notional_usd: float
    max_days_to_liquidate: float
    total_dtl_breaches: int
    total_spread_spikes: int
    total_depth_collapses: int
    portfolio_l_var_usd: float
    position_metrics: List[PositionLiquidityMetrics]
    status: str                          # 'LIQUIDITY_HEALTHY', 'LIQUIDITY_RISK_ALERT'
    audit_notes: str

class MainEngine:
    """
    Legacy MainEngine retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def process(self, value: float) -> float:
        return value * self.config.param

class RealTimeLiquidityMonitorEngine:
    """
    Real-time portfolio liquidity risk monitor tracking Days to Liquidate (DTL),
    bid-ask spread spikes, order book depth collapse, and Liquidity-Adjusted VaR (L-VaR).
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def audit_portfolio_liquidity(
        self,
        positions: List[PortfolioPositionLiquidity],
        baseline_var_99_usd: float = 100000.0
    ) -> RealTimeLiquidityReport:
        """
        Audits DTL, spread spikes, L2 depth collapse, and computes Liquidity-Adjusted VaR (L-VaR).
        """
        if not positions:
            return RealTimeLiquidityReport(
                total_portfolio_notional_usd=0.0,
                max_days_to_liquidate=0.0,
                total_dtl_breaches=0,
                total_spread_spikes=0,
                total_depth_collapses=0,
                portfolio_l_var_usd=round(baseline_var_99_usd, 2),
                position_metrics=[],
                status="NO_POSITIONS",
                audit_notes="No positions provided for liquidity audit."
            )

        pos_metrics_list: List[PositionLiquidityMetrics] = []
        total_notional = 0.0
        max_dtl = 0.0
        dtl_breaches = 0
        spread_spikes = 0
        depth_collapses = 0
        total_liquidation_cost_usd = 0.0

        for p in positions:
            notional = abs(p.position_size) * p.current_price
            total_notional += notional

            # 1. Days to Liquidate (DTL)
            daily_cap_vol = max(1.0, p.adv * self.config.max_participation_pct)
            dtl = abs(p.position_size) / daily_cap_vol
            if dtl > max_dtl:
                max_dtl = dtl

            dtl_flag = dtl > self.config.max_dtl_threshold_days
            if dtl_flag:
                dtl_breaches += 1

            # 2. Spread Spike Ratio
            norm_spread = max(0.0001, p.normal_spread)
            spread_ratio = p.bid_ask_spread / norm_spread
            spread_flag = spread_ratio >= self.config.spread_spike_threshold_ratio
            if spread_flag:
                spread_spikes += 1

            # 3. Depth Collapse Pct
            norm_depth = max(1.0, p.normal_l2_depth)
            depth_drop = 1.0 - (p.l2_depth_top3 / norm_depth)
            depth_flag = depth_drop >= self.config.depth_drop_threshold_pct
            if depth_flag:
                depth_collapses += 1

            # 4. Liquidity Cost Contribution = 0.5 * Notional * (RelativeSpread + 0.1 * DTL)
            rel_spread = p.bid_ask_spread / max(0.01, p.current_price)
            liq_cost = 0.5 * notional * (rel_spread + (0.1 * dtl))
            total_liquidation_cost_usd += liq_cost

            pos_metrics_list.append(
                PositionLiquidityMetrics(
                    symbol=p.symbol,
                    notional_usd=round(notional, 2),
                    days_to_liquidate=round(dtl, 2),
                    spread_ratio=round(spread_ratio, 2),
                    depth_drop_pct=round(max(0.0, depth_drop * 100.0), 1),
                    dtl_breached=dtl_flag,
                    spread_spike_flag=spread_flag,
                    depth_collapse_flag=depth_flag,
                    l_var_contribution_usd=round(liq_cost, 2)
                )
            )

        portfolio_l_var = baseline_var_99_usd + total_liquidation_cost_usd
        is_healthy = dtl_breaches == 0 and spread_spikes == 0 and depth_collapses == 0
        status = "LIQUIDITY_HEALTHY" if is_healthy else "LIQUIDITY_RISK_ALERT"

        notes = (
            f"REAL-TIME LIQUIDITY REPORT [{status}]: "
            f"Portfolio Notional = ${total_notional:,.2f}, Max DTL = {max_dtl:.2f} days "
            f"(Breaches: {dtl_breaches}, Spread Spikes: {spread_spikes}, Depth Collapses: {depth_collapses}). "
            f"Base VaR = ${baseline_var_99_usd:,.2f}, L-VaR = ${portfolio_l_var:,.2f}."
        )

        if not is_healthy:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RealTimeLiquidityReport(
            total_portfolio_notional_usd=round(total_notional, 2),
            max_days_to_liquidate=round(max_dtl, 2),
            total_dtl_breaches=dtl_breaches,
            total_spread_spikes=spread_spikes,
            total_depth_collapses=depth_collapses,
            portfolio_l_var_usd=round(portfolio_l_var, 2),
            position_metrics=pos_metrics_list,
            status=status,
            audit_notes=notes
        )
