"""WebSocket feed liveness detection, REST polling failover, handover tick
deduplication, and stream stabilisation for live market data.

What this module decides
------------------------
It answers three questions for one instrument, continuously:

1. **Is the streaming feed alive?** Not "did a trade print recently" -- those
   are different questions, and conflating them is the single most common
   defect in this pattern. See "Liveness is not tick arrival" below.
2. **If it is not, what should feed the strategy instead?** A throttled REST
   poll, until the stream comes back.
3. **When neither source is producing data, does the caller know?** The
   ``BLIND_NO_DATA`` mode exists so that "no new tick" and "no data source at
   all" are never the same observation.

Liveness is not tick arrival
----------------------------
A quiet instrument produces no ticks. A dead socket also produces no ticks.
Degrading on trade silence alone therefore pins an illiquid symbol into
permanent REST polling, which is how a fallback path earns an IP ban.

The transport already carries the liveness signal:

* RFC 6455 Sec. 5.5.2 -- "A Ping frame may serve either as a keepalive or as a
  means to verify that the remote endpoint is still responsive"; Sec. 5.5.3 --
  an unsolicited Pong "serves as a unidirectional heartbeat".
* Binance spot streams send a ping frame every 20 seconds and disconnect if no
  pong is returned within a minute.
* Kite Connect sends a 1-byte heartbeat "every couple seconds" when there is no
  data to stream.

Call :meth:`FeedFallbackManager.on_websocket_heartbeat` from the pong/heartbeat
handler. Silence is then measured against *transport* liveness, and
``silence_timeout_seconds`` is sized from the venue's heartbeat cadence rather
than from how often the instrument trades. TCP alone will not do this for you:
RFC 1122 Sec. 4.2.3.6 requires TCP keep-alives to "default to off" and, when
enabled, to "default to no less than two hours", which is why a silently frozen
socket can hang indefinitely with no ``on_close`` event.

The fallback is lossy, and says so
----------------------------------
A REST quote/ticker endpoint returns a **snapshot**, not the trades that
happened while the stream was down. Every print in the gap is gone. Volume,
VWAP and any trade-count-driven indicator computed across a handover is wrong
unless the caller backfills from a historical-trades endpoint.
:attr:`FeedStatus.last_degradation_gap_seconds` reports the size of that hole
so the caller can backfill or invalidate, rather than silently carrying on.

Deduplication needs identity, not just a watermark
--------------------------------------------------
A strict ``timestamp > watermark`` test discards genuine distinct trades that
share a timestamp, and venue timestamp resolution is coarser than callers
assume:

* Kite Connect binary ticks carry ``last_trade_time`` / ``exchange_timestamp``
  as int32 fields, and the REST quote renders them as ``"YYYY-MM-DD HH:MM:SS"``
  -- one-second resolution. Strict ``>`` throws away every tick after the first
  in each second.
* Alpaca timestamps are RFC-3339 with nanosecond precision; a nanosecond epoch
  does not fit exactly in a float64, so two trades microseconds apart can
  compare equal once converted.

So a tick is accepted when its timestamp is *ahead* of the watermark, or equal
to it with an ``identity`` (exchange trade id / sequence number) not yet seen at
that instant. Pass ``TickPayload.identity`` wherever the venue provides one --
Alpaca's ``i`` field, an ITCH sequence number, an exchange trade id.

This module is illustrative reference code, not a drop-in production service.
Every threshold is an operational default to be calibrated against the venue's
published limits -- see ``references/standards.md``.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
import threading
import time
from typing import Callable, Optional, Set

logger = logging.getLogger(__name__)

# Ceiling on how many tick identities are retained for one watermark instant.
# Reset whenever the watermark advances, so this only binds on a feed that is
# stuck republishing the same timestamp.
_MAX_IDENTITIES_PER_INSTANT = 4096


class FeedMode(str, Enum):
    """Which source is currently expected to feed the strategy."""

    HEALTHY_WEBSOCKET = "HEALTHY_WEBSOCKET"
    DEGRADED_POLLING = "DEGRADED_POLLING"
    #: Degraded *and* the REST fallback is failing. The strategy has no price
    #: source at all. Escalate -- see
    #: ``capital-preservation-mode-for-degraded-conditions``.
    BLIND_NO_DATA = "BLIND_NO_DATA"


@dataclass
class TickPayload:
    """One market data observation.

    Args:
        symbol: Instrument identifier. Must match the manager's symbol; a
            mismatch is a caller routing bug and raises.
        price: Last traded / quoted price. Non-finite or non-positive values are
            rejected as decode defects rather than propagated.
        volume: Traded size carried by this observation. Note that a REST
            snapshot's size field is *not* the volume traded during a feed
            outage; see the module docstring.
        timestamp: **Exchange** timestamp, as epoch seconds. There is
            deliberately no default: defaulting to local receipt time mixes
            clock domains, and because receipt time always leads exchange time
            by the network latency, one defaulted tick pushes the dedup
            watermark into the future and silently suppresses genuine ticks
            until exchange time catches up.
        source: Which path delivered it, set by the manager.
        identity: Venue-supplied unique id for this observation (trade id,
            sequence number). Required to distinguish distinct trades that
            share a coarse timestamp.
    """

    symbol: str
    price: float
    volume: float
    timestamp: float
    source: str = "WEBSOCKET"
    identity: Optional[str] = None


@dataclass
class FeedStatus:
    """Point-in-time snapshot of feed health and handover accounting."""

    symbol: str
    feed_mode: FeedMode
    seconds_since_liveness: float
    last_processed_timestamp: float
    consecutive_ws_ticks: int
    consecutive_poll_failures: int
    #: Silence measured at the moment of the most recent degradation. The trades
    #: printed during this window are not recoverable from a snapshot endpoint.
    last_degradation_gap_seconds: float = 0.0
    degradation_count: int = 0
    duplicate_tick_count: int = 0
    #: Ticks discarded because their timestamp was *behind* the watermark --
    #: genuine data loss, usually a REST snapshot having jumped ahead of
    #: buffered WebSocket ticks. A rising count means the handover is dropping
    #: real prints.
    stale_tick_count: int = 0
    rejected_tick_count: int = 0
    poll_attempt_count: int = 0
    poll_failure_count: int = 0
    throttled_poll_count: int = 0


class FeedFallbackManager:
    """Detects WebSocket feed death, fails over to throttled REST polling,
    deduplicates ticks across the handover, and restores the stream once it is
    demonstrably stable again.

    All public methods are safe to call concurrently from the socket read
    thread, a polling worker and a health-check loop.

    Args:
        symbol: The single instrument this manager tracks.
        silence_timeout_seconds: Transport silence tolerated before degrading.
            Size this from the venue's heartbeat cadence, not from how often the
            instrument trades -- with ``heartbeat_interval_seconds`` supplied,
            a value below twice that cadence is rejected as a misconfiguration.
        required_stabilization_ticks: Consecutive WebSocket observations needed
            to hand back. The run resets if any inter-arrival gap exceeds
            ``silence_timeout_seconds``, so stragglers dribbling in from a still
            broken feed cannot accumulate into a false recovery.
        min_poll_interval_seconds: Floor on the interval between REST calls.
            The published limits are the binding constraint and they are
            per-venue: Kite Connect allows 1 request/second on ``/quote``;
            Alpaca throttles at 200 requests/minute per account; Binance is
            weight-based and escalates repeat offenders from a 2-minute to a
            3-day IP ban. The default of 1.0s is the strictest of those, not a
            universally safe value. Cross-symbol budgeting belongs in
            ``multi-broker-rate-limit-handling``.
        max_consecutive_poll_failures: Failed REST polls before declaring
            ``BLIND_NO_DATA``.
        heartbeat_interval_seconds: The venue's documented heartbeat/ping
            cadence, if it has one. Used only to validate
            ``silence_timeout_seconds``.
        monotonic_clock: Injectable elapsed-time source for deterministic tests.
            Must be monotonic: wall-clock (``time.time``) is subject to NTP
            steps, which can make a measured interval negative and suppress
            degradation entirely.

    Raises:
        ValueError: on non-finite, non-positive or mutually inconsistent
            configuration.
    """

    def __init__(
        self,
        symbol: str,
        silence_timeout_seconds: float = 3.0,
        required_stabilization_ticks: int = 5,
        min_poll_interval_seconds: float = 1.0,
        max_consecutive_poll_failures: int = 3,
        heartbeat_interval_seconds: Optional[float] = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        self.silence_timeout_seconds = self._positive_float(
            silence_timeout_seconds, "silence_timeout_seconds"
        )
        self.min_poll_interval_seconds = self._positive_float(
            min_poll_interval_seconds, "min_poll_interval_seconds"
        )
        if (
            not isinstance(required_stabilization_ticks, int)
            or isinstance(required_stabilization_ticks, bool)
            or required_stabilization_ticks < 1
        ):
            # 0 would make the manager declare recovery on the first straggler
            # tick, which is the failure this counter exists to prevent.
            raise ValueError("required_stabilization_ticks must be an int >= 1")
        if (
            not isinstance(max_consecutive_poll_failures, int)
            or isinstance(max_consecutive_poll_failures, bool)
            or max_consecutive_poll_failures < 1
        ):
            raise ValueError("max_consecutive_poll_failures must be an int >= 1")
        if heartbeat_interval_seconds is not None:
            heartbeat = self._positive_float(
                heartbeat_interval_seconds, "heartbeat_interval_seconds"
            )
            if self.silence_timeout_seconds < 2.0 * heartbeat:
                raise ValueError(
                    "silence_timeout_seconds (%r) must be at least twice the venue "
                    "heartbeat interval (%r); a tighter window degrades on a single "
                    "dropped heartbeat"
                    % (self.silence_timeout_seconds, heartbeat)
                )
        self.symbol = symbol
        self.required_stabilization_ticks = required_stabilization_ticks
        self.max_consecutive_poll_failures = max_consecutive_poll_failures
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._clock = monotonic_clock

        self._lock = threading.RLock()
        self.feed_mode: FeedMode = FeedMode.HEALTHY_WEBSOCKET
        #: Watermark in the **exchange** clock domain.
        self.last_processed_timestamp: float = 0.0
        self._identities_at_watermark: Set[str] = set()
        #: Monotonic instants. Never comparable to ``last_processed_timestamp``.
        self._last_liveness_mono: float = self._clock()
        self._last_ws_tick_mono: float = self._last_liveness_mono
        self._last_poll_mono: Optional[float] = None
        self.consecutive_ws_ticks: int = 0
        self.consecutive_poll_failures: int = 0

        self._last_degradation_gap_seconds: float = 0.0
        self._degradation_count: int = 0
        self._duplicate_tick_count: int = 0
        self._stale_tick_count: int = 0
        self._rejected_tick_count: int = 0
        self._poll_attempt_count: int = 0
        self._poll_failure_count: int = 0
        self._throttled_poll_count: int = 0

    @staticmethod
    def _positive_float(value: float, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("%s must be a number, got %r" % (name, value))
        value = float(value)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("%s must be finite and > 0, got %r" % (name, value))
        return value

    # -- WebSocket path ----------------------------------------------------

    def on_websocket_heartbeat(self) -> None:
        """Records transport-level liveness (a pong, or a venue heartbeat frame).

        Call this from the WebSocket keepalive handler. It proves the socket is
        alive without asserting that a trade occurred, which is what lets an
        illiquid instrument sit quiet without being failed over.
        """
        with self._lock:
            self._last_liveness_mono = self._clock()

    def ingest_websocket_tick(self, tick: TickPayload) -> Optional[TickPayload]:
        """Ingests one WebSocket observation.

        Returns the tick if it is new and should reach the strategy, or ``None``
        if it was a duplicate, arrived behind the dedup watermark, or failed
        validation. Liveness and the stabilisation run are recorded even for
        ticks that are then deduplicated: a repeated tick still proves the
        socket is alive.

        Raises:
            ValueError: if the tick is for a different symbol. That is a caller
                fan-out bug, not a feed condition, and silently accepting it
                would corrupt this instrument's watermark with another
                instrument's prints.
        """
        self._require_symbol(tick)
        with self._lock:
            now = self._clock()
            gap = now - self._last_ws_tick_mono
            self._last_ws_tick_mono = now
            self._last_liveness_mono = now

            if gap > self.silence_timeout_seconds:
                # This tick starts a fresh run: whatever arrived before the gap
                # cannot count towards proving the stream is stable now.
                self.consecutive_ws_ticks = 1
            else:
                self.consecutive_ws_ticks += 1

            if (
                self.feed_mode is not FeedMode.HEALTHY_WEBSOCKET
                and self.consecutive_ws_ticks >= self.required_stabilization_ticks
            ):
                previous = self.feed_mode
                self.feed_mode = FeedMode.HEALTHY_WEBSOCKET
                self.consecutive_poll_failures = 0
                logger.info(
                    "WebSocket feed for %s stabilised after %d consecutive ticks; "
                    "%s -> HEALTHY_WEBSOCKET. Stop the polling worker.",
                    self.symbol,
                    self.consecutive_ws_ticks,
                    previous.value,
                )

            if not self._validate_tick_values(tick):
                return None
            if not self._accept_timestamp(tick, "WebSocket"):
                return None
            return tick

    # -- Health ------------------------------------------------------------

    def check_feed_health(self) -> FeedMode:
        """Evaluates transport silence and degrades if it exceeds the threshold.

        Degradation is evaluated from ``HEALTHY_WEBSOCKET`` only; once degraded,
        the way back is the stabilisation run in
        :meth:`ingest_websocket_tick`, never the mere passage of time.
        """
        with self._lock:
            elapsed = self._clock() - self._last_liveness_mono
            if (
                elapsed > self.silence_timeout_seconds
                and self.feed_mode is FeedMode.HEALTHY_WEBSOCKET
            ):
                self.feed_mode = FeedMode.DEGRADED_POLLING
                self.consecutive_ws_ticks = 0
                self.consecutive_poll_failures = 0
                self._last_degradation_gap_seconds = elapsed
                self._degradation_count += 1
                logger.warning(
                    "WebSocket transport silent for %.3fs (> %.3fs) on %s; degrading to "
                    "REST polling. Trades printed during this window are NOT recoverable "
                    "from a snapshot endpoint -- backfill or invalidate volume-derived "
                    "indicators.",
                    elapsed,
                    self.silence_timeout_seconds,
                    self.symbol,
                )
            return self.feed_mode

    def is_blind(self) -> bool:
        """True when neither the stream nor the REST fallback is delivering data.

        A strategy holding open positions must treat this as a risk event, not
        as a quiet market. Wire it to the capital-preservation gate or kill
        switch: ``None`` from a polling call means "nothing new", which is not
        the same thing.

        ``False`` is not a clean bill of health. It is also ``False`` throughout
        ``DEGRADED_POLLING``, where prices are snapshots and the trade series
        has a hole in it. Read ``get_status().feed_mode`` for the three-way
        distinction before deciding what a strategy is allowed to do.
        """
        with self._lock:
            return self.feed_mode is FeedMode.BLIND_NO_DATA

    def get_status(self) -> FeedStatus:
        """Returns a consistent snapshot of health and handover counters."""
        with self._lock:
            return FeedStatus(
                symbol=self.symbol,
                feed_mode=self.feed_mode,
                seconds_since_liveness=self._clock() - self._last_liveness_mono,
                last_processed_timestamp=self.last_processed_timestamp,
                consecutive_ws_ticks=self.consecutive_ws_ticks,
                consecutive_poll_failures=self.consecutive_poll_failures,
                last_degradation_gap_seconds=self._last_degradation_gap_seconds,
                degradation_count=self._degradation_count,
                duplicate_tick_count=self._duplicate_tick_count,
                stale_tick_count=self._stale_tick_count,
                rejected_tick_count=self._rejected_tick_count,
                poll_attempt_count=self._poll_attempt_count,
                poll_failure_count=self._poll_failure_count,
                throttled_poll_count=self._throttled_poll_count,
            )

    # -- REST fallback path -------------------------------------------------

    def poll_rest_fallback(
        self, rest_fetch_fn: Callable[[str], Optional[TickPayload]]
    ) -> Optional[TickPayload]:
        """Executes one throttled REST poll while the stream is unavailable.

        Returns a new tick, or ``None`` when the feed is healthy, when the call
        was throttled, when it failed, or when the result deduplicated away.
        ``None`` is therefore never evidence that data is flowing --
        :meth:`is_blind` and :meth:`get_status` are.

        The throttle is a local floor only. It knows nothing about other symbols
        sharing the same credentials, and per-symbol polling scales the request
        rate linearly with the universe: at one request per symbol per second,
        four symbols already exceed Alpaca's 200/minute account budget. Every
        venue in ``references/standards.md`` offers a batch endpoint (Kite
        ``/quote`` takes 500 instruments, Alpaca ``/v2/stocks/trades/latest``
        takes a ``symbols`` list, Binance ``/api/v3/ticker/price`` returns every
        symbol for weight 4) -- batch there and dispatch here.
        """
        if not callable(rest_fetch_fn):
            raise ValueError("rest_fetch_fn must be callable")
        self.check_feed_health()
        with self._lock:
            if self.feed_mode is FeedMode.HEALTHY_WEBSOCKET:
                return None
            now = self._clock()
            if (
                self._last_poll_mono is not None
                and now - self._last_poll_mono < self.min_poll_interval_seconds
            ):
                # Throttled, not failed: this must not count towards the blind
                # threshold, or a caller polling in a tight loop would declare
                # itself blind while the fallback is working perfectly.
                self._throttled_poll_count += 1
                return None
            self._last_poll_mono = now
            self._poll_attempt_count += 1

        # Deliberately outside the lock: a blocking HTTP call must not hold the
        # socket read thread out of ingest_websocket_tick.
        try:
            polled_tick = rest_fetch_fn(self.symbol)
        except Exception as exc:  # noqa: BLE001 - a failing fallback must not kill the loop
            self._record_poll_failure("%s: %s" % (type(exc).__name__, exc))
            return None

        if polled_tick is None:
            self._record_poll_failure("fetch returned no quote")
            return None
        self._require_symbol(polled_tick)

        with self._lock:
            if not self._validate_tick_values(polled_tick):
                self._record_poll_failure_locked("non-finite or non-positive quote")
                return None

            self.consecutive_poll_failures = 0
            if self.feed_mode is FeedMode.BLIND_NO_DATA:
                self.feed_mode = FeedMode.DEGRADED_POLLING
                logger.warning(
                    "REST fallback for %s recovered; BLIND_NO_DATA -> DEGRADED_POLLING.",
                    self.symbol,
                )

            polled_tick.source = "REST_POLLING"
            if not self._accept_timestamp(polled_tick, "REST"):
                # A snapshot endpoint repeats the last trade until a new one
                # prints, so this is the normal steady state on a quiet
                # instrument, not a fault.
                return None
            logger.info(
                "REST polled tick accepted for %s at %s (ts=%s)",
                self.symbol,
                polled_tick.price,
                polled_tick.timestamp,
            )
            return polled_tick

    def _record_poll_failure(self, reason: str) -> None:
        with self._lock:
            self._record_poll_failure_locked(reason)

    def _record_poll_failure_locked(self, reason: str) -> None:
        """Caller holds the lock."""
        self._poll_failure_count += 1
        self.consecutive_poll_failures += 1
        logger.error(
            "REST fallback poll failed for %s (%d consecutive): %s",
            self.symbol,
            self.consecutive_poll_failures,
            reason,
        )
        if (
            self.consecutive_poll_failures >= self.max_consecutive_poll_failures
            and self.feed_mode is not FeedMode.BLIND_NO_DATA
        ):
            self.feed_mode = FeedMode.BLIND_NO_DATA
            logger.critical(
                "NO PRICE SOURCE for %s: stream silent and REST fallback failed %d times. "
                "Escalate to the capital-preservation gate or kill switch; open positions "
                "are unmonitored.",
                self.symbol,
                self.consecutive_poll_failures,
            )

    # -- Validation and deduplication --------------------------------------

    def _require_symbol(self, tick: TickPayload) -> None:
        if not isinstance(tick, TickPayload):
            raise ValueError("expected a TickPayload, got %r" % (type(tick).__name__,))
        if tick.symbol != self.symbol:
            raise ValueError(
                "tick for %r routed to the %r manager: fan-out bug. Accepting it "
                "would advance this instrument's dedup watermark with another "
                "instrument's prints." % (tick.symbol, self.symbol)
            )

    def _validate_tick_values(self, tick: TickPayload) -> bool:
        """Rejects wire-level decode defects without killing the read loop.

        A ``None`` timestamp is a real case, not a hypothetical: Kite Connect's
        REST quote documents ``last_trade_time`` as nullable, so an instrument
        that has not traded yet yields a quote with no trade time to
        deduplicate on. Caller holds the lock.
        """
        problem: Optional[str] = None
        if tick.timestamp is None or not isinstance(tick.timestamp, (int, float)):
            problem = "missing or non-numeric timestamp"
        elif not math.isfinite(float(tick.timestamp)):
            problem = "non-finite timestamp"
        elif not isinstance(tick.price, (int, float)) or not math.isfinite(
            float(tick.price)
        ):
            problem = "non-finite price"
        elif float(tick.price) <= 0.0:
            problem = "non-positive price"
        elif not isinstance(tick.volume, (int, float)) or not math.isfinite(
            float(tick.volume)
        ):
            problem = "non-finite volume"
        if problem is None:
            return True
        self._rejected_tick_count += 1
        logger.warning(
            "Rejected %s tick for %s: %s (price=%r ts=%r)",
            tick.source,
            self.symbol,
            problem,
            tick.price,
            tick.timestamp,
        )
        return False

    def _accept_timestamp(self, tick: TickPayload, origin: str) -> bool:
        """Applies identity-aware watermark deduplication. Caller holds the lock."""
        timestamp = float(tick.timestamp)
        if timestamp > self.last_processed_timestamp:
            self.last_processed_timestamp = timestamp
            self._identities_at_watermark = (
                {tick.identity} if tick.identity is not None else set()
            )
            return True

        if timestamp == self.last_processed_timestamp:
            if tick.identity is None:
                # No way to tell a genuine same-instant trade from a repeat of
                # the one already processed. Dropping is the safe direction:
                # re-feeding a duplicate print corrupts volume and trade counts.
                self._duplicate_tick_count += 1
                logger.debug(
                    "%s tick for %s at watermark %s carries no identity; treated as a "
                    "duplicate. Supply TickPayload.identity to keep same-instant trades.",
                    origin,
                    self.symbol,
                    timestamp,
                )
                return False
            if tick.identity in self._identities_at_watermark:
                self._duplicate_tick_count += 1
                return False
            if len(self._identities_at_watermark) >= _MAX_IDENTITIES_PER_INSTANT:
                self._duplicate_tick_count += 1
                logger.warning(
                    "Identity set for %s at timestamp %s reached %d entries; dropping. "
                    "The feed is republishing one instant -- check the timestamp field.",
                    self.symbol,
                    timestamp,
                    _MAX_IDENTITIES_PER_INSTANT,
                )
                return False
            self._identities_at_watermark.add(tick.identity)
            return True

        # timestamp < watermark: a real observation arriving behind the
        # watermark, typically buffered WebSocket ticks surfacing after a REST
        # snapshot jumped ahead of them. Counted because it is data loss, not
        # noise.
        self._stale_tick_count += 1
        logger.debug(
            "%s tick for %s dropped: ts %s is behind watermark %s.",
            origin,
            self.symbol,
            timestamp,
            self.last_processed_timestamp,
        )
        return False
