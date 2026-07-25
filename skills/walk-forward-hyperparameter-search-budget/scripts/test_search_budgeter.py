"""Unit tests for walk-forward-hyperparameter-search-budget."""
import unittest
from search_budgeter import HyperparameterSearchBudgeter


class TestHyperparameterSearchBudgeter(unittest.TestCase):
    def setUp(self):
        self.budgeter = HyperparameterSearchBudgeter(max_trials_per_year=100)

    def test_grid_within_budget_passes(self):
        grid = {
            "fast_period": [5, 10, 15],
            "slow_period": [20, 30, 40],
        }  # 3x3 = 9 combinations
        combos, report = self.budgeter.audit_and_prune(grid, in_sample_days=252)

        self.assertFalse(report.is_budget_exceeded)
        self.assertEqual(len(combos), 9)
        self.assertEqual(report.overfitting_risk_level, "LOW")

    def test_overlarge_grid_pruned(self):
        grid = {
            "param1": list(range(20)),
            "param2": list(range(20)),
            "param3": list(range(10)),
        }  # 20x20x10 = 4,000 combinations!
        combos, report = self.budgeter.audit_and_prune(grid, in_sample_days=252)  # Max allowed ~100

        self.assertTrue(report.is_budget_exceeded)
        self.assertTrue(report.pruning_applied)
        self.assertLessEqual(len(combos), report.allowed_budget_max)
        self.assertEqual(report.overfitting_risk_level, "HIGH")


if __name__ == "__main__":
    unittest.main()
