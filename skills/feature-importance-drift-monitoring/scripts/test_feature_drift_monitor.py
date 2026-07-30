import unittest
from feature_drift_monitor import (
    FeatureImportanceDriftMonitorEngine, FeatureDriftAuditReport
)

class TestFeatureImportanceDriftMonitorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FeatureImportanceDriftMonitorEngine(min_spearman_rank_threshold=0.70, max_degradation_drop_pct=0.80)
        self.base_map = {
            "rsi_14": 0.40,
            "volatility_20d": 0.30,
            "trend_50d": 0.20,
            "sentiment_score": 0.10
        }

    def test_stable_feature_importance_passes(self):
        # Live importance matches baseline ranking closely (rsi #1, vol #2, trend #3, sentiment #4)
        live_map = {
            "rsi_14": 0.38,
            "volatility_20d": 0.32,
            "trend_50d": 0.18,
            "sentiment_score": 0.12
        }
        report = self.engine.audit_feature_importance_drift("XGB_ALPHA_V1", "2026-W30", self.base_map, live_map)

        self.assertEqual(report.spearman_rank_correlation, 1.0)
        self.assertFalse(report.is_retrain_triggered)
        self.assertEqual(report.status, "FEATURE_STABILITY_NORMAL")

    def test_regime_shift_feature_drift_triggers_retrain(self):
        # Major regime shift: sentiment_score rises to #1 (0.45) while rsi_14 drops to #4 (0.05) -> rho = -0.80!
        live_map = {
            "rsi_14": 0.05,
            "volatility_20d": 0.20,
            "trend_50d": 0.30,
            "sentiment_score": 0.45
        }
        report = self.engine.audit_feature_importance_drift("XGB_ALPHA_V1", "2026-W30-CRASH", self.base_map, live_map)

        self.assertLess(report.spearman_rank_correlation, 0.70)
        self.assertTrue(report.is_retrain_triggered)
        self.assertEqual(report.status, "FEATURE_DRIFT_ALERT_TRIGGERED")
        self.assertIn("rsi_14", report.degraded_features)

if __name__ == '__main__':
    unittest.main()
