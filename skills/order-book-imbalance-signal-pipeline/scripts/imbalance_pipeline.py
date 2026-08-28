"""Fast-path order book imbalance (OBI) and weighted mid-price signal engine.

Computes the signed top-of-book queue imbalance

    I = (V_bid - V_ask) / (V_bid + V_ask)          I in [-1, +1]

and the imbalance-weighted mid-price

    W = (V_bid * P_ask + V_ask * P_bid) / (V_bid + V_ask)

then classifies I against a calibrated threshold and dispatches non-neutral
signals to a strategy callback.

Naming: ``W`` above is the **weighted mid-price**, which is what this module
computes and what industry shorthand loosely calls "the micro-price". Stoikov's
micro-price is a different object -- ``P_micro = M + g(I, S)``, a calibrated
estimator of the future mid conditional on imbalance and spread -- and is not
computed here. The weighted mid is explicitly not a martingale.

The two outputs are not independent. Writing ``M`` for the mid and ``s`` for the
spread, ``W - M == I_top * s / 2`` identically, so "weighted-mid divergence from
mid" carries no information beyond the top-of-book imbalance and the spread. The
identity is used here as a correctness invariant, never as a second signal.

Fail-closed contract: this engine sits between a market data feed and an
execution worker, so a book it cannot trust must never leave here looking like a
tradeable signal. Non-finite or negative volumes, non-positive prices, a crossed
or locked book, a regressing timestamp, and insufficient depth all produce an
``UNRELIABLE`` result whose numeric fields are ``None`` -- not a zero, not a
``NaN``, and never a directional signal. ``strict=True`` raises instead.

Latency: this is CPython. On the reference platform used to write this module
(CPython 3.11, Windows 11, commodity laptop) a full ``process_l2_update`` call
measured about 1.9 microseconds in a warm loop, against about 1.4 microseconds
for an unvalidated version of the same arithmetic -- roughly 0.4 microseconds
buys the fail-closed checks above. Sub-microsecond OBI generation is a
C++/Rust/FPGA result, not a Python one -- see
``binary-protocol-parsing-for-low-latency-feeds``. This module is a reference and
research implementation whose value is correctness under bad data.
"""

import logging
import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Rejection kinds recorded in FastPathOBIPipelineEngine.rejections_by_kind.
REJECTION_KINDS: Tuple[str, ...] = (
    "INVALID_VOLUME",
    "INVALID_PRICE",
    "CROSSED_OR_LOCKED_BOOK",
    "EMPTY_BOOK",
    "INVALID_TIMESTAMP",
    "TIMESTAMP_REGRESSION",
    "INSUFFICIENT_DEPTH",
    "MALFORMED_DEPTH",
)


class OBIConfigurationError(ValueError):
    """Raised when the engine is constructed with unusable parameters."""


class OBIValidationError(ValueError):
    """Raised in strict mode when an update cannot produce a trustworthy signal."""


class ImbalanceSignalType(Enum):
    """Classification of one L2 update.

    ``UNRELIABLE`` is distinct from ``NEUTRAL`` on purpose: ``NEUTRAL`` means the
    imbalance was measured and sits inside the threshold band, ``UNRELIABLE``
    means no imbalance could be trusted from this update at all.
    """

    NEUTRAL = "NEUTRAL"
    HIGH_BUY_PRESSURE = "HIGH_BUY_PRESSURE"
    HIGH_SELL_PRESSURE = "HIGH_SELL_PRESSURE"
    UNRELIABLE = "UNRELIABLE"


@dataclass
class L2OrderBookTop:
    """One L2 book update.

    The top-of-book fields are authoritative. ``bid_depth`` / ``ask_depth`` carry
    the levels *behind* the touch as ``(price, volume)`` pairs ordered outward --
    level 2 first. Level 1 must **not** be repeated there; passing a full ladder
    that starts at the touch double-counts the best queue and is rejected as
    ``MALFORMED_DEPTH``.
    """

    symbol: str
    bid_price: float
    bid_volume: float
    ask_price: float
    ask_volume: float
    timestamp_ns: int
    bid_depth: Optional[Sequence[Tuple[float, float]]] = None
    ask_depth: Optional[Sequence[Tuple[float, float]]] = None


@dataclass
class OBISignalResult:
    """Outcome of one update.

    On an ``UNRELIABLE`` result every numeric field is ``None`` and
    ``rejection_reason`` names the kind. Consumers must branch on
    ``signal_type``; a ``None`` propagated into arithmetic raises, which is the
    intended failure mode. A zero or ``NaN`` sentinel would instead reach the
    execution worker as a plausible price.
    """

    symbol: str
    timestamp_ns: int
    imbalance: Optional[float]            # signed, [-1.0, +1.0], over levels_used
    weighted_mid_price: Optional[float]   # top-of-book only
    mid_price: Optional[float]            # top-of-book only
    spread: Optional[float]
    signal_type: ImbalanceSignalType
    levels_used: int
    calculation_latency_ns: int
    rejection_reason: Optional[str] = None

    @property
    def is_actionable(self) -> bool:
        """True only for a directional signal from a book that validated."""
        return self.signal_type in (
            ImbalanceSignalType.HIGH_BUY_PRESSURE,
            ImbalanceSignalType.HIGH_SELL_PRESSURE,
        )


@dataclass
class OBIPipelineReport:
    """Audit summary of a pipeline run."""

    updates_processed: int
    signals_emitted: int
    rejected_updates: int
    rejections_by_kind: Dict[str, int]
    callback_errors: int
    status: str
    notes: str


class FastPathOBIPipelineEngine:
    """Order book imbalance signal pipeline with fail-closed input validation.

    The engine is single-threaded by contract: it holds per-symbol timestamp
    state and performs no locking. Shard by symbol across instances, or
    synchronise upstream -- see ``order-book-depth-processing-l2-l3``.
    """

    def __init__(
        self,
        imbalance_threshold: float = 0.60,
        signal_callback: Optional[Callable[["OBISignalResult"], None]] = None,
        depth_levels: int = 1,
        strict: bool = False,
        allow_locked_book: bool = False,
    ) -> None:
        """
        Args:
            imbalance_threshold: absolute |I| at or above which a directional
                signal is emitted. Must lie in (0.0, 1.0]. There is no universal
                value -- the predictive strength of queue imbalance is tick-size
                dependent, so calibrate per instrument.
            signal_callback: invoked for each actionable signal. Exceptions raised
                by it are caught and counted; they never propagate into the feed
                loop.
            depth_levels: number of price levels per side aggregated into the
                imbalance. 1 is top-of-book. An update carrying fewer levels is
                rejected rather than silently downgraded, so the signal keeps one
                fixed meaning across ticks.
            strict: raise ``OBIValidationError`` on the first untrustworthy update
                instead of returning an ``UNRELIABLE`` result. This governs input
                validation only -- a failing ``signal_callback`` is still caught
                and counted, because a strategy bug is not a reason to stop
                processing market data for every other symbol.
            allow_locked_book: accept a locked book (bid == ask), which occurs
                legitimately on venues without a locked-market prohibition. A
                crossed book (bid > ask) is always rejected.

        Raises:
            OBIConfigurationError: on an unusable threshold or depth setting.
        """
        if isinstance(imbalance_threshold, bool) or not isinstance(
            imbalance_threshold, (int, float)
        ):
            raise OBIConfigurationError(
                "imbalance_threshold must be a real number, got "
                f"{type(imbalance_threshold).__name__}."
            )
        threshold = float(imbalance_threshold)
        if not math.isfinite(threshold) or not 0.0 < threshold <= 1.0:
            # A threshold of 0.0 classifies a perfectly balanced book as
            # HIGH_BUY_PRESSURE (0.0 >= 0.0); a negative one fires on every tick.
            raise OBIConfigurationError(
                f"imbalance_threshold must lie in (0.0, 1.0], got {imbalance_threshold!r}. "
                "Zero or below emits a buy signal on every update; above 1.0 can "
                "never trigger."
            )
        if isinstance(depth_levels, bool) or not isinstance(depth_levels, int) or depth_levels < 1:
            raise OBIConfigurationError(
                f"depth_levels must be an integer >= 1, got {depth_levels!r}."
            )

        self.imbalance_threshold: float = threshold
        self.signal_callback = signal_callback
        self.depth_levels: int = depth_levels
        self.strict: bool = strict
        self.allow_locked_book: bool = allow_locked_book

        self.updates_processed: int = 0
        self.total_signals_emitted: int = 0
        self.rejected_update_count: int = 0
        self.callback_error_count: int = 0
        self.rejections_by_kind: Dict[str, int] = {kind: 0 for kind in REJECTION_KINDS}
        self._last_timestamp_ns: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_volume(value: object) -> bool:
        """A queue size must be a finite, non-negative real number."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(value) and value >= 0.0

    @staticmethod
    def _is_valid_price(value: object) -> bool:
        """A displayed price must be finite and strictly positive."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(value) and value > 0.0

    def _validate_depth(
        self,
        levels: Optional[Sequence[Tuple[float, float]]],
        side: str,
        touch_price: float,
    ) -> Optional[str]:
        """Check the levels behind the touch. Returns a rejection kind or None.

        Prices must move monotonically away from the touch: strictly descending
        for bids, strictly ascending for asks. A first level equal to the touch is
        the signature of a caller passing the full ladder including level 1, which
        would double-count the best queue.
        """
        if levels is None:
            return None
        if not isinstance(levels, (list, tuple)):
            # A generator would be consumed by this scan and then fail the later
            # length check with an unhandled TypeError, taking the feed loop with
            # it. Require a concrete, re-readable sequence.
            return "MALFORMED_DEPTH"
        previous = touch_price
        for entry in levels:
            if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                return "MALFORMED_DEPTH"
            price, volume = entry
            if not self._is_valid_price(price):
                return "INVALID_PRICE"
            if not self._is_valid_volume(volume):
                return "INVALID_VOLUME"
            if side == "bid" and not price < previous:
                return "MALFORMED_DEPTH"
            if side == "ask" and not price > previous:
                return "MALFORMED_DEPTH"
            previous = price
        return None

    def _reject(
        self,
        book_top: L2OrderBookTop,
        kind: str,
        detail: str,
        latency_ns: int,
    ) -> OBISignalResult:
        """Record a rejection and return UNRELIABLE, or raise in strict mode."""
        if kind not in self.rejections_by_kind:
            raise KeyError(f"Unknown rejection kind: {kind!r}")
        self.rejected_update_count += 1
        self.rejections_by_kind[kind] += 1
        if self.strict:
            raise OBIValidationError(
                f"OBI update rejected [{book_top.symbol}] {kind}: {detail}"
            )
        logger.warning(
            "OBI update rejected [%s] %s: %s", book_top.symbol, kind, detail
        )
        return OBISignalResult(
            symbol=book_top.symbol,
            timestamp_ns=book_top.timestamp_ns,
            imbalance=None,
            weighted_mid_price=None,
            mid_price=None,
            spread=None,
            signal_type=ImbalanceSignalType.UNRELIABLE,
            levels_used=0,
            calculation_latency_ns=latency_ns,
            rejection_reason=kind,
        )

    # ------------------------------------------------------------------
    # Fast path
    # ------------------------------------------------------------------

    def process_l2_update(self, book_top: L2OrderBookTop) -> OBISignalResult:
        """Validate one L2 update, compute the imbalance, classify and dispatch.

        ``calculation_latency_ns`` times the validation, arithmetic and
        classification span only. It excludes result construction, logging and
        callback dispatch, and it is floor-limited by the platform clock -- a
        100 ns granularity on the reference platform. Read it as a relative
        regression tripwire, never as a tick-to-trade measurement; that belongs to
        ``tick-to-trade-latency-measurement``.

        Returns:
            An ``OBISignalResult``. ``UNRELIABLE`` results carry ``None`` numerics
            and a ``rejection_reason``.

        Raises:
            OBIValidationError: in strict mode, on any untrustworthy update.
        """
        t_start = time.perf_counter_ns()
        self.updates_processed += 1

        p_bid = book_top.bid_price
        p_ask = book_top.ask_price
        v_bid = book_top.bid_volume
        v_ask = book_top.ask_volume

        # Order matters: reject non-finite and negative inputs before any
        # arithmetic. A NaN volume survives a `total <= 0.0` guard (every
        # comparison with NaN is False), so an unguarded fast path yields a NaN
        # imbalance, a NaN weighted mid and a NEUTRAL classification -- silently,
        # on a tick that carried no information at all. A negative volume is worse:
        # it stays finite and drives |I| outside [-1, +1] (bid -100 against ask 200
        # gives I = -3.0), which classifies as a full-strength directional signal.
        if not (self._is_valid_volume(v_bid) and self._is_valid_volume(v_ask)):
            return self._reject(
                book_top,
                "INVALID_VOLUME",
                f"bid_volume={v_bid!r} ask_volume={v_ask!r} (must be finite and >= 0)",
                time.perf_counter_ns() - t_start,
            )
        if not (self._is_valid_price(p_bid) and self._is_valid_price(p_ask)):
            return self._reject(
                book_top,
                "INVALID_PRICE",
                f"bid_price={p_bid!r} ask_price={p_ask!r} (must be finite and > 0)",
                time.perf_counter_ns() - t_start,
            )

        # A crossed book means the two sides came from different points in time.
        # The imbalance stays arithmetically well defined and becomes economically
        # meaningless, so it must not be signed off as a signal.
        if p_bid > p_ask or (p_bid == p_ask and not self.allow_locked_book):
            return self._reject(
                book_top,
                "CROSSED_OR_LOCKED_BOOK",
                f"bid={p_bid} ask={p_ask}; the two sides are not synchronised",
                time.perf_counter_ns() - t_start,
            )

        # The timestamp must be a real integer nanosecond count before it can
        # order anything. A float NaN slipped in here would be accepted (every
        # comparison against NaN is False), stored, and would then disable the
        # regression check for the following update as well.
        if isinstance(book_top.timestamp_ns, bool) or not isinstance(
            book_top.timestamp_ns, int
        ) or book_top.timestamp_ns < 0:
            return self._reject(
                book_top,
                "INVALID_TIMESTAMP",
                f"timestamp_ns={book_top.timestamp_ns!r} (must be a non-negative int)",
                time.perf_counter_ns() - t_start,
            )

        # Timestamps are compared within one symbol's own feed clock domain. This
        # detects out-of-order delivery only; wall-clock staleness needs a
        # synchronised host clock -- see clock-skew-correction-for-tick-timestamps.
        last_ts = self._last_timestamp_ns.get(book_top.symbol)
        if last_ts is not None and book_top.timestamp_ns < last_ts:
            return self._reject(
                book_top,
                "TIMESTAMP_REGRESSION",
                f"timestamp_ns={book_top.timestamp_ns} precedes last seen {last_ts}",
                time.perf_counter_ns() - t_start,
            )

        bid_kind = self._validate_depth(book_top.bid_depth, "bid", p_bid)
        if bid_kind is not None:
            return self._reject(
                book_top,
                bid_kind,
                f"bid_depth={book_top.bid_depth!r} is not a valid ladder below "
                f"bid_price={p_bid} (level 1 must not be repeated)",
                time.perf_counter_ns() - t_start,
            )
        ask_kind = self._validate_depth(book_top.ask_depth, "ask", p_ask)
        if ask_kind is not None:
            return self._reject(
                book_top,
                ask_kind,
                f"ask_depth={book_top.ask_depth!r} is not a valid ladder above "
                f"ask_price={p_ask} (level 1 must not be repeated)",
                time.perf_counter_ns() - t_start,
            )

        # Aggregate exactly self.depth_levels levels per side. Falling back to a
        # shallower book on a thin tick would silently change what the signal
        # measures from one update to the next.
        extra_levels = self.depth_levels - 1
        if extra_levels:
            bid_extra = book_top.bid_depth or ()
            ask_extra = book_top.ask_depth or ()
            if len(bid_extra) < extra_levels or len(ask_extra) < extra_levels:
                return self._reject(
                    book_top,
                    "INSUFFICIENT_DEPTH",
                    f"depth_levels={self.depth_levels} requires {extra_levels} level(s) "
                    f"behind the touch on each side; got bid={len(bid_extra)} "
                    f"ask={len(ask_extra)}",
                    time.perf_counter_ns() - t_start,
                )
            total_bid = v_bid + math.fsum(vol for _, vol in bid_extra[:extra_levels])
            total_ask = v_ask + math.fsum(vol for _, vol in ask_extra[:extra_levels])
        else:
            total_bid = v_bid
            total_ask = v_ask

        total_volume = total_bid + total_ask
        if total_volume <= 0.0:
            # No resting size anywhere in the aggregated window. That is an absence
            # of data, not a balanced book, so it must not read NEUTRAL.
            return self._reject(
                book_top,
                "EMPTY_BOOK",
                f"aggregated volume is zero over {self.depth_levels} level(s)",
                time.perf_counter_ns() - t_start,
            )

        imbalance = (total_bid - total_ask) / total_volume

        # Top-of-book only: the weighted mid is defined on the touch prices.
        # W = I_top * P_ask + (1 - I_top) * P_bid with I_top = V_bid / (V_bid + V_ask),
        # i.e. a heavy bid queue pulls the fair price towards the ask.
        top_volume = v_bid + v_ask
        if top_volume > 0.0:
            weighted_mid: Optional[float] = ((v_bid * p_ask) + (v_ask * p_bid)) / top_volume
        else:
            # Both touch queues empty while depth behind them is not: no weighted
            # mid exists, though the aggregated imbalance still does.
            weighted_mid = None
        mid_price = (p_bid + p_ask) / 2.0
        spread = p_ask - p_bid

        # Classify on the same float that is reported. Rounding the reported value
        # while classifying the raw one lets a result read imbalance == 0.60
        # alongside signal_type == NEUTRAL.
        if imbalance >= self.imbalance_threshold:
            signal_type = ImbalanceSignalType.HIGH_BUY_PRESSURE
        elif imbalance <= -self.imbalance_threshold:
            signal_type = ImbalanceSignalType.HIGH_SELL_PRESSURE
        else:
            signal_type = ImbalanceSignalType.NEUTRAL

        latency_ns = time.perf_counter_ns() - t_start

        self._last_timestamp_ns[book_top.symbol] = book_top.timestamp_ns

        result = OBISignalResult(
            symbol=book_top.symbol,
            timestamp_ns=book_top.timestamp_ns,
            imbalance=imbalance,
            weighted_mid_price=weighted_mid,
            mid_price=mid_price,
            spread=spread,
            signal_type=signal_type,
            levels_used=self.depth_levels,
            calculation_latency_ns=latency_ns,
        )

        if result.is_actionable:
            self.total_signals_emitted += 1
            # Lazy %-formatting: the argument tuple is only rendered when INFO is
            # enabled, so a disabled logger costs one level check on the hot path.
            logger.info(
                "OBI signal [%s]: %s I=%+.4f levels=%d weighted_mid=%s mid=%.4f (calc %d ns)",
                book_top.symbol,
                signal_type.value,
                imbalance,
                self.depth_levels,
                "n/a" if weighted_mid is None else format(weighted_mid, ".4f"),
                mid_price,
                latency_ns,
            )
            self._dispatch(result)

        return result

    def _dispatch(self, result: OBISignalResult) -> None:
        """Hand the signal to the strategy callback without risking the feed loop.

        A strategy that raises must not take the market data thread down with it:
        the next tick still has to be processed, and the failure has to be visible
        rather than fatal. ``BaseException`` is deliberately not caught, so
        ``KeyboardInterrupt`` and interpreter shutdown still work.
        """
        if self.signal_callback is None:
            return
        try:
            self.signal_callback(result)
        except Exception:
            self.callback_error_count += 1
            logger.exception(
                "OBI signal callback failed for %s at %d; signal not delivered.",
                result.symbol,
                result.timestamp_ns,
            )

    def generate_report(self) -> OBIPipelineReport:
        """Summarise the run. Status is clean only when nothing was rejected."""
        clean = self.rejected_update_count == 0 and self.callback_error_count == 0
        if clean:
            status = "OBI_PIPELINE_CLEAN"
            notes = (
                f"{self.updates_processed} update(s) processed, "
                f"{self.total_signals_emitted} signal(s) emitted, no rejections."
            )
        else:
            status = "OBI_PIPELINE_DEGRADED"
            breakdown = ", ".join(
                f"{kind}={count}"
                for kind, count in self.rejections_by_kind.items()
                if count
            ) or "none"
            notes = (
                f"{self.rejected_update_count} of {self.updates_processed} update(s) "
                f"rejected ({breakdown}); {self.callback_error_count} callback error(s). "
                "Any imbalance statistic aggregated over this run is computed on a "
                "partial sample."
            )
        logger.info("%s: %s", status, notes)
        return OBIPipelineReport(
            updates_processed=self.updates_processed,
            signals_emitted=self.total_signals_emitted,
            rejected_updates=self.rejected_update_count,
            rejections_by_kind=dict(self.rejections_by_kind),
            callback_errors=self.callback_error_count,
            status=status,
            notes=notes,
        )
