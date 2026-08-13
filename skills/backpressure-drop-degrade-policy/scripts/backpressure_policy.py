"""
backpressure-drop-degrade-policy: Explicit per-stream backpressure management for
real-time trading pipelines.

Provides tier-isolated bounded queues, rate-limited alerting, post-session telemetry,
and concrete handlers for DROP_OLDEST, SAMPLE, DEGRADE (tick->OHLC), and NEVER_DROP.

Contract every caller must honour
---------------------------------
``handle_full()`` returns a :class:`BackpressureDecision`. **Check
``decision.accepted``.** A ``NEVER_DROP`` stream at capacity returns
``accepted=False``: the item was *not* enqueued and the manager cannot store it
for you. Ignoring that flag silently discards risk-critical data, which is the
exact failure this skill exists to prevent.

Threading
---------
``collections.deque`` guarantees that individual appends and pops are
thread-safe, but a *sequence* of them is not atomic. Reading ``len(queue)`` and
then popping that many times races with a concurrent consumer and raises
``IndexError: pop from an empty deque``. All queue mutation here happens under a
lock, and every pop is individually guarded.

Bounded-deque caveat
--------------------
A ``deque(maxlen=N)`` silently discards from the opposite end when it is full
and you append. That is an *implicit* drop-oldest policy applied by the data
structure. Route pushes through this manager so the policy is the one you chose.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
import threading
import time
from typing import Any, Callable, Deque, Dict, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "PolicyType",
    "BackpressureAction",
    "BackpressureError",
    "UnknownStreamError",
    "NeverDropOverflowError",
    "BackpressureDecision",
    "StreamMetrics",
    "TickAggregator",
    "RateLimitedAlert",
    "BackpressureManager",
    "BackpressurePolicy",
    "DROP_OLDEST",
    "SAMPLE",
    "DEGRADE",
    "NEVER_DROP",
]


class PolicyType(str, Enum):
    DROP_OLDEST = "drop_oldest"
    SAMPLE = "sample"
    DEGRADE = "degrade"
    NEVER_DROP = "never_drop"


DROP_OLDEST = PolicyType.DROP_OLDEST.value
SAMPLE = PolicyType.SAMPLE.value
DEGRADE = PolicyType.DEGRADE.value
NEVER_DROP = PolicyType.NEVER_DROP.value

_VALID_POLICIES = {p.value for p in PolicyType}


class BackpressureAction(str, Enum):
    """What the manager actually did with the item."""

    ENQUEUED_AFTER_DROP = "enqueued_after_drop"   # DROP_OLDEST: oldest evicted, item admitted
    ENQUEUED_AFTER_SAMPLE = "enqueued_after_sample"  # SAMPLE: item admitted on its turn
    THROTTLED = "throttled"                        # SAMPLE: item skipped by 1-in-N rule
    AGGREGATED = "aggregated"                      # DEGRADE: item folded into an OHLC bar
    REJECTED_OVERFLOW = "rejected_overflow"        # NEVER_DROP: caller must handle the item


class BackpressureError(RuntimeError):
    """Base class for backpressure configuration and policy errors."""


class UnknownStreamError(BackpressureError):
    """Raised when a stream has no configured policy.

    Defaulting an unconfigured stream to a drop policy is how a mistyped
    risk-stream name silently becomes a data-loss path.
    """


class NeverDropOverflowError(BackpressureError):
    """Raised in strict mode when a NEVER_DROP stream cannot accept an item."""


@dataclass
class BackpressureDecision:
    """Outcome of one overflow event."""

    stream_name: str
    policy: str
    action: BackpressureAction
    accepted: bool          # True if the item is now in the queue or folded into a bar
    emitted_item: Any = None  # completed OHLC bar (DEGRADE), else None
    discarded_count: int = 0
    rejected_item: Any = None  # populated only when accepted is False

    def __bool__(self) -> bool:
        """A decision is truthy when the item was accepted."""
        return self.accepted


@dataclass
class StreamMetrics:
    stream_name: str
    policy: str
    total_observed: int = 0        # pushes seen via observe()
    overflow_events: int = 0       # calls to handle_full()
    total_dropped: int = 0         # evicted by DROP_OLDEST
    total_sampled: int = 0         # skipped by SAMPLE's 1-in-N rule
    total_degraded: int = 0        # folded into OHLC bars
    never_drop_overflows: int = 0  # risk items the caller must account for
    alert_count: int = 0
    high_watermark_breaches: int = 0
    last_event_timestamp: Optional[float] = None

    @property
    def total_discarded(self) -> int:
        return self.total_dropped + self.total_sampled

    def to_dict(self) -> Dict[str, Any]:
        # Rate is expressed against observed pushes. It is None rather than 0.0
        # when nothing was observed, because a 0.0 would read as "healthy".
        drop_rate = (
            round(self.total_discarded / self.total_observed * 100, 2)
            if self.total_observed > 0
            else None
        )
        return {
            "stream_name": self.stream_name,
            "policy": self.policy,
            "total_observed": self.total_observed,
            "overflow_events": self.overflow_events,
            "total_dropped": self.total_dropped,
            "total_sampled": self.total_sampled,
            "total_degraded": self.total_degraded,
            "total_discarded": self.total_discarded,
            "never_drop_overflows": self.never_drop_overflows,
            "alert_count": self.alert_count,
            "high_watermark_breaches": self.high_watermark_breaches,
            "drop_rate_pct": drop_rate,
        }


class TickAggregator:
    """Aggregates a raw tick stream into fixed-interval OHLC bars for DEGRADE mode."""

    def __init__(self, symbol: str, interval_sec: float = 1.0) -> None:
        if not isinstance(interval_sec, (int, float)) or isinstance(interval_sec, bool):
            raise BackpressureError("interval_sec must be a number")
        if not math.isfinite(interval_sec) or interval_sec <= 0:
            raise BackpressureError(f"interval_sec must be positive and finite, got {interval_sec!r}")
        self.symbol = symbol
        self.interval_sec = float(interval_sec)
        self.current_bar: Optional[Dict[str, Any]] = None
        self.bar_start_time: float = 0.0

    @staticmethod
    def _numeric(tick: Dict[str, Any], *keys: str) -> Optional[float]:
        """Returns the first present, finite numeric value among ``keys``.

        Uses explicit presence checks rather than ``or`` chaining: a legitimate
        value of ``0`` is falsy and would otherwise fall through to the default.
        """
        for key in keys:
            if key not in tick:
                continue
            value = tick[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not math.isfinite(value):
                continue
            return float(value)
        return None

    def add_tick(self, tick: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Folds a tick into the current bar; returns a completed bar if one closed.

        Raises:
            BackpressureError: If the tick carries no usable price. Substituting
                0.0 would silently corrupt the OHLC bars that degraded mode feeds
                downstream.
        """
        if not isinstance(tick, dict):
            raise BackpressureError(f"tick must be a dict, got {type(tick).__name__}")

        price = self._numeric(tick, "price", "ltp")
        if price is None:
            raise BackpressureError(
                f"tick for {self.symbol!r} has no finite 'price'/'ltp'; refusing to "
                "substitute 0.0 into an OHLC bar"
            )
        volume = self._numeric(tick, "volume", "qty")
        if volume is None:
            volume = 0.0
        timestamp = self._numeric(tick, "timestamp")
        if timestamp is None:
            timestamp = time.time()

        completed_bar: Optional[Dict[str, Any]] = None
        starting_new_bar = (
            self.current_bar is None
            or (timestamp - self.bar_start_time) >= self.interval_sec
            # An out-of-order tick predating the current bar must not be folded
            # into it; that would rewrite a bar the consumer may already hold.
            or timestamp < self.bar_start_time
        )

        if starting_new_bar:
            if self.current_bar is not None:
                completed_bar = dict(self.current_bar)
            # Snap to a deterministic interval boundary so bars align across streams.
            self.bar_start_time = timestamp - (timestamp % self.interval_sec)
            self.current_bar = {
                "symbol": self.symbol,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "timestamp": timestamp,
                "bar_start_time": self.bar_start_time,
                "is_bar": True,
            }
        else:
            bar = self.current_bar
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price
            bar["volume"] += volume

        return completed_bar


class RateLimitedAlert:
    """Enforces a per-stream cooldown so alert storms cannot flood the alert sink."""

    def __init__(self, alert_fn: Callable[[str], None], cooldown_sec: float = 5.0) -> None:
        if not callable(alert_fn):
            raise BackpressureError("alert_fn must be callable")
        if not isinstance(cooldown_sec, (int, float)) or isinstance(cooldown_sec, bool):
            raise BackpressureError("cooldown_sec must be a number")
        if not math.isfinite(cooldown_sec) or cooldown_sec < 0:
            raise BackpressureError(f"cooldown_sec must be >= 0 and finite, got {cooldown_sec!r}")
        self.alert_fn = alert_fn
        self.cooldown_sec = float(cooldown_sec)
        self.last_alert_time: Dict[str, float] = {}
        self._lock = threading.Lock()

    def trigger(self, stream_name: str, message: str) -> bool:
        """Fires the alert unless within cooldown. Returns True if it fired.

        The alert callback is invoked outside the lock: a slow or blocking sink
        must not become the pipeline's new bottleneck.
        """
        now = time.monotonic()
        with self._lock:
            last = self.last_alert_time.get(stream_name)
            if last is not None and (now - last) < self.cooldown_sec:
                return False
            self.last_alert_time[stream_name] = now
        try:
            self.alert_fn(f"[{stream_name}] {message}")
        except Exception:
            # An alert sink failure must never take down the trading pipeline.
            logger.exception("Alert sink raised for stream %s", stream_name)
        return True


class BackpressureManager:
    """Explicit per-stream backpressure dispatcher with telemetry and alerting.

    Args:
        policy_by_stream: Stream name -> policy. Every stream must be declared.
        alert_fn: Alert sink. Defaults to a log warning; production should pass a
            real out-of-band alerter (SKILL.md requires more than a log line).
        alert_cooldown_sec: Per-stream alert cooldown.
        high_watermark_pct: Fraction of capacity at which advisory alerts fire.
        sample_keep_every_n: SAMPLE admits 1 of every N items under overload.
        strict_unknown_stream: Raise on an undeclared stream instead of guessing.
        strict_never_drop: Raise when a NEVER_DROP stream overflows.
        on_never_drop_overflow: Called with (stream_name, item) on NEVER_DROP
            overflow, for emergency handling such as tripping a kill switch.
    """

    def __init__(
        self,
        policy_by_stream: Dict[str, str],
        alert_fn: Optional[Callable[[str], None]] = None,
        alert_cooldown_sec: float = 5.0,
        high_watermark_pct: float = 0.8,
        sample_keep_every_n: int = 2,
        strict_unknown_stream: bool = True,
        strict_never_drop: bool = False,
        on_never_drop_overflow: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        if not isinstance(policy_by_stream, dict):
            raise BackpressureError("policy_by_stream must be a dict")
        for stream, policy in policy_by_stream.items():
            if policy not in _VALID_POLICIES:
                raise BackpressureError(
                    f"stream {stream!r} has unknown policy {policy!r}; "
                    f"expected one of {sorted(_VALID_POLICIES)}"
                )
        if not isinstance(high_watermark_pct, (int, float)) or isinstance(high_watermark_pct, bool):
            raise BackpressureError("high_watermark_pct must be a number")
        if not 0 < high_watermark_pct <= 1:
            raise BackpressureError(
                f"high_watermark_pct must be in (0, 1], got {high_watermark_pct!r}"
            )
        if isinstance(sample_keep_every_n, bool) or not isinstance(sample_keep_every_n, int):
            raise BackpressureError("sample_keep_every_n must be an int")
        if sample_keep_every_n < 1:
            raise BackpressureError(
                f"sample_keep_every_n must be >= 1, got {sample_keep_every_n}"
            )

        self.policy_by_stream = dict(policy_by_stream)
        self.alert_limiter = RateLimitedAlert(
            alert_fn if alert_fn is not None else (lambda msg: logger.warning(msg)),
            cooldown_sec=alert_cooldown_sec,
        )
        self.high_watermark_pct = float(high_watermark_pct)
        self.sample_keep_every_n = sample_keep_every_n
        self.strict_unknown_stream = strict_unknown_stream
        self.strict_never_drop = strict_never_drop
        self.on_never_drop_overflow = on_never_drop_overflow

        self._lock = threading.RLock()
        self.metrics: Dict[str, StreamMetrics] = {
            stream: StreamMetrics(stream_name=stream, policy=policy)
            for stream, policy in self.policy_by_stream.items()
        }
        self.aggregators: Dict[str, TickAggregator] = {
            stream: TickAggregator(symbol=stream)
            for stream, policy in self.policy_by_stream.items()
            if policy == DEGRADE
        }
        self._sample_counter: Dict[str, int] = {}

    # ------------------------------------------------------------------ helpers

    def _resolve_policy(self, stream_name: str) -> str:
        policy = self.policy_by_stream.get(stream_name)
        if policy is None:
            if self.strict_unknown_stream:
                raise UnknownStreamError(
                    f"stream {stream_name!r} has no configured backpressure policy. "
                    "Declare it explicitly - defaulting would silently apply a drop "
                    "policy to a possibly risk-critical stream."
                )
            policy = DROP_OLDEST
            with self._lock:
                self.policy_by_stream.setdefault(stream_name, policy)
                self.metrics.setdefault(
                    stream_name, StreamMetrics(stream_name=stream_name, policy=policy)
                )
            self.alert_limiter.trigger(
                stream_name,
                f"WARNING: undeclared stream {stream_name!r} defaulted to {policy}.",
            )
        return policy

    def _metrics_for(self, stream_name: str, policy: str) -> StreamMetrics:
        return self.metrics.setdefault(
            stream_name, StreamMetrics(stream_name=stream_name, policy=policy)
        )

    @staticmethod
    def _safe_popleft(queue: Deque[Any]) -> bool:
        """Pops one item, tolerating a concurrent consumer having drained it."""
        try:
            queue.popleft()
            return True
        except IndexError:
            return False

    # ------------------------------------------------------------- observation

    def observe(self, stream_name: str, queue: Deque[Any]) -> None:
        """Records a push and emits an early warning before the queue is full.

        Call this on every push, not only on overflow. ``handle_full`` alone
        cannot give advance warning because by the time it runs the queue is
        already at capacity — for a NEVER_DROP stream that is already too late.
        """
        policy = self._resolve_policy(stream_name)
        with self._lock:
            metrics = self._metrics_for(stream_name, policy)
            metrics.total_observed += 1
            metrics.last_event_timestamp = time.time()
        self._check_watermark(stream_name, queue, policy)

    def _check_watermark(self, stream_name: str, queue: Deque[Any], policy: str) -> None:
        max_len = queue.maxlen
        if not max_len:
            return
        fill = len(queue) / max_len
        if fill < self.high_watermark_pct:
            return

        with self._lock:
            metrics = self._metrics_for(stream_name, policy)
            metrics.high_watermark_breaches += 1

        severity = "CRITICAL" if policy == NEVER_DROP else "WARNING"
        fired = self.alert_limiter.trigger(
            stream_name,
            f"{severity}: stream '{stream_name}' ({policy}) approaching capacity "
            f"{len(queue)}/{max_len} ({fill * 100:.1f}%)",
        )
        if fired:
            with self._lock:
                self._metrics_for(stream_name, policy).alert_count += 1

    # ------------------------------------------------------------ overflow path

    def handle_full(
        self, stream_name: str, queue: Deque[Any], item: Any = None
    ) -> BackpressureDecision:
        """Applies the stream's policy to an overflow event.

        Returns:
            A :class:`BackpressureDecision`. **Check ``accepted``** — a
            ``NEVER_DROP`` stream returns ``accepted=False`` and the item is the
            caller's responsibility.

        Raises:
            UnknownStreamError: Undeclared stream in strict mode.
            NeverDropOverflowError: NEVER_DROP overflow in strict mode.
            BackpressureError: On a DEGRADE stream, if ``item`` carries no usable
                price. This is a caller error and fails loudly by design —
                substituting 0.0 would corrupt the OHLC bars silently.
        """
        policy = self._resolve_policy(stream_name)

        with self._lock:
            metrics = self._metrics_for(stream_name, policy)
            metrics.overflow_events += 1
            metrics.last_event_timestamp = time.time()

            if policy == NEVER_DROP:
                metrics.never_drop_overflows += 1
                metrics.alert_count += 1
            elif policy == DROP_OLDEST:
                discarded = 1 if self._safe_popleft(queue) else 0
                metrics.total_dropped += discarded
                if item is not None:
                    queue.append(item)
                return BackpressureDecision(
                    stream_name, policy, BackpressureAction.ENQUEUED_AFTER_DROP,
                    accepted=True, discarded_count=discarded,
                )
            elif policy == SAMPLE:
                count = self._sample_counter.get(stream_name, 0) + 1
                self._sample_counter[stream_name] = count
                if count % self.sample_keep_every_n != 0:
                    # Throttled: skip this item, leave the existing backlog intact.
                    metrics.total_sampled += 1
                    return BackpressureDecision(
                        stream_name, policy, BackpressureAction.THROTTLED,
                        accepted=False, discarded_count=1, rejected_item=item,
                    )
                discarded = 1 if self._safe_popleft(queue) else 0
                metrics.total_dropped += discarded
                if item is not None:
                    queue.append(item)
                return BackpressureDecision(
                    stream_name, policy, BackpressureAction.ENQUEUED_AFTER_SAMPLE,
                    accepted=True, discarded_count=discarded,
                )
            elif policy == DEGRADE:
                metrics.total_degraded += 1
                aggregator = self.aggregators.setdefault(
                    stream_name, TickAggregator(symbol=stream_name)
                )
                tick = item if isinstance(item, dict) else {"price": item}
                completed_bar = aggregator.add_tick(tick)
                return BackpressureDecision(
                    stream_name, policy, BackpressureAction.AGGREGATED,
                    accepted=True, emitted_item=completed_bar,
                )

        # NEVER_DROP falls through to here, deliberately outside the lock so the
        # alert sink and emergency callback cannot stall other streams.
        max_len = queue.maxlen if queue.maxlen is not None else "unbounded"
        self.alert_limiter.trigger(
            stream_name,
            f"CRITICAL: NEVER_DROP stream '{stream_name}' at capacity "
            f"({len(queue)}/{max_len}). Risk backlog detected - item NOT enqueued.",
        )
        if self.on_never_drop_overflow is not None:
            try:
                self.on_never_drop_overflow(stream_name, item)
            except Exception:
                logger.exception("on_never_drop_overflow raised for %s", stream_name)
        if self.strict_never_drop:
            raise NeverDropOverflowError(
                f"NEVER_DROP stream {stream_name!r} overflowed; item not enqueued"
            )
        return BackpressureDecision(
            stream_name, policy, BackpressureAction.REJECTED_OVERFLOW,
            accepted=False, rejected_item=item,
        )

    # ---------------------------------------------------------------- telemetry

    def get_metrics_summary(self) -> Dict[str, Dict[str, Any]]:
        """Returns post-session summary metrics for all managed streams."""
        with self._lock:
            return {stream: m.to_dict() for stream, m in self.metrics.items()}


class BackpressurePolicy:
    """Legacy wrapper preserving the pre-2.0 return shape.

    ``handle_full`` returns the completed OHLC bar (DEGRADE) or ``None``, which
    cannot express rejection. Prefer :class:`BackpressureManager` directly so
    ``accepted`` is visible.
    """

    def __init__(self, policy_by_stream: dict, alert_fn: Callable[[str], None]) -> None:
        self.manager = BackpressureManager(
            policy_by_stream=policy_by_stream, alert_fn=alert_fn
        )

    def handle_full(self, stream_name: str, queue: Deque[Any], item: Any = None) -> Optional[Any]:
        return self.manager.handle_full(stream_name, queue, item).emitted_item
