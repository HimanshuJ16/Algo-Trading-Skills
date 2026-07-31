import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    enabled: bool = True
    benchmark_target_is_bps: float = 10.0  # Target IS in bps

@dataclass
class ExecutedOrderRecord:
    order_id: str
    venue: str
    symbol: str
    side: str                            # 'BUY', 'SELL'
    parent_qty: float
    executed_qty: float
    avg_fill_price: float
    arrival_price: float
    market_vwap: float
    arrival_midquote: float
    arrival_quoted_spread: float

@dataclass
class SingleOrderMetrics:
    order_id: str
    venue: str
    symbol: str
    implementation_shortfall_bps: float
    vwap_slippage_bps: float
    effective_spread: float
    eqr_ratio: float
    fill_rate_pct: float
    score_points: float                  # 0 to 100

@dataclass
class ExecutionQualityScorecardReport:
    total_orders_audited: int
    avg_implementation_shortfall_bps: float
    avg_vwap_slippage_bps: float
    avg_eqr_ratio: float
    overall_fill_rate_pct: float
    composite_scorecard_rating: str     # 'A', 'B', 'C', 'D', 'F'
    order_metrics: List[SingleOrderMetrics]
    status: str                          # 'SCORECARD_AUDIT_PASSED', 'SCORECARD_AUDIT_FAILED'
    audit_notes: str

class Engine:
    """
    Legacy Engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled

class PostTradeExecutionQualityScorecard:
    """
    Post-trade execution quality scorecard engine evaluating Implementation Shortfall (IS),
    VWAP slippage, Effective vs Quoted Ratio (EQR), fill rates, and SEC Rule 605 metrics.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def evaluate_scorecard(
        self, orders: List[ExecutedOrderRecord]
    ) -> ExecutionQualityScorecardReport:
        """
        Calculates IS, VWAP slippage, Effective Spread, EQR, Fill Rate, and Composite Grade for executed orders.
        """
        if not self.config.enabled:
            return ExecutionQualityScorecardReport(
                total_orders_audited=len(orders),
                avg_implementation_shortfall_bps=0.0,
                avg_vwap_slippage_bps=0.0,
                avg_eqr_ratio=0.0,
                overall_fill_rate_pct=0.0,
                composite_scorecard_rating="N/A",
                order_metrics=[],
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled."
            )

        if not orders:
            return ExecutionQualityScorecardReport(
                total_orders_audited=0,
                avg_implementation_shortfall_bps=0.0,
                avg_vwap_slippage_bps=0.0,
                avg_eqr_ratio=0.0,
                overall_fill_rate_pct=0.0,
                composite_scorecard_rating="N/A",
                order_metrics=[],
                status="NO_ORDERS_AUDITED",
                audit_notes="No orders submitted for scorecard audit."
            )

        order_metrics_list: List[SingleOrderMetrics] = []
        total_is_bps = 0.0
        total_vwap_bps = 0.0
        total_eqr = 0.0
        total_fill_rate = 0.0
        total_score_points = 0.0

        for o in orders:
            side_sign = 1.0 if o.side.upper() == "BUY" else -1.0

            # 1. Implementation Shortfall (bps)
            # IS = side_sign * (avg_fill_price - arrival_price) / arrival_price * 10000
            is_bps = (side_sign * (o.avg_fill_price - o.arrival_price) / max(0.0001, o.arrival_price)) * 10000.0

            # 2. VWAP Slippage (bps)
            # VWAP_Slippage = side_sign * (avg_fill_price - market_vwap) / market_vwap * 10000
            vwap_bps = (side_sign * (o.avg_fill_price - o.market_vwap) / max(0.0001, o.market_vwap)) * 10000.0

            # 3. Effective Spread & EQR Ratio
            # Effective Spread = 2 * side_sign * (avg_fill_price - arrival_midquote)
            eff_spread = 2.0 * side_sign * (o.avg_fill_price - o.arrival_midquote)
            quoted_spread = max(0.0001, o.arrival_quoted_spread)
            eqr = eff_spread / quoted_spread

            # 4. Fill Rate %
            fill_rate = (o.executed_qty / max(1.0, o.parent_qty)) * 100.0

            # 5. Single Order Score Calculation (Base 100)
            score = 100.0 - (max(0.0, is_bps) * 2.0) - (max(0.0, eqr - 1.0) * 20.0) + (fill_rate - 100.0)
            score = max(0.0, min(100.0, score))

            total_is_bps += is_bps
            total_vwap_bps += vwap_bps
            total_eqr += eqr
            total_fill_rate += fill_rate
            total_score_points += score

            order_metrics_list.append(
                SingleOrderMetrics(
                    order_id=o.order_id,
                    venue=o.venue,
                    symbol=o.symbol,
                    implementation_shortfall_bps=round(is_bps, 2),
                    vwap_slippage_bps=round(vwap_bps, 2),
                    effective_spread=round(eff_spread, 4),
                    eqr_ratio=round(eqr, 4),
                    fill_rate_pct=round(fill_rate, 2),
                    score_points=round(score, 1)
                )
            )

        n = len(orders)
        avg_is = total_is_bps / n
        avg_vwap = total_vwap_bps / n
        avg_eqr = total_eqr / n
        avg_fill = total_fill_rate / n
        avg_score = total_score_points / n

        # Grade Mapping
        if avg_score >= 90.0:
            rating = "A"
        elif avg_score >= 80.0:
            rating = "B"
        elif avg_score >= 70.0:
            rating = "C"
        elif avg_score >= 60.0:
            rating = "D"
        else:
            rating = "F"

        status = "SCORECARD_AUDIT_PASSED" if avg_score >= 70.0 else "SCORECARD_AUDIT_FAILED"

        notes = (
            f"EXECUTION QUALITY SCORECARD AUDIT [{status} - RATING: {rating} ({avg_score:.1f}/100)]: "
            f"Orders = {n}, Avg IS = {avg_is:+.2f} bps, Avg VWAP Slippage = {avg_vwap:+.2f} bps, "
            f"Avg EQR Ratio = {avg_eqr:.2f}, Overall Fill Rate = {avg_fill:.1f}%."
        )

        logger.info(notes)

        return ExecutionQualityScorecardReport(
            total_orders_audited=n,
            avg_implementation_shortfall_bps=round(avg_is, 2),
            avg_vwap_slippage_bps=round(avg_vwap, 2),
            avg_eqr_ratio=round(avg_eqr, 4),
            overall_fill_rate_pct=round(avg_fill, 2),
            composite_scorecard_rating=rating,
            order_metrics=order_metrics_list,
            status=status,
            audit_notes=notes
        )
