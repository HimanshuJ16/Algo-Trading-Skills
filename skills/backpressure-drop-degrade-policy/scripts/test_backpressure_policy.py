"""Verification unit tests for backpressure_policy.py.

Coverage:
1. NEVER_DROP surfaces rejection to the caller instead of silently discarding.
2. Undeclared streams fail closed rather than defaulting to a drop policy.
3. Concurrent consumers cannot crash the overflow path (IndexError regression).
4. DROP_OLDEST / SAMPLE / DEGRADE behaviour matches documentation.
5. Watermark early warning fires before capacity, including for NEVER_DROP.
6. Telemetry semantics are correct and non-misleading.
7. Input validation and aggregator edge cases.
"""
import collections
import logging
import threading
import time
import unittest

from backpressure_policy import (
    BackpressureAction,
    BackpressureError,
    BackpressureManager,
    BackpressurePolicy,
    DEGRADE,
    DROP_OLDEST,
    NEVER_DROP,
    NeverDropOverflowError,
    RateLimitedAlert,
    SAMPLE,
    TickAggregator,
    UnknownStreamError,
)

logging.getLogger("backpressure_policy").setLevel(logging.CRITICAL)

POLICIES = {
    "risk_stream": NEVER_DROP,
    "ltp_stream": DROP_OLDEST,
    "ui_dashboard": SAMPLE,
    "trend_analytics": DEGRADE,
}


class BaseManagerTest(unittest.TestCase):
    def setUp(self):
        self.alerts = []
        self.manager = BackpressureManager(
            policy_by_stream=POLICIES,
            alert_fn=self.alerts.append,
            alert_cooldown_sec=0.1,
        )


class TestNeverDrop(BaseManagerTest):
    """The headline guarantee: risk data is never silently discarded."""

    def test_rejection_is_visible_to_the_caller(self):
        queue = collections.deque([1, 2, 3], maxlen=3)
        decision = self.manager.handle_full("risk_stream", queue, item=999)

        self.assertFalse(decision.accepted)
        self.assertFalse(decision, "decision should be falsy when the item was rejected")
        self.assertEqual(decision.action, BackpressureAction.REJECTED_OVERFLOW)
        # The caller can still recover the item — it is not swallowed.
        self.assertEqual(decision.rejected_item, 999)
        self.assertNotIn(999, queue)

    def test_overflow_callback_receives_the_item(self):
        seen = []
        mgr = BackpressureManager(
            POLICIES, alert_fn=self.alerts.append, alert_cooldown_sec=0,
            on_never_drop_overflow=lambda s, i: seen.append((s, i)),
        )
        mgr.handle_full("risk_stream", collections.deque([1], maxlen=1), item=42)
        self.assertEqual(seen, [("risk_stream", 42)])

    def test_strict_mode_raises(self):
        mgr = BackpressureManager(
            POLICIES, alert_fn=self.alerts.append, alert_cooldown_sec=0,
            strict_never_drop=True,
        )
        with self.assertRaises(NeverDropOverflowError):
            mgr.handle_full("risk_stream", collections.deque([1], maxlen=1), item=42)

    def test_alert_fires_and_is_rate_limited(self):
        queue = collections.deque([1, 2, 3], maxlen=3)
        self.manager.handle_full("risk_stream", queue, 4)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("CRITICAL", self.alerts[0])
        self.assertIn("NEVER_DROP", self.alerts[0])

        self.manager.handle_full("risk_stream", queue, 5)
        self.assertEqual(len(self.alerts), 1, "cooldown should suppress the duplicate")

        time.sleep(0.15)
        self.manager.handle_full("risk_stream", queue, 6)
        self.assertEqual(len(self.alerts), 2, "alert should fire again after cooldown")

    def test_never_drop_gets_early_warning_before_capacity(self):
        # SKILL.md requires an alert as the stream *approaches* capacity; an
        # alert only at capacity is already too late for a risk stream.
        queue = collections.deque([1, 2, 3, 4], maxlen=5)  # 80% full
        self.manager.observe("risk_stream", queue)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("CRITICAL", self.alerts[0])
        self.assertIn("approaching capacity", self.alerts[0])


class TestUnknownStream(BaseManagerTest):
    """A mistyped risk stream must not silently become a drop stream."""

    def test_undeclared_stream_raises_by_default(self):
        queue = collections.deque([1, 2, 3], maxlen=3)
        with self.assertRaises(UnknownStreamError):
            self.manager.handle_full("risk_strem", queue, item=42)
        self.assertNotIn(42, queue)

    def test_observe_also_rejects_undeclared_stream(self):
        with self.assertRaises(UnknownStreamError):
            self.manager.observe("typo", collections.deque([1], maxlen=2))

    def test_non_strict_mode_defaults_but_alerts(self):
        mgr = BackpressureManager(
            POLICIES, alert_fn=self.alerts.append, alert_cooldown_sec=0,
            strict_unknown_stream=False,
        )
        queue = collections.deque([1, 2, 3], maxlen=3)
        decision = mgr.handle_full("risk_strem", queue, item=42)
        self.assertTrue(decision.accepted)
        self.assertTrue(any("undeclared stream" in a for a in self.alerts))


class StaleLengthDeque(collections.deque):
    """A deque that over-reports its length.

    This reproduces the exact TOCTOU window deterministically: a naive code
    read ``len(queue)`` once and then popped that many times, so a consumer
    draining in between raised ``IndexError``. Simulating it with a stale length
    is reliable, whereas racing real threads detected the defect in 36/40 runs
    one moment and 0/4 the next — too flaky to serve as a regression test.
    """

    def __len__(self):
        return super().__len__() + 5


class TestConcurrencySafety(BaseManagerTest):
    """Regression: an earlier overflow path raised IndexError against a live consumer."""

    def test_pops_are_guarded_against_a_stale_length_read(self):
        mgr = BackpressureManager(
            {"ui": SAMPLE}, alert_fn=lambda m: None, alert_cooldown_sec=0,
            sample_keep_every_n=1,
        )
        queue = StaleLengthDeque([1, 2, 3])
        # older this raised IndexError: pop from an empty deque.
        decision = mgr.handle_full("ui", queue, item=99)
        self.assertTrue(decision.accepted)

    def test_drop_oldest_guarded_against_stale_length(self):
        mgr = BackpressureManager(
            {"ltp": DROP_OLDEST}, alert_fn=lambda m: None, alert_cooldown_sec=0
        )
        queue = StaleLengthDeque()
        decision = mgr.handle_full("ltp", queue, item=1)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.discarded_count, 0)

    def test_concurrent_producers_smoke(self):
        """Coarse smoke test: no crashes and no lost overflow counts."""
        mgr = BackpressureManager(
            {"ltp": DROP_OLDEST}, alert_fn=lambda m: None, alert_cooldown_sec=0
        )
        queue = collections.deque(range(10), maxlen=10)
        n_threads, n_each = 4, 500

        def push():
            for i in range(n_each):
                mgr.handle_full("ltp", queue, item=i)

        threads = [threading.Thread(target=push) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(
            mgr.get_metrics_summary()["ltp"]["overflow_events"], n_threads * n_each
        )


class TestDropOldest(BaseManagerTest):
    def test_evicts_oldest_and_admits_newest(self):
        queue = collections.deque([10, 20, 30], maxlen=3)
        decision = self.manager.handle_full("ltp_stream", queue, 40)

        self.assertEqual(list(queue), [20, 30, 40])
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.action, BackpressureAction.ENQUEUED_AFTER_DROP)
        self.assertEqual(decision.discarded_count, 1)
        self.assertEqual(self.manager.get_metrics_summary()["ltp_stream"]["total_dropped"], 1)

    def test_empty_queue_does_not_raise(self):
        queue = collections.deque([], maxlen=3)
        decision = self.manager.handle_full("ltp_stream", queue, 40)
        self.assertEqual(list(queue), [40])
        self.assertEqual(decision.discarded_count, 0)


class TestSample(BaseManagerTest):
    """SAMPLE must throttle admission, not destroy the existing backlog."""

    def test_backlog_is_preserved(self):
        mgr = BackpressureManager(
            {"ui": SAMPLE}, alert_fn=lambda m: None, alert_cooldown_sec=0,
            sample_keep_every_n=2,
        )
        queue = collections.deque(range(100), maxlen=100)
        mgr.handle_full("ui", queue, item=999)
        # older discarded ~50 items on a single overflow event.
        self.assertGreaterEqual(len(queue), 99)

    def test_keeps_one_in_every_n(self):
        mgr = BackpressureManager(
            {"ui": SAMPLE}, alert_fn=lambda m: None, alert_cooldown_sec=0,
            sample_keep_every_n=3,
        )
        queue = collections.deque(range(5), maxlen=5)
        actions = [mgr.handle_full("ui", queue, item=100 + i).accepted for i in range(9)]
        # 1-in-3 admitted: items 3, 6, 9 of the sequence.
        self.assertEqual(actions, [False, False, True, False, False, True, False, False, True])
        self.assertEqual(mgr.get_metrics_summary()["ui"]["total_sampled"], 6)

    def test_keep_every_1_admits_everything(self):
        mgr = BackpressureManager(
            {"ui": SAMPLE}, alert_fn=lambda m: None, alert_cooldown_sec=0,
            sample_keep_every_n=1,
        )
        queue = collections.deque(range(3), maxlen=3)
        self.assertTrue(all(mgr.handle_full("ui", queue, item=i).accepted for i in range(5)))

    def test_throttled_item_is_recoverable(self):
        mgr = BackpressureManager(
            {"ui": SAMPLE}, alert_fn=lambda m: None, alert_cooldown_sec=0,
            sample_keep_every_n=2,
        )
        decision = mgr.handle_full("ui", collections.deque(range(3), maxlen=3), item="X")
        self.assertEqual(decision.action, BackpressureAction.THROTTLED)
        self.assertEqual(decision.rejected_item, "X")


class TestDegradeAggregation(unittest.TestCase):
    def test_ohlc_bar_values(self):
        agg = TickAggregator(symbol="NIFTY", interval_sec=1.0)
        now = 1000.0

        self.assertIsNone(agg.add_tick({"price": 100.0, "volume": 10, "timestamp": now}))
        self.assertIsNone(agg.add_tick({"price": 105.0, "volume": 15, "timestamp": now + 0.5}))
        bar = agg.add_tick({"price": 98.0, "volume": 5, "timestamp": now + 1.2})

        self.assertIsNotNone(bar)
        # Independently derived: bar 1 saw prices 100.0 then 105.0, volumes 10+15.
        self.assertEqual(bar["open"], 100.0)
        self.assertEqual(bar["high"], 105.0)
        self.assertEqual(bar["low"], 100.0)
        self.assertEqual(bar["close"], 105.0)
        self.assertEqual(bar["volume"], 25)

    def test_zero_volume_tick_is_not_treated_as_missing(self):
        agg = TickAggregator("X", interval_sec=1.0)
        agg.add_tick({"price": 50.0, "volume": 0, "timestamp": 100.0})
        self.assertEqual(agg.current_bar["volume"], 0.0)

    def test_missing_price_raises_instead_of_zero_filling(self):
        agg = TickAggregator("X", interval_sec=1.0)
        with self.assertRaises(BackpressureError):
            agg.add_tick({"volume": 5, "timestamp": 100.0})

    def test_non_finite_price_raises(self):
        agg = TickAggregator("X", interval_sec=1.0)
        with self.assertRaises(BackpressureError):
            agg.add_tick({"price": float("nan"), "timestamp": 100.0})

    def test_out_of_order_tick_starts_a_new_bar(self):
        agg = TickAggregator("X", interval_sec=1.0)
        agg.add_tick({"price": 10.0, "timestamp": 1000.0})
        # A late tick predating the open bar must not silently rewrite it.
        agg.add_tick({"price": 20.0, "timestamp": 990.0})
        self.assertEqual(agg.current_bar["open"], 20.0)

    def test_invalid_interval_rejected(self):
        for bad in (0, -1.0, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(BackpressureError):
                    TickAggregator("X", interval_sec=bad)

    def test_degrade_via_manager_returns_completed_bar(self):
        mgr = BackpressureManager(
            {"trend": DEGRADE}, alert_fn=lambda m: None, alert_cooldown_sec=0
        )
        q = collections.deque(maxlen=1)
        mgr.handle_full("trend", q, {"price": 10.0, "volume": 1, "timestamp": 1000.0})
        decision = mgr.handle_full("trend", q, {"price": 12.0, "volume": 2, "timestamp": 1001.5})
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.action, BackpressureAction.AGGREGATED)
        self.assertIsNotNone(decision.emitted_item)
        self.assertEqual(decision.emitted_item["open"], 10.0)


class TestWatermarkAndTelemetry(BaseManagerTest):
    def test_watermark_alert_below_capacity(self):
        queue = collections.deque(range(8), maxlen=10)  # 80%
        self.manager.observe("ltp_stream", queue)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("WARNING", self.alerts[0])

    def test_no_alert_below_watermark(self):
        queue = collections.deque(range(5), maxlen=10)  # 50%
        self.manager.observe("ltp_stream", queue)
        self.assertEqual(self.alerts, [])

    def test_unbounded_queue_does_not_alert(self):
        self.manager.observe("ltp_stream", collections.deque(range(1000)))
        self.assertEqual(self.alerts, [])

    def test_drop_rate_is_none_when_nothing_observed(self):
        # 0.0 would read as "healthy"; absence of data is not health.
        self.assertIsNone(self.manager.get_metrics_summary()["ltp_stream"]["drop_rate_pct"])

    def test_drop_rate_uses_observed_pushes(self):
        queue = collections.deque(maxlen=2)
        for i in range(10):
            self.manager.observe("ltp_stream", queue)
            if len(queue) == queue.maxlen:
                self.manager.handle_full("ltp_stream", queue, i)
            else:
                queue.append(i)
        summary = self.manager.get_metrics_summary()["ltp_stream"]
        self.assertEqual(summary["total_observed"], 10)
        self.assertEqual(summary["overflow_events"], 8)
        self.assertEqual(summary["total_dropped"], 8)
        # Independently derived: 8 discards over 10 observed pushes = 80.0%.
        self.assertEqual(summary["drop_rate_pct"], 80.0)

    def test_never_drop_overflows_are_counted(self):
        queue = collections.deque([1], maxlen=1)
        for _ in range(3):
            self.manager.handle_full("risk_stream", queue, 9)
        self.assertEqual(
            self.manager.get_metrics_summary()["risk_stream"]["never_drop_overflows"], 3
        )


class TestValidation(unittest.TestCase):
    def test_unknown_policy_rejected(self):
        with self.assertRaises(BackpressureError):
            BackpressureManager({"s": "yolo"})

    def test_invalid_watermark_rejected(self):
        for bad in (0, -0.5, 1.5):
            with self.subTest(bad=bad):
                with self.assertRaises(BackpressureError):
                    BackpressureManager(POLICIES, high_watermark_pct=bad)

    def test_invalid_sample_n_rejected(self):
        for bad in (0, -1, True):
            with self.subTest(bad=bad):
                with self.assertRaises(BackpressureError):
                    BackpressureManager(POLICIES, sample_keep_every_n=bad)

    def test_negative_cooldown_rejected(self):
        with self.assertRaises(BackpressureError):
            BackpressureManager(POLICIES, alert_cooldown_sec=-1)

    def test_non_callable_alert_rejected(self):
        with self.assertRaises(BackpressureError):
            RateLimitedAlert("not callable")

    def test_alert_sink_failure_does_not_break_pipeline(self):
        def bad_sink(msg):
            raise RuntimeError("alert service down")

        mgr = BackpressureManager(POLICIES, alert_fn=bad_sink, alert_cooldown_sec=0)
        queue = collections.deque([1], maxlen=1)
        decision = mgr.handle_full("risk_stream", queue, 2)  # must not propagate
        self.assertFalse(decision.accepted)


class TestBackwardCompatibility(unittest.TestCase):
    def test_legacy_wrapper_preserves_old_return_shape(self):
        old = BackpressurePolicy(POLICIES, lambda m: None)
        queue = collections.deque([1, 2], maxlen=2)
        self.assertIsNone(old.handle_full("ltp_stream", queue, 3))
        self.assertEqual(list(queue), [2, 3])

    def test_legacy_wrapper_returns_bar_for_degrade(self):
        old = BackpressurePolicy(POLICIES, lambda m: None)
        q = collections.deque(maxlen=1)
        old.handle_full("trend_analytics", q, {"price": 1.0, "timestamp": 1000.0})
        bar = old.handle_full("trend_analytics", q, {"price": 2.0, "timestamp": 1002.0})
        self.assertIsNotNone(bar)
        self.assertEqual(bar["open"], 1.0)


if __name__ == "__main__":
    unittest.main()
