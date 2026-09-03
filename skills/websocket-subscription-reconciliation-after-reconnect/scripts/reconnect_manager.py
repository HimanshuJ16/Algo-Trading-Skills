"""
websocket-subscription-reconciliation-after-reconnect: desired-state subscription
manager for market-data WebSockets.

Provides:

* ``WebSocketReconnectEngine`` -- one authoritative set of desired
  subscriptions that outlives connection objects, jittered exponential backoff
  that is actually bounded by ``max_delay``, resubscribe-then-backfill
  ordering, and reconciliation of the broker's own subscription
  acknowledgement against desired state.
* ``TickDeduplicator`` -- a bounded, thread-safe second line of defence against
  ticks delivered twice around a reconnect boundary.

Four design decisions here are load-bearing:

1. **Durations are measured on** ``time.monotonic()``. Wall-clock stamps
   (``time.time()``) are recorded separately and used only for the REST
   backfill window. A clock step during an outage -- exactly when NTP is most
   likely to correct a drifted host -- would otherwise corrupt the measured gap
   and therefore the size of the backfill request.
2. **Symbols are stored verbatim** apart from surrounding whitespace, which is
   never meaningful and silently subscribes to nothing. Case folding is opt-in via
   ``symbol_normalizer``. Binance stream names are lower-case and several
   venues treat symbol case as significant, so silently upper-casing a symbol
   subscribes to an instrument the venue does not recognise -- a silent
   coverage gap, which is the failure this skill exists to prevent.
3. **Resubscription happens before backfill.** Backfilling first leaves a
   second, silent gap between the end of the backfill window and the instant
   the stream actually goes live.
4. **Nothing here proves a subscription is live.** ``subscribe_fn`` returning
   is not an acknowledgement; IBKR's own TWS API guidance is that a client
   should not proceed assuming the connection is fine when the expected
   callback has not arrived. Feed the broker's confirmed subscription list into
   ``reconcile_subscriptions()``.

Standard library only, by design.
"""
from collections import deque
from dataclasses import dataclass
import logging
import random
import threading
import time
from typing import Callable, Deque, FrozenSet, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Delay floor: a backoff of zero is a busy-loop against a broker that is
# already unhealthy.
MIN_BACKOFF_SEC = 0.1

# 2 ** 62 seconds already exceeds any plausible ``max_delay``; capping the
# exponent keeps a long outage from driving ``attempt`` high enough that
# ``base_delay * 2 ** (attempt - 1)`` raises OverflowError inside the reconnect
# loop.
MAX_BACKOFF_EXPONENT = 62

TickSignature = Tuple[str, float, Optional[int]]


@dataclass(frozen=True)
class SubscriptionReconciliation:
    """Outcome of comparing the broker's confirmed subscriptions to desired state."""

    confirmed: FrozenSet[str]
    missing: FrozenSet[str]
    unexpected: FrozenSet[str]

    @property
    def is_clean(self) -> bool:
        """True when the broker confirms exactly the desired set -- no more, no fewer."""
        return not self.missing and not self.unexpected


@dataclass
class ReconnectEvent:
    """Audit record for one disconnect/reconnect cycle.

    ``gap_duration_sec`` is measured on a monotonic clock; the two timestamps
    are wall-clock and are the window handed to the REST backfill.
    """

    disconnect_timestamp: float
    reconnect_timestamp: float
    gap_duration_sec: float
    subscribed_symbols_count: int
    backfill_executed: bool
    backfill_error: Optional[str] = None
    reconciliation: Optional[SubscriptionReconciliation] = None


class TickDeduplicator:
    """Bounded, thread-safe sliding-window deduplicator for redelivered ticks.

    Intended as a *second* line of defence behind correct desired-state
    resubscription, for brokers that replay a short window of ticks around a
    reconnect boundary.

    The signature is ``(symbol, timestamp, seq_num)``. Supply a real
    per-message sequence number wherever the feed carries one. With
    ``seq_num=None`` the key degenerates to ``(symbol, timestamp)``, and on any
    feed whose timestamps are coarser than its tick rate -- Kite's tick
    ``exchange_timestamp`` is one-second resolution -- two genuinely different
    ticks share a key and the second is dropped as a false duplicate.

    Thread safety matters in practice: a fan-out consumer pool sharing one
    unlocked deduplicator desynchronises ``seen_signatures`` from
    ``history_queue``, which both leaks memory and strands signatures in the
    set forever, permanently suppressing later genuine ticks that reuse a
    stranded key.
    """

    def __init__(self, max_history: int = 10_000) -> None:
        if not isinstance(max_history, int) or isinstance(max_history, bool) or max_history < 1:
            raise ValueError(f"max_history must be an integer >= 1, got {max_history!r}")
        self._lock = threading.Lock()
        self.seen_signatures: Set[TickSignature] = set()
        self.history_queue: Deque[TickSignature] = deque(maxlen=max_history)

    def is_duplicate(
        self,
        symbol: str,
        timestamp: float,
        seq_num: Optional[int] = None,
    ) -> bool:
        """Return True if this tick was already seen inside the retained window.

        A non-duplicate is recorded as a side effect, evicting the oldest
        signature once the window is full.
        """
        sig: TickSignature = (symbol, timestamp, seq_num)
        with self._lock:
            if sig in self.seen_signatures:
                return True

            if len(self.history_queue) == self.history_queue.maxlen:
                self.seen_signatures.discard(self.history_queue.popleft())

            self.history_queue.append(sig)
            self.seen_signatures.add(sig)
            return False


class WebSocketReconnectEngine:
    """Desired-state subscription manager for a reconnecting market-data WebSocket.

    The desired set is the single source of truth and is deliberately decoupled
    from the connection object: every reconnect resubscribes from the current
    set, never by replaying a log of past subscribe calls (which double-counts
    anything already resubscribed in an earlier cycle).

    All public methods are safe to call from the SDK's network callback thread
    and the application thread concurrently. ``on_reconnect()`` additionally
    serialises against itself, so two overlapping reconnect handlers cannot
    both backfill the same gap -- do not call it from inside ``subscribe_fn``
    or ``backfill_fn``, and give both callbacks their own socket timeouts,
    since a callback that blocks forever blocks the whole reconnect path.
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter_pct: float = 0.20,
        symbol_normalizer: Optional[Callable[[str], str]] = None,
        history_limit: int = 1_000,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """
        Args:
            base_delay: First retry delay in seconds; must be > 0.
            max_delay: Hard ceiling on the returned delay, jitter included.
            jitter_pct: Symmetric jitter band, 0.0-1.0, as a fraction of the
                capped delay. RFC 6455 section 7.2.3 directs clients to
                randomise reconnect timing after an abnormal closure; a
                symmetric band decorrelates clients but spreads them less than
                full jitter (uniform over ``[0, cap]``).
            symbol_normalizer: Optional canonicaliser applied to every symbol.
                Defaults to None, i.e. symbols are stored exactly as supplied.
                Pass ``str.upper`` only when the venue is known to be
                case-insensitive.
            history_limit: Retained ``ReconnectEvent`` records; the deque is
                bounded so a long-lived process with a flapping link cannot
                grow this without limit.
            monotonic_clock: Source for gap *durations*. Injectable so the gap
                semantics can be tested deterministically rather than slept
                through.
            wall_clock: Source for the *timestamps* handed to the REST backfill
                and written to the audit record.
        """
        if base_delay <= 0:
            raise ValueError(f"base_delay must be > 0, got {base_delay!r}")
        if max_delay < base_delay:
            raise ValueError(f"max_delay ({max_delay!r}) must be >= base_delay ({base_delay!r})")
        if max_delay < MIN_BACKOFF_SEC:
            raise ValueError(f"max_delay must be >= {MIN_BACKOFF_SEC}, got {max_delay!r}")
        if not 0.0 <= jitter_pct <= 1.0:
            raise ValueError(f"jitter_pct must be within [0.0, 1.0], got {jitter_pct!r}")
        if not isinstance(history_limit, int) or isinstance(history_limit, bool) or history_limit < 1:
            raise ValueError(f"history_limit must be an integer >= 1, got {history_limit!r}")

        if not callable(monotonic_clock) or not callable(wall_clock):
            raise ValueError("monotonic_clock and wall_clock must be callables returning float")

        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter_pct = jitter_pct
        self.symbol_normalizer = symbol_normalizer
        self._monotonic = monotonic_clock
        self._wall = wall_clock

        self.desired_symbols: Set[str] = set()
        self.last_disconnect: Optional[float] = None
        self.reconnect_history: Deque[ReconnectEvent] = deque(maxlen=history_limit)
        self.dedup = TickDeduplicator()

        self._disconnect_monotonic: Optional[float] = None
        self._lock = threading.RLock()
        self._reconnect_lock = threading.Lock()

    # -- desired subscription state ---------------------------------------

    def _normalize(self, symbol: str) -> str:
        """Validate a symbol and apply the configured normaliser.

        Surrounding whitespace is stripped -- it is never meaningful in a
        symbol, and a trailing space silently subscribes to nothing. Everything
        else, case included, is preserved unless ``symbol_normalizer`` says
        otherwise.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        stripped = symbol.strip()
        return self.symbol_normalizer(stripped) if self.symbol_normalizer else stripped

    def subscribe(self, symbol: str) -> None:
        """Add a symbol to desired state. Idempotent; the set absorbs repeats."""
        normalized = self._normalize(symbol)
        with self._lock:
            self.desired_symbols.add(normalized)

    def unsubscribe(self, symbol: str) -> None:
        """Remove a symbol from desired state. Safe if it was never subscribed."""
        normalized = self._normalize(symbol)
        with self._lock:
            self.desired_symbols.discard(normalized)

    def snapshot_desired(self) -> List[str]:
        """Return a sorted, point-in-time copy of desired state."""
        with self._lock:
            return sorted(self.desired_symbols)

    # -- connection lifecycle ---------------------------------------------

    def on_disconnect(self, reason: str = "") -> None:
        """Record the start of a gap.

        Repeat notifications for the same outage are ignored: SDKs routinely
        fire both an error and a close callback for one drop, and overwriting
        the timestamp would shrink the measured gap and the backfill window.
        """
        with self._lock:
            if self._disconnect_monotonic is not None:
                logger.debug(
                    "Ignoring repeat disconnect notification (reason=%s).",
                    reason or "unspecified",
                )
                return
            self._disconnect_monotonic = self._monotonic()
            self.last_disconnect = self._wall()
            symbol_count = len(self.desired_symbols)

        logger.warning(
            "WebSocket DISCONNECTED (reason=%s). Desired subscriptions to restore: %d",
            reason or "unspecified",
            symbol_count,
        )

    def calculate_backoff(self, attempt: int) -> float:
        """Delay in seconds before retry number ``attempt`` (1 = first retry).

        Exponential growth capped at ``max_delay``, then a symmetric
        ``jitter_pct`` band, then clamped back into
        ``[MIN_BACKOFF_SEC, max_delay]`` -- the clamp matters, because applying
        jitter after the cap and returning that directly lets the delay exceed
        the stated maximum by the full jitter percentage.
        """
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise ValueError(f"attempt must be an integer >= 1 (first retry is 1), got {attempt!r}")

        exponent = min(attempt - 1, MAX_BACKOFF_EXPONENT)
        raw_delay = min(self.max_delay, self.base_delay * (2.0 ** exponent))
        jitter = raw_delay * self.jitter_pct * (2.0 * random.random() - 1.0)
        return min(self.max_delay, max(MIN_BACKOFF_SEC, raw_delay + jitter))

    def reconcile_subscriptions(self, confirmed: Iterable[str]) -> SubscriptionReconciliation:
        """Compare the broker's confirmed subscription list against desired state.

        Call this from the handler for whatever acknowledgement the venue
        sends -- Alpaca, for instance, replies to every subscribe with the
        session's *entire* current subscription list, which makes this an exact
        check rather than an estimate.

        ``missing`` is a silent coverage gap; ``unexpected`` is a duplicate or
        stale subscription still consuming a market-data slot. Both are logged
        at ERROR because neither is visible in the tick stream itself.
        """
        confirmed_set = frozenset(confirmed)
        with self._lock:
            desired = frozenset(self.desired_symbols)

        result = SubscriptionReconciliation(
            confirmed=confirmed_set,
            missing=frozenset(desired - confirmed_set),
            unexpected=frozenset(confirmed_set - desired),
        )
        if result.missing:
            logger.error(
                "Subscription reconciliation: %d desired symbol(s) NOT confirmed by broker: %s",
                len(result.missing),
                sorted(result.missing),
            )
        if result.unexpected:
            logger.error(
                "Subscription reconciliation: broker reports %d symbol(s) not in desired state: %s",
                len(result.unexpected),
                sorted(result.unexpected),
            )
        if result.is_clean:
            logger.info(
                "Subscription reconciliation clean: %d symbol(s) confirmed.",
                len(confirmed_set),
            )
        return result

    def on_reconnect(
        self,
        subscribe_fn: Callable[[List[str]], None],
        backfill_fn: Optional[Callable[[List[str], float, float], None]] = None,
    ) -> ReconnectEvent:
        """Resubscribe fresh from desired state, then backfill the gap.

        The ordering is deliberate. Backfilling first leaves a second, silent
        gap between the end of the backfill window and the moment the stream
        actually goes live; resubscribing first makes the backfill window
        ``[disconnect, resubscription complete]``, which overlaps the live
        stream. Overlap is the safe direction -- ``TickDeduplicator`` absorbs
        it, a gap is unrecoverable.

        Args:
            subscribe_fn: Called once with the sorted desired symbols. If it
                returns a collection, that is treated as the broker's confirmed
                subscription list and reconciled immediately; venues that
                acknowledge asynchronously should call
                ``reconcile_subscriptions()`` from their ack handler instead.
                An exception propagates and the disconnect timestamps are left
                intact, so a later retry still backfills from the original
                disconnect.
            backfill_fn: Called as ``(symbols, gap_start_wall, gap_end_wall)``.
                A failure is recorded on the event rather than raised, but it
                means the gap is unfilled -- treat a non-empty
                ``backfill_error`` as missing data, not as a warning.
        """
        with self._reconnect_lock:
            with self._lock:
                disconnect_wall = self.last_disconnect
                disconnect_monotonic = self._disconnect_monotonic
                symbols = sorted(self.desired_symbols)

            # 1. Resubscribe fresh from desired state -- never from a replayed
            #    log of past subscribe calls.
            acknowledgement = subscribe_fn(symbols)
            resubscribed_wall = self._wall()
            gap_duration = (
                self._monotonic() - disconnect_monotonic if disconnect_monotonic is not None else 0.0
            )
            logger.info(
                "WebSocket RECONNECTED. Resubscribed fresh to %d symbol(s) after a %.2fs gap.",
                len(symbols),
                gap_duration,
            )

            # Reconcile only a collection of symbol strings. Anything else --
            # a mock, a status code, a list of internal token ids -- is not an
            # acknowledgement in the desired set's vocabulary, and comparing it
            # would raise a false alarm on every reconnect.
            reconciliation: Optional[SubscriptionReconciliation] = None
            if isinstance(acknowledgement, (set, frozenset, list, tuple)) and all(
                isinstance(item, str) for item in acknowledgement
            ):
                reconciliation = self.reconcile_subscriptions(acknowledgement)

            # 2. Backfill [disconnect, resubscription complete] over REST.
            backfill_executed = False
            backfill_error: Optional[str] = None
            if backfill_fn is not None and disconnect_wall is not None:
                try:
                    backfill_fn(symbols, disconnect_wall, resubscribed_wall)
                except Exception as exc:  # recorded on the event, never swallowed
                    backfill_error = f"{type(exc).__name__}: {exc}"
                    logger.error(
                        "Gap backfill FAILED over a %.2fs gap; %d symbol(s) have unfilled data: %s",
                        gap_duration,
                        len(symbols),
                        backfill_error,
                    )
                else:
                    backfill_executed = True
                    logger.info(
                        "Gap backfill EXECUTED for %d symbol(s) over a %.2fs gap.",
                        len(symbols),
                        gap_duration,
                    )
            elif backfill_fn is not None:
                logger.info("No preceding disconnect recorded; skipping gap backfill.")

            event = ReconnectEvent(
                disconnect_timestamp=(
                    disconnect_wall if disconnect_wall is not None else resubscribed_wall
                ),
                reconnect_timestamp=resubscribed_wall,
                gap_duration_sec=gap_duration,
                subscribed_symbols_count=len(symbols),
                backfill_executed=backfill_executed,
                backfill_error=backfill_error,
                reconciliation=reconciliation,
            )
            with self._lock:
                self.reconnect_history.append(event)
                self.last_disconnect = None
                self._disconnect_monotonic = None
            return event


class SubscriptionManager:
    """Minimal legacy shim kept for backward compatibility.

    Deprecated: use ``WebSocketReconnectEngine``, which measures the gap on a
    monotonic clock, bounds its history, is thread-safe, and can reconcile the
    broker's acknowledgement. This class is retained only so existing callers
    keep working; note that its ``backfill_fn`` contract
    (``symbols, gap_seconds``) differs from the engine's.
    """

    def __init__(self) -> None:
        self.desired_symbols: Set[str] = set()
        self.last_disconnect: Optional[float] = None
        self.gap_log: List[float] = []

    def add_symbol(self, symbol: str) -> None:
        self.desired_symbols.add(symbol)

    def remove_symbol(self, symbol: str) -> None:
        self.desired_symbols.discard(symbol)

    def on_disconnect(self) -> None:
        self.last_disconnect = time.monotonic()

    def on_reconnect(
        self,
        subscribe_fn: Callable[[List[str]], None],
        backfill_fn: Optional[Callable[[Set[str], float], None]] = None,
    ) -> None:
        # ``is not None``, not truthiness: a monotonic clock legitimately reads
        # 0.0 near process start, and a truthiness test would discard that as
        # "never disconnected", losing the gap record and the backfill with it.
        if self.last_disconnect is not None:
            gap = time.monotonic() - self.last_disconnect
            self.gap_log.append(gap)
            if backfill_fn:
                backfill_fn(self.desired_symbols, gap)
        subscribe_fn(sorted(self.desired_symbols))
        self.last_disconnect = None
