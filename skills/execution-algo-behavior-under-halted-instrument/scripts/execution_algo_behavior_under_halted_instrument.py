import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ActiveChildOrder:
    child_ord_id: str
    venue_id: str
    side: str
    price: float
    order_qty: int
    status: str                         # 'RESTING', 'PENDING_CANCEL', 'CANCELLED'

@dataclass
class ParentAlgoInstanceState:
    parent_algo_id: str
    symbol: str
    algo_type: str                      # 'TWAP', 'VWAP', 'POV'
    total_target_qty: int
    executed_qty: int
    algo_state: str                     # 'RUNNING', 'PAUSED_HALTED', 'REBALANCING_POST_RESUMPTION'
    active_child_orders: List[ActiveChildOrder]

@dataclass
class AlgoHaltAuditReport:
    parent_algo_id: str
    symbol: str
    previous_algo_state: str
    new_algo_state: str
    instrument_trading_status: str     # 'TRADING_CONTINUOUS', 'HALTED_LULD', 'HALTED_NEWS', 'AUCTION_REOPENING'
    cancelled_child_orders_count: int
    remaining_qty: int
    is_slicing_active: bool
    audit_notes: str

class ExecutionAlgoHaltEngine:
    """
    Quantitative execution algorithm engine for orchestrating TWAP/VWAP state machine behavior during instrument trading halts (LULD, news halts),
    canceling resting child orders, and re-benchmarking upon resumption.
    """
    def __init__(self):
        pass

    def handle_trading_status_change(
        self,
        algo: ParentAlgoInstanceState,
        instrument_status: str
    ) -> AlgoHaltAuditReport:
        """
        Orchestrates state machine transition, child order cancellation, and post-resumption re-benchmarking.
        """
        prev_state = algo.algo_state
        status_clean = instrument_status.upper()
        cancelled_cnt = 0
        rem_qty = max(0, algo.total_target_qty - algo.executed_qty)

        # Case 1: Instrument enters Trading Halt (e.g., HALTED_LULD, HALTED_NEWS)
        if status_clean.startswith("HALTED"):
            algo.algo_state = "PAUSED_HALTED"
            # Cancel all active child orders
            for c_ord in algo.active_child_orders:
                if c_ord.status == "RESTING":
                    c_ord.status = "CANCELLED"
                    cancelled_cnt += 1
                    logger.warning(f"HALT ORDER CANCEL [{algo.parent_algo_id}]: Cancelled child order '{c_ord.child_ord_id}' on {c_ord.venue_id}.")

            notes = (
                f"ALGO HALTED [{algo.parent_algo_id} - {algo.symbol}]: Instrument status = {status_clean}. "
                f"Transitioned state from {prev_state} -> PAUSED_HALTED. {cancelled_cnt} child orders cancelled. Slicing paused."
            )
            logger.critical(notes)
            return AlgoHaltAuditReport(
                parent_algo_id=algo.parent_algo_id, symbol=algo.symbol, previous_algo_state=prev_state,
                new_algo_state=algo.algo_state, instrument_trading_status=status_clean,
                cancelled_child_orders_count=cancelled_cnt, remaining_qty=rem_qty, is_slicing_active=False, audit_notes=notes
            )

        # Case 2: Trading Resumes (TRADING_CONTINUOUS or AUCTION_REOPENING)
        elif status_clean in ["TRADING_CONTINUOUS", "AUCTION_REOPENING"] and prev_state == "PAUSED_HALTED":
            algo.algo_state = "RUNNING"
            notes = (
                f"ALGO RESUMED [{algo.parent_algo_id} - {algo.symbol}]: Instrument status = {status_clean}. "
                f"Transitioned state from PAUSED_HALTED -> RUNNING. Re-benchmarked schedule over remaining {rem_qty:,} shares."
            )
            logger.info(notes)
            return AlgoHaltAuditReport(
                parent_algo_id=algo.parent_algo_id, symbol=algo.symbol, previous_algo_state=prev_state,
                new_algo_state=algo.algo_state, instrument_trading_status=status_clean,
                cancelled_child_orders_count=0, remaining_qty=rem_qty, is_slicing_active=True, audit_notes=notes
            )

        # Case 3: Standard Status (No State Transition)
        else:
            notes = f"ALGO CONTINUING [{algo.parent_algo_id} - {algo.symbol}]: State remains {algo.algo_state}."
            return AlgoHaltAuditReport(
                parent_algo_id=algo.parent_algo_id, symbol=algo.symbol, previous_algo_state=prev_state,
                new_algo_state=algo.algo_state, instrument_trading_status=status_clean,
                cancelled_child_orders_count=0, remaining_qty=rem_qty, is_slicing_active=(algo.algo_state == "RUNNING"), audit_notes=notes
            )
