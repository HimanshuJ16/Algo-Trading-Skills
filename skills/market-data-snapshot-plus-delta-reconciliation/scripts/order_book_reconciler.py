"""
market-data-snapshot-plus-delta-reconciliation: Production-grade L2 order book snapshot initializer,
WebSocket delta update buffer, sequence alignment engine, and sequence gap detector.

Implements the venue-documented snapshot/delta reconciliation procedure (Binance spot
"How to manage a local order book correctly"):

  1. Buffer WebSocket delta events; note the first event's ``first_update_id`` (``U``).
  2. Fetch a REST depth snapshot with ``last_update_id`` (``lastUpdateId``).
  3. If ``last_update_id`` is strictly less than the first buffered ``U``, the snapshot
     predates the delta stream -- discard it and fetch a fresher one.
  4. Discard buffered events where ``final_update_id`` (``u``) <= ``last_update_id``.
  5. The first surviving event must satisfy ``U <= last_update_id + 1 <= u``.
  6. Apply every remaining event in strict sequence (``U == previous u + 1``).

Scope / limitations:
  - Price levels are keyed by ``float``. Callers must parse each venue's decimal price
    strings consistently (always ``float(price_str)``), because two different textual
    renderings of the same level would otherwise become two distinct keys.
  - Non-positive prices are rejected as malformed. Instruments that legitimately quote
    at or below zero (calendar spreads, the April-2020 WTI settlement) are out of scope.
  - Binance USD-M futures uses a different first-event window (``U <= lastUpdateId AND
    u >= lastUpdateId``) plus a ``pu == previous u`` check; see references/standards.md.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MAX_BUFFER_SIZE = 1000

PriceLevel = Tuple[float, float]


class OrderBookError(RuntimeError):
    """Raised when sequence gaps or order book state corruption occurs."""
    pass


class BookState(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    SYNCHRONIZED = "SYNCHRONIZED"
    CORRUPT = "CORRUPT"


def _validate_levels(levels: Sequence[PriceLevel], side: str, context: str) -> None:
    """Rejects malformed price levels before they can poison the book.

    A NaN price is unremovable once inserted (a later ``pop`` with a different NaN
    object misses it) and silently corrupts ``max()``/``min()`` top-of-book selection,
    so non-finite values must never reach the book dictionaries.
    """
    for level in levels:
        try:
            price, qty = level
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context}: malformed {side} level {level!r}, expected (price, qty)") from exc
        if isinstance(price, bool) or isinstance(qty, bool):
            raise ValueError(f"{context}: boolean {side} level {level!r}")
        if not isinstance(price, (int, float)) or not isinstance(qty, (int, float)):
            raise ValueError(f"{context}: non-numeric {side} level {level!r}")
        if not math.isfinite(price) or not math.isfinite(qty):
            raise ValueError(f"{context}: non-finite {side} level {level!r}")
        if price <= 0:
            raise ValueError(f"{context}: non-positive {side} price {price!r}")
        if qty < 0:
            raise ValueError(f"{context}: negative {side} quantity {qty!r} (use 0 to delete a level)")


@dataclass
class DeltaUpdate:
    first_update_id: int
    final_update_id: int
    bids: List[PriceLevel]  # [(price, qty)]
    asks: List[PriceLevel]  # [(price, qty)]

    def __post_init__(self) -> None:
        if self.first_update_id < 0 or self.final_update_id < 0:
            raise ValueError(
                f"DeltaUpdate sequence IDs must be non-negative, got "
                f"({self.first_update_id}, {self.final_update_id})")
        if self.first_update_id > self.final_update_id:
            raise ValueError(
                f"DeltaUpdate first_update_id {self.first_update_id} exceeds "
                f"final_update_id {self.final_update_id}")
        _validate_levels(self.bids, "bid", "DeltaUpdate")
        _validate_levels(self.asks, "ask", "DeltaUpdate")


class OrderBookReconciler:
    """
    Maintains a local Level 2 order book by buffering WebSockets deltas, aligning
    initial REST snapshots by sequence ID, and enforcing strict sequence continuity.

    The book is only readable via :meth:`get_top_of_book` while the state is
    ``SYNCHRONIZED``. Any detected gap moves the book to ``CORRUPT``, clears the
    levels so no stale depth can be read, and requires a fresh snapshot.
    """

    def __init__(self, symbol: str, max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE):
        if max_buffer_size <= 0:
            raise ValueError(f"max_buffer_size must be positive, got {max_buffer_size}")
        self.symbol = symbol
        self.max_buffer_size = max_buffer_size
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.last_sequence_id: int = -1
        self.state: BookState = BookState.UNINITIALIZED
        self.delta_buffer: List[DeltaUpdate] = []

    def buffer_delta(self, delta: DeltaUpdate) -> None:
        """Buffers WebSocket deltas prior to snapshot application.

        While the book is ``SYNCHRONIZED`` the delta is applied immediately. While it is
        ``UNINITIALIZED`` or ``CORRUPT`` the delta is queued so that the next snapshot can
        be freshness-checked against it -- dropping deltas during re-sync would hide the
        hole between the old stream and the new snapshot.

        Raises:
            OrderBookError: the buffer is full, meaning no usable snapshot ever arrived.
                The caller must tear down and restart the subscription. Deltas are refused
                rather than silently dropped, because a dropped delta would break the
                sequence-continuity guarantee the next snapshot is checked against.
                Also propagated from :meth:`process_delta` when the book is ``SYNCHRONIZED``
                and the delta opens a sequence gap.
        """
        if self.state == BookState.SYNCHRONIZED:
            self.process_delta(delta)
            return

        if len(self.delta_buffer) >= self.max_buffer_size:
            self.state = BookState.CORRUPT
            msg = (
                f"Delta buffer overflow for '{self.symbol}' at {self.max_buffer_size} events "
                f"without a usable snapshot. Restart the subscription and re-snapshot."
            )
            logger.critical(msg)
            raise OrderBookError(msg)

        self.delta_buffer.append(delta)

    def apply_snapshot(self, last_update_id: int, snapshot_bids: List[PriceLevel], snapshot_asks: List[PriceLevel]) -> None:
        """Applies a REST snapshot and reconciles the buffered deltas against it.

        Raises:
            ValueError: ``last_update_id`` or a snapshot price level is malformed.
            OrderBookError: the snapshot is older than the buffered delta stream (fetch a
                fresher snapshot and call again -- the buffer is retained), or the buffer
                itself contains a sequence gap (restart the subscription -- the buffer is
                discarded). In both cases the state is left ``CORRUPT`` and the book empty.
        """
        if isinstance(last_update_id, bool) or not isinstance(last_update_id, int) or last_update_id < 0:
            raise ValueError(f"last_update_id must be a non-negative int, got {last_update_id!r}")
        _validate_levels(snapshot_bids, "bid", "snapshot")
        _validate_levels(snapshot_asks, "ask", "snapshot")

        self.bids.clear()
        self.asks.clear()

        for price, qty in snapshot_bids:
            if qty > 0:
                self.bids[price] = qty
        for price, qty in snapshot_asks:
            if qty > 0:
                self.asks[price] = qty

        self.last_sequence_id = last_update_id

        fresh = [d for d in self.delta_buffer if d.final_update_id > last_update_id]
        stale_count = len(self.delta_buffer) - len(fresh)
        logger.info(
            f"Snapshot applied for '{self.symbol}' with sequence {last_update_id}. "
            f"Reconciling {len(self.delta_buffer)} buffered deltas ({stale_count} stale)...")

        if fresh:
            first = fresh[0]
            # Venue rule: the first surviving event must straddle last_update_id + 1. If it
            # starts later, the snapshot predates the buffered delta stream and the hole
            # between them is unrecoverable -- a fresher snapshot is required.
            if not (first.first_update_id <= last_update_id + 1 <= first.final_update_id):
                self._mark_corrupt(
                    f"Snapshot for '{self.symbol}' (last_update_id {last_update_id}) predates the buffered "
                    f"delta stream (first surviving event [{first.first_update_id}, {first.final_update_id}]). "
                    f"Fetch a fresher snapshot and retry.",
                    clear_buffer=False,
                )

            for delta in fresh:
                if delta.first_update_id > self.last_sequence_id + 1:
                    self._mark_corrupt(
                        f"Sequence gap inside the buffered delta stream for '{self.symbol}': expected "
                        f"first_update_id <= {self.last_sequence_id + 1}, got {delta.first_update_id}. "
                        f"Restart the subscription and re-snapshot.",
                        clear_buffer=True,
                    )
                if delta.final_update_id <= self.last_sequence_id:
                    continue
                self._apply_delta_payload(delta)

        applied_count = len(fresh)
        self.delta_buffer.clear()
        self.state = BookState.SYNCHRONIZED
        logger.info(
            f"Snapshot reconciliation complete for '{self.symbol}': {applied_count} applied, "
            f"{stale_count} stale discarded, book sequence {self.last_sequence_id}.")

    def process_delta(self, delta: DeltaUpdate) -> None:
        """Processes a real-time delta and validates sequence continuity.

        Raises:
            OrderBookError: a sequence gap was detected. The book is cleared, the state is
                set to ``CORRUPT``, and the offending delta becomes the new buffer head so
                the next snapshot can be freshness-checked against it.
        """
        if self.state != BookState.SYNCHRONIZED:
            self.buffer_delta(delta)
            return

        # Continuity: the event must start no later than last_sequence_id + 1.
        if delta.first_update_id > self.last_sequence_id + 1:
            self._mark_corrupt(
                f"SEQUENCE GAP DETECTED in order book for '{self.symbol}'! "
                f"Expected <= {self.last_sequence_id + 1}, got {delta.first_update_id}. Re-snapshot required.",
                clear_buffer=True,
                requeue=delta,
            )

        if delta.final_update_id <= self.last_sequence_id:
            logger.debug(f"Stale delta skipped: final_update_id {delta.final_update_id} <= last_sequence_id {self.last_sequence_id}")
            return

        self._apply_delta_payload(delta)

    def _mark_corrupt(self, msg: str, clear_buffer: bool, requeue: Optional[DeltaUpdate] = None) -> None:
        """Empties the book, marks it CORRUPT and raises ``OrderBookError``. Never returns."""
        self.state = BookState.CORRUPT
        self.bids.clear()
        self.asks.clear()
        # No valid book version survives corruption -- in particular, a rejected snapshot
        # must not leave its own last_update_id behind for an operator to misread as live.
        self.last_sequence_id = -1
        if clear_buffer:
            self.delta_buffer.clear()
        if requeue is not None:
            self.delta_buffer.append(requeue)
        logger.critical(msg)
        raise OrderBookError(msg)

    def _apply_delta_payload(self, delta: DeltaUpdate) -> None:
        """Applies bid/ask updates to internal book dictionaries.

        Quantities are absolute level sizes, not increments. A quantity of exactly zero
        deletes the level; negative quantities are rejected upstream by ``_validate_levels``.
        """
        for price, qty in delta.bids:
            if qty == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty

        for price, qty in delta.asks:
            if qty == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty

        self.last_sequence_id = delta.final_update_id

    def is_crossed(self) -> bool:
        """True if best bid >= best ask, which proves the local book has desynchronized.

        Venues do not publish crossed books, so a crossed local book means a delta was
        missed or misapplied. Treat it as a re-snapshot trigger, not as a tradable quote.
        """
        if not self.bids or not self.asks:
            return False
        return max(self.bids.keys()) >= min(self.asks.keys())

    def get_top_of_book(self) -> Tuple[Optional[PriceLevel], Optional[PriceLevel]]:
        """Returns best bid (max price) and best ask (min price) as ((bid_price, bid_qty), (ask_price, ask_qty))."""
        if self.state != BookState.SYNCHRONIZED or not self.bids or not self.asks:
            return None, None

        best_bid_price = max(self.bids.keys())
        best_ask_price = min(self.asks.keys())

        if best_bid_price >= best_ask_price:
            logger.warning(
                f"CROSSED BOOK for '{self.symbol}': best bid {best_bid_price} >= best ask {best_ask_price} "
                f"at sequence {self.last_sequence_id}. Local book is desynchronized; re-snapshot required.")

        return (best_bid_price, self.bids[best_bid_price]), (best_ask_price, self.asks[best_ask_price])
