import unittest
from leakage_free_tuner import (
    LeakageFreeHyperparameterTunerEngine, LeakageFreeTuningReport
)

class TestLeakageFreeHyperparameterTunerEngine(unittest.TestCase):

    def setUp(self):
        self.tuner = LeakageFreeHyperparameterTunerEngine(
            outer_folds_count=3, inner_folds_count=2, purge_window_samples=5, embargo_pct=0.01
        )
        self.param_grid = [
            {"max_depth": 3, "learning_rate": 0.01},
            {"max_depth": 5, "learning_rate": 0.05},
            {"max_depth": 7, "learning_rate": 0.10}
        ]

    def test_purged_embargoed_index_generation(self):
        # 100 samples, validation fold [30, 50)
        # Purge window 5 -> purges [25, 30) (5 samples)
        # Embargo 1% of 100 = 1 sample -> embargoes [50, 51) (1 sample)
        train_idx, val_idx, purged, embargoed = self.tuner.generate_purged_embargoed_indices(
            n_samples=100, val_start=30, val_end=50
        )

        self.assertEqual(len(val_idx), 20)
        self.assertEqual(purged, 5)
        self.assertEqual(embargoed, 1)
        self.assertEqual(len(train_idx), 100 - 20 - 5 - 1) # 74 samples

    def test_leakage_free_tuning_orchestration(self):
        def eval_func(params, train_idx, val_idx):
            # Simulated Sharpe evaluation
            depth = params["max_depth"]
            return 1.5 + (depth * 0.1)

        report = self.tuner.execute_leakage_free_tuning(
            n_samples=300, param_grid=self.param_grid, simulated_eval_func=eval_func
        )

        self.assertTrue(report.is_leakage_free_guaranteed)
        self.assertIn("max_depth", report.best_params)
        self.assertGreater(report.purged_samples_count, 0)
        self.assertGreater(report.embargoed_samples_count, 0)
        self.assertGreater(report.leakage_overestimation_haircut, 0.0)

if __name__ == '__main__':
    unittest.main()
