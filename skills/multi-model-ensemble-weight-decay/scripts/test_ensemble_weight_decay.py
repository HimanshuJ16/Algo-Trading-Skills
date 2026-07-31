import unittest
from ensemble_weight_decay import (
    EnsembleWeightDecayEngine, ModelTelemetry, EnsembleConfig, EnsembleWeightReport
)

class TestEnsembleWeightDecayEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EnsembleWeightDecayEngine()

    def test_exponential_loss_softmax_reweighting_and_demotion(self):
        # 3 models: Model A (Loss=0.1), Model B (Loss=0.5), Model C (Loss=1.5)
        # Model C has high loss -> raw weight falls below floor 0.05 -> demoted!
        models = [
            ModelTelemetry("XGBoost_v1", recent_loss=0.1, recent_ic=0.08),
            ModelTelemetry("LightGBM_v1", recent_loss=0.5, recent_ic=0.04),
            ModelTelemetry("Ridge_v1", recent_loss=2.5, recent_ic=0.01),
        ]

        cfg = EnsembleConfig(decay_factor_lambda=0.90, temperature_beta=2.0, min_weight_floor=0.05)
        report = self.engine.reweight_ensemble("ML_ALPHA_ENSEMBLE", cfg, models)

        self.assertEqual(report.status, "ENSEMBLE_REWEIGHTED_SUCCESS")
        self.assertEqual(report.active_model_count, 2)
        self.assertEqual(report.demoted_model_count, 1)

        # Active models normalized weights sum to 1.0
        active_weights_sum = sum(s.final_normalized_weight for s in report.model_statuses if s.is_active)
        self.assertAlmostEqual(active_weights_sum, 1.0, places=3)

        # Model A has lower loss -> higher weight than Model B
        xgb_stat = next(s for s in report.model_statuses if s.model_id == "XGBoost_v1")
        lgb_stat = next(s for s in report.model_statuses if s.model_id == "LightGBM_v1")
        ridge_stat = next(s for s in report.model_statuses if s.model_id == "Ridge_v1")

        self.assertGreater(xgb_stat.final_normalized_weight, lgb_stat.final_normalized_weight)
        self.assertEqual(ridge_stat.final_normalized_weight, 0.0)
        self.assertFalse(ridge_stat.is_active)

    def test_negative_ic_demotion(self):
        # Model with negative IC demoted in IC_SOFTMAX scheme
        models = [
            ModelTelemetry("Model_Good", recent_loss=0.2, recent_ic=0.05),
            ModelTelemetry("Model_Bad", recent_loss=0.2, recent_ic=-0.03),
        ]
        cfg = EnsembleConfig(weighting_method="IC_SOFTMAX")

        report = self.engine.reweight_ensemble("IC_ENSEMBLE", cfg, models)

        bad_stat = next(s for s in report.model_statuses if s.model_id == "Model_Bad")
        self.assertFalse(bad_stat.is_active)
        self.assertEqual(bad_stat.status, "DEMOTED_NEGATIVE_IC")

if __name__ == '__main__':
    unittest.main()
