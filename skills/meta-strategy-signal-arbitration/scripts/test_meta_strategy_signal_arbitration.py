import unittest
from meta_strategy_signal_arbitration import (
    MetaStrategySignalArbitratorEngine, SubStrategySignal, StrategyWeightConfig, MetaStrategyArbitrationReport
)

class TestMetaStrategySignalArbitratorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MetaStrategySignalArbitratorEngine(
            deadband_threshold=0.05, estimated_transaction_cost_bps=10.0
        )
        self.weights = [
            StrategyWeightConfig("TREND_01", weight=0.50),
            StrategyWeightConfig("MEAN_REV_02", weight=0.50)
        ]

    def test_internal_order_netting_and_savings(self):
        # Strategy 1: BUY +$100,000 | Strategy 2: SELL -$60,000
        # Gross Notional = $160,000 | Net Executable Notional = +$40,000
        # Netted Volume = $160k - $40k = $120,000 -> Savings @ 10 bps = $120.00 -> APPROVED!
        signals = [
            SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=1.0, target_notional_usd=100000.0),
            SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=-0.6, conviction_score=1.0, target_notional_usd=-60000.0)
        ]

        report = self.engine.arbitrate_strategy_signals("AAPL", self.weights, signals)

        self.assertEqual(report.status, "ARBITRATION_NETTED_ORDER_GENERATED")
        self.assertEqual(report.gross_notional_usd, 160000.0)
        self.assertEqual(report.net_executable_notional_usd, 40000.0)
        self.assertEqual(report.internal_netting_savings_usd, 120.0)
        self.assertFalse(report.is_risk_veto_active)

    def test_risk_off_veto_override(self):
        # Strategy 2 triggers Risk Veto -> ARBITRATION_VETO_RISK_OFF!
        signals = [
            SubStrategySignal("TREND_01", "TSLA", raw_signal=1.0, conviction_score=1.0, target_notional_usd=50000.0),
            SubStrategySignal("MEAN_REV_02", "TSLA", raw_signal=-1.0, conviction_score=1.0, target_notional_usd=-50000.0, is_risk_veto=True)
        ]

        report = self.engine.arbitrate_strategy_signals("TSLA", self.weights, signals)

        self.assertEqual(report.status, "ARBITRATION_VETO_RISK_OFF")
        self.assertTrue(report.is_risk_veto_active)
        self.assertEqual(report.net_executable_notional_usd, 0.0)

if __name__ == '__main__':
    unittest.main()
