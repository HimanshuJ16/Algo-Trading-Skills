import unittest
from model_card_generator import (
    ModelCardGeneratorEngine, ModelIdentity, ModelPerformanceMetrics, ModelGovernanceConfig, ModelCardReport
)

class TestModelCardGeneratorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ModelCardGeneratorEngine()
        self.identity = ModelIdentity(
            model_id="ML_ALPHA_001",
            name="XGBoost Momentum Alpha",
            version="1.2.0",
            author="Quant Research Team",
            model_type="ML_ALPHA",
            asset_class="US_EQUITIES",
            intended_use="Intraday 5-minute equity momentum alpha signal generation.",
            out_of_scope_uses=["Penny stocks < $5.00", "Illiquid post-market sessions"]
        )
        self.performance = ModelPerformanceMetrics(
            sharpe_ratio=2.15,
            sortino_ratio=2.85,
            max_drawdown_pct=12.5,
            annual_return_pct=28.4,
            win_rate_pct=57.2,
            capacity_usd=10000000.0
        )
        self.governance = ModelGovernanceConfig(
            sr_compliant=True,
            is_validated_by_mrm=True,
            validation_date="2026-05-01"
        )

    def test_compliant_model_card_generation(self):
        report = self.engine.generate_model_card(self.identity, self.performance, self.governance)

        self.assertEqual(report.status, "MODEL_CARD_GENERATED_COMPLIANT")
        self.assertTrue(report.is_mrm_compliant)
        self.assertIn("# Model Card: XGBoost Momentum Alpha", report.markdown_content)
        self.assertIn("## 1. Intended Use & Scope", report.markdown_content)
        self.assertIn("Sharpe Ratio", report.markdown_content)

    def test_non_compliant_missing_mrm_validation(self):
        # Unvalidated model by MRM -> MODEL_CARD_NON_COMPLIANT_DEFICIT!
        bad_gov = ModelGovernanceConfig(is_validated_by_mrm=False)
        report = self.engine.generate_model_card(self.identity, self.performance, bad_gov)

        self.assertEqual(report.status, "MODEL_CARD_NON_COMPLIANT_DEFICIT")
        self.assertFalse(report.is_mrm_compliant)

if __name__ == '__main__':
    unittest.main()
