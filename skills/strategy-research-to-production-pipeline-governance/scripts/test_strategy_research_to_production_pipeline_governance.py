import unittest
from strategy_research_to_production_pipeline_governance import (
    Config, Engine,
    StrategyResearchToProductionGovernanceEngine, PipelineStage,
    StagePromotionArtifacts, StagePromotionDecision
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")

    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())

class TestStrategyPipelineGovernanceEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyResearchToProductionGovernanceEngine(
            min_backtest_sharpe=1.50,
            max_backtest_drawdown_pct=15.0,
            max_shadow_tracking_error_pct=5.0,
            min_paper_trading_days=14
        )

    def test_approved_promotion_to_live_production(self):
        artifacts = StagePromotionArtifacts(
            git_commit_hash="a1b2c3d4e5f",
            dataset_checksum="md5_checksum_12345",
            backtest_sharpe=2.1,
            backtest_max_drawdown_pct=10.0,
            shadow_tracking_error_pct=2.5,
            paper_trading_days=21,
            has_risk_committee_signoff=True,
            author_id="Quant_Researcher_01",
            validator_id="Risk_CRO_02"
        )
        decision = self.engine.evaluate_stage_promotion(
            "STAT_ARB_PROD_01",
            PipelineStage.PAPER_TRADING_SHADOW,
            PipelineStage.LIVE_PRODUCTION,
            artifacts
        )

        self.assertTrue(decision.is_approved)
        self.assertEqual(decision.status_code, "APPROVED_FOR_PROMOTION")
        self.assertEqual(len(decision.failed_gates), 0)
        self.assertGreater(len(decision.passed_gates), 3)

    def test_rejected_promotion_due_to_missing_risk_signoff_and_tracking_error(self):
        artifacts = StagePromotionArtifacts(
            git_commit_hash="a1b2c3d4e5f",
            dataset_checksum="md5_checksum_12345",
            backtest_sharpe=2.1,
            backtest_max_drawdown_pct=10.0,
            shadow_tracking_error_pct=8.5,        # BREACH! 8.5% > 5% max error
            paper_trading_days=7,                 # BREACH! 7d < 14d min
            has_risk_committee_signoff=False,     # BREACH! Missing sign-off
            author_id="Quant_Researcher_01",
            validator_id=""
        )
        decision = self.engine.evaluate_stage_promotion(
            "FAIL_STRAT_01",
            PipelineStage.PAPER_TRADING_SHADOW,
            PipelineStage.LIVE_PRODUCTION,
            artifacts
        )

        self.assertFalse(decision.is_approved)
        self.assertIn("REJECTED", decision.status_code)
        self.assertEqual(len(decision.failed_gates), 3)

if __name__ == '__main__':
    unittest.main()
