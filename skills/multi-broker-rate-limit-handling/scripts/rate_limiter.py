"""
multi-broker-rate-limit-handling: Production-grade per-endpoint-class token bucket,
priority tier queues (Tier 0 Risk Bypass), jittered exponential backoff,
and multi-broker rate limit telemetry.
"""
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
import logging
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CallTier(IntEnum):
    TIER_0_KILL = 0      # Emergency kill switch / risk cancellations
    TIER_1_ORDER = 1     # New order placement / modifications
    TIER_2_STATUS = 2    # Order status polling / margin checks
    TIER_3_DATA = 3      # Quotes / market data / historical backfill


# Backward compatibility constants
TIER_KILL = CallTier.TIER_0_KILL.value
TIER_ORDER = CallTier.TIER_1_ORDER.value
TIER_STATUS = CallTier.TIER_2_STATUS.value
TIER_DATA = CallTier.TIER_3_DATA.value


@dataclass
class RateLimiterMetrics:
    total_calls: int = 0
    tier_0_bypasses: int = 0
    rate_limit_hits_429: int = 0
    total_backoff_sec: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "tier_0_bypasses": self.tier_0_bypasses,
            "rate_limit_hits_429": self.rate_limit_hits_429,
            "total_backoff_sec": round(self.total_backoff_sec, 3),
        }


class TokenBucket:
    """Thread-safe token bucket rate limiter with replenish rate and capacity limits."""

    def __init__(self, rate_per_sec: float, capacity: float):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = capacity
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def try_consume(self, tokens: float = 1.0) -> bool:
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last = now
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False


class MultiBrokerRateLimiter:
    """
    Per-broker, per-endpoint token bucket manager with Tier 0 priority bypass
    and jittered exponential backoff for HTTP 429 rate limit responses.
    """

    def __init__(self, alert_fn: Optional[Callable[[str], None]] = None):
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))
        self.buckets: Dict[str, TokenBucket] = {}
        self.metrics = RateLimiterMetrics()

    def register_endpoint_bucket(self, broker: str, endpoint_category: str, rate_per_sec: float, capacity: float):
        key = f"{broker.lower()}:{endpoint_category.lower()}"
        self.buckets[key] = TokenBucket(rate_per_sec=rate_per_sec, capacity=capacity)

    def _get_bucket(self, broker: str, endpoint_category: str) -> TokenBucket:
        key = f"{broker.lower()}:{endpoint_category.lower()}"
        if key not in self.buckets:
            # Default fallback 10 calls/sec with 10 capacity
            self.buckets[key] = TokenBucket(rate_per_sec=10.0, capacity=10.0)
        return self.buckets[key]

    def execute_call(
        self,
        broker: str,
        endpoint_category: str,
        tier: int,
        call_fn: Callable[[], Any],
        max_retries: int = 3,
    ) -> Any:
        self.metrics.total_calls += 1
        bucket = self._get_bucket(broker, endpoint_category)

        # Tier 0 (Kill Switch): Bypass rate-limit queueing if bucket tokens exist
        if tier == CallTier.TIER_0_KILL.value:
            self.metrics.tier_0_bypasses += 1
            if not bucket.try_consume(1.0):
                self.alert_fn(f"CRITICAL: Tier 0 Call to '{broker}:{endpoint_category}' bucket empty!")
            return call_fn()

        # Tiers 1-3: Wait for bucket availability and apply exponential backoff on 429
        attempt = 0
        while attempt <= max_retries:
            while not bucket.try_consume(1.0):
                time.sleep(0.05)

            try:
                return call_fn()
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "Rate limit" in err_str:
                    self.metrics.rate_limit_hits_429 += 1
                    attempt += 1
                    if attempt > max_retries:
                        raise e
                    # Exponential backoff with random jitter
                    backoff = (0.2 * (2 ** attempt)) + random.uniform(0.05, 0.2)
                    self.metrics.total_backoff_sec += backoff
                    logger.warning(f"HTTP 429 on {broker}:{endpoint_category} (Tier {tier}). Backing off {backoff:.2f}s...")
                    time.sleep(backoff)
                else:
                    raise e


# Backward compatibility wrapper class
class TieredCallQueue:

    def __init__(self, buckets: dict):
        self.buckets = buckets  # {tier: TokenBucket}
        self.queues = {tier: deque() for tier in buckets}

    def enqueue(self, tier, call_fn):
        self.queues[tier].append(call_fn)

    def drain_tier(self, tier):
        bucket = self.buckets[tier]
        results = []
        q = self.queues[tier]
        while q and bucket.try_consume():
            results.append(q.popleft()())
        return results

    def drain_all_by_priority(self):
        for tier in sorted(self.queues):
            self.drain_tier(tier)
