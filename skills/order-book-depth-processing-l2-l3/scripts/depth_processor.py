"""Thread-safe Level 2 / Level 3 order book depth processor and microstructure
metrics engine.

Maintains an aggregated price-level book from either L2 price-level updates or
L3 order-by-order events, and derives top-of-book, weighted mid-price and depth
imbalance from it under a single mutex.

Two design decisions drive the whole module:

**Nothing enters the book unvalidated.** A non-finite price, a negative size or
an unrecognised side does not fail anywhere near the code that consumes it -- it
produces a book that looks healthy and disagrees with the venue. NaN is the
worst of these: ``float('nan') >= x`` is ``False``, so a single NaN price
silently disables the crossed-book guard and then propagates through every
metric as NaN. Every field is therefore range-checked at ingress.

**A message that cannot be applied is counted, never absorbed.** An execution
for an order that was never added, a duplicate order reference, an
over-execution and a crossed book are recorded in ``violations_by_kind``. A
dropped feed message that is quietly ignored leaves a book that is wrong for the
rest of the session with nothing in the output saying so.

Quantity semantics follow the venue contract these feeds share: an L2 update
carries the **absolute** new quantity for a price level, and a quantity of zero
removes the level (Binance spot, *How to manage a local order book correctly*:
"If the quantity is zero, remove the price level from the order book"). A
*negative* quantity is not a removal instruction -- it is feed corruption, and
is rejected.

The weighted mid-price is the canonical imbalance-weighted midpoint, in which
the bid price carries the **ask** volume::

    P_wmid = (V_ask * P_bid + V_bid * P_ask) / (V_bid + V_ask)

This is the weighted mid, not Stoikov's micro-price: the micro-price is a
martingale estimate derived from the book's dynamics, and the weighted mid is
one of the biased estimators it is defined against.

This is a correctness-first CPython reference implementation. ``compute_metrics``
sorts the full book on each call (O(n log n) in the number of resting price
levels); sustained full-depth rates need a sorted container and a compiled
language.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, Iterable, List, Mapping, Tuple

logger = logging.getLogger(__name__)

# Sides as they arrive on the feeds this skill covers: Nasdaq TotalView-ITCH
# uses 'B' / 'S', Coinbase Exchange level3 uses 'buy' / 'sell', and normalised
# internal pipelines commonly use 'BID' / 'ASK'. Anything outside these sets is
# rejected rather than defaulted -- defaulting an unrecognised side to one book
# silently places buy orders on the ask side.
_BUY_SIDES = frozenset({"BUY", "B", "BID"})
_SELL_SIDES = frozenset({"SELL", "S", "ASK"})

# Residual level volume at or below which a price level is treated as empty and
# removed. Sized for float accumulation error across many partial reductions,
# well below any venue's minimum order increment.
_LEVEL_EPSILON = 1e-9

VIOLATION_UNKNOWN_ORDER = "UNKNOWN_ORDER"
VIOLATION_DUPLICATE_ORDER_ID = "DUPLICATE_ORDER_ID"
VIOLATION_OVER_EXECUTE = "OVER_EXECUTE"
VIOLATION_CROSSED_BOOK = "CROSSED_BOOK"


class DepthProcessorError(ValueError):
    """Raised when an update is malformed and cannot be applied to the book.

    Signals bad *input* -- a non-finite price, a negative size, an unrecognised
    side, an invalid depth level count, or metrics requested from an empty book.
    A well-formed update that cannot be applied to a *consistent* book is an
    integrity violation instead: it is counted, not raised.
    """


@dataclass(frozen=True)
class DepthMetrics:
    """Microstructure metrics derived from one consistent view of the book.

    ``weighted_mid_price`` is computed from **top-of-book** volumes only, while
    ``imbalance_ratio`` aggregates up to ``depth_levels`` levels on each side.
    The two use different volume aggregations on purpose -- the weighted mid is
    defined at the touch, and depth imbalance is a deliberately deeper signal --
    so the volumes and the level counts behind the ratio are reported alongside
    it.

    ``bid_levels`` and ``ask_levels`` are the counts *actually* aggregated, which
    a thin side can make smaller than the requested cap and unequal to each
    other. That asymmetry is real depth information, not an error, but an
    imbalance computed over two bid levels and five ask levels is not the same
    statistic as one computed over five and five -- so it is reported rather
    than hidden behind a single number.

    ``is_crossed`` is authoritative: when it is ``True`` the book is locked
    (best bid == best ask) or crossed (best bid > best ask), ``spread`` is zero
    or negative, and no field on this object may be traded on.
    """

    best_bid: float
    best_ask: float
    mid_price: float
    weighted_mid_price: float
    imbalance_ratio: float          # in [-1.0, +1.0]
    spread: float                   # <= 0.0 when is_crossed
    is_crossed: bool
    bid_levels: int                 # bid levels actually aggregated
    ask_levels: int                 # ask levels actually aggregated
    total_bid_volume: float         # summed over bid_levels
    total_ask_volume: float         # summed over ask_levels


@dataclass(frozen=True)
class BookSnapshot:
    """An immutable point-in-time copy of the top N levels of both sides.

    Produced under the processor's lock, so the bid and ask sides are mutually
    consistent -- unlike two separate reads of the live ``bids`` / ``asks``
    views, which can straddle a mutation.
    """

    symbol: str
    bids: Tuple[Tuple[float, float], ...]   # (price, volume), descending price
    asks: Tuple[Tuple[float, float], ...]   # (price, volume), ascending price
    is_crossed: bool


def _unpack_level(entry: object, context: str) -> Tuple[float, float]:
    """Unpack one ``(price, quantity)`` update, or raise.

    A row of the wrong arity is a decoder bug, and tuple unpacking would surface
    it as a bare ``ValueError`` from inside the ingress loop with nothing naming
    the feed or the field.
    """
    try:
        price, quantity = entry  # type: ignore[misc]
    except (TypeError, ValueError) as exc:
        raise DepthProcessorError(
            f"{context}: expected a (price, quantity) pair, got {entry!r}"
        ) from exc
    return (
        _validate_price(price, context),
        _validate_size(quantity, context, allow_zero=True),
    )


def _validate_price(price: float, context: str) -> float:
    """Return ``price`` as a finite, strictly positive float, or raise."""
    try:
        value = float(price)
    except (TypeError, ValueError) as exc:
        raise DepthProcessorError(f"{context}: price {price!r} is not numeric") from exc
    if not math.isfinite(value):
        raise DepthProcessorError(f"{context}: price must be finite, got {price!r}")
    if value <= 0.0:
        raise DepthProcessorError(f"{context}: price must be positive, got {value!r}")
    return value


def _validate_size(size: float, context: str, *, allow_zero: bool) -> float:
    """Return ``size`` as a finite, non-negative float, or raise.

    A negative quantity is never a delete instruction -- venues signal removal
    with an explicit zero -- so it is rejected as feed corruption.
    """
    try:
        value = float(size)
    except (TypeError, ValueError) as exc:
        raise DepthProcessorError(f"{context}: size {size!r} is not numeric") from exc
    if not math.isfinite(value):
        raise DepthProcessorError(f"{context}: size must be finite, got {size!r}")
    if value < 0.0:
        raise DepthProcessorError(
            f"{context}: negative size {value!r} is feed corruption, not a removal "
            f"instruction (venues remove a level with quantity 0)"
        )
    if value == 0.0 and not allow_zero:
        raise DepthProcessorError(f"{context}: size must be greater than zero")
    return value


def _normalize_side(side: str, context: str) -> str:
    """Map a venue side token onto ``'BUY'`` or ``'SELL'``, or raise."""
    if not isinstance(side, str):
        raise DepthProcessorError(f"{context}: side must be a string, got {side!r}")
    token = side.strip().upper()
    if token in _BUY_SIDES:
        return "BUY"
    if token in _SELL_SIDES:
        return "SELL"
    raise DepthProcessorError(
        f"{context}: unrecognised side {side!r}; expected one of "
        f"{sorted(_BUY_SIDES | _SELL_SIDES)}"
    )


class L2L3DepthProcessor:
    """Thread-safe aggregated depth book for L2 price-level and L3 order feeds.

    All state transitions, and all reads that must be mutually consistent,
    happen under a single :class:`threading.Lock`. ``bids``, ``asks`` and
    ``l3_orders`` are exposed as read-only views so callers cannot corrupt the
    book from outside the lock; for a bid/ask pair guaranteed to come from the
    same instant, use :meth:`get_snapshot`.

    Do not drive one instance from both an L2 and an L3 feed for the same
    symbol. L2 updates *set* a level's absolute volume while L3 events
    accumulate into it; interleaving them makes the aggregate meaningless.

    The lock is not reentrant: no public method may be called from inside a
    logging handler that this processor's own warnings reach, because those
    warnings are emitted while the lock is held.
    """

    def __init__(self, symbol: str) -> None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise DepthProcessorError("symbol must be a non-empty string")
        self.symbol: str = symbol
        self._lock = threading.Lock()
        self._bids: Dict[float, float] = {}   # price -> aggregated volume
        self._asks: Dict[float, float] = {}   # price -> aggregated volume
        self._l3_orders: Dict[str, Tuple[str, float, float]] = {}  # id -> (side, price, size)
        self._violations: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Read-only state views
    # ------------------------------------------------------------------
    @property
    def bids(self) -> Mapping[float, float]:
        """Read-only view of the bid side (price -> aggregated volume)."""
        return MappingProxyType(self._bids)

    @property
    def asks(self) -> Mapping[float, float]:
        """Read-only view of the ask side (price -> aggregated volume)."""
        return MappingProxyType(self._asks)

    @property
    def l3_orders(self) -> Mapping[str, Tuple[str, float, float]]:
        """Read-only view of resting L3 orders (order_id -> (side, price, size))."""
        return MappingProxyType(self._l3_orders)

    @property
    def violations_by_kind(self) -> Mapping[str, int]:
        """Counts of well-formed updates that could not be applied consistently."""
        with self._lock:
            return MappingProxyType(dict(self._violations))

    @property
    def integrity_violation_count(self) -> int:
        """Total integrity violations. Non-zero invalidates any derived statistic."""
        with self._lock:
            return sum(self._violations.values())

    @property
    def is_crossed(self) -> bool:
        """``True`` if the book is currently locked or crossed."""
        with self._lock:
            return self._is_crossed_internal()

    # ------------------------------------------------------------------
    # Level 2 (price-aggregated) ingress
    # ------------------------------------------------------------------
    def update_l2_depth(
        self,
        bid_updates: Iterable[Tuple[float, float]],
        ask_updates: Iterable[Tuple[float, float]],
    ) -> bool:
        """Atomically apply absolute L2 price-level quantities.

        Each ``(price, quantity)`` pair *sets* that level to ``quantity``; a
        quantity of exactly zero removes the level. Quantities are absolute, not
        increments.

        Returns ``True`` when the resulting book is consistent and ``False`` when
        it is locked or crossed. A ``False`` return means the local book no
        longer matches the venue and must be re-synchronised from a fresh
        snapshot. The applied state is deliberately **kept**, not rolled back: a
        crossed book usually means an earlier message was dropped, and
        discarding the update that exposed the gap leaves the book wrong in a
        way nothing downstream can detect. Call :meth:`reset` and rebuild.

        Raises:
            DepthProcessorError: on a malformed price or quantity. Every update
                in the batch is validated before any is applied, so a rejected
                batch leaves the book untouched.
        """
        clean_bids = [_unpack_level(entry, "L2 bid") for entry in bid_updates]
        clean_asks = [_unpack_level(entry, "L2 ask") for entry in ask_updates]

        with self._lock:
            for price, qty in clean_bids:
                if qty == 0.0:
                    self._bids.pop(price, None)
                else:
                    self._bids[price] = qty
            for price, qty in clean_asks:
                if qty == 0.0:
                    self._asks.pop(price, None)
                else:
                    self._asks[price] = qty

            if self._is_crossed_internal():
                self._record_violation(VIOLATION_CROSSED_BOOK)
                logger.warning(
                    "Crossed or locked book for %s: best bid %s >= best ask %s. "
                    "Local book is out of sync; re-synchronise from a snapshot.",
                    self.symbol, max(self._bids), min(self._asks),
                )
                return False
            return True

    # ------------------------------------------------------------------
    # Level 3 (order-by-order) ingress
    # ------------------------------------------------------------------
    def add_l3_order(self, order_id: str, side: str, price: float, size: float) -> bool:
        """Atomically rest a new L3 order and add its size to its price level.

        Returns ``True`` when the order was added. A repeated ``order_id`` for an
        order already resting is rejected and counted as ``DUPLICATE_ORDER_ID``:
        order reference numbers are unique for the session on the feeds this
        covers, so a repeat means a message was missed. Accepting it would add
        the size twice and strand the surplus at that level permanently, because
        the eventual cancel can only deduct one order's worth.

        Raises:
            DepthProcessorError: on a malformed id, side, price or size.
        """
        if not isinstance(order_id, str) or not order_id:
            raise DepthProcessorError(
                f"L3 add: order_id must be a non-empty string, got {order_id!r}"
            )
        clean_side = _normalize_side(side, f"L3 add {order_id}")
        clean_price = _validate_price(price, f"L3 add {order_id}")
        clean_size = _validate_size(size, f"L3 add {order_id}", allow_zero=False)

        with self._lock:
            if order_id in self._l3_orders:
                self._record_violation(VIOLATION_DUPLICATE_ORDER_ID)
                logger.warning(
                    "Duplicate L3 order id %r on %s: order already resting, add ignored.",
                    order_id, self.symbol,
                )
                return False
            self._l3_orders[order_id] = (clean_side, clean_price, clean_size)
            book = self._bids if clean_side == "BUY" else self._asks
            book[clean_price] = book.get(clean_price, 0.0) + clean_size
            return True

    def cancel_l3_order(self, order_id: str) -> bool:
        """Atomically remove a resting L3 order in full and deduct its size.

        Returns ``True`` when an order was removed. A cancel for an unknown id is
        counted as ``UNKNOWN_ORDER`` rather than silently ignored -- it means the
        matching add was never seen, so the book has been diverging since.
        """
        with self._lock:
            order = self._l3_orders.pop(order_id, None)
            if order is None:
                self._record_violation(VIOLATION_UNKNOWN_ORDER)
                logger.warning(
                    "Cancel for unknown L3 order id %r on %s: add was never observed.",
                    order_id, self.symbol,
                )
                return False
            side, price, size = order
            self._deduct_locked(side, price, size)
            return True

    def execute_l3_order(self, order_id: str, executed_size: float) -> bool:
        """Atomically apply a partial or full execution against a resting order.

        Deducts ``executed_size`` from the order's remaining size and from its
        price level, removing the order once nothing remains. This is the path
        for Nasdaq ITCH ``E`` / ``C`` executions, ITCH ``X`` partial cancels and
        Coinbase ``match`` messages.

        An execution larger than the resting size is counted as ``OVER_EXECUTE``
        and the order is removed. Clamping without flagging would hide the fact
        that the book had already diverged before the execution arrived.

        Returns ``True`` when the execution applied cleanly against a known
        order, ``False`` when the order was unknown or the size over-executed.
        """
        clean_size = _validate_size(
            executed_size, f"L3 execute {order_id}", allow_zero=False
        )
        with self._lock:
            order = self._l3_orders.get(order_id)
            if order is None:
                self._record_violation(VIOLATION_UNKNOWN_ORDER)
                logger.warning(
                    "Execution for unknown L3 order id %r on %s: add was never observed.",
                    order_id, self.symbol,
                )
                return False

            side, price, resting = order
            over_executed = clean_size > resting + _LEVEL_EPSILON
            applied = min(clean_size, resting)
            remaining = resting - applied
            self._deduct_locked(side, price, applied)

            if over_executed:
                self._record_violation(VIOLATION_OVER_EXECUTE)
                logger.warning(
                    "Over-execution on %s order %r: %s executed against %s resting.",
                    self.symbol, order_id, clean_size, resting,
                )
            if over_executed or remaining <= _LEVEL_EPSILON:
                self._l3_orders.pop(order_id, None)
            else:
                self._l3_orders[order_id] = (side, price, remaining)
            return not over_executed

    def modify_l3_order(self, order_id: str, new_size: float) -> bool:
        """Atomically set a resting order's displayed size to an absolute value.

        This is the path for a size change that is not an execution -- the
        Coinbase Exchange ``change`` message, whose ``size`` is "the updated size
        at the price level, not a delta". Price is not modifiable in place: a
        price change loses queue priority, and is a cancel followed by an add
        under a new identity.

        Returns ``True`` when the order was modified, ``False`` when the id is
        unknown (counted as ``UNKNOWN_ORDER``).
        """
        clean_size = _validate_size(new_size, f"L3 modify {order_id}", allow_zero=False)
        with self._lock:
            order = self._l3_orders.get(order_id)
            if order is None:
                self._record_violation(VIOLATION_UNKNOWN_ORDER)
                logger.warning(
                    "Modify for unknown L3 order id %r on %s: add was never observed.",
                    order_id, self.symbol,
                )
                return False
            side, price, resting = order
            book = self._bids if side == "BUY" else self._asks
            book[price] = book.get(price, 0.0) - resting + clean_size
            if book[price] <= _LEVEL_EPSILON:
                book.pop(price, None)
            self._l3_orders[order_id] = (side, price, clean_size)
            return True

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Atomically clear all book and order state, keeping violation counts.

        The recovery step after a crossed book or a detected sequence gap:
        discard the local book so no stale depth can be read, then rebuild from
        a fresh venue snapshot. Violation counters survive deliberately, so a
        session's divergence history is not erased by recovering from it.
        """
        with self._lock:
            self._bids.clear()
            self._asks.clear()
            self._l3_orders.clear()
        logger.info("Depth state reset for %s; rebuild from a fresh snapshot.", self.symbol)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_snapshot(self, depth_levels: int = 5) -> BookSnapshot:
        """Return an immutable, mutually consistent copy of the top N levels."""
        levels = self._validate_depth_levels(depth_levels)
        with self._lock:
            return BookSnapshot(
                symbol=self.symbol,
                bids=tuple(self._top_bids_locked(levels)),
                asks=tuple(self._top_asks_locked(levels)),
                is_crossed=self._is_crossed_internal(),
            )

    def compute_metrics(self, depth_levels: int = 5) -> DepthMetrics:
        """Compute weighted mid-price, depth imbalance and spread atomically.

        ``depth_levels`` sets how many price levels per side feed the imbalance
        ratio; the weighted mid-price always uses top-of-book volumes.

        Every metric is returned even when the book is crossed, with
        ``is_crossed=True`` -- a fabricated neutral imbalance would read as a
        balanced book to a caller that forgot to check the flag. **Gate on
        ``is_crossed`` before consuming any field.**

        Raises:
            DepthProcessorError: if ``depth_levels`` is not a positive integer,
                if either side of the book is empty, or if the aggregated volume
                is zero on both sides (no defined imbalance).
        """
        levels = self._validate_depth_levels(depth_levels)
        with self._lock:
            if not self._bids or not self._asks:
                raise DepthProcessorError(
                    f"Cannot compute metrics for {self.symbol!r}: "
                    f"{'bid' if not self._bids else 'ask'} side is empty."
                )

            top_bids = self._top_bids_locked(levels)
            top_asks = self._top_asks_locked(levels)

            best_bid, top_bid_vol = top_bids[0]
            best_ask, top_ask_vol = top_asks[0]

            total_bid_vol = math.fsum(v for _, v in top_bids)
            total_ask_vol = math.fsum(v for _, v in top_asks)
            depth_vol = total_bid_vol + total_ask_vol
            touch_vol = top_bid_vol + top_ask_vol

            # Volumes are validated strictly positive at ingress, so these
            # denominators are positive for any non-empty book. Clamping them
            # with an epsilon floor instead would silently rescale the result
            # for any book whose sizes are legitimately small -- a crypto book
            # quoted in fractions of a coin would return a weighted mid nowhere
            # near the touch, with no error raised.
            if touch_vol <= 0.0 or depth_vol <= 0.0:
                raise DepthProcessorError(
                    f"Cannot compute metrics for {self.symbol!r}: zero aggregate volume."
                )

            weighted_mid = (best_bid * top_ask_vol + best_ask * top_bid_vol) / touch_vol
            imbalance = (total_bid_vol - total_ask_vol) / depth_vol

            return DepthMetrics(
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=(best_bid + best_ask) / 2.0,
                weighted_mid_price=weighted_mid,
                imbalance_ratio=imbalance,
                spread=best_ask - best_bid,
                is_crossed=best_bid >= best_ask,
                bid_levels=len(top_bids),
                ask_levels=len(top_asks),
                total_bid_volume=total_bid_vol,
                total_ask_volume=total_ask_vol,
            )

    # ------------------------------------------------------------------
    # Internals -- every caller below must already hold the lock
    # ------------------------------------------------------------------
    def _is_crossed_internal(self) -> bool:
        """``True`` when best bid >= best ask (covers both locked and crossed)."""
        if not self._bids or not self._asks:
            return False
        return max(self._bids) >= min(self._asks)

    def _top_bids_locked(self, levels: int) -> List[Tuple[float, float]]:
        return sorted(self._bids.items(), key=lambda kv: kv[0], reverse=True)[:levels]

    def _top_asks_locked(self, levels: int) -> List[Tuple[float, float]]:
        return sorted(self._asks.items(), key=lambda kv: kv[0])[:levels]

    def _deduct_locked(self, side: str, price: float, size: float) -> None:
        book = self._bids if side == "BUY" else self._asks
        if price not in book:
            return
        book[price] -= size
        if book[price] <= _LEVEL_EPSILON:
            book.pop(price, None)

    def _record_violation(self, kind: str) -> None:
        self._violations[kind] = self._violations.get(kind, 0) + 1

    @staticmethod
    def _validate_depth_levels(depth_levels: int) -> int:
        """Reject non-positive depth counts.

        ``0`` indexes an empty slice and raises ``IndexError`` deep inside the
        metrics path, and ``-1`` silently drops the *last* level through
        Python's negative slicing, returning a plausible but wrong imbalance
        rather than failing.
        """
        if isinstance(depth_levels, bool) or not isinstance(depth_levels, int):
            raise DepthProcessorError(
                f"depth_levels must be an int, got {type(depth_levels).__name__}"
            )
        if depth_levels < 1:
            raise DepthProcessorError(f"depth_levels must be >= 1, got {depth_levels}")
        return depth_levels
