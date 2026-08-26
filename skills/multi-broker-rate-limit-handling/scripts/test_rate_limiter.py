"""
Unit tests for multi-broker-rate-limit-handling.

Covered behaviour:
1.  TokenBucket capacity, replenishment, input validation, monotonic-clock safety.
2.  Multi-window budgets (Fyers-style 10/sec + 200/min) and all-or-nothing consumption.
3.  Account-wide budgets shared across endpoint categories (Alpaca/Breeze-style).
4.  Structural rate-limit classification -- and the regression that an unrelated
    error whose text contains "429" is NOT retried.
5.  Full-jitter backoff bounded by a cap.
6.  RFC 9110 Retry-After honouring, in both delay-seconds and HTTP-date form.
7.  Tier 0 bypass and its exhausted-budget alert.
8.  Strict tier priority: a Tier 3 burst cannot overtake a waiting Tier 1 order.
9.  Wait deadlines instead of unbounded spinning.
10. Strict mode rejecting unregistered budgets (typo protection).
11. Concurrency: no over-issue under thread contention.
12. Telemetry and backward compatibility with TieredCallQueue.
"""
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from rate_limiter import (
    _PriorityGate,
    CallTier,
    MultiBrokerRateLimiter,
    RateLimitError,
    RateLimitWaitTimeout,
    TIER_DATA,
    TIER_KILL,
    TieredCallQueue,
    TokenBucket,
    UnregisteredBudgetError,
    default_rate_limit_classifier,
    full_jitter_backoff,
    parse_retry_after,
)


class FakeClock:
    """Injectable monotonic clock so pacing is tested deterministically."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.slept = 0.0

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


class TestTokenBucket(unittest.TestCase):

    def test_capacity_and_replenishment(self):
        clock = FakeClock()
        bucket = TokenBucket(rate_per_sec=5.0, capacity=5.0, time_fn=clock.time)
        self.assertTrue(bucket.try_consume(5.0))
        self.assertFalse(bucket.try_consume(1.0))

        clock.advance(0.25)  # 1.25 tokens replenished
        self.assertTrue(bucket.try_consume(1.0))
        self.assertFalse(bucket.try_consume(1.0))

    def test_refill_never_exceeds_capacity(self):
        clock = FakeClock()
        bucket = TokenBucket(rate_per_sec=10.0, capacity=4.0, time_fn=clock.time)
        clock.advance(3600.0)
        self.assertEqual(bucket.wait_time_for(4.0), 0.0)
        self.assertTrue(bucket.try_consume(4.0))
        self.assertFalse(bucket.try_consume(1.0))

    def test_invalid_construction_rejected(self):
        # A zero/negative rate previously produced a bucket that could never refill,
        # which made the limiter's acquire loop spin forever.
        for rate, capacity in ((0.0, 5.0), (-1.0, 5.0), (5.0, 0.0), (5.0, -2.0)):
            with self.assertRaises(ValueError):
                TokenBucket(rate_per_sec=rate, capacity=capacity)

    def test_request_larger_than_capacity_is_rejected_not_hung(self):
        bucket = TokenBucket(rate_per_sec=1.0, capacity=2.0)
        with self.assertRaises(ValueError):
            bucket.wait_time_for(5.0)

    def test_clock_going_backwards_does_not_remove_tokens(self):
        clock = FakeClock()
        bucket = TokenBucket(rate_per_sec=1.0, capacity=10.0, time_fn=clock.time)
        self.assertTrue(bucket.try_consume(5.0))
        clock.advance(-30.0)  # simulated NTP step-back
        self.assertTrue(bucket.try_consume(5.0))

    def test_per_interval_factory(self):
        clock = FakeClock()
        bucket = TokenBucket.per_interval(200, 60.0, time_fn=clock.time)
        self.assertAlmostEqual(bucket.rate, 200 / 60.0)
        self.assertEqual(bucket.capacity, 200)

    def test_wait_time_is_a_probe_not_a_consumption(self):
        clock = FakeClock()
        bucket = TokenBucket(rate_per_sec=2.0, capacity=2.0, time_fn=clock.time)
        self.assertEqual(bucket.wait_time_for(1.0), 0.0)
        self.assertEqual(bucket.wait_time_for(1.0), 0.0)  # unchanged by probing
        self.assertTrue(bucket.try_consume(2.0))
        self.assertAlmostEqual(bucket.wait_time_for(1.0), 0.5)


class TestBackoffAndRetryAfter(unittest.TestCase):

    def test_full_jitter_is_bounded_by_the_cap(self):
        # The pre-fix formula was `0.2 * 2**attempt + uniform(0.05, 0.2)` with no cap
        # at all: attempt 8 yielded ~51s. Full jitter must stay inside [0, cap).
        samples = [full_jitter_backoff(8, base_sec=1.0, cap_sec=16.0) for _ in range(400)]
        self.assertTrue(all(0.0 <= s <= 16.0 for s in samples))
        self.assertLess(max(samples), 16.0001)
        mean = sum(samples) / len(samples)
        self.assertGreater(mean, 5.0)
        self.assertLess(mean, 11.0)

    def test_full_jitter_actually_varies_at_the_cap(self):
        # Additive-jitter-under-a-cap returns exactly `cap` for every caller once the
        # exponential term passes the cap, so a throttled fleet retries in lockstep.
        samples = {round(full_jitter_backoff(10, base_sec=1.0, cap_sec=4.0), 6) for _ in range(200)}
        self.assertGreater(len(samples), 50)

    def test_backoff_rejects_invalid_parameters(self):
        with self.assertRaises(ValueError):
            full_jitter_backoff(-1)
        with self.assertRaises(ValueError):
            full_jitter_backoff(1, base_sec=0.0)

    def test_parse_retry_after_delay_seconds(self):
        self.assertEqual(parse_retry_after("120"), 120.0)
        self.assertEqual(parse_retry_after(30), 30.0)
        self.assertEqual(parse_retry_after("-5"), 0.0)

    def test_parse_retry_after_http_date(self):
        now = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
        future = (now + timedelta(seconds=120)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertAlmostEqual(parse_retry_after(future, now=now), 120.0, delta=1.0)

    def test_parse_retry_after_past_date_clamps_to_zero(self):
        now = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
        past = (now - timedelta(seconds=300)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertEqual(parse_retry_after(past, now=now), 0.0)

    def test_parse_retry_after_malformed_returns_none(self):
        # None must mean "fall back to jitter", never "retry immediately".
        for bad in ("soon", "", None, object(), True):
            self.assertIsNone(parse_retry_after(bad))


class TestClassification(unittest.TestCase):

    def test_rate_limit_error_is_classified(self):
        limited, retry_after = default_rate_limit_classifier(
            RateLimitError("throttled", retry_after="15")
        )
        self.assertTrue(limited)
        self.assertEqual(retry_after, 15.0)

    def test_status_code_attribute_is_classified(self):
        exc = Exception("nope")
        exc.status_code = 429
        limited, _ = default_rate_limit_classifier(exc)
        self.assertTrue(limited)

    def test_response_object_shape_is_classified(self):
        class Response:
            status_code = 429
            headers = {"Retry-After": "7"}

        exc = Exception("too many requests")
        exc.response = Response()
        limited, retry_after = default_rate_limit_classifier(exc)
        self.assertTrue(limited)
        self.assertEqual(retry_after, 7.0)

    def test_message_text_alone_is_not_classified(self):
        # THE regression: "429" appearing in an order id, price or quantity must not
        # be read as a throttle.
        for message in (
            "Order 429123 rejected: insufficient margin",
            "Limit price 429.50 outside circuit band",
            "HTTP 429 Rate Limit Exceeded",
        ):
            limited, _ = default_rate_limit_classifier(Exception(message))
            self.assertFalse(limited, message)

    def test_permanent_client_errors_are_not_classified(self):
        for status in (400, 401, 403, 404):
            exc = Exception("permanent")
            exc.status_code = status
            limited, _ = default_rate_limit_classifier(exc)
            self.assertFalse(limited)


class TestMultiBrokerRateLimiter(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.alerts = []
        self.limiter = MultiBrokerRateLimiter(
            alert_fn=self.alerts.append,
            time_fn=self.clock.time,
            sleep_fn=self.clock.sleep,
            rand_fn=lambda lo, hi: hi,  # deterministic worst-case backoff
        )
        self.limiter.register_endpoint_bucket("fyers", "order", rate_per_sec=10.0, capacity=10.0)
        self.limiter.register_endpoint_bucket("fyers", "quote", rate_per_sec=2.0, capacity=2.0)

    # -- Tier 0 ------------------------------------------------------------
    def test_tier_0_bypass_executes(self):
        call_mock = Mock(return_value="KILL_SUCCESS")
        res = self.limiter.execute_call(
            "fyers", "order", CallTier.TIER_0_KILL.value, call_mock
        )
        self.assertEqual(res, "KILL_SUCCESS")
        call_mock.assert_called_once()
        self.assertEqual(self.limiter.metrics.tier_0_bypasses, 0)  # budget had capacity
        self.assertEqual(self.alerts, [])

    def test_tier_0_dispatches_and_alerts_when_budget_exhausted(self):
        for _ in range(10):
            self.assertTrue(self.limiter._get_budget("fyers", "order").try_consume(1.0))
        call_mock = Mock(return_value="KILL_SUCCESS")
        res = self.limiter.execute_call(
            "fyers", "order", CallTier.TIER_0_KILL.value, call_mock
        )
        self.assertEqual(res, "KILL_SUCCESS")  # never withheld
        call_mock.assert_called_once()
        self.assertEqual(self.limiter.metrics.tier_0_bypasses, 1)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Tier 0", self.alerts[0])

    # -- retry / backoff ---------------------------------------------------
    def test_structured_429_is_retried(self):
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RateLimitError("throttled")
            return "SUCCESS"

        res = self.limiter.execute_call(
            "fyers", "order", CallTier.TIER_1_ORDER.value, flaky, max_retries=2
        )
        self.assertEqual(res, "SUCCESS")
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(self.limiter.metrics.rate_limit_hits_429, 1)
        self.assertEqual(self.limiter.metrics.rate_limit_hits_by_tier, {1: 1})

    def test_unclassifiable_error_is_never_retried(self):
        # Regression: the old substring classifier retried this, which can duplicate
        # a live order the broker already accepted.
        attempts = {"n": 0}

        def rejected():
            attempts["n"] += 1
            raise Exception("Order 429123 rejected: insufficient margin")

        with self.assertRaises(Exception) as ctx:
            self.limiter.execute_call(
                "fyers", "order", CallTier.TIER_1_ORDER.value, rejected, max_retries=3
            )
        self.assertIn("insufficient margin", str(ctx.exception))
        self.assertEqual(attempts["n"], 1)
        self.assertEqual(self.limiter.metrics.rate_limit_hits_429, 0)

    def test_retry_budget_is_exhausted_then_error_propagates(self):
        attempts = {"n": 0}

        def always_limited():
            attempts["n"] += 1
            raise RateLimitError("throttled")

        with self.assertRaises(RateLimitError):
            self.limiter.execute_call(
                "fyers", "order", CallTier.TIER_2_STATUS.value, always_limited, max_retries=2
            )
        self.assertEqual(attempts["n"], 3)  # initial + 2 retries

    def test_backoff_is_capped(self):
        # rand_fn returns the ceiling, so each sleep equals min(cap, base*2**attempt).
        limiter = MultiBrokerRateLimiter(
            alert_fn=self.alerts.append,
            time_fn=self.clock.time,
            sleep_fn=self.clock.sleep,
            rand_fn=lambda lo, hi: hi,
            base_backoff_sec=1.0,
            max_backoff_sec=2.0,
        )
        limiter.register_endpoint_bucket("b", "order", 100.0, 100.0)

        def always_limited():
            raise RateLimitError("throttled")

        with self.assertRaises(RateLimitError):
            limiter.execute_call("b", "order", 2, always_limited, max_retries=5)
        # attempts 1..5 -> min(2, 1*2**n) = 2,2,2,2 for n>=1 ; total must not blow up.
        self.assertLessEqual(limiter.metrics.total_backoff_sec, 5 * 2.0 + 1e-9)
        self.assertGreater(limiter.metrics.total_backoff_sec, 0.0)

    def test_retry_after_overrides_jitter(self):
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RateLimitError("throttled", retry_after="5")
            return "OK"

        before = self.clock.slept
        res = self.limiter.execute_call("fyers", "order", 2, flaky, max_retries=2)
        self.assertEqual(res, "OK")
        self.assertAlmostEqual(self.clock.slept - before, 5.0)
        self.assertEqual(self.limiter.metrics.retry_after_honored, 1)

    def test_retry_after_beyond_cap_escalates_instead_of_sleeping(self):
        def flaky():
            raise RateLimitError("throttled", retry_after="3600")

        before = self.clock.slept
        with self.assertRaises(RateLimitError):
            self.limiter.execute_call("fyers", "order", 2, flaky, max_retries=3)
        self.assertEqual(self.clock.slept, before)  # did not block for an hour
        self.assertTrue(any("Retry-After" in a for a in self.alerts))

    # -- multi-window & account budgets ------------------------------------
    def test_multi_window_budget_enforces_the_stricter_window(self):
        # Fyers v3 shape: 10/sec AND 200/min against the same counter. Ten calls in
        # the first second are fine; the per-minute window still holds 190.
        limiter = MultiBrokerRateLimiter(time_fn=self.clock.time, sleep_fn=self.clock.sleep)
        limiter.register_endpoint_windows("fyers", "data", [(10, 1.0), (200, 60.0)])
        budget = limiter._get_budget("fyers", "data")

        for _ in range(10):
            self.assertTrue(budget.try_consume(1.0))
        self.assertFalse(budget.try_consume(1.0))  # per-second window binds

        self.clock.advance(60.0)
        granted = sum(1 for _ in range(200) if budget.try_consume(1.0))
        self.assertEqual(granted, 10)  # per-second window still caps the burst

    def test_all_or_nothing_consumption_does_not_leak_tokens(self):
        # If the per-minute window refuses, the per-second window must not have been
        # debited -- otherwise every rejected attempt silently under-throttles.
        limiter = MultiBrokerRateLimiter(time_fn=self.clock.time, sleep_fn=self.clock.sleep)
        limiter.register_endpoint_windows("x", "data", [(100, 1.0), (5, 60.0)])
        budget = limiter._get_budget("x", "data")

        for _ in range(5):
            self.assertTrue(budget.try_consume(1.0))
        for _ in range(20):
            self.assertFalse(budget.try_consume(1.0))  # per-minute window exhausted

        fast_window = min(budget.buckets, key=lambda b: b.capacity / b.rate)
        self.assertGreater(fast_window.tokens, 90.0)  # not drained by failed attempts

    def test_account_budget_is_shared_across_endpoint_categories(self):
        # Alpaca/Breeze shape: one account-wide cap covering every endpoint.
        limiter = MultiBrokerRateLimiter(time_fn=self.clock.time, sleep_fn=self.clock.sleep)
        limiter.register_endpoint_bucket("alpaca", "order", 100.0, 100.0)
        limiter.register_endpoint_bucket("alpaca", "quote", 100.0, 100.0)
        limiter.register_account_bucket("alpaca", [(4, 60.0)])

        order_budget = limiter._get_budget("alpaca", "order")
        quote_budget = limiter._get_budget("alpaca", "quote")

        self.assertTrue(order_budget.try_consume(1.0))
        self.assertTrue(order_budget.try_consume(1.0))
        self.assertTrue(quote_budget.try_consume(1.0))
        self.assertTrue(quote_budget.try_consume(1.0))
        # Account cap of 4/min is now spent, even though both endpoint buckets are full.
        self.assertFalse(order_budget.try_consume(1.0))
        self.assertFalse(quote_budget.try_consume(1.0))

    # -- admission control -------------------------------------------------
    def test_wait_deadline_raises_instead_of_spinning_forever(self):
        limiter = MultiBrokerRateLimiter(
            time_fn=self.clock.time, sleep_fn=self.clock.sleep, max_wait_sec=0.5
        )
        limiter.register_endpoint_windows("slow", "data", [(1, 3600.0)])
        limiter.execute_call("slow", "data", 3, lambda: "first")

        with self.assertRaises(RateLimitWaitTimeout) as ctx:
            limiter.execute_call("slow", "data", 3, lambda: "second")
        self.assertIn("slow:data", str(ctx.exception))
        self.assertEqual(limiter.metrics.wait_timeouts, 1)

    def test_priority_gate_blocks_lower_tiers_while_a_higher_tier_waits(self):
        gate = _PriorityGate()
        self.assertTrue(gate.may_proceed(3))
        gate.enter(1)
        self.assertFalse(gate.may_proceed(3))
        self.assertFalse(gate.may_proceed(2))
        self.assertTrue(gate.may_proceed(1))   # peers are not blocked by each other
        self.assertTrue(gate.may_proceed(0))
        gate.leave(1)
        self.assertTrue(gate.may_proceed(3))

    def test_tier_1_order_is_not_starved_by_a_tier_3_burst(self):
        # A quote burst and one order placement all contend for a single-token budget
        # that refills every 200ms. Every caller is confirmed parked at the gate while
        # the budget is empty, so exactly one token is up for grabs when it refills.
        # With strict priority the Tier 1 order must take it; without the gate it wins
        # only ~1 time in 9, which is what makes this a regression test rather than a
        # coin flip. (Registration is sub-millisecond against a 200ms refill.)
        n_data = 8
        limiter = MultiBrokerRateLimiter(max_wait_sec=10.0)
        limiter.register_endpoint_windows("kite", "shared", [(1, 0.2)])
        budget = limiter._get_budget("kite", "shared")
        gate = limiter._get_gate("kite")
        self.assertTrue(budget.try_consume(1.0))  # drain, so every caller must wait

        served = []

        def call(tier, tag):
            try:
                limiter.execute_call("kite", "shared", tier, lambda: served.append(tag))
            except RateLimitWaitTimeout:
                pass

        def wait_for_waiters(tier, count, timeout=5.0):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if gate._waiting.get(tier, 0) >= count:
                    return True
                time.sleep(0.002)
            return False

        threads = [
            threading.Thread(target=call, args=(3, ("data", i))) for i in range(n_data)
        ]
        for t in threads:
            t.start()
        self.assertTrue(wait_for_waiters(3, n_data), "Tier 3 callers never parked")

        order_thread = threading.Thread(target=call, args=(1, ("order", 0)))
        order_thread.start()
        threads.append(order_thread)
        self.assertTrue(wait_for_waiters(1, 1), "Tier 1 caller never parked")

        for t in threads:
            t.join(timeout=20.0)

        self.assertTrue(served, "no call was ever served")
        self.assertEqual(
            served[0],
            ("order", 0),
            f"Tier 1 order was overtaken by lower-priority calls: {served[:3]}",
        )

    def test_concurrent_callers_do_not_over_issue(self):
        limiter = MultiBrokerRateLimiter(max_wait_sec=0.05)
        limiter.register_endpoint_windows("race", "data", [(4, 3600.0)])
        granted = []
        lock = threading.Lock()
        start = threading.Event()

        def worker():
            start.wait(5.0)
            try:
                limiter.execute_call("race", "data", 3, lambda: None)
            except RateLimitWaitTimeout:
                return
            with lock:
                granted.append(1)

        threads = [threading.Thread(target=worker) for _ in range(32)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(timeout=15.0)

        self.assertEqual(len(granted), 4)

    # -- registration safety -----------------------------------------------
    def test_strict_mode_rejects_unregistered_endpoint(self):
        # A typo ("quotes" vs the registered "quote") must not silently inherit a
        # permissive default in place of the broker's real limit.
        limiter = MultiBrokerRateLimiter(strict=True, time_fn=self.clock.time)
        limiter.register_endpoint_bucket("kite", "quote", 1.0, 1.0)
        with self.assertRaises(UnregisteredBudgetError):
            limiter.execute_call("kite", "quotes", 3, lambda: None)

    def test_non_strict_mode_warns_and_falls_back(self):
        with self.assertLogs("rate_limiter", level="WARNING") as logs:
            self.limiter.execute_call("unknown", "whatever", 3, lambda: None)
        self.assertTrue(any("not a documented broker limit" in m for m in logs.output))

    def test_broker_and_endpoint_keys_are_case_insensitive(self):
        self.limiter.execute_call("FYERS", "ORDER", 2, lambda: None)
        self.assertIn("fyers:order", self.limiter.snapshot()["endpoint_budgets"])

    def test_invalid_execute_call_arguments_rejected(self):
        with self.assertRaises(ValueError):
            self.limiter.execute_call("fyers", "order", 2, lambda: None, max_retries=-1)
        with self.assertRaises(ValueError):
            self.limiter.execute_call("fyers", "order", 2, lambda: None, tokens=0)

    def test_unsatisfiable_token_request_fails_fast(self):
        # An oversized request can never be granted. Left to the wait loop it would
        # burn the entire deadline and then surface as a timeout, disguising a
        # configuration error as transient congestion.
        limiter = MultiBrokerRateLimiter(time_fn=self.clock.time, sleep_fn=self.clock.sleep)
        limiter.register_endpoint_bucket("h", "order", 5.0, 5.0)
        before = self.clock.slept
        for tier in (0, 1, 3):
            with self.assertRaises(ValueError):
                limiter.execute_call("h", "order", tier, lambda: None, tokens=99)
        self.assertEqual(self.clock.slept, before)  # did not wait at all

    # -- telemetry ---------------------------------------------------------
    def test_snapshot_reports_metrics_and_budgets(self):
        self.limiter.execute_call("fyers", "order", CallTier.TIER_0_KILL.value, lambda: None)
        self.limiter.execute_call("fyers", "quote", CallTier.TIER_3_DATA.value, lambda: None)
        snap = self.limiter.snapshot()
        self.assertEqual(snap["metrics"]["total_calls"], 2)
        self.assertEqual(snap["metrics"]["calls_by_tier"], {0: 1, 3: 1})
        self.assertEqual(snap["endpoint_budgets"], ["fyers:order", "fyers:quote"])
        self.assertEqual(snap["account_budgets"], [])


class TestTieredCallQueueBackwardCompatibility(unittest.TestCase):

    def test_drains_highest_priority_first(self):
        queue = TieredCallQueue({TIER_KILL: TokenBucket(10, 10), TIER_DATA: TokenBucket(10, 10)})
        m0 = Mock(return_value="res0")
        m3 = Mock(return_value="res3")
        queue.enqueue(TIER_KILL, m0)
        queue.enqueue(TIER_DATA, m3)

        results = queue.drain_all_by_priority()
        m0.assert_called_once()
        m3.assert_called_once()
        self.assertEqual(results, ["res0", "res3"])

    def test_enqueue_to_unregistered_tier_raises(self):
        queue = TieredCallQueue({TIER_KILL: TokenBucket(10, 10)})
        with self.assertRaises(KeyError):
            queue.enqueue(TIER_DATA, lambda: None)

    def test_drain_stops_when_bucket_is_empty(self):
        queue = TieredCallQueue({TIER_DATA: TokenBucket(rate_per_sec=1.0, capacity=2.0)})
        for _ in range(5):
            queue.enqueue(TIER_DATA, lambda: "x")
        self.assertEqual(len(queue.drain_tier(TIER_DATA)), 2)
        self.assertEqual(len(queue.queues[TIER_DATA]), 3)


if __name__ == "__main__":
    unittest.main()
