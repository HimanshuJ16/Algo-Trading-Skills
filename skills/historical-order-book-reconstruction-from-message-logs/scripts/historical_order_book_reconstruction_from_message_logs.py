"""Level 3 (market-by-order) message-log replay into Level 2 aggregated depth.

Replays an ordered stream of per-order lifecycle events (ADD, CANCEL, DELETE,
EXECUTE, REPLACE) and maintains both the L3 order map and the L2 price-level
aggregation incrementally, so a snapshot after every message stays cheap.

Message semantics follow the Nasdaq TotalView-ITCH 5.0 interface specification
(v5.0, 03/06/2015), which is the reference model this module implements:

  * Sec. 4.4 "Modify Order Messages" -- modify messages carry a share count that
    is *deducted* from the order's remaining displayed shares, effects are
    cumulative, and "when the number of display shares for an order reaches
    zero, the order is dead and should be removed from the book".
  * Sec. 4.4.3 Order Cancel ('X') -- "sent whenever an order on the book is
    modified as a result of a partial cancellation"; carries Canceled Shares.
  * Sec. 4.4.4 Order Delete ('D') -- "All remaining shares are no longer
    accessible so the order must be removed from the book"; carries NO share
    count. CANCEL and DELETE are therefore distinct message types, not one.
  * Sec. 4.4.5 Order Replace ('U') -- carries an Original *and* a New Order
    Reference Number ("the NASDAQ system will use this new order reference
    number for all subsequent updates"), an absolute new Shares total, and a new
    Price. Side is NOT carried: "Since the side, stock symbol and attribution
    (if any) cannot be changed by an Order Replace event, these fields are not
    included in the message. Firms should retain the side, stock symbol and
    MPID from the original Add Order message."
  * Sec. 4.3 Add Order -- the Order Reference Number is "day-unique", so an ADD
    for an already-live order id is a log-integrity error, not an update.
  * Sec. 3 Data Types -- "a field flagged as Price (4) has an implied 4 decimal
    places". Prices are integers on the wire; this module keeps them as integer
    ticks internally so that price-level aggregation is exact.

The same ADD / partial-cancel / total-delete / execute distinction appears in
LOBSTER message files as event types 1 / 2 / 3 / 4, with prices given as
"dollar price times 10000".

This module reconstructs the *displayed* book only. Hidden-order executions,
cross/auction prints and trading halts (LOBSTER event types 5, 6 and 7; Nasdaq
Trade 'P' and Cross Trade 'Q' messages) do not decrement a resting displayed
order and must not be fed in as EXECUTE messages.
"""

from __future__ import annotations

import heapq
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# --- Side / message-type vocabulary -----------------------------------------
BUY = "BUY"
SELL = "SELL"
VALID_SIDES = frozenset({BUY, SELL})

MSG_ADD = "ADD"
MSG_CANCEL = "CANCEL"
MSG_DELETE = "DELETE"
MSG_EXECUTE = "EXECUTE"
MSG_REPLACE = "REPLACE"
VALID_MSG_TYPES = frozenset({MSG_ADD, MSG_CANCEL, MSG_DELETE, MSG_EXECUTE, MSG_REPLACE})

# Nasdaq ITCH "Price (4)" and LOBSTER both use 4 implied decimal places.
DEFAULT_PRICE_SCALE = 10_000

# --- Integrity violation kinds ----------------------------------------------
VIOLATION_UNKNOWN_ORDER = "UNKNOWN_ORDER"
VIOLATION_DUPLICATE_ORDER_ID = "DUPLICATE_ORDER_ID"
VIOLATION_OVER_CANCEL = "OVER_CANCEL"
VIOLATION_OVER_EXECUTE = "OVER_EXECUTE"
VIOLATION_TIMESTAMP_REGRESSION = "TIMESTAMP_REGRESSION"


class BookIntegrityError(ValueError):
    """Raised in strict mode when a message cannot be applied to a consistent book.

    Every occurrence means the reconstructed book has diverged from the real one
    -- almost always because messages were dropped, duplicated or reordered
    upstream. Treat it as a data-quality failure, not a recoverable condition.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"[{kind}] {message}")
        self.kind = kind


@dataclass
class IntegrityViolation:
    """One book-integrity anomaly detected during replay."""

    kind: str
    order_id: str
    timestamp_nanos: int
    detail: str


@dataclass
class L3OrderMessage:
    """A single market-by-order lifecycle event.

    Field meaning depends on ``msg_type``:

    ==========  =================================================================
    msg_type    interpretation
    ==========  =================================================================
    ADD         ``side``/``price``/``quantity`` describe a new resting order.
    CANCEL      ``quantity`` is the number of shares *removed* (partial cancel).
                ``side``/``price`` are ignored; the resting order supplies them.
    DELETE      whole order removed. ``side``/``price``/``quantity`` are ignored.
    EXECUTE     ``quantity`` is the number of shares *executed* (a decrement).
    REPLACE     ``quantity`` is the new **absolute** displayed total and
                ``price`` the new price. ``new_order_id`` is required. ``side``
                is inherited from the original order and ignored here.
    ==========  =================================================================

    ``timestamp_nanos`` is nanoseconds since midnight in ITCH; any consistent
    monotonic integer clock works, as only ordering is used.
    """

    order_id: str
    msg_type: str
    side: str = ""
    price: float = 0.0
    quantity: int = 0
    timestamp_nanos: int = 0
    new_order_id: Optional[str] = None

    def __post_init__(self) -> None:
        # Normalise only; semantic validation is centralised in the engine so
        # that the strict/permissive policy lives in exactly one place.
        self.order_id = str(self.order_id).strip()
        self.msg_type = str(self.msg_type).strip().upper()
        self.side = str(self.side).strip().upper()
        if self.new_order_id is not None:
            self.new_order_id = str(self.new_order_id).strip()

    # Convenience constructors -- they make the per-type field meaning explicit
    # at the call site, which positional construction does not.
    @classmethod
    def add(cls, order_id: str, side: str, price: float, quantity: int,
            timestamp_nanos: int) -> "L3OrderMessage":
        return cls(order_id, MSG_ADD, side, price, quantity, timestamp_nanos)

    @classmethod
    def cancel(cls, order_id: str, quantity: int, timestamp_nanos: int) -> "L3OrderMessage":
        """Partial cancellation: ``quantity`` shares are removed from the order."""
        return cls(order_id, MSG_CANCEL, quantity=quantity, timestamp_nanos=timestamp_nanos)

    @classmethod
    def delete(cls, order_id: str, timestamp_nanos: int) -> "L3OrderMessage":
        """Total deletion: all remaining shares are removed from the book."""
        return cls(order_id, MSG_DELETE, timestamp_nanos=timestamp_nanos)

    @classmethod
    def execute(cls, order_id: str, quantity: int, timestamp_nanos: int) -> "L3OrderMessage":
        return cls(order_id, MSG_EXECUTE, quantity=quantity, timestamp_nanos=timestamp_nanos)

    @classmethod
    def replace(cls, order_id: str, new_order_id: str, price: float, quantity: int,
                timestamp_nanos: int) -> "L3OrderMessage":
        """Cancel-replace: ``quantity`` is the new absolute displayed total."""
        return cls(order_id, MSG_REPLACE, price=price, quantity=quantity,
                   timestamp_nanos=timestamp_nanos, new_order_id=new_order_id)


@dataclass
class OrderDetail:
    """A live resting order in the L3 map."""

    order_id: str
    side: str
    price_ticks: int
    quantity: int
    price_scale: int = DEFAULT_PRICE_SCALE

    @property
    def price(self) -> float:
        return self.price_ticks / self.price_scale


@dataclass
class PriceLevelDepth:
    """Aggregated displayed depth at one price level."""

    price: float
    total_quantity: int
    order_count: int
    price_ticks: int = 0


@dataclass
class OrderBookReconstructionReport:
    """Point-in-time L2 snapshot plus the integrity state of the replay."""

    symbol: str
    best_bid_price: Optional[float]
    best_bid_qty: int
    best_ask_price: Optional[float]
    best_ask_qty: int
    mid_price: Optional[float]
    spread: Optional[float]
    is_crossed_book: bool
    is_locked_book: bool
    l2_bids: List[PriceLevelDepth]
    l2_asks: List[PriceLevelDepth]
    depth_levels_requested: int
    total_active_l3_orders: int
    messages_processed: int
    last_timestamp_nanos: Optional[int]
    integrity_violation_count: int
    integrity_violations_by_kind: Dict[str, int]
    audit_notes: str


class HistoricalOrderBookReconstructEngine:
    """Replays L3 message logs into L2 aggregated depth and BBO state.

    The L3 order map and the L2 price-level aggregation are updated together on
    every message, so ``get_l2_reconstructed_snapshot`` costs O(L log n) in the
    number of *distinct price levels* L rather than O(N) in the number of live
    orders N. That matters because the intended workload -- a snapshot after
    every message across a multi-million-message session -- is exactly the case
    where an O(N)-per-snapshot rebuild degrades to O(N*M).

    Prices are held as integer ticks (``round(price * price_scale)``) so that
    two orders quoted at the same tick always aggregate into one price level.
    Binary floats do not guarantee that: values reaching this engine from
    different parse paths can differ in their last bits and silently split one
    level in two, understating displayed depth at the BBO.

    Args:
        symbol: Instrument the replay covers. Used for audit output only; this
            engine holds a single symbol's book and does not filter by symbol.
        price_scale: Ticks per price unit; must be a positive power of ten.
            10_000 matches Nasdaq ITCH ``Price (4)`` and LOBSTER's
            "dollar price times 10000". Use 100 for a cent-quoted feed.
        strict: If True, raise :class:`BookIntegrityError` on the first
            integrity violation instead of recording it and continuing.
        max_retained_violations: Cap on the number of violation *records* kept
            in memory. Counts by kind are always exact and uncapped.
        max_price: Optional upper bound, in currency units, above which a price
            is rejected as a unit error. Off by default. Sub-tick prices are
            always rejected, which catches a price divided by the scale twice;
            this catches the symmetric mistake of feeding raw wire integers
            straight through, which otherwise reads as a plausible book
            10,000x too high. Nasdaq documents the maximum ``Price (4)`` value
            as 200_000.0000 -- a reasonable setting for ITCH equities, but it
            is venue-specific, so nothing is imposed by default.
    """

    def __init__(self, symbol: str = "AAPL", price_scale: int = DEFAULT_PRICE_SCALE,
                 strict: bool = False, max_retained_violations: int = 1_000,
                 max_price: Optional[float] = None) -> None:
        if isinstance(price_scale, bool) or not isinstance(price_scale, int) or price_scale < 1:
            raise ValueError(f"price_scale must be a positive integer, got {price_scale!r}.")
        exponent = round(math.log10(price_scale))
        if 10 ** exponent != price_scale:
            raise ValueError(
                f"price_scale must be a power of ten (e.g. 10000 for ITCH Price(4)), "
                f"got {price_scale!r}.")
        if max_retained_violations < 0:
            raise ValueError("max_retained_violations must be >= 0.")
        if max_price is not None and (not math.isfinite(max_price) or max_price <= 0):
            raise ValueError(f"max_price must be a positive finite number, got {max_price!r}.")

        self.symbol = symbol
        self.price_scale = price_scale
        self.strict = strict
        self.max_retained_violations = max_retained_violations
        self.max_price = max_price
        self._price_decimals = exponent

        self.active_orders: Dict[str, OrderDetail] = {}   # order_id -> OrderDetail
        # price_ticks -> [total_quantity, order_count], maintained incrementally.
        self._bid_levels: Dict[int, List[int]] = {}
        self._ask_levels: Dict[int, List[int]] = {}

        self.messages_processed: int = 0
        self.last_timestamp_nanos: Optional[int] = None
        self.violations: List[IntegrityViolation] = []
        self.violations_by_kind: Dict[str, int] = {}

    # ------------------------------------------------------------------ utils
    def _to_ticks(self, price: float, context: str) -> int:
        """Convert a price in currency units to exact integer ticks."""
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise TypeError(f"{context}: price must be numeric, got {type(price).__name__}.")
        if not math.isfinite(price):
            raise ValueError(f"{context}: price must be finite, got {price!r}.")
        if price <= 0:
            raise ValueError(f"{context}: price must be > 0, got {price!r}.")
        if self.max_price is not None and price > self.max_price:
            raise ValueError(
                f"{context}: price {price!r} exceeds max_price {self.max_price!r}; "
                f"check whether raw wire integers are being passed without dividing "
                f"by price_scale={self.price_scale}.")
        scaled = price * self.price_scale
        ticks = round(scaled)
        # A price that is not representable at this scale means the caller has
        # the wrong price_scale or is passing raw wire integers as prices.
        if abs(scaled - ticks) > 1e-6:
            raise ValueError(
                f"{context}: price {price!r} is not representable at price_scale="
                f"{self.price_scale} (sub-tick residual {scaled - ticks:.3e}).")
        return int(ticks)

    def _record_violation(self, kind: str, order_id: str, timestamp_nanos: int,
                          detail: str) -> None:
        self.violations_by_kind[kind] = self.violations_by_kind.get(kind, 0) + 1
        if self.strict:
            raise BookIntegrityError(kind, f"order_id={order_id!r} ts={timestamp_nanos}: {detail}")
        if len(self.violations) < self.max_retained_violations:
            self.violations.append(IntegrityViolation(kind, order_id, timestamp_nanos, detail))
        logger.warning("Book integrity violation [%s] symbol=%s order_id=%s ts=%s: %s",
                       kind, self.symbol, order_id, timestamp_nanos, detail)

    @property
    def integrity_violation_count(self) -> int:
        """Total violations seen, including those beyond the retention cap."""
        return sum(self.violations_by_kind.values())

    def _levels_for(self, side: str) -> Dict[int, List[int]]:
        return self._bid_levels if side == BUY else self._ask_levels

    def _level_insert(self, side: str, price_ticks: int, quantity: int) -> None:
        levels = self._levels_for(side)
        level = levels.get(price_ticks)
        if level is None:
            levels[price_ticks] = [quantity, 1]
        else:
            level[0] += quantity
            level[1] += 1

    def _level_remove_order(self, side: str, price_ticks: int, quantity: int) -> None:
        levels = self._levels_for(side)
        level = levels[price_ticks]
        level[0] -= quantity
        level[1] -= 1
        if level[1] <= 0:
            del levels[price_ticks]

    def _level_reduce_qty(self, side: str, price_ticks: int, delta: int) -> None:
        """Reduce displayed size at a level without removing the order itself."""
        self._levels_for(side)[price_ticks][0] -= delta

    def _insert_order(self, order: OrderDetail) -> None:
        self.active_orders[order.order_id] = order
        self._level_insert(order.side, order.price_ticks, order.quantity)

    def _remove_order(self, order: OrderDetail) -> None:
        del self.active_orders[order.order_id]
        self._level_remove_order(order.side, order.price_ticks, order.quantity)

    # ------------------------------------------------------------- validation
    @staticmethod
    def _require_positive_int(value: object, context: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{context}: quantity must be an int, got {type(value).__name__}.")
        if value <= 0:
            raise ValueError(f"{context}: quantity must be > 0, got {value}.")
        return value

    def _validate_common(self, msg: L3OrderMessage) -> None:
        if not msg.order_id:
            raise ValueError("L3OrderMessage.order_id must be a non-empty string.")
        if msg.msg_type not in VALID_MSG_TYPES:
            raise ValueError(
                f"Unknown message type {msg.msg_type!r}; expected one of "
                f"{sorted(VALID_MSG_TYPES)}.")
        if isinstance(msg.timestamp_nanos, bool) or not isinstance(msg.timestamp_nanos, int):
            raise TypeError(
                f"timestamp_nanos must be an int, got {type(msg.timestamp_nanos).__name__}.")
        if msg.timestamp_nanos < 0:
            raise ValueError(f"timestamp_nanos must be >= 0, got {msg.timestamp_nanos}.")

    def _check_timestamp_order(self, msg: L3OrderMessage) -> None:
        """Flag a backwards clock. Equal timestamps are legal (ITCH allows ties)."""
        if (self.last_timestamp_nanos is not None
                and msg.timestamp_nanos < self.last_timestamp_nanos):
            self._record_violation(
                VIOLATION_TIMESTAMP_REGRESSION, msg.order_id, msg.timestamp_nanos,
                f"message timestamp {msg.timestamp_nanos} precedes previous "
                f"{self.last_timestamp_nanos}; L3 replay is order-dependent so the "
                f"reconstructed book is no longer trustworthy from this point.")
        if (self.last_timestamp_nanos is None
                or msg.timestamp_nanos > self.last_timestamp_nanos):
            self.last_timestamp_nanos = msg.timestamp_nanos

    # ---------------------------------------------------------------- replay
    def process_l3_message(self, msg: L3OrderMessage) -> None:
        """Apply one L3 event to the order map and the L2 level aggregation.

        Malformed messages (bad type, non-positive quantity, non-finite price)
        raise. Well-formed messages that cannot be applied to a consistent book
        -- a cancel for an order never added, an over-execution, a duplicate
        order id -- are *integrity violations*: recorded and counted here, or
        raised as :class:`BookIntegrityError` when the engine is strict.
        """
        self._validate_common(msg)
        self._check_timestamp_order(msg)
        self.messages_processed += 1

        if msg.msg_type == MSG_ADD:
            self._apply_add(msg)
        elif msg.msg_type == MSG_CANCEL:
            self._apply_decrement(msg, "CANCEL", VIOLATION_OVER_CANCEL)
        elif msg.msg_type == MSG_EXECUTE:
            self._apply_decrement(msg, "EXECUTE", VIOLATION_OVER_EXECUTE)
        elif msg.msg_type == MSG_DELETE:
            self._apply_delete(msg)
        else:  # MSG_REPLACE -- _validate_common has already rejected anything else.
            self._apply_replace(msg)

    def _apply_add(self, msg: L3OrderMessage) -> None:
        if msg.side not in VALID_SIDES:
            # Silently bucketing an unrecognised side as an ask would fabricate
            # depth on the wrong side of the book and manufacture crossed books.
            raise ValueError(
                f"ADD {msg.order_id!r}: side must be one of {sorted(VALID_SIDES)}, "
                f"got {msg.side!r}.")
        quantity = self._require_positive_int(msg.quantity, f"ADD {msg.order_id!r}")
        price_ticks = self._to_ticks(msg.price, f"ADD {msg.order_id!r}")

        existing = self.active_orders.get(msg.order_id)
        if existing is not None:
            # ITCH order reference numbers are day-unique, so this means an
            # earlier DELETE/EXECUTE was dropped. Superseding the stale order is
            # the recovery that matches the likelier cause; it is still a
            # divergence and is reported as one.
            self._record_violation(
                VIOLATION_DUPLICATE_ORDER_ID, msg.order_id, msg.timestamp_nanos,
                f"ADD for an already-live order id (existing {existing.quantity} @ "
                f"{existing.price}); superseding the stale order.")
            self._remove_order(existing)

        self._insert_order(OrderDetail(
            order_id=msg.order_id, side=msg.side, price_ticks=price_ticks,
            quantity=quantity, price_scale=self.price_scale))

    def _apply_decrement(self, msg: L3OrderMessage, label: str, over_kind: str) -> None:
        """Shared CANCEL/EXECUTE path: both deduct shares from a resting order."""
        quantity = self._require_positive_int(msg.quantity, f"{label} {msg.order_id!r}")
        existing = self.active_orders.get(msg.order_id)
        if existing is None:
            self._record_violation(
                VIOLATION_UNKNOWN_ORDER, msg.order_id, msg.timestamp_nanos,
                f"{label} for an order that is not on the book; the corresponding "
                f"ADD was never seen (dropped or out-of-range message).")
            return

        if quantity > existing.quantity:
            self._record_violation(
                over_kind, msg.order_id, msg.timestamp_nanos,
                f"{label} of {quantity} shares exceeds the {existing.quantity} "
                f"remaining; removing the order, but displayed size has already "
                f"diverged from the real book.")
            self._remove_order(existing)
            return

        if quantity == existing.quantity:
            # "When the number of display shares for an order reaches zero, the
            # order is dead and should be removed from the book." (ITCH 5.0 4.4)
            self._remove_order(existing)
            return

        existing.quantity -= quantity
        self._level_reduce_qty(existing.side, existing.price_ticks, quantity)

    def _apply_delete(self, msg: L3OrderMessage) -> None:
        existing = self.active_orders.get(msg.order_id)
        if existing is None:
            self._record_violation(
                VIOLATION_UNKNOWN_ORDER, msg.order_id, msg.timestamp_nanos,
                "DELETE for an order that is not on the book; the corresponding "
                "ADD was never seen (dropped or out-of-range message).")
            return
        self._remove_order(existing)

    def _apply_replace(self, msg: L3OrderMessage) -> None:
        new_order_id = msg.new_order_id if msg.new_order_id else msg.order_id
        quantity = self._require_positive_int(msg.quantity, f"REPLACE {msg.order_id!r}")
        price_ticks = self._to_ticks(msg.price, f"REPLACE {msg.order_id!r}")

        original = self.active_orders.get(msg.order_id)
        if original is None:
            # Never fabricate a resting order from a replace: the side is only
            # knowable from the original ADD, and inventing depth is worse than
            # a gap that is reported.
            self._record_violation(
                VIOLATION_UNKNOWN_ORDER, msg.order_id, msg.timestamp_nanos,
                "REPLACE of an order that is not on the book; the original ADD was "
                "never seen, and the replacement's side cannot be inferred, so no "
                "order is created.")
            return

        if new_order_id != msg.order_id and new_order_id in self.active_orders:
            self._record_violation(
                VIOLATION_DUPLICATE_ORDER_ID, new_order_id, msg.timestamp_nanos,
                "REPLACE target order id is already live; superseding it.")
            self._remove_order(self.active_orders[new_order_id])

        # Side is inherited from the original ADD -- an Order Replace cannot
        # change it and does not carry it (ITCH 5.0 sec. 4.4.5).
        side = original.side
        self._remove_order(original)
        self._insert_order(OrderDetail(
            order_id=new_order_id, side=side, price_ticks=price_ticks,
            quantity=quantity, price_scale=self.price_scale))

    # -------------------------------------------------------------- snapshot
    def _top_levels(self, side: str, top_n_levels: int) -> List[PriceLevelDepth]:
        levels = self._levels_for(side)
        picker = heapq.nlargest if side == BUY else heapq.nsmallest
        return [
            PriceLevelDepth(
                price=ticks / self.price_scale,
                total_quantity=levels[ticks][0],
                order_count=levels[ticks][1],
                price_ticks=ticks,
            )
            for ticks in picker(top_n_levels, levels)
        ]

    def get_l2_reconstructed_snapshot(
            self, top_n_levels: int = 5) -> OrderBookReconstructionReport:
        """Aggregate current L3 state into a top-N L2 depth snapshot and BBO metrics.

        Note: when the book is crossed, ``mid_price`` and ``spread`` are still
        populated (the spread is negative). Always gate downstream use on
        ``is_crossed_book`` / ``is_locked_book``.
        """
        if isinstance(top_n_levels, bool) or not isinstance(top_n_levels, int):
            raise TypeError(
                f"top_n_levels must be an int, got {type(top_n_levels).__name__}.")
        if top_n_levels < 1:
            raise ValueError(f"top_n_levels must be >= 1, got {top_n_levels}.")

        l2_bids = self._top_levels(BUY, top_n_levels)
        l2_asks = self._top_levels(SELL, top_n_levels)

        best_bid_ticks = l2_bids[0].price_ticks if l2_bids else None
        best_ask_ticks = l2_asks[0].price_ticks if l2_asks else None
        best_bid = l2_bids[0].price if l2_bids else None
        best_bid_qty = l2_bids[0].total_quantity if l2_bids else 0
        best_ask = l2_asks[0].price if l2_asks else None
        best_ask_qty = l2_asks[0].total_quantity if l2_asks else 0

        mid_price: Optional[float] = None
        spread: Optional[float] = None
        is_crossed = False
        is_locked = False

        if best_bid_ticks is not None and best_ask_ticks is not None:
            # Compared in integer ticks, so the crossed/locked decision is exact.
            is_crossed = best_bid_ticks > best_ask_ticks
            is_locked = best_bid_ticks == best_ask_ticks
            mid_price = round((best_bid_ticks + best_ask_ticks) / (2 * self.price_scale),
                              self._price_decimals + 1)
            spread = round((best_ask_ticks - best_bid_ticks) / self.price_scale,
                           self._price_decimals)

        violation_count = self.integrity_violation_count
        notes = (
            f"L2 BOOK RECONSTRUCTED [{self.symbol}]: "
            f"BBO = {best_bid} ({best_bid_qty:,}) / {best_ask} ({best_ask_qty:,}), "
            f"Spread = {spread}, Mid = {mid_price}. "
            f"Active L3 Orders = {len(self.active_orders)}, "
            f"Messages = {self.messages_processed:,}, "
            f"Integrity Violations = {violation_count:,}."
        )
        if is_crossed:
            notes += " ALERT: CROSSED ORDER BOOK (bid > ask)."
        elif is_locked:
            notes += " ALERT: LOCKED ORDER BOOK (bid == ask)."
        if violation_count:
            notes += (" ALERT: book diverged from the message log; see "
                      "`violations_by_kind`.")

        if is_crossed or is_locked or violation_count:
            logger.warning(notes)
        else:
            # Snapshots are taken per message in tick-by-tick replay, so the
            # clean-book path must not emit a log line per message.
            logger.debug(notes)

        return OrderBookReconstructionReport(
            symbol=self.symbol,
            best_bid_price=best_bid,
            best_bid_qty=best_bid_qty,
            best_ask_price=best_ask,
            best_ask_qty=best_ask_qty,
            mid_price=mid_price,
            spread=spread,
            is_crossed_book=is_crossed,
            is_locked_book=is_locked,
            l2_bids=l2_bids,
            l2_asks=l2_asks,
            depth_levels_requested=top_n_levels,
            total_active_l3_orders=len(self.active_orders),
            messages_processed=self.messages_processed,
            last_timestamp_nanos=self.last_timestamp_nanos,
            integrity_violation_count=violation_count,
            integrity_violations_by_kind=dict(self.violations_by_kind),
            audit_notes=notes,
        )
