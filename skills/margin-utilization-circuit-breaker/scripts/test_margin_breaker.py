"""
Unit tests for margin-utilization-circuit-breaker skill.
"""
import unittest
from margin_breaker import MarginOrderCheck, MarginStatus, MarginUtilizationBreaker


class TestMarginUtilizationBreaker(unittest.TestCase):

    def setUp(self):
        self.breaker = MarginUtilizationBreaker(warning_threshold=0.60, hard_stop_threshold=0.80)

    def test_normal_utilization(self):
        """50% utilization should be NORMAL and approved."""
        result = self.breaker.check_order(50000, 100000)
        self.assertTrue(result.approved)
        self.assertEqual(result.margin_state.status, MarginStatus.NORMAL)
        self.assertAlmostEqual(result.margin_state.utilization_pct, 0.50)

    def test_warning_utilization(self):
        """70% utilization should trigger WARNING but still approve (no additional margin)."""
        state = self.breaker.evaluate_margin(70000, 100000)
        self.assertEqual(state.status, MarginStatus.WARNING)
        self.assertIn("MARGIN WARNING", state.message)

    def test_hard_stop_blocks_orders(self):
        """90% utilization should trigger HARD_STOP and block new entries."""
        result = self.breaker.check_order(90000, 100000)
        self.assertFalse(result.approved)
        self.assertEqual(result.margin_state.status, MarginStatus.HARD_STOP)
        self.assertIn("MARGIN HARD STOP", result.rejection_reason)

    def test_projected_margin_breach(self):
        """Current 70% + additional margin pushing to 85% should be blocked."""
        result = self.breaker.check_order(70000, 100000, additional_margin_required=15000)
        # Projected: 85000/100000 = 85% > 80% hard stop
        self.assertFalse(result.approved)
        self.assertEqual(result.margin_state.status, MarginStatus.HARD_STOP)


if __name__ == "__main__":
    unittest.main()
