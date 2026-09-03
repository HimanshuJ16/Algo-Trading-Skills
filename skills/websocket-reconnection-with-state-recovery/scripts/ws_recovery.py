"""
websocket-reconnection-with-state-recovery: connection lifecycle state machine, bounded
jittered exponential backoff, symbol re-subscription, and sequence-gap recovery for
market-data and order-update WebSocket streams.

Design contract
---------------
* Backoff is **bounded by** ``max_backoff_sec``. Jitter is drawn *inside* the cap
  (AWS "Full Jitter": ``sleep = random(0, min(cap, base * 2**attempt))``), never added
  on top of it.
* Sequence recovery is **fail-closed**. A gap that cannot be provably filled latches the
  symbol as unsynchronised and withholds every subsequent message for it until the caller
  re-snapshots and calls :meth:`WebSocketStateRecoveryManager.resynchronize`. Emitting
  post-gap messages into an order book that has a hole in it is the failure this module
  exists to prevent.
* A REST fill is only accepted when it covers the missing range **exactly** - same symbol,
  contiguous, ascending, no more and no less. A partial fill is a failed fill.

This module is a correctness reference (CPython, standard library only), not a colocated
feed handler.
"""
from collections import deque
from dataclasses import dataclass
import logging
import random
import threading
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# 1.0 * 2**30 already exceeds any sane backoff ceiling by nine orders of magnitude.
# Capping the exponent keeps `base * 2**attempt` off the float-conversion overflow that
# an unbounded attempt counter reaches after ~1024 reconnects.
_MAX_BACKOFF_EXPONENT = 30


class ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATED = "AUTHENTICATED"
    SUBSCRIBED = "SUBSCRIBED"
    RECOVERING_GAP = "RECOVERING_GAP"
    STREAMING = "STREAMING"


@dataclass(frozen=True)
class SequenceGap:
    """An inclusive range of sequence ids observed to be missing on one symbol."""

    symbol: str
    first_missing: int
    last_missing: int
    reason: str = ""

    @property
    def size(self) -> int:
        return self.last_missing - self.first_missing + 1


@dataclass
class WSMessage:
    symbol: str
    sequence_id: int
    data: Dict[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("WSMessage.symbol must be a non-empty string")
        if isinstance(self.sequence_id, bool) or not isinstance(self.sequence_id, int):
            raise TypeError("WSMessage.sequence_id must be an int")
        if self.sequence_id < 0:
            raise ValueError("WSMessage.sequence_id must be non-negative")


class WebSocketStateRecoveryManager:
    """
    Manages WebSocket lifecycle transitions, bounded jittered backoff, symbol channel
    re-subscription, and fail-closed sequence gap recovery.

    Thread safety: every public method holds one re-entrant lock, so a socket read thread
    and a health/reconnect thread may share one instance. ``rest_gap_fill_fn`` is invoked
    **while that lock is held** - give it its own network timeout, or a hung HTTP call
    freezes ingestion for every symbol.
    """

    def __init__(
        self,
        base_backoff_sec: float = 1.0,
        max_backoff_sec: float = 30.0,
        jitter_factor: float = 1.0,
        rest_gap_fill_fn: Optional[Callable[[str, int, int], Sequence[WSMessage]]] = None,
        max_gap_fill_size: int = 1000,
        requires_auth: bool = False,
        max_retained_messages: int = 10_000,
        rng: Optional[random.Random] = None,
    ):
        """
        Args:
            base_backoff_sec: Delay ceiling for the *first* retry, in seconds.
            max_backoff_sec: Hard ceiling for every retry delay. Never exceeded.
            jitter_factor: Fraction of the capped delay that is randomised.
                ``1.0`` reproduces AWS "Full Jitter" (``random(0, capped)``),
                ``0.5`` reproduces "Equal Jitter" (``capped/2 + random(0, capped/2)``),
                ``0.0`` disables jitter. Must lie in ``[0.0, 1.0]``.
            rest_gap_fill_fn: ``(symbol, first_missing, last_missing) -> messages``.
                Must return the missing range exactly, or the fill is rejected. Omit it
                when the venue exposes no id-addressable history endpoint; the manager
                then only reports that the stream is broken.
            max_gap_fill_size: Largest gap this manager will attempt to REST-fill.
                Venue history endpoints are paged (Binance ``GET /api/v3/aggTrades``
                returns at most 1000 records per call), so an outage-sized hole must
                escalate to a re-snapshot rather than to an unbounded refetch loop.
            requires_auth: True for private/order-update streams, which pass through
                ``AUTHENTICATED`` on the way to ``SUBSCRIBED``.
            max_retained_messages: Bound on ``processed_messages``. ``0`` disables
                retention entirely; an unbounded list is a leak in a 24/7 feed handler.
            rng: Injectable randomness, so backoff is reproducible under test.
        """
        if not base_backoff_sec > 0:
            raise ValueError("base_backoff_sec must be > 0")
        if max_backoff_sec < base_backoff_sec:
            raise ValueError("max_backoff_sec must be >= base_backoff_sec")
        if not 0.0 <= jitter_factor <= 1.0:
            raise ValueError("jitter_factor must lie in [0.0, 1.0]")
        if max_gap_fill_size < 1:
            raise ValueError("max_gap_fill_size must be >= 1")
        if max_retained_messages < 0:
            raise ValueError("max_retained_messages must be >= 0")

        self.state = ConnectionState.DISCONNECTED
        self.state_history: Deque[ConnectionState] = deque([ConnectionState.DISCONNECTED], maxlen=64)
        self.base_backoff_sec = base_backoff_sec
        self.max_backoff_sec = max_backoff_sec
        self.jitter_factor = jitter_factor
        self.max_gap_fill_size = max_gap_fill_size
        self.requires_auth = requires_auth
        self._rest_gap_fill_fn = rest_gap_fill_fn
        self._rng = rng if rng is not None else random.Random()
        self._lock = threading.RLock()

        self.reconnect_attempts = 0
        self.subscribed_symbols: Set[str] = set()
        self.last_seen_sequence: Dict[str, int] = {}
        # maxlen=0 makes retention a no-op without a second flag to keep in sync.
        self.processed_messages: Deque[WSMessage] = deque(maxlen=max_retained_messages)

        self._unsynced: Dict[str, SequenceGap] = {}
        self.duplicate_message_count = 0
        self.withheld_message_count = 0
        self.gap_fill_success_count = 0
        self.gap_fill_failure_count = 0

    # ------------------------------------------------------------- state machine

    def _set_state(self, new_state: ConnectionState) -> None:
        """Records every transition so the documented state machine is auditable."""
        self.state = new_state
        if not self.state_history or self.state_history[-1] is not new_state:
            self.state_history.append(new_state)

    # ------------------------------------------------------------------ backoff

    def compute_next_backoff(self, attempt: Optional[int] = None) -> float:
        """
        Delay for retry number ``attempt`` (0-based), bounded by ``max_backoff_sec``.

        ``capped = min(max_backoff_sec, base_backoff_sec * 2**attempt)``, then jitter is
        drawn *within* ``capped``. Attempt 0 therefore yields a delay in
        ``[0, base_backoff_sec]`` under full jitter - the base delay is a ceiling for the
        first retry, not a floor added on top of it.
        """
        with self._lock:
            k = self.reconnect_attempts if attempt is None else attempt
            if k < 0:
                raise ValueError("attempt must be >= 0")
            exponent = min(k, _MAX_BACKOFF_EXPONENT)
            capped = min(self.max_backoff_sec, self.base_backoff_sec * (2 ** exponent))
            fixed = capped * (1.0 - self.jitter_factor)
            return fixed + self._rng.uniform(0.0, capped - fixed)

    # ------------------------------------------------------- connection lifecycle

    def register_symbol_subscription(self, symbol: str) -> None:
        """Registers a symbol to be re-subscribed on every reconnect."""
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        with self._lock:
            self.subscribed_symbols.add(symbol.strip().upper())

    def on_connection_lost(self, reason: str = "Network Disconnect", scheduled: bool = False) -> float:
        """
        Transitions to ``DISCONNECTED`` and returns the delay to wait before reconnecting.

        Args:
            reason: Free-text cause, logged.
            scheduled: True for an *expected* rotation - Binance documents that a single
                connection "is only valid for 24 hours" - which must not escalate the
                failure backoff. The delay is still jittered, because every client that
                connected in the same minute is evicted in the same minute.
        """
        with self._lock:
            self._set_state(ConnectionState.DISCONNECTED)
            if scheduled:
                delay = self.compute_next_backoff(attempt=0)
                logger.info(
                    "Scheduled WebSocket rotation (%s). Reconnecting in %.2fs "
                    "(failure backoff not escalated).", reason, delay,
                )
                return delay

            delay = self.compute_next_backoff()
            self.reconnect_attempts += 1
            logger.warning(
                "WebSocket connection lost (%s). Reconnect attempt #%d. Waiting %.2fs before retry.",
                reason, self.reconnect_attempts, delay,
            )
            return delay

    def on_connection_established(self) -> List[str]:
        """
        Transitions ``CONNECTING -> [AUTHENTICATED] -> SUBSCRIBED`` and returns the
        symbols to re-subscribe, sorted for deterministic frame ordering.

        The returned list is the caller's *desired* subscription set, rebuilt from current
        state rather than replayed from a log of past subscribe calls.
        """
        with self._lock:
            self._set_state(ConnectionState.CONNECTING)
            if self.requires_auth:
                self._set_state(ConnectionState.AUTHENTICATED)
            self._set_state(ConnectionState.SUBSCRIBED)
            symbols = sorted(self.subscribed_symbols)
            logger.info("WebSocket connected. Re-subscribing %d symbol channel(s).", len(symbols))
            return symbols

    # ------------------------------------------------------------- stream state

    def is_synchronized(self, symbol: Optional[str] = None) -> bool:
        """
        True when local state provably matches the publisher's.

        Gate every consumer that builds state - an order book, a position view, a bar
        aggregator - on this. False means a gap was detected and not provably filled;
        only :meth:`resynchronize` clears it.
        """
        with self._lock:
            if symbol is None:
                return not self._unsynced
            return symbol.strip().upper() not in self._unsynced

    def unrecovered_gaps(self) -> Dict[str, SequenceGap]:
        """Snapshot of the latched, unfilled gaps keyed by symbol."""
        with self._lock:
            return dict(self._unsynced)

    def resynchronize(self, symbol: str, next_sequence_id: int) -> None:
        """
        Clears a latched gap after the caller has rebuilt state from a fresh snapshot.

        ``next_sequence_id`` is the first sequence id the manager should expect *after*
        the snapshot - e.g. Binance's ``lastUpdateId + 1``. This is the documented
        recovery path on every venue that offers no message-level retransmission.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if isinstance(next_sequence_id, bool) or not isinstance(next_sequence_id, int):
            raise TypeError("next_sequence_id must be an int")
        if next_sequence_id < 0:
            raise ValueError("next_sequence_id must be non-negative")
        key = symbol.strip().upper()
        with self._lock:
            self._unsynced.pop(key, None)
            self.last_seen_sequence[key] = next_sequence_id - 1
            if not self._unsynced:
                self._set_state(ConnectionState.STREAMING)
            logger.info(
                "Resynchronized %s from snapshot; next expected sequence %d.", key, next_sequence_id
            )

    # ------------------------------------------------------------------ ingestion

    def process_incoming_message(self, message: WSMessage) -> List[WSMessage]:
        """
        Ingests one message and returns what is safe to hand downstream, in order.

        Returns an empty list - never a partial view of a broken stream - when the message
        is a duplicate/stale replay, or when the symbol is latched unsynchronised. On a
        fillable gap the returned list is the recovered messages followed by the current
        one.
        """
        if not isinstance(message, WSMessage):
            raise TypeError("message must be a WSMessage")

        symbol = message.symbol.strip().upper()
        seq = message.sequence_id

        with self._lock:
            if symbol in self._unsynced:
                self.withheld_message_count += 1
                logger.debug(
                    "Withholding %s seq %d: stream unsynchronized pending resynchronize().",
                    symbol, seq,
                )
                return []

            last_seq = self.last_seen_sequence.get(symbol)

            if last_seq is not None and seq <= last_seq:
                # Never regress the watermark. A stale or duplicated frame that rewinds it
                # fabricates a gap on the next message and re-emits already-applied state.
                self.duplicate_message_count += 1
                logger.debug(
                    "Dropping non-advancing %s seq %d (watermark %d). A large backward jump "
                    "is a publisher restart, not a duplicate - resynchronize() in that case.",
                    symbol, seq, last_seq,
                )
                return []

            emitted: List[WSMessage] = []

            if last_seq is not None and seq > last_seq + 1:
                recovered = self._recover_gap(symbol, last_seq + 1, seq - 1)
                if recovered is None:
                    return []
                emitted.extend(recovered)
            elif last_seq is None:
                logger.info(
                    "Adopting %s seq %d as baseline. Nothing before it is observable - seed "
                    "from a snapshot instead if state must be correct from the session open.",
                    symbol, seq,
                )

            emitted.append(message)
            self.last_seen_sequence[symbol] = seq
            self._set_state(ConnectionState.STREAMING)
            self.processed_messages.extend(emitted)
            # A message processed end-to-end is the only proof the connection actually works;
            # resetting on "socket opened" would flat-line the backoff of a connect/drop loop.
            self.reconnect_attempts = 0
            return emitted

    # ------------------------------------------------------------------ internals

    def _recover_gap(
        self, symbol: str, first_missing: int, last_missing: int
    ) -> Optional[List[WSMessage]]:
        """
        Attempts to fill ``[first_missing, last_missing]``. Returns the recovered messages,
        or None after latching the symbol unsynchronised. Caller holds the lock.
        """
        gap_size = last_missing - first_missing + 1
        logger.warning(
            "SEQUENCE GAP on %s: watermark %d, received %d. Missing [%d..%d] (%d message(s)).",
            symbol, first_missing - 1, last_missing + 1, first_missing, last_missing, gap_size,
        )
        self._set_state(ConnectionState.RECOVERING_GAP)

        if self._rest_gap_fill_fn is None:
            return self._latch_unsynced(
                symbol, first_missing, last_missing, "no gap-fill callback configured"
            )

        if gap_size > self.max_gap_fill_size:
            return self._latch_unsynced(
                symbol, first_missing, last_missing,
                "gap of {} exceeds max_gap_fill_size={}; re-snapshot instead".format(
                    gap_size, self.max_gap_fill_size
                ),
            )

        try:
            fetched = self._rest_gap_fill_fn(symbol, first_missing, last_missing)
        except Exception as exc:  # any transport failure is simply an unfilled gap
            logger.exception(
                "REST gap fill raised for %s [%d..%d].", symbol, first_missing, last_missing
            )
            return self._latch_unsynced(
                symbol, first_missing, last_missing,
                "gap-fill callback raised {}".format(type(exc).__name__),
            )

        problem = self._validate_fill(fetched, symbol, first_missing, last_missing)
        if problem is not None:
            return self._latch_unsynced(symbol, first_missing, last_missing, problem)

        recovered = list(fetched)
        for filled in recovered:
            self.last_seen_sequence[symbol] = filled.sequence_id
        self.gap_fill_success_count += 1
        logger.info("REST gap fill recovered %d message(s) for %s.", len(recovered), symbol)
        return recovered

    @staticmethod
    def _validate_fill(
        fetched: object, symbol: str, first_missing: int, last_missing: int
    ) -> Optional[str]:
        """Returns None when the fill covers the range exactly, else why it was rejected."""
        if fetched is None:
            return "gap-fill callback returned None"
        if isinstance(fetched, (str, bytes)) or not isinstance(fetched, Sequence):
            return "gap-fill callback did not return a sequence of WSMessage"
        expected_count = last_missing - first_missing + 1
        if len(fetched) != expected_count:
            return "partial fill: expected {} message(s), received {}".format(
                expected_count, len(fetched)
            )
        for offset, msg in enumerate(fetched):
            want = first_missing + offset
            if not isinstance(msg, WSMessage):
                return "gap-fill callback returned a non-WSMessage element"
            if msg.symbol.strip().upper() != symbol:
                return "gap-fill returned symbol {!r}, expected {!r}".format(msg.symbol, symbol)
            if msg.sequence_id != want:
                return "gap-fill out of order or non-contiguous at sequence {} (expected {})".format(
                    msg.sequence_id, want
                )
        return None

    def _latch_unsynced(
        self, symbol: str, first_missing: int, last_missing: int, reason: str
    ) -> None:
        """Records an unfilled gap and holds the symbol closed until resynchronize()."""
        self._unsynced[symbol] = SequenceGap(
            symbol=symbol,
            first_missing=first_missing,
            last_missing=last_missing,
            reason=reason,
        )
        self.gap_fill_failure_count += 1
        self._set_state(ConnectionState.RECOVERING_GAP)
        logger.error(
            "UNRECOVERED GAP on %s [%d..%d]: %s. Withholding %s messages until resynchronize() "
            "is called with a fresh snapshot position.",
            symbol, first_missing, last_missing, reason, symbol,
        )
        return None
