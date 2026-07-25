"""
Unit tests for broker-side-order-throttle-detection skill.
"""
import unittest
from throttle_detector import OrderThrottleDetector, ThrottleState


class TestOrderThrottleDetector(unittest.TestCase):

    def setUp(self):
        self.detector = OrderThrottleDetector(window_size=20, z_score_threshold=3.0, max_absolute_rtt_ms=500.0)

    def test_normal_latency(self):
        # Record baseline orders around 15ms
        for i in range(10):
            res = self.detector.record_order_ack(f"ORD_{i}", 1000.0 + i, 1000.015 + i)
            self.assertEqual(res.state, ThrottleState.NORMAL)
            self.assertFalse(res.is_throttled)

    def test_silent_throttle_absolute_spike(self):
        # Baseline ~20ms
        for i in range(10):
            self.detector.record_order_ack(f"ORD_{i}", 1000.0 + i, 1000.020 + i)

        # Order with 600ms latency spike -> SILENT_THROTTLE
        res = self.detector.record_order_ack("ORD_SPIKE", 2000.0, 2000.600)
        self.assertEqual(res.state, ThrottleState.SILENT_THROTTLE)
        self.assertTrue(res.is_throttled)
        self.assertGreaterEqual(res.recommended_backoff_ms, 100.0)

    def test_statistical_anomaly_trigger(self):
        # Consistent tight baseline of 10ms (std ~0)
        for i in range(10):
            self.detector.record_order_ack(f"ORD_{i}", 1000.0 + i, 1000.010 + i)

        # RTT of 200ms (below 500ms max, but 19x above baseline) -> Z-Score anomaly
        res = self.detector.record_order_ack("ORD_ANOMALY", 2000.0, 2000.200)
        self.assertEqual(res.state, ThrottleState.SILENT_THROTTLE)
        self.assertTrue(res.is_throttled)


if __name__ == "__main__":
    unittest.main()
