"""
Unit tests for counterparty-and-broker-concentration-risk skill.
"""
import unittest
from counterparty_monitor import CounterpartyConcentrationMonitor


class TestCounterpartyConcentrationMonitor(unittest.TestCase):

    def setUp(self):
        self.monitor = CounterpartyConcentrationMonitor(max_single_counterparty_pct=0.40)
        self.monitor.register_counterparty("IBKR", 300000)
        self.monitor.register_counterparty("Alpaca", 200000)
        self.monitor.register_counterparty("Schwab", 500000)
        # Total AUM = 1,000,000

    def test_concentration_calculation(self):
        """Verify concentration percentages are correct."""
        report = self.monitor.get_concentration_report()
        schwab = next(r for r in report if r["counterparty"] == "Schwab")
        self.assertAlmostEqual(schwab["concentration_pct"], 0.50)
        self.assertTrue(schwab["breached"])  # 50% > 40% limit

    def test_allocation_within_limit(self):
        """Allocation keeping concentration under limit should be approved."""
        result = self.monitor.check_allocation("Alpaca", 50000)
        # Projected: 250000 / 1050000 ≈ 23.8% < 40%
        self.assertTrue(result.approved)

    def test_allocation_exceeding_limit(self):
        """Allocation pushing concentration over limit should be blocked."""
        result = self.monitor.check_allocation("IBKR", 500000)
        # Projected: 800000 / 1500000 ≈ 53.3% > 40%
        self.assertFalse(result.approved)
        self.assertIn("COUNTERPARTY CONCENTRATION BREACH", result.rejection_reason)


if __name__ == "__main__":
    unittest.main()
