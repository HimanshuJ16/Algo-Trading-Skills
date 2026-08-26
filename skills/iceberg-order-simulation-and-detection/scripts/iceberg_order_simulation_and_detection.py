"""
iceberg-order-simulation-and-detection: price-level screen for hidden (iceberg /
reserve) liquidity, driven by aggregated Level 2 depth plus trade prints.

An iceberg order is a limit order carrying a total size and a smaller *display*
(peak) size; only the peak rests visibly in the book, and the venue replenishes it
from the hidden remainder as it executes. The screen this module implements is the
standard first-order one: at a single price level, compare cumulative volume that
actually printed against the depth that was ever displayed there, and require the
level to have been visibly replenished.

    V_cum  = sum of trade quantity at price P consumed from the tracked book side
    Q_0    = displayed depth at P when the level was baselined
    detect = (V_cum / Q_0 >= min_volume_ratio) and (refills >= min_refill_count)
    Q_hidden_hat = max(0, V_cum - Q_0)

``Q_hidden_hat`` is a *lower bound* on hidden size, and only under the assumption
that every refill at the level came from the same resting order. That assumption is
the whole ballgame, and aggregated depth cannot verify it -- see Limitations.

Venue semantics this screen depends on (verified 2026-08):

- **CME Globex** native ("exchange-held") icebergs are entered with FIX tag
  1138-DisplayQty. When the displayed quantity is refreshed the order keeps the
  **same OrderID**, and trade summary messages carry the true trade volume, which
  may exceed the resting display quantity. CME states this combination makes native
  icebergs detectable unambiguously and accurately -- *from Market by Order (MBO)
  data*. ISV-held (synthetic) icebergs instead submit a new order per refresh and
  receive a **new OrderID**.
  https://www.cmegroup.com/articles/faqs/market-by-order-mbo.html
- **Nasdaq** Reserve Orders (Equity 4, Rule 4703(h)): when an execution reduces the
  displayed portion below a round lot, a new displayed order is entered and receives
  a **new timestamp**, while the reserve portion keeps its original timestamp. Two
  consequences for detection: replenishment fires *below a round lot*, not strictly
  at zero, and Nasdaq's optional Random Reserve lets the participant randomize the
  display size, so a constant peak must not be treated as a requirement.
  https://www.federalregister.gov/documents/2021/02/18/2021-03214/
- Detection via "discrepancies between the resting volume of an order and the actual
  trade size as indicated by trade summary messages, as well as by tracking order
  modifications that follow trade events" is the published approach for CME native
  icebergs (Zotikov, *CME Iceberg Order Detection and Prediction*, arXiv:1909.09495).
  That method operates on **order-level** messages.

Limitations (deliberate, and load-bearing -- read before acting on an output):

- **Aggregated depth cannot attribute a refill to an order.** This module consumes
  price-level (L2 / Market-by-Price) depth. At that aggregation, a level replenished
  by twenty independent participants is indistinguishable from one iceberg refilling
  twenty times. Every source above resolves this with order-level (MBO / L3) data.
  Where MBO is available, use it; treat this screen as a candidate generator only.
- **The score is not a probability.** ``confidence_score`` is an ordinal heuristic
  bounded by MAX_CONFIDENCE_SCORE (< 1.0) precisely because the hypothesis cannot be
  confirmed from the input data. Do not size positions off it and do not report it
  as a detection probability.
- **Thresholds are tunable defaults, not standards.** ``min_volume_ratio = 1.5`` and
  ``min_refill_count = 2`` are starting points, not values published by any venue or
  regulator. Calibrate per instrument and liquidity tier against labelled data before
  relying on them.
- **Not a surveillance or enforcement tool.** A positive screen is not evidence of
  layering, spoofing, or any other abusive practice: iceberg and reserve orders are
  ordinary, explicitly supported order types on every venue cited above.
- **No cross-level inference.** Each price level is scored independently; a large
  order working several levels is not aggregated into a single parent.
"""
import logging
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

#: Resting book sides accepted on a depth snapshot.
BOOK_SIDES: Tuple[str, ...] = ("BID", "ASK")

#: Aggressor sides accepted on a trade print.
AGGRESSOR_SIDES: Tuple[str, ...] = ("BUY", "SELL")

#: Which resting side a given aggressor consumes. A SELL aggressor lifts resting
#: bids; a BUY aggressor lifts resting asks. This is the link that makes book side
#: and aggressor side redundant confirmations of the same hypothesis rather than
#: interchangeable inputs.
AGGRESSOR_CONSUMES: Dict[str, str] = {"SELL": "BID", "BUY": "ASK"}

#: Signal emitted when the hidden resting order sits on the bid.
SIGNAL_BULLISH = "BULLISH_HIDDEN_BUY"

#: Signal emitted when the hidden resting order sits on the ask.
SIGNAL_BEARISH = "BEARISH_HIDDEN_SELL"

#: Ceiling on ``confidence_score``. Strictly below 1.0: on aggregated price-level
#: depth the iceberg hypothesis is never confirmable, so the engine must never
#: report certainty. See the module docstring.
MAX_CONFIDENCE_SCORE = 0.95

#: Volume ratio beyond which additional ratio adds no score. Without this cap a
#: single outsized print at a thin level saturates the score on its own.
CONFIDENCE_RATIO_CAP = 3.0

#: Refill count beyond which additional refills add no score.
CONFIDENCE_REFILL_CAP = 5

#: Score deduction when trades from *both* aggressor sides printed at the level.
#: Two-sided flow means the level changed sides, so its volume no longer describes
#: a single resting order.
CONFIDENCE_PENALTY_CONTRA_FLOW = 0.15

#: Score deduction when observed refill peaks are not of a consistent size. Weaker
#: evidence, not disqualifying: Nasdaq Random Reserve randomizes display size.
CONFIDENCE_PENALTY_INCONSISTENT_PEAK = 0.10

#: Relative tolerance within which two refill peaks count as the same peak size.
PEAK_CONSISTENCY_TOLERANCE = 0.10

#: How long a level must sit empty before a reappearance is treated as a *new*
#: resting order rather than an iceberg refill. Venue refreshes are immediate; a
#: level that stays empty and then returns is a different order and must be
#: re-baselined. 1 second is deliberately loose -- tighten it for a colocated feed.
DEFAULT_LEVEL_RESET_DWELL_NANOS = 1_000_000_000

#: Maximum concurrently tracked price levels; least-recently-updated is evicted.
#: Bounds memory on a long session that walks thousands of levels.
DEFAULT_MAX_TRACKED_LEVELS = 4096

#: Maximum trade IDs retained for duplicate suppression (FIFO eviction).
DEFAULT_DEDUP_CAPACITY = 100_000

#: Decimal places used to canonicalize a price into a level key when no tick size is
#: supplied. Collapses float representation noise (0.1 + 0.2 != 0.3) without merging
#: genuinely distinct levels on fine-tick instruments such as crypto.
DEFAULT_PRICE_KEY_DECIMALS = 10

LevelKey = Union[int, float]


@dataclass
class TradePrint:
    trade_id: str
    price: float
    quantity: int
    aggressor_side: str                 # 'BUY' or 'SELL'
    timestamp_nanos: int


@dataclass
class Level2DepthSnapshot:
    price: float
    side: str                           # 'BID' or 'ASK'
    displayed_quantity: int
    timestamp_nanos: int


@dataclass
class IcebergDetectionReport:
    symbol: str
    detected_price: float
    iceberg_side: str                   # 'BUY' (hidden bid) or 'SELL' (hidden ask)
    initial_display_quantity: int
    cumulative_traded_quantity: int     # Consumed from the tracked side only
    estimated_hidden_quantity: int      # Lower bound; see module docstring
    refill_count: int
    confidence_score: float             # Ordinal heuristic in [0, MAX_CONFIDENCE_SCORE]
    signal_classification: str
    audit_notes: str
    # --- Diagnostics (defaulted; existing positional construction is unaffected) ---
    contra_side_traded_quantity: int = 0    # Volume at this price from the other aggressor
    refill_peaks_consistent: bool = True    # Were refills to a repeatable peak size?
    observed_refill_peaks: Tuple[int, ...] = ()
    is_initial_detection: bool = True       # False on re-emission at the same level
    volume_ratio: float = 0.0


def _validate_price(price: Any, context: str) -> float:
    """
    Rejects non-numeric and non-finite prices.

    Positivity is deliberately *not* required: negative prices are real (CME WTI
    settled below zero on 2020-04-20, and calendar and option spreads quote negative
    routinely). Only NaN and Inf are rejected -- a NaN price is never equal to
    itself, so it would spawn an unreachable tracker on every single message.
    """
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise TypeError(f"{context}: price must be numeric, got {type(price).__name__}.")
    value = float(price)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(
            f"{context}: price must be finite, got {price!r}. A non-finite price key "
            "creates a tracker that can never be looked up again."
        )
    return value


def _validate_quantity(quantity: Any, context: str, field_name: str, allow_zero: bool) -> int:
    """Rejects non-integral, negative, and (optionally) zero quantities."""
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise TypeError(
            f"{context}: {field_name} must be an int, got {type(quantity).__name__}. "
            "Fractional sizes must be scaled to integer units by the caller."
        )
    if quantity < 0:
        raise ValueError(
            f"{context}: {field_name} must be >= 0, got {quantity}. A negative quantity "
            "silently reduces cumulative volume and suppresses detection."
        )
    if quantity == 0 and not allow_zero:
        raise ValueError(f"{context}: {field_name} must be > 0, got 0.")
    return quantity


def _validate_side(side: Any, allowed: Tuple[str, ...], context: str, field_name: str) -> str:
    """Normalizes case and rejects anything outside the allowed literals."""
    if not isinstance(side, str):
        raise TypeError(f"{context}: {field_name} must be a str, got {type(side).__name__}.")
    normalized = side.strip().upper()
    if normalized not in allowed:
        raise ValueError(
            f"{context}: {field_name} must be one of {allowed}, got {side!r}. An "
            "unrecognized side would be compared against the wrong literal and "
            "silently invert the signal."
        )
    return normalized


def _validate_timestamp(ts: Any, context: str) -> int:
    """Timestamps order the state machine, so a non-integral one is rejected."""
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise TypeError(f"{context}: timestamp_nanos must be an int, got {type(ts).__name__}.")
    return ts


def _peaks_are_consistent(peaks: List[int]) -> bool:
    """
    True when every observed refill peak is within PEAK_CONSISTENCY_TOLERANCE of the
    first one.

    A venue-held iceberg replenishes to a repeatable peak, which is the feature that
    separates it from organic replenishment by unrelated participants. Nasdaq Random
    Reserve randomizes the display size, so this is supporting evidence only: it
    modulates the score and never gates detection.
    """
    if len(peaks) < 2:
        return True
    baseline = float(peaks[0])
    if baseline <= 0:
        return False
    return all(abs(p - baseline) / baseline <= PEAK_CONSISTENCY_TOLERANCE for p in peaks)


class IcebergDetectorEngine:
    """
    Screens aggregated Level 2 depth and trade prints for price levels whose printed
    volume materially exceeds the depth ever displayed there, with repeated visible
    replenishment -- the price-level signature of an iceberg / reserve order.

    Read the module docstring before acting on an output. In particular this is a
    candidate generator on price-level data, not a confirmation, and
    ``confidence_score`` is not a probability.

    Not thread-safe: all state is plain mutable containers. Feed one instance from a
    single consumer, or guard it externally.
    """

    def __init__(
        self,
        symbol: str = "AAPL",
        min_volume_ratio: float = 1.5,
        min_refill_count: int = 2,
        tick_size: Optional[float] = None,
        level_reset_dwell_nanos: int = DEFAULT_LEVEL_RESET_DWELL_NANOS,
        max_tracked_levels: int = DEFAULT_MAX_TRACKED_LEVELS,
        dedup_capacity: int = DEFAULT_DEDUP_CAPACITY,
    ):
        """
        Args:
            symbol: Instrument label, carried through to reports.
            min_volume_ratio: V_cum / Q_0 required to flag. A tunable default, not a
                published standard.
            min_refill_count: Visible replenishments required to flag.
            tick_size: Instrument tick. When set, prices are binned to integer ticks
                so two representations of the same economic level cannot split into
                separate trackers. Recommended. Left ``None``, level keys fall back to
                rounding at DEFAULT_PRICE_KEY_DECIMALS, which removes float noise but
                cannot repair a genuinely mis-scaled feed.
            level_reset_dwell_nanos: How long a level must stay empty before a
                reappearance re-baselines the tracker instead of counting as a refill.
            max_tracked_levels: Cap on concurrently tracked levels (LRU eviction).
            dedup_capacity: Trade IDs retained for duplicate suppression; 0 disables.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}.")
        if isinstance(min_volume_ratio, bool) or not isinstance(min_volume_ratio, (int, float)) \
                or min_volume_ratio <= 1.0:
            raise ValueError(
                f"min_volume_ratio must be a number > 1.0, got {min_volume_ratio!r}. At or "
                "below 1.0 the level has not traded more than it displayed, so there is no "
                "volume discrepancy to attribute to hidden size."
            )
        if isinstance(min_refill_count, bool) or not isinstance(min_refill_count, int) \
                or min_refill_count < 1:
            raise ValueError(
                f"min_refill_count must be an int >= 1, got {min_refill_count!r}. Zero would "
                "flag a single aggressive sweep, which is not an iceberg."
            )
        if tick_size is not None and (isinstance(tick_size, bool)
                                      or not isinstance(tick_size, (int, float))
                                      or tick_size <= 0):
            raise ValueError(f"tick_size must be a positive number or None, got {tick_size!r}.")
        if isinstance(level_reset_dwell_nanos, bool) or not isinstance(level_reset_dwell_nanos, int) \
                or level_reset_dwell_nanos < 0:
            raise ValueError(
                f"level_reset_dwell_nanos must be an int >= 0, got {level_reset_dwell_nanos!r}.")
        if isinstance(max_tracked_levels, bool) or not isinstance(max_tracked_levels, int) \
                or max_tracked_levels < 1:
            raise ValueError(f"max_tracked_levels must be an int >= 1, got {max_tracked_levels!r}.")
        if isinstance(dedup_capacity, bool) or not isinstance(dedup_capacity, int) \
                or dedup_capacity < 0:
            raise ValueError(f"dedup_capacity must be an int >= 0, got {dedup_capacity!r}.")

        self.symbol = symbol
        self.min_volume_ratio = float(min_volume_ratio)
        self.min_refill_count = min_refill_count
        self.tick_size = float(tick_size) if tick_size is not None else None
        self.level_reset_dwell_nanos = level_reset_dwell_nanos
        self.max_tracked_levels = max_tracked_levels
        self.dedup_capacity = dedup_capacity

        #: Level key -> tracker state. Ordered so the least-recently-updated level can
        #: be evicted. The key is opaque; read state through ``get_level_state``.
        self.price_trackers: "OrderedDict[LevelKey, Dict[str, Any]]" = OrderedDict()
        self._seen_trade_ids: Set[str] = set()
        self._trade_id_order: Deque[str] = deque()

        if self.tick_size is None:
            logger.debug(
                "%s: no tick_size supplied; price levels keyed by rounding to %d decimals.",
                self.symbol, DEFAULT_PRICE_KEY_DECIMALS,
            )

    # ------------------------------------------------------------------ internals

    def _level_key(self, price: float) -> LevelKey:
        """
        Canonicalizes a price into a stable dict key.

        Raw floats are unusable as level keys: a feed that computes 0.1 + 0.2 and one
        that parses "0.30" produce values that are not equal, splitting one economic
        level into two half-populated trackers and suppressing detection at both.
        """
        if self.tick_size is not None:
            ticks = price / self.tick_size
            # Guard the conversion: a corrupt price near the float ceiling overflows
            # int() outright, and beyond 2**53 a tick count is no longer exactly
            # representable, so distinct levels would silently collide.
            if ticks != ticks or abs(ticks) > 2 ** 53:
                raise ValueError(
                    f"price {price!r} is {ticks!r} ticks at tick_size={self.tick_size!r}, "
                    "which cannot be represented as an exact level key. Check the feed's "
                    "price scaling."
                )
            return int(round(ticks))
        return round(price, DEFAULT_PRICE_KEY_DECIMALS)

    def _touch(self, key: LevelKey) -> None:
        """Marks a level most-recently-used and evicts the coldest if over cap."""
        self.price_trackers.move_to_end(key)
        while len(self.price_trackers) > self.max_tracked_levels:
            evicted_key, _ = self.price_trackers.popitem(last=False)
            logger.debug("%s: evicted stale price level tracker %r (cap %d).",
                         self.symbol, evicted_key, self.max_tracked_levels)

    @staticmethod
    def _new_tracker(price: float, side: str, displayed: int, ts: int) -> Dict[str, Any]:
        return {
            "price": price,
            "side": side,
            "initial_display": displayed,
            "current_display": displayed,
            "cumulative_traded": 0,
            "contra_traded": 0,
            "refill_count": 0,
            "refill_peaks": [],
            "last_snapshot_nanos": ts,
            "emptied_at_nanos": None if displayed > 0 else ts,
            "detections_emitted": 0,
        }

    def _is_duplicate_trade(self, trade_id: Optional[str]) -> bool:
        """
        Suppresses a trade ID already accumulated.

        Feed reconnects, snapshot-plus-delta recovery and replayed sessions all
        redeliver trade prints. Re-adding the same execution inflates V_cum and so
        inflates the hidden-size estimate, which is the quantity being reported.
        """
        if not self.dedup_capacity or not trade_id:
            return False
        if trade_id in self._seen_trade_ids:
            return True
        self._seen_trade_ids.add(trade_id)
        self._trade_id_order.append(trade_id)
        while len(self._trade_id_order) > self.dedup_capacity:
            self._seen_trade_ids.discard(self._trade_id_order.popleft())
        return False

    # --------------------------------------------------------------------- public

    def reset(self) -> None:
        """Clears all level trackers and the duplicate-trade window."""
        self.price_trackers.clear()
        self._seen_trade_ids.clear()
        self._trade_id_order.clear()

    def get_level_state(self, price: float) -> Optional[Dict[str, Any]]:
        """Returns a shallow copy of the tracker at ``price``, or None if untracked."""
        key = self._level_key(_validate_price(price, "get_level_state"))
        tracker = self.price_trackers.get(key)
        return dict(tracker) if tracker is not None else None

    def process_l2_depth(self, snapshot: Level2DepthSnapshot) -> None:
        """
        Folds a price-level depth observation into the tracker for that level.

        Counts a refill when displayed depth increases at a level that has already
        traded on the tracked side. Re-baselines the level instead when the resting
        order can no longer be the same one: the level flipped between bid and ask, or
        it sat empty for longer than ``level_reset_dwell_nanos``. Snapshots older than
        the last one processed for the level are dropped rather than applied -- a
        late-arriving stale snapshot shows depth "increasing" back to its earlier value
        and would otherwise book a refill that never happened.
        """
        context = f"process_l2_depth[{self.symbol}]"
        price = _validate_price(snapshot.price, context)
        side = _validate_side(snapshot.side, BOOK_SIDES, context, "side")
        displayed = _validate_quantity(snapshot.displayed_quantity, context,
                                       "displayed_quantity", allow_zero=True)
        ts = _validate_timestamp(snapshot.timestamp_nanos, context)

        key = self._level_key(price)
        tracker = self.price_trackers.get(key)

        if tracker is None:
            self.price_trackers[key] = self._new_tracker(price, side, displayed, ts)
            self._touch(key)
            return

        if ts < tracker["last_snapshot_nanos"]:
            logger.warning(
                "%s: dropping out-of-order depth snapshot at %.4f (ts=%d < last=%d); "
                "applying it would book a phantom refill.",
                self.symbol, price, ts, tracker["last_snapshot_nanos"],
            )
            return

        side_flipped = side != tracker["side"]
        emptied_at = tracker["emptied_at_nanos"]
        dwelled_empty = (
            displayed > 0
            and emptied_at is not None
            and (ts - emptied_at) > self.level_reset_dwell_nanos
        )

        if side_flipped or dwelled_empty:
            reason = (f"side flipped {tracker['side']}->{side}" if side_flipped
                      else "level sat empty beyond the reset dwell")
            logger.info("%s: re-baselining level %.4f (%s); the prior resting order is gone.",
                        self.symbol, price, reason)
            self.price_trackers[key] = self._new_tracker(price, side, displayed, ts)
            self._touch(key)
            return

        if displayed > tracker["current_display"] and tracker["cumulative_traded"] > 0:
            tracker["refill_count"] += 1
            tracker["refill_peaks"].append(displayed)

        tracker["current_display"] = displayed
        tracker["last_snapshot_nanos"] = ts
        if displayed == 0:
            # Stamp only the *transition* into empty. Re-stamping on every zero-depth
            # snapshot restarts the dwell clock, so a level receiving empty heartbeats
            # would never accumulate dwell and never re-baseline.
            if tracker["emptied_at_nanos"] is None:
                tracker["emptied_at_nanos"] = ts
        else:
            tracker["emptied_at_nanos"] = None
        self._touch(key)

    def process_trade_print(self, trade: TradePrint) -> Optional[IcebergDetectionReport]:
        """
        Accumulates an execution at its price level and re-evaluates the screen.

        Only volume consumed from the *tracked resting side* accrues to V_cum. A SELL
        aggressor lifts resting bids and a BUY aggressor lifts resting asks, so a print
        on the other side of the level did not come out of the resting order being
        measured: it is recorded separately as ``contra_side_traded_quantity`` and
        penalized in the score rather than counted as hidden size.

        Returns a report on every trade for which the screen is met at this level (see
        ``is_initial_detection`` to distinguish the first), or None.
        """
        context = f"process_trade_print[{self.symbol}]"
        price = _validate_price(trade.price, context)
        quantity = _validate_quantity(trade.quantity, context, "quantity", allow_zero=False)
        aggressor = _validate_side(trade.aggressor_side, AGGRESSOR_SIDES, context, "aggressor_side")
        _validate_timestamp(trade.timestamp_nanos, context)
        if trade.trade_id is not None and not isinstance(trade.trade_id, str):
            raise TypeError(
                f"{context}: trade_id must be a str or None, got {type(trade.trade_id).__name__}.")

        if self._is_duplicate_trade(trade.trade_id):
            logger.warning("%s: ignoring duplicate trade_id %r at %.4f; already accumulated.",
                           self.symbol, trade.trade_id, price)
            return None

        consumed_side = AGGRESSOR_CONSUMES[aggressor]
        key = self._level_key(price)
        tracker = self.price_trackers.get(key)

        if tracker is None:
            # No depth observed at this level yet. Baseline from the print itself and
            # infer the resting side from the aggressor. Q_0 == V_cum here, so the
            # ratio starts at 1.0 and the level cannot flag until real depth or
            # further same-side volume arrives.
            tracker = self._new_tracker(price, consumed_side, quantity, trade.timestamp_nanos)
            tracker["cumulative_traded"] = quantity
            self.price_trackers[key] = tracker
        elif consumed_side == tracker["side"]:
            tracker["cumulative_traded"] += quantity
        else:
            tracker["contra_traded"] += quantity
        self._touch(key)

        initial_q = tracker["initial_display"]
        cum_q = tracker["cumulative_traded"]
        contra_q = tracker["contra_traded"]
        refill_c = tracker["refill_count"]

        if initial_q <= 0:
            # The level was baselined while displaying nothing. V_cum / 0 is not a
            # discrepancy against displayed depth, and treating it as one reports
            # ordinary displayed volume as 100% hidden. Wait for a real baseline.
            logger.debug("%s: level %.4f has no positive display baseline; screen skipped.",
                         self.symbol, price)
            return None

        vol_ratio = cum_q / float(initial_q)
        if vol_ratio < self.min_volume_ratio or refill_c < self.min_refill_count:
            return None

        estimated_hidden = max(0, cum_q - initial_q)
        peaks = list(tracker["refill_peaks"])
        peaks_consistent = _peaks_are_consistent(peaks)

        # Book side is authoritative for classification: the hidden order rests on the
        # side the depth snapshots reported. Deriving it from the aggressor instead
        # lets a single contra-side print invert the signal.
        is_buy_iceberg = tracker["side"] == "BID"
        iceberg_side = "BUY" if is_buy_iceberg else "SELL"
        signal = SIGNAL_BULLISH if is_buy_iceberg else SIGNAL_BEARISH

        confidence = 0.5
        confidence += 0.1 * min(vol_ratio, CONFIDENCE_RATIO_CAP)
        confidence += 0.1 * min(refill_c, CONFIDENCE_REFILL_CAP)
        if contra_q > 0:
            confidence -= CONFIDENCE_PENALTY_CONTRA_FLOW
        if not peaks_consistent:
            confidence -= CONFIDENCE_PENALTY_INCONSISTENT_PEAK
        confidence = round(max(0.0, min(MAX_CONFIDENCE_SCORE, confidence)), 2)

        is_initial = tracker["detections_emitted"] == 0
        tracker["detections_emitted"] += 1

        notes = (
            f"ICEBERG CANDIDATE [{self.symbol} @ {price:.2f}]: {signal} ({iceberg_side} side). "
            f"Baseline display = {initial_q:,}, traded on side = {cum_q:,} ({vol_ratio:.2f}x), "
            f"contra-side = {contra_q:,}. Estimated hidden >= {estimated_hidden:,}, "
            f"refills = {refill_c} (peaks "
            f"{'consistent' if peaks_consistent else 'INCONSISTENT'}), "
            f"heuristic score = {confidence:.2f}. Screen only: price-level depth cannot "
            f"attribute refills to a single resting order."
        )
        logger.info(notes)

        return IcebergDetectionReport(
            symbol=self.symbol,
            detected_price=price,
            iceberg_side=iceberg_side,
            initial_display_quantity=initial_q,
            cumulative_traded_quantity=cum_q,
            estimated_hidden_quantity=estimated_hidden,
            refill_count=refill_c,
            confidence_score=confidence,
            signal_classification=signal,
            audit_notes=notes,
            contra_side_traded_quantity=contra_q,
            refill_peaks_consistent=peaks_consistent,
            observed_refill_peaks=tuple(peaks),
            is_initial_detection=is_initial,
            volume_ratio=round(vol_ratio, 4),
        )
