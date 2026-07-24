"""
Unit tests for value-at-risk-var-live-monitoring skill.

Tests:
1. Parametric VaR, Historical VaR, and CVaR calculation on synthetic returns.
2. VaR breach warning detection and order entry blocking.
3. Zero-position portfolio handling.
"""
import unittest
from var_monitor import LiveRiskStatus, LiveValueAtRiskMonitor, VaRMetrics, VaRMonitorError


class TestLiveValueAtRiskMonitor(unittest.TestCase):

    def setUp(self):
        self.monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        # Normal return series (~1% daily std)
        self.normal_returns = {
            "AAPL": [0.01, -0.005, 0.015, -0.01, 0.008, -0.003, 0.012, -0.007, 0.005, -0.002] * 5
        }
        # Volatile return series (~5% daily std -> VaR > 5%)
        self.volatile_returns = {
            "TSLA": [0.05, -0.06, 0.07, -0.08, 0.06, -0.05, 0.04, -0.07, 0.08, -0.09] * 5
        }

    def test_normal_var_calculation(self):
        positions = {"AAPL": 100.0}
        prices = {"AAPL": 150.0}
        nav = 100000.0

        status = self.monitor.evaluate_live_risk(positions, prices, self.normal_returns, nav)

        self.assertTrue(status.approved)
        self.assertFalse(status.var_metrics.is_breached)
        self.assertLess(status.var_metrics.parametric_var_pct, 0.05)

    def test_volatile_var_breach_detection(self):
        positions = {"TSLA": 500.0}  # $100,000 USD position (100% NAV)
        prices = {"TSLA": 200.0}
        nav = 100000.0

        status = self.monitor.evaluate_live_risk(positions, prices, self.volatile_returns, nav)

        self.assertFalse(status.approved)
        self.assertTrue(status.var_metrics.is_breached)
        self.assertIn("LIVE VAR BREACH", status.breach_reason)
        self.assertGreater(status.var_metrics.parametric_var_pct, 0.05)

    def test_empty_positions(self):
        status = self.monitor.evaluate_live_risk({}, {}, {}, 100000.0)
        self.assertTrue(status.approved)
        self.assertEqual(status.var_metrics.parametric_var_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
