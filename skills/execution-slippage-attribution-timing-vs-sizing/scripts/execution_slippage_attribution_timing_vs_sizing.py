import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class TradeExecutionSummary:
    trade_id: str
    symbol: str
    side: str                           # 'BUY' or 'SELL'
    order_qty: int
    decision_price: float               # Price when PM made trading decision
    arrival_price: float                # Price when order reached broker/exchange
    average_exec_price: float           # Weighted average fill price achieved
    decision_time_iso: str
    arrival_time_iso: str
    completion_time_iso: str

@dataclass
class SlippageAttributionAuditReport:
    trade_id: str
    symbol: str
    side: str
    decision_price: float
    arrival_price: float
    average_exec_price: float
    total_is_slippage_bps: float        # Total Implementation Shortfall
    timing_delay_slippage_bps: float    # Decision to Arrival
    sizing_impact_slippage_bps: float   # Arrival to Completion
    timing_contribution_pct: float      # % of total cost
    sizing_contribution_pct: float      # % of total cost
    primary_slippage_driver: str       # 'TIMING_DRIVEN_SLIPPAGE', 'SIZING_DRIVEN_SLIPPAGE', 'ZERO_SLIPPAGE'
    strategy_action_recommendation: str # 'ACCELERATE_ORDER_DISPATCH', 'REDUCE_PARTICIPATION_RATE_CEILING', 'OPTIMAL'
    audit_notes: str

class ExecutionSlippageAttributionEngine:
    """
    Quantitative post-trade TCA engine for decomposing Implementation Shortfall (IS) into timing/delay slippage vs sizing/market impact slippage,
    identifying primary slippage drivers, and recommending execution strategy adjustments.
    """
    def __init__(self):
        pass

    def attribute_execution_slippage(self, trade: TradeExecutionSummary) -> SlippageAttributionAuditReport:
        """
        Decomposes total Implementation Shortfall into timing vs sizing components and recommends strategy tuning.
        """
        if trade.decision_price <= 0:
            raise ValueError("Decision price must be > 0.")

        side_sign = 1.0 if trade.side.upper() == "BUY" else -1.0

        # 1. Total Implementation Shortfall (IS)
        total_is_bps = round(side_sign * ((trade.average_exec_price - trade.decision_price) / trade.decision_price) * 10000.0, 2)

        # 2. Timing / Delay Slippage (Decision -> Arrival)
        timing_is_bps = round(side_sign * ((trade.arrival_price - trade.decision_price) / trade.decision_price) * 10000.0, 2)

        # 3. Sizing / Market Impact Slippage (Arrival -> Completion)
        sizing_is_bps = round(side_sign * ((trade.average_exec_price - trade.arrival_price) / trade.decision_price) * 10000.0, 2)

        # Mathematical verification: Total IS == Timing IS + Sizing IS
        total_calc = round(timing_is_bps + sizing_is_bps, 2)

        abs_total = abs(total_is_bps)
        if abs_total > 1e-4:
            timing_pct = round((timing_is_bps / abs_total) * 100.0, 1)
            sizing_pct = round((sizing_is_bps / abs_total) * 100.0, 1)
        else:
            timing_pct = 0.0
            sizing_pct = 0.0

        # Determine Primary Slippage Driver & Strategy Recommendation
        if abs(timing_is_bps) > abs(sizing_is_bps) and abs(timing_is_bps) > 1.0:
            driver = "TIMING_DRIVEN_SLIPPAGE"
            rec = "ACCELERATE_ORDER_DISPATCH"
            notes = (
                f"SLIPPAGE ATTRIBUTION [{trade.trade_id} - {trade.symbol}]: Total IS = {total_is_bps:+.2f}bps. "
                f"Driven primarily by TIMING DELAY ({timing_is_bps:+.2f}bps / {timing_pct:.1f}%). Recommend ACCELERATE_ORDER_DISPATCH to cut latency."
            )
            logger.warning(notes)
        elif abs(sizing_is_bps) > abs(timing_is_bps) and abs(sizing_is_bps) > 1.0:
            driver = "SIZING_DRIVEN_SLIPPAGE"
            rec = "REDUCE_PARTICIPATION_RATE_CEILING"
            notes = (
                f"SLIPPAGE ATTRIBUTION [{trade.trade_id} - {trade.symbol}]: Total IS = {total_is_bps:+.2f}bps. "
                f"Driven primarily by SIZING MARKET IMPACT ({sizing_is_bps:+.2f}bps / {sizing_pct:.1f}%). Recommend REDUCE_PARTICIPATION_RATE_CEILING to lower market footprint."
            )
            logger.warning(notes)
        else:
            driver = "ZERO_SLIPPAGE"
            rec = "OPTIMAL"
            notes = f"SLIPPAGE ATTRIBUTION [{trade.trade_id} - {trade.symbol}]: Minimal slippage (Total IS = {total_is_bps:+.2f}bps)."
            logger.info(notes)

        return SlippageAttributionAuditReport(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            side=trade.side.upper(),
            decision_price=trade.decision_price,
            arrival_price=trade.arrival_price,
            average_exec_price=trade.average_exec_price,
            total_is_slippage_bps=total_calc,
            timing_delay_slippage_bps=timing_is_bps,
            sizing_impact_slippage_bps=sizing_is_bps,
            timing_contribution_pct=timing_pct,
            sizing_contribution_pct=sizing_pct,
            primary_slippage_driver=driver,
            strategy_action_recommendation=rec,
            audit_notes=notes
        )
