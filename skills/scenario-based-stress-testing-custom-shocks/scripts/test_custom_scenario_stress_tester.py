import unittest
from custom_scenario_stress_tester import (
    CustomScenarioStressTester, ScenarioResult,
    AssetPosition, StressScenarioDefinition, FactorShock,
    StressScenarioCategory, StressTestReport
)

class TestCustomScenarioStressTesterLegacy(unittest.TestCase):
    def test_scenarios(self):
        scenarios = {"Crash": -0.2, "Rally": 0.1}
        tester = CustomScenarioStressTester(scenarios)
        results = tester.run_stress_test(1000)

        self.assertEqual(len(results), 2)
        res_map = {r.scenario_name: r.pnl for r in results}
        self.assertEqual(res_map["Crash"], -200)
        self.assertEqual(res_map["Rally"], 100)

class TestCustomScenarioStressTesterAdvanced(unittest.TestCase):

    def setUp(self):
        self.tester = CustomScenarioStressTester(max_allowed_drawdown_pct=20.0)
        self.positions = [
            AssetPosition("AAPL", "EQUITY_SPOT", 500000.0, beta_to_factor=1.1),
            AssetPosition("MSFT", "EQUITY_SPOT", 500000.0, beta_to_factor=0.9),
        ]

    def test_predefined_historical_scenarios(self):
        report = self.tester.run_multi_factor_stress_test(self.positions)
        self.assertEqual(report.total_scenarios_evaluated, 3)
        self.assertGreater(abs(report.max_loss_usd), 0.0)

        # Lehman 2008 (-35% equity shock): AAPL (500k * -0.35 * 1.1 = -192.5k), MSFT (500k * -0.35 * 0.9 = -157.5k) -> Total = -350k (-35%)
        lehman_res = [r for r in report.results if r.scenario_id == "SCEN_2008_LEHMAN"][0]
        self.assertEqual(lehman_res.simulated_pnl_usd, -350000.0)
        self.assertEqual(lehman_res.percentage_loss_pct, -35.0)
        self.assertTrue(lehman_res.is_drawdown_limit_breached)  # |-35%| > 20% limit

    def test_custom_hypothetical_scenario(self):
        custom_scen = StressScenarioDefinition(
            scenario_id="SCEN_CUSTOM_TECH",
            scenario_name="Tech Sector Flash Crash",
            category=StressScenarioCategory.CUSTOM_HYPOTHETICAL,
            shocks=[FactorShock("EQUITY_SPOT", -0.15)]
        )
        report = self.tester.run_multi_factor_stress_test(self.positions, custom_scenarios=[custom_scen])
        self.assertEqual(report.total_scenarios_evaluated, 4)

        custom_res = [r for r in report.results if r.scenario_id == "SCEN_CUSTOM_TECH"][0]
        self.assertEqual(custom_res.simulated_pnl_usd, -150000.0)
        self.assertEqual(custom_res.percentage_loss_pct, -15.0)
        self.assertFalse(custom_res.is_drawdown_limit_breached)  # |-15%| <= 20% limit

    def test_empty_positions_raises_error(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test([])

if __name__ == '__main__':
    unittest.main()
