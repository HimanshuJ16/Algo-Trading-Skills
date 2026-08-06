import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class TickRegime(Enum):
    STANDARD_CENT = "STANDARD_CENT"      # Standard $0.01 tick size
    WIDENED_FIVE_CENT = "WIDENED_FIVE_CENT" # Widened $0.05 tick size (e.g. SEC Pilot Test Groups)
    SUB_PENNY = "SUB_PENNY"              # Sub-penny $0.001 / $0.005 tick size
    DYNAMIC_BAND = "DYNAMIC_BAND"        # Dynamic tick size based on price level (MiFID II / TASE)


class AlgoStrategyType(Enum):
    PASSIVE_MARKET_MAKING = "PASSIVE_MARKET_MAKING"
    TWAP_VWAP_SLICING = "TWAP_VWAP_SLICING"
    STAT_ARB = "STAT_ARB"
    MOMENTUM_TAKER = "MOMENTUM_TAKER"


class MicrostructureError(Exception):
    """Base exception for microstructure assessment errors."""
    pass


@dataclass
class TickSnapshot:
    timestamp_ns: int
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    last_trade_price: Optional[float] = None
    last_trade_size: Optional[float] = None
    last_trade_is_buy: Optional[bool] = None
    future_mid_price_5m: Optional[float] = None  # Future mid for realized spread calculation


@dataclass
class TickMetrics:
    symbol: str
    regime: TickRegime
    sample_count: int
    avg_quoted_spread: float
    avg_effective_spread: float
    avg_realized_spread_5m: float
    avg_top_depth_shares: float
    avg_order_to_trade_ratio: float
    realized_fill_rate_pct: float
    adverse_selection_bps: float


@dataclass
class RegimeComparisonResult:
    symbol: str
    baseline_regime: TickRegime
    test_regime: TickRegime
    quoted_spread_change_pct: float
    effective_spread_change_pct: float
    top_depth_change_pct: float
    fill_rate_change_pct: float
    adverse_selection_change_bps: float
    key_findings: List[str] = field(default_factory=list)


class TickSizeImpactEngine:
    """
    Institutional Market Microstructure Analytics Engine.
    
    Evaluates the impact of tick size regime changes (e.g. $0.01 to $0.05 widening
    or sub-penny narrowing) on liquidity metrics, spread components (Quoted, Effective,
    Realized), order book depth, queue times, and algorithmic strategy execution.
    """

    def __init__(self):
        logger.info("Initialized Tick Size Pilot Program Impact Assessment Engine")

    @staticmethod
    def calculate_quoted_spread(bid: float, ask: float) -> float:
        """Calculates simple quoted spread (Ask - Bid)."""
        if ask <= bid:
            raise MicrostructureError(f"Invalid market state: Ask ({ask}) <= Bid ({bid})")
        return ask - bid

    @staticmethod
    def calculate_effective_spread(trade_price: float, mid_price: float, is_buy: bool) -> float:
        """
        Calculates effective spread ($): 2 * Direction * (Trade_Price - Midpoint).
        Measures actual execution cost paid by market takers.
        """
        direction = 1.0 if is_buy else -1.0
        return 2.0 * direction * (trade_price - mid_price)

    @staticmethod
    def calculate_realized_spread(trade_price: float, future_mid_price: float, is_buy: bool) -> float:
        """
        Calculates 5-minute realized spread ($): 2 * Direction * (Trade_Price - Midpoint_future).
        Measures market maker revenue net of adverse selection.
        """
        direction = 1.0 if is_buy else -1.0
        return 2.0 * direction * (trade_price - future_mid_price)

    def evaluate_microstructure_metrics(
        self, symbol: str, regime: TickRegime, snapshots: List[TickSnapshot], total_messages: int = 0, total_fills: int = 0
    ) -> TickMetrics:
        """
        Computes aggregate microstructure metrics across a time series of tick snapshots.
        """
        if not snapshots:
            raise MicrostructureError("Cannot compute metrics on empty snapshot list.")

        quoted_spreads = []
        effective_spreads = []
        realized_spreads = []
        top_depths = []
        adverse_selections = []

        for s in snapshots:
            mid = (s.bid_price + s.ask_price) / 2.0
            q_spread = self.calculate_quoted_spread(s.bid_price, s.ask_price)
            quoted_spreads.append(q_spread)
            top_depths.append(s.bid_size + s.ask_size)

            if s.last_trade_price is not None and s.last_trade_is_buy is not None:
                eff_spread = self.calculate_effective_spread(s.last_trade_price, mid, s.last_trade_is_buy)
                effective_spreads.append(eff_spread)

                if s.future_mid_price_5m is not None:
                    real_spread = self.calculate_realized_spread(s.last_trade_price, s.future_mid_price_5m, s.last_trade_is_buy)
                    realized_spreads.append(real_spread)
                    # Adverse selection = Effective Spread - Realized Spread
                    adv_sel = (eff_spread - real_spread) / mid * 10_000.0  # in bps
                    adverse_selections.append(adv_sel)

        avg_q_spread = sum(quoted_spreads) / len(quoted_spreads)
        avg_eff_spread = sum(effective_spreads) / len(effective_spreads) if effective_spreads else avg_q_spread
        avg_real_spread = sum(realized_spreads) / len(realized_spreads) if realized_spreads else avg_eff_spread / 2.0
        avg_depth = sum(top_depths) / len(top_depths)
        avg_adv_sel = sum(adverse_selections) / len(adverse_selections) if adverse_selections else 0.0

        otr = (total_messages / total_fills) if total_fills > 0 else 0.0
        fill_rate = (total_fills / total_messages * 100.0) if total_messages > 0 else 0.0

        logger.info(
            f"Evaluated {symbol} under {regime.value}: Quoted Spread=${avg_q_spread:.4f}, Depth={avg_depth:.0f} shares"
        )

        return TickMetrics(
            symbol=symbol,
            regime=regime,
            sample_count=len(snapshots),
            avg_quoted_spread=avg_q_spread,
            avg_effective_spread=avg_eff_spread,
            avg_realized_spread_5m=avg_real_spread,
            avg_top_depth_shares=avg_depth,
            avg_order_to_trade_ratio=otr,
            realized_fill_rate_pct=fill_rate,
            adverse_selection_bps=avg_adv_sel,
        )

    def compare_regimes(self, baseline: TickMetrics, test: TickMetrics) -> RegimeComparisonResult:
        """
        Compares baseline tick regime vs test tick regime metrics to quantify market impact.
        """
        q_spread_change = ((test.avg_quoted_spread - baseline.avg_quoted_spread) / baseline.avg_quoted_spread) * 100.0
        eff_spread_change = ((test.avg_effective_spread - baseline.avg_effective_spread) / baseline.avg_effective_spread) * 100.0
        depth_change = ((test.avg_top_depth_shares - baseline.avg_top_depth_shares) / baseline.avg_top_depth_shares) * 100.0
        fill_rate_change = test.realized_fill_rate_pct - baseline.realized_fill_rate_pct
        adv_sel_change = test.adverse_selection_bps - baseline.adverse_selection_bps

        findings = []
        if q_spread_change > 20.0:
            findings.append(f"Quoted spread widened by {q_spread_change:.1f}%. Execution cost for market takers increased.")
        elif q_spread_change < -20.0:
            findings.append(f"Quoted spread compressed by {abs(q_spread_change):.1f}%. Market taker execution costs reduced.")

        if depth_change > 30.0:
            findings.append(f"Inside book depth expanded by {depth_change:.1f}%. Longer queue waiting times for passive limit orders.")
        elif depth_change < -30.0:
            findings.append(f"Inside book depth thinned by {abs(depth_change):.1f}%. Increased price volatility and order flickering.")

        if adv_sel_change > 2.0:
            findings.append(f"Adverse selection increased by {adv_sel_change:.2f} bps for passive limit orders.")

        logger.info(f"Regime Comparison completed for {baseline.symbol}: Spread Delta={q_spread_change:.1f}%, Depth Delta={depth_change:.1f}%")

        return RegimeComparisonResult(
            symbol=baseline.symbol,
            baseline_regime=baseline.regime,
            test_regime=test.regime,
            quoted_spread_change_pct=q_spread_change,
            effective_spread_change_pct=eff_spread_change,
            top_depth_change_pct=depth_change,
            fill_rate_change_pct=fill_rate_change,
            adverse_selection_change_bps=adv_sel_change,
            key_findings=findings,
        )

    def recommend_strategy_tuning(
        self, algo_type: AlgoStrategyType, comparison: RegimeComparisonResult
    ) -> List[str]:
        """
        Generates quantitative recommendations for algorithm execution parameters based on regime changes.
        """
        recommendations = []

        if algo_type == AlgoStrategyType.PASSIVE_MARKET_MAKING:
            if comparison.quoted_spread_change_pct > 0 and comparison.top_depth_change_pct > 0:
                recommendations.append("Widened Tick Regime: Increase passive order size; join bid/ask early to secure queue priority.")
                recommendations.append("Use Pegged (Primary/Market) order types with offset to avoid queue subordination.")
            if comparison.adverse_selection_change_bps > 1.5:
                recommendations.append("High Adverse Selection: Tighten inventory risk limits and lower cancel latency.")

        elif algo_type == AlgoStrategyType.TWAP_VWAP_SLICING:
            if comparison.quoted_spread_change_pct > 25.0:
                recommendations.append("Widened Spread: Shift slicing mix toward passive limit orders to capture spread rebate.")
                recommendations.append("Enforce strict price caps to avoid crossing wider $0.05 spreads.")
            elif comparison.quoted_spread_change_pct < -25.0:
                recommendations.append("Compressed Spread: Cross the spread aggressively using market/ioc orders due to lower spread penalty.")

        elif algo_type == AlgoStrategyType.MOMENTUM_TAKER:
            if comparison.quoted_spread_change_pct > 0:
                recommendations.append("Higher Take Costs: Require higher signal conviction threshold before initiating market orders.")

        return recommendations

