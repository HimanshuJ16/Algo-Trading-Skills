"""
Unit tests for stress-testing-against-historical-crash-scenarios skill.
"""
import unittest
from stress_tester import CrashScenario, HistoricalStressTester, StressTestReport


class TestHistoricalStressTester(unittest.TestCase):

    def setUp(self):
        self.tester = HistoricalStressTester(max_stressed_loss_pct=0.15)

    def test_stress_pnl_calculation(self):
        """Verify stressed P&L matches manual calculation for a known scenario."""
        scenario = CrashScenario(
            name="TEST_CRASH",
            description="Test scenario",
            asset_returns={"AAPL": -0.20, "MSFT": -0.10, "DEFAULT": -0.15},
        )
        tester = HistoricalStressTester(max_stressed_loss_pct=0.50, scenarios=[scenario])

        positions = {"AAPL": 100, "MSFT": 200}
        prices = {"AAPL": 150.0, "MSFT": 300.0}
        nav = 75000.0  # AAPL=15k + MSFT=60k = 75k

        report = tester.run_stress_test(positions, prices, nav)

        # AAPL impact: 15000 * -0.20 = -3000
        # MSFT impact: 60000 * -0.10 = -6000
        # Total: -9000, pct: -9000/75000 = -12%
        self.assertEqual(len(report.results), 1)
        self.assertAlmostEqual(report.results[0].stressed_pnl_usd, -9000.0, places=2)
        self.assertAlmostEqual(report.worst_loss_pct, 0.12, places=2)
        self.assertFalse(report.threshold_breached)

    def test_breach_detection(self):
        """Verify threshold breach is correctly flagged when stressed loss exceeds limit."""
        scenario = CrashScenario(
            name="SEVERE_CRASH",
            description="Severe crash",
            asset_returns={"SPY": -0.50, "DEFAULT": -0.50},
        )
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15, scenarios=[scenario])

        positions = {"SPY": 1000}
        prices = {"SPY": 100.0}
        nav = 100000.0  # 100% in SPY

        report = tester.run_stress_test(positions, prices, nav)

        # SPY impact: 100000 * -0.50 = -50000, pct = 50% >> 15% threshold
        self.assertTrue(report.threshold_breached)
        self.assertIn("STRESS TEST BREACH", report.breach_reason)
        self.assertAlmostEqual(report.worst_loss_pct, 0.50, places=2)

    def test_worst_scenario_identification(self):
        """Verify worst-case scenario is correctly identified from multiple scenarios."""
        scenarios = [
            CrashScenario("MILD", "Mild", {"AAPL": -0.05, "DEFAULT": -0.05}),
            CrashScenario("SEVERE", "Severe", {"AAPL": -0.40, "DEFAULT": -0.40}),
            CrashScenario("MODERATE", "Moderate", {"AAPL": -0.15, "DEFAULT": -0.15}),
        ]
        tester = HistoricalStressTester(max_stressed_loss_pct=0.50, scenarios=scenarios)

        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        nav = 10000.0

        report = tester.run_stress_test(positions, prices, nav)

        self.assertEqual(report.worst_scenario, "SEVERE")
        self.assertAlmostEqual(report.worst_loss_pct, 0.40, places=2)


if __name__ == "__main__":
    unittest.main()
