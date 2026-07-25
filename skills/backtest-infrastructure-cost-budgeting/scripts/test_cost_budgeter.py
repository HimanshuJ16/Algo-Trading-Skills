import unittest
from cost_budgeter import BacktestCostBudgeter, BacktestJobSpec, CloudPricing

class TestBacktestCostBudgeter(unittest.TestCase):
    def setUp(self):
        # Sample pricing similar to AWS on-demand (very simplified)
        self.pricing = CloudPricing(
            cpu_hourly_rate=0.04, 
            ram_hourly_rate=0.005, 
            storage_monthly_rate_per_gb=0.10
        )
        self.budgeter = BacktestCostBudgeter(self.pricing, max_budget=100.0)

    def test_estimate_costs_under_budget(self):
        job = BacktestJobSpec(
            instruments=100,
            parameter_combinations=50,
            cpu_hours_per_unit=0.1,
            memory_gb_required=4.0,
            storage_gb_per_unit=0.01
        )
        # Total units = 5000
        # CPU hours = 500 -> cost: 500 * 0.04 = $20
        # RAM cost = 500 * 4 * 0.005 = $10
        # Storage = 5000 * 0.01 = 50 GB -> cost: 50 * 0.10 = $5
        # Total = 35.0
        
        result = self.budgeter.estimate_costs(job)
        self.assertEqual(result["total_units"], 5000)
        self.assertAlmostEqual(result["cpu_cost"], 20.0)
        self.assertAlmostEqual(result["ram_cost"], 10.0)
        self.assertAlmostEqual(result["storage_cost"], 5.0)
        self.assertAlmostEqual(result["total_cost"], 35.0)
        self.assertFalse(result["is_over_budget"])

    def test_estimate_costs_over_budget(self):
        job = BacktestJobSpec(
            instruments=1000,
            parameter_combinations=500,
            cpu_hours_per_unit=0.5,
            memory_gb_required=16.0,
            storage_gb_per_unit=0.1
        )
        result = self.budgeter.estimate_costs(job)
        self.assertTrue(result["is_over_budget"])
        self.assertTrue(result["total_cost"] > 100.0)

if __name__ == '__main__':
    unittest.main()
