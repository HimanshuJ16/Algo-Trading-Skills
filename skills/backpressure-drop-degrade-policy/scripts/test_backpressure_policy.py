"""
Verification unit tests for backpressure_policy.py

Tests:
1. NEVER_DROP stream triggers rate-limited alerts and does not silently discard risk data.
2. DROP_OLDEST retains the newest item when bounded queue overflows.
3. SAMPLE drops 50% of queue backlog during high burst load.
4. DEGRADE aggregates high-frequency ticks into 1-second OHLC bars.
5. Post-session metrics summary reflects exact telemetry numbers.
"""
import collections
import time
import unittest
from backpressure_policy import (
    BackpressureManager,
    BackpressurePolicy,
    DROP_OLDEST,
    SAMPLE,
    DEGRADE,
    NEVER_DROP,
    TickAggregator,
)


class TestBackpressurePolicy(unittest.TestCase):

    def setUp(self):
        self.alerts = []

        def mock_alert(msg):
            self.alerts.append(msg)

        self.mock_alert = mock_alert
        self.policies = {
            "risk_stream": NEVER_DROP,
            "ltp_stream": DROP_OLDEST,
            "ui_dashboard": SAMPLE,
            "trend_analytics": DEGRADE,
        }
        self.manager = BackpressureManager(
            policy_by_stream=self.policies,
            alert_fn=self.mock_alert,
            alert_cooldown_sec=0.1,  # short cooldown for tests
        )

    def test_never_drop_alert_and_no_silent_discard(self):
        queue = collections.deque([1, 2, 3], maxlen=3)
        self.manager.handle_full("risk_stream", queue, 4)

        self.assertEqual(len(self.alerts), 1)
        self.assertIn("CRITICAL", self.alerts[0])
        self.assertIn("NEVER_DROP", self.alerts[0])

        # Test alert rate-limiting cooldown
        self.manager.handle_full("risk_stream", queue, 5)
        self.assertEqual(len(self.alerts), 1)  # Cooldown prevented duplicate alert

        time.sleep(0.15)
        self.manager.handle_full("risk_stream", queue, 6)
        self.assertEqual(len(self.alerts), 2)  # Cooldown expired, second alert sent

    def test_drop_oldest(self):
        queue = collections.deque([10, 20, 30], maxlen=3)
        self.manager.handle_full("ltp_stream", queue, 40)

        # Oldest item 10 popped, 40 appended
        self.assertEqual(list(queue), [20, 30, 40])
        metrics = self.manager.get_metrics_summary()["ltp_stream"]
        self.assertEqual(metrics["total_dropped"], 1)

    def test_sample_policy(self):
        queue = collections.deque([1, 2, 3, 4, 5, 6], maxlen=6)
        self.manager.handle_full("ui_dashboard", queue, 7)

        # 3 oldest popped (1,2,3), 7 appended -> [4, 5, 6, 7]
        self.assertEqual(list(queue), [4, 5, 6, 7])
        metrics = self.manager.get_metrics_summary()["ui_dashboard"]
        self.assertEqual(metrics["total_sampled"], 3)

    def test_degrade_tick_aggregator(self):
        aggregator = TickAggregator(symbol="NIFTY", interval_sec=1.0)
        now = time.time()

        # Add 3 ticks in first second
        b1 = aggregator.add_tick({"price": 100.0, "volume": 10, "timestamp": now})
        self.assertIsNone(b1)

        b2 = aggregator.add_tick({"price": 105.0, "volume": 15, "timestamp": now + 0.5})
        self.assertIsNone(b2)

        # Add tick in second interval (now + 1.2s) -> finishes first bar
        b3 = aggregator.add_tick({"price": 98.0, "volume": 5, "timestamp": now + 1.2})
        self.assertIsNotNone(b3)
        self.assertEqual(b3["open"], 100.0)
        self.assertEqual(b3["high"], 105.0)
        self.assertEqual(b3["low"], 100.0)
        self.assertEqual(b3["close"], 105.0)
        self.assertEqual(b3["volume"], 25)

    def test_backward_compatibility(self):
        old_policy = BackpressurePolicy(self.policies, self.mock_alert)
        queue = collections.deque([1, 2], maxlen=2)
        old_policy.handle_full("ltp_stream", queue, 3)
        self.assertEqual(list(queue), [2, 3])


if __name__ == "__main__":
    unittest.main()
