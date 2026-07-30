import unittest
from custodial_vs_non_custodial_tradeoff_assessment import (
    CustodialTradeoffAssessorEngine, StrategyRequirements, CustodyTradeoffReport
)

class TestCustodialTradeoffAssessorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CustodialTradeoffAssessorEngine()

    def test_hft_strategy_recommends_custodial_or_hybrid(self):
        req = StrategyRequirements(
            strategy_name="HFT_Arbitrage",
            required_latency_ms=1.0,
            monthly_volume_usd=50_000_000.0,
            max_counterparty_risk_pct=15.0,
            gas_sensitivity_score=0.9,
            requires_key_sovereignty=False
        )
        report = self.engine.evaluate_strategy_custody(req)
        
        self.assertIn(report.recommended_architecture, ["CUSTODIAL_CEX", "HYBRID_OFF_EXCHANGE_SETTLEMENT"])
        self.assertGreater(report.recommended_score, 70.0)

    def test_treasury_strategy_recommends_non_custodial_dex(self):
        req = StrategyRequirements(
            strategy_name="Long_Term_Treasury",
            required_latency_ms=12000.0,
            monthly_volume_usd=500_000.0,
            max_counterparty_risk_pct=0.0,
            gas_sensitivity_score=0.1,
            requires_key_sovereignty=True
        )
        report = self.engine.evaluate_strategy_custody(req)
        
        self.assertEqual(report.recommended_architecture, "NON_CUSTODIAL_DEX")
        self.assertGreaterEqual(report.recommended_score, 85.0)

if __name__ == '__main__':
    unittest.main()
