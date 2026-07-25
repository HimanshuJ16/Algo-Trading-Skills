import unittest
from custom_scenario_stress_tester import CustomScenarioStressTester

class TestCustomScenarioStressTester(unittest.TestCase):
    def test_scenarios(self):
        scenarios = {"Crash": -0.2, "Rally": 0.1}
        tester = CustomScenarioStressTester(scenarios)
        results = tester.run_stress_test(1000)
        
        self.assertEqual(len(results), 2)
        res_map = {r.scenario_name: r.pnl for r in results}
        self.assertEqual(res_map["Crash"], -200)
        self.assertEqual(res_map["Rally"], 100)
