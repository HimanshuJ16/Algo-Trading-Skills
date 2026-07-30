import unittest
from explainable_boosting_pricer import (
    ExplainableBoostingPricerEngine, EbmSignalAuditReport
)

class TestExplainableBoostingPricerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExplainableBoostingPricerEngine(model_id="EBM_SIGNAL_ALPHA", base_intercept_beta0=0.50)
        
        # Single feature shape: f_rsi(rsi) = (rsi - 50) / 100
        self.engine.register_single_feature_shape("rsi_14", lambda rsi: (rsi - 50.0) / 100.0)
        # Single feature shape: f_vol(vol) = -1.0 * vol
        self.engine.register_single_feature_shape("volatility", lambda vol: -1.0 * vol)

        # 2D Interaction shape: f_rsi_vol(rsi, vol) = 0.5 * (rsi/100) * vol
        self.engine.register_interaction_shape("rsi_14", "volatility", lambda rsi, vol: 0.5 * (rsi / 100.0) * vol)

    def test_ebm_glassbox_exact_attribution_identity(self):
        # RSI = 70 (f_rsi = 0.20), Volatility = 0.10 (f_vol = -0.10), Interaction = 0.5 * 0.70 * 0.10 = +0.035
        # Total Score = 0.50 + 0.20 - 0.10 + 0.035 = 0.635
        features = {"rsi_14": 70.0, "volatility": 0.10}
        report = self.engine.evaluate_ebm_signal("AAPL", features)

        self.assertEqual(report.total_predicted_score, 0.635)
        self.assertTrue(report.is_exact_additive_identity_valid)
        self.assertEqual(report.status, "PASS_GOVERNANCE_AUDIT")
        self.assertEqual(len(report.single_feature_contributions), 2)
        self.assertEqual(len(report.interaction_contributions), 1)

if __name__ == '__main__':
    unittest.main()
