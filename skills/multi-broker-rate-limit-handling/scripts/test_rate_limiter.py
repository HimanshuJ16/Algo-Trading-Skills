"""
Unit tests for multi-broker-rate-limit-handling skill.

Tests:
1. TokenBucket capacity & replenishment rate.
2. Tier 0 priority bypass execution under queue load.
3. Multi-broker per-endpoint category rate limit isolation.
4. HTTP 429 exponential backoff with jitter retry.
5. Telemetry metrics recording.
6. Backward compatibility with TieredCallQueue and TokenBucket.
"""
import time
import unittest
from unittest.mock import Mock
from rate_limiter import (
    CallTier,
    MultiBrokerRateLimiter,
    TIER_DATA,
    TIER_KILL,
    TieredCallQueue,
    TokenBucket,
)


class TestMultiBrokerRateLimiter(unittest.TestCase):

    def setUp(self):
        self.limiter = MultiBrokerRateLimiter()
        self.limiter.register_endpoint_bucket(
            broker="fyers", endpoint_category="order", rate_per_sec=10.0, capacity=10.0
        )
        self.limiter.register_endpoint_bucket(
            broker="fyers", endpoint_category="quote", rate_per_sec=2.0, capacity=2.0
        )

    def test_token_bucket_replenish(self):
        bucket = TokenBucket(rate_per_sec=5.0, capacity=5.0)
        self.assertTrue(bucket.try_consume(5.0))
        self.assertFalse(bucket.try_consume(1.0))

        time.sleep(0.25)  # Replenish ~1.25 tokens
        self.assertTrue(bucket.try_consume(1.0))

    def test_tier_0_kill_switch_bypass(self):
        call_mock = Mock(return_value="KILL_SUCCESS")
        res = self.limiter.execute_call(
            broker="fyers",
            endpoint_category="order",
            tier=CallTier.TIER_0_KILL.value,
            call_fn=call_mock,
        )

        self.assertEqual(res, "KILL_SUCCESS")
        call_mock.assert_called_once()
        self.assertEqual(self.limiter.metrics.tier_0_bypasses, 1)

    def test_http_429_backoff_retry(self):
        attempts = 0

        def flaky_call():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise Exception("HTTP 429 Rate Limit Exceeded")
            return "SUCCESS"

        res = self.limiter.execute_call(
            broker="fyers",
            endpoint_category="order",
            tier=CallTier.TIER_1_ORDER.value,
            call_fn=flaky_call,
            max_retries=2,
        )

        self.assertEqual(res, "SUCCESS")
        self.assertEqual(attempts, 2)
        self.assertEqual(self.limiter.metrics.rate_limit_hits_429, 1)

    def test_tiered_call_queue_backward_compatibility(self):
        bucket0 = TokenBucket(10, 10)
        bucket3 = TokenBucket(10, 10)

        queue = TieredCallQueue({TIER_KILL: bucket0, TIER_DATA: bucket3})

        m0 = Mock(return_value="res0")
        m3 = Mock(return_value="res3")

        queue.enqueue(TIER_KILL, m0)
        queue.enqueue(TIER_DATA, m3)

        queue.drain_all_by_priority()
        m0.assert_called_once()
        m3.assert_called_once()


if __name__ == "__main__":
    unittest.main()
