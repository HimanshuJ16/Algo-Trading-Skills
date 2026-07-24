"""
market-data-snapshot-plus-delta-reconciliation: Production-grade L2 order book snapshot initializer,
WebSocket delta update buffer, sequence alignment engine, and sequence gap detector.
"""
from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class OrderBookError(RuntimeError):
    """Raised when sequence gaps or order book state corruption occurs."""
    pass


class BookState(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    SYNCHRONIZED = "SYNCHRONIZED"
    CORRUPT = "CORRUPT"


@dataclass
class DeltaUpdate:
    first_update_id: int
    final_update_id: int
    bids: List[Tuple[float, float]]  # [(price, qty)]
    asks: List[Tuple[float, float]]  # [(price, qty)]


class OrderBookReconciler:
    """
    Maintains a local Level 2 order book by buffering WebSockets deltas, aligning
    initial REST snapshots by sequence ID, and enforcing strict sequence continuity.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.last_sequence_id: int = -1
        self.state: BookState = BookState.UNINITIALIZED
        self.delta_buffer: List[DeltaUpdate] = []

    def buffer_delta(self, delta: DeltaUpdate):
        """Buffers WebSocket deltas prior to snapshot application."""
        if self.state == BookState.UNINITIALIZED:
            self.delta_buffer.append(delta)
        elif self.state == BookState.SYNCHRONIZED:
            self.process_delta(delta)

    def apply_snapshot(self, last_update_id: int, snapshot_bids: List[Tuple[float, float]], snapshot_asks: List[Tuple[float, float]]):
        """Applies initial REST snapshot and reconciles buffered deltas."""
        self.bids.clear()
        self.asks.clear()

        for price, qty in snapshot_bids:
            if qty > 0:
                self.bids[price] = qty
        for price, qty in snapshot_asks:
            if qty > 0:
                self.asks[price] = qty

        self.last_sequence_id = last_update_id
        self.state = BookState.SYNCHRONIZED
        logger.info(f"Snapshot applied for '{self.symbol}' with sequence {last_update_id}. Reconciling {len(self.delta_buffer)} buffered deltas...")

        # Process buffered deltas
        stale_count = 0
        applied_count = 0
        for delta in self.delta_buffer:
            if delta.final_update_id <= last_update_id:
                stale_count += 1
                continue
            self._apply_delta_payload(delta)
            applied_count += 1

        self.delta_buffer.clear()
        logger.info(f"Snapshot reconciliation complete for '{self.symbol}': {applied_count} applied, {stale_count} stale discarded.")

    def process_delta(self, delta: DeltaUpdate):
        """Processes real-time delta and validates sequence continuity."""
        if self.state != BookState.SYNCHRONIZED:
            self.buffer_delta(delta)
            return

        # Check sequence continuity: delta.first_update_id must be <= last_sequence_id + 1
        if delta.first_update_id > self.last_sequence_id + 1:
            self.state = BookState.CORRUPT
            msg = (
                f"SEQUENCE GAP DETECTED in order book for '{self.symbol}'! "
                f"Expected <= {self.last_sequence_id + 1}, got {delta.first_update_id}. Re-snapshot required."
            )
            logger.critical(msg)
            raise OrderBookError(msg)

        if delta.final_update_id <= self.last_sequence_id:
            logger.debug(f"Stale delta skipped: final_update_id {delta.final_update_id} <= last_sequence_id {self.last_sequence_id}")
            return

        self._apply_delta_payload(delta)

    def _apply_delta_payload(self, delta: DeltaUpdate):
        """Applies bid/ask updates to internal book dictionaries."""
        for price, qty in delta.bids:
            if qty <= 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty

        for price, qty in delta.asks:
            if qty <= 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty

        self.last_sequence_id = delta.final_update_id

    def get_top_of_book(self) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        """Returns best bid (max price) and best ask (min price) as ((bid_price, bid_qty), (ask_price, ask_qty))."""
        if self.state != BookState.SYNCHRONIZED or not self.bids or not self.asks:
            return None, None

        best_bid_price = max(self.bids.keys())
        best_ask_price = min(self.asks.keys())

        return (best_bid_price, self.bids[best_bid_price]), (best_ask_price, self.asks[best_ask_price])
