"""
backpressure-drop-degrade-policy: Explicit per-stream backpressure management for real-time trading pipelines.

Provides tier-isolated bounded queues, rate-limited alerting, post-session telemetry,
and concrete handlers for DROP_OLDEST, SAMPLE, DEGRADE (tick->OHLC), and NEVER_DROP policies.
"""
from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any, Callable, Dict, List, Optional
import collections

logger = logging.getLogger(__name__)


class PolicyType(str, Enum):
    DROP_OLDEST = "drop_oldest"
    SAMPLE = "sample"
    DEGRADE = "degrade"
    NEVER_DROP = "never_drop"


# Backward compatibility constants
DROP_OLDEST = PolicyType.DROP_OLDEST.value
SAMPLE = PolicyType.SAMPLE.value
DEGRADE = PolicyType.DEGRADE.value
NEVER_DROP = PolicyType.NEVER_DROP.value


@dataclass
class StreamMetrics:
    stream_name: str
    policy: str
    total_pushed: int = 0
    total_processed: int = 0
    total_dropped: int = 0
    total_sampled: int = 0
    total_degraded: int = 0
    alert_count: int = 0
    high_watermark_breaches: int = 0
    last_event_timestamp: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stream_name": self.stream_name,
            "policy": self.policy,
            "total_pushed": self.total_pushed,
            "total_processed": self.total_processed,
            "total_dropped": self.total_dropped,
            "total_sampled": self.total_sampled,
            "total_degraded": self.total_degraded,
            "alert_count": self.alert_count,
            "high_watermark_breaches": self.high_watermark_breaches,
            "drop_rate_pct": round(
                (self.total_dropped / self.total_pushed * 100) if self.total_pushed > 0 else 0.0, 2
            ),
        }


class TickAggregator:
    """Aggregates raw tick stream into 1-second OHLC bars during DEGRADE policy mode."""

    def __init__(self, symbol: str, interval_sec: float = 1.0):
        self.symbol = symbol
        self.interval_sec = interval_sec
        self.current_bar: Optional[Dict[str, Any]] = None
        self.bar_start_time: float = 0.0

    def add_tick(self, tick: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        price = tick.get("price") or tick.get("ltp") or 0.0
        volume = tick.get("volume") or tick.get("qty") or 0.0
        timestamp = tick.get("timestamp") or time.time()

        completed_bar = None
        if self.current_bar is None or (timestamp - self.bar_start_time) >= self.interval_sec:
            if self.current_bar is not None:
                completed_bar = dict(self.current_bar)

            self.bar_start_time = timestamp
            self.current_bar = {
                "symbol": self.symbol,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "timestamp": timestamp,
                "is_bar": True,
            }
        else:
            self.current_bar["high"] = max(self.current_bar["high"], price)
            self.current_bar["low"] = min(self.current_bar["low"], price)
            self.current_bar["close"] = price
            self.current_bar["volume"] += volume

        return completed_bar


class RateLimitedAlert:
    """Prevents alert flooding by enforcing a cooldown window between alerts per stream."""

    def __init__(self, alert_fn: Callable[[str], None], cooldown_sec: float = 5.0):
        self.alert_fn = alert_fn
        self.cooldown_sec = cooldown_sec
        self.last_alert_time: Dict[str, float] = {}

    def trigger(self, stream_name: str, message: str):
        now = time.time()
        last = self.last_alert_time.get(stream_name, 0.0)
        if now - last >= self.cooldown_sec:
            self.last_alert_time[stream_name] = now
            self.alert_fn(f"[{stream_name}] {message}")


class BackpressureManager:
    """
    Explicit per-stream backpressure dispatcher with tier isolation, rate-limited alerting,
    and post-session telemetry.
    """

    def __init__(
        self,
        policy_by_stream: Dict[str, str],
        alert_fn: Optional[Callable[[str], None]] = None,
        alert_cooldown_sec: float = 5.0,
        high_watermark_pct: float = 0.8,
    ):
        self.policy_by_stream = policy_by_stream
        self.alert_limiter = RateLimitedAlert(
            alert_fn or (lambda msg: logger.warning(msg)),
            cooldown_sec=alert_cooldown_sec,
        )
        self.high_watermark_pct = high_watermark_pct
        self.metrics: Dict[str, StreamMetrics] = {}
        self.aggregators: Dict[str, TickAggregator] = {}

        for stream, policy in policy_by_stream.items():
            self.metrics[stream] = StreamMetrics(stream_name=stream, policy=policy)
            if policy == DEGRADE:
                self.aggregators[stream] = TickAggregator(symbol=stream)

    def handle_full(
        self, stream_name: str, queue: collections.deque, item: Any = None
    ) -> Optional[Any]:
        """
        Handles queue overflow according to the stream's explicit backpressure policy.
        Returns:
            Optional item to process (e.g. aggregated OHLC bar if DEGRADE produced one), or None.
        """
        policy = self.policy_by_stream.get(stream_name, DROP_OLDEST)
        metrics = self.metrics.setdefault(
            stream_name, StreamMetrics(stream_name=stream_name, policy=policy)
        )
        metrics.total_pushed += 1
        metrics.last_event_timestamp = time.time()

        if policy == NEVER_DROP:
            metrics.alert_count += 1
            max_len = queue.maxlen if queue.maxlen is not None else "unbounded"
            self.alert_limiter.trigger(
                stream_name,
                f"CRITICAL: NEVER_DROP stream '{stream_name}' at capacity ({len(queue)}/{max_len}). Risk backlog detected!",
            )
            return None

        elif policy == DROP_OLDEST:
            if queue:
                queue.popleft()
                metrics.total_dropped += 1
            if item is not None:
                queue.append(item)
            return None

        elif policy == SAMPLE:
            discard_count = max(1, len(queue) // 2)
            for _ in range(min(discard_count, len(queue))):
                queue.popleft()
                metrics.total_sampled += 1
            if item is not None:
                queue.append(item)
            return None

        elif policy == DEGRADE:
            metrics.total_degraded += 1
            aggregator = self.aggregators.setdefault(
                stream_name, TickAggregator(symbol=stream_name)
            )
            tick_dict = item if isinstance(item, dict) else {"price": item}
            completed_bar = aggregator.add_tick(tick_dict)
            return completed_bar

        return None

    def check_watermark(self, stream_name: str, queue_len: int, max_len: int):
        """Checks if queue length exceeds high watermark and triggers advisory alert."""
        if max_len > 0 and (queue_len / max_len) >= self.high_watermark_pct:
            metrics = self.metrics.get(stream_name)
            if metrics:
                metrics.high_watermark_breaches += 1
            self.alert_limiter.trigger(
                stream_name,
                f"WARNING: Stream '{stream_name}' queue watermark at {queue_len}/{max_len} ({queue_len/max_len*100:.1f}%)",
            )

    def get_metrics_summary(self) -> Dict[str, Dict[str, Any]]:
        """Returns post-session summary metrics for all managed streams."""
        return {stream: m.to_dict() for stream, m in self.metrics.items()}


# Backward compatibility wrapper class
class BackpressurePolicy:

    def __init__(self, policy_by_stream: dict, alert_fn):
        self.manager = BackpressureManager(policy_by_stream=policy_by_stream, alert_fn=alert_fn)

    def handle_full(self, stream_name, queue, item=None):
        return self.manager.handle_full(stream_name, queue, item)
