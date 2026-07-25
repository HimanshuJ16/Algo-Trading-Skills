"""
Unit tests for broker-side-order-throttle-detection skill.
"""
import unittest
from throttle_detector import OrderThrottleDetector, ThrottleState


class TestOrderThrottleDetector(unittest.TestCase):

    def setUp(self):
        self.detector = OrderThrottleDetector(
            alpha=0.2, 
            z_score_threshold=3.0, 
            max_absolute_rtt_ms=500.0,
            min_variance_clamp=1.0
        )

    def test_normal_latency(self):
        # Establish baseline
        for i in range(20):
            res = self.detector.record_order_ack(f"ORD_{i}", 1000.0 + i, 1000.015 + i)
            self.assertEqual(res.state, ThrottleState.NORMAL)
            self.assertFalse(res.is_throttled)
            self.assertEqual(res.recommended_backoff_ms, 0.0)

    def test_silent_throttle_absolute_spike(self):
        for i in range(20):
            self.detector.record_order_ack(f"ORD_{i}", 1000.0 + i, 1000.020 + i)

        # Spike to 600ms RTT
        res = self.detector.record_order_ack("ORD_SPIKE", 2000.0, 2000.600)
        self.assertEqual(res.state, ThrottleState.SILENT_THROTTLE)
        self.assertTrue(res.is_throttled)
        self.assertGreaterEqual(res.recommended_backoff_ms, 10.0)

    def test_statistical_anomaly_trigger(self):
        for i in range(20):
            self.detector.record_order_ack(f"ORD_{i}", 1000.0 + i, 1000.010 + i)

        # 200ms is below absolute max, but significantly higher than EWMA
        res = self.detector.record_order_ack("ORD_ANOMALY", 2000.0, 2000.200)
        self.assertEqual(res.state, ThrottleState.SILENT_THROTTLE)
        self.assertTrue(res.is_throttled)

    def test_aimd_backoff_recovery(self):
        for i in range(20):
            self.detector.record_order_ack(f"ORD_{i}", 1000.0 + i, 1000.015 + i)
            
        # Trigger silent throttle
        res = self.detector.record_order_ack("ORD_SPIKE", 2000.0, 2000.600)
        initial_backoff = res.recommended_backoff_ms
        
        # Another throttle should multiplicatively increase backoff
        res2 = self.detector.record_order_ack("ORD_SPIKE_2", 2001.0, 2001.600)
        self.assertGreater(res2.recommended_backoff_ms, initial_backoff)
        
        # Normal latency should additively decrease backoff
        res3 = self.detector.record_order_ack("ORD_NORMAL", 2002.0, 2002.015)
        self.assertLess(res3.recommended_backoff_ms, res2.recommended_backoff_ms)


if __name__ == "__main__":
    unittest.main()
