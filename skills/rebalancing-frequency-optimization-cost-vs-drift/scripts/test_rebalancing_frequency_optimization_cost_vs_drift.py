import unittest
from rebalancing_frequency_optimization_cost_vs_drift import (
    Config, Engine, RebalancingFrequencyOptimizerEngine,
    AssetWeight, RebalanceOptimizationReport
)

class TestRebalancingFrequencyOptimizationCostVsDrift(unittest.TestCase):

    def setUp(self):
        self.config = Config(enabled=True, drift_penalty_lambda=100.0, max_drift_threshold_pct=0.05)
        self.legacy_engine = Engine(self.config)
        self.optimizer_engine = RebalancingFrequencyOptimizerEngine(self.config)

    def test_legacy_init_and_run(self):
        self.assertTrue(self.legacy_engine.config.enabled)
        self.assertTrue(self.legacy_engine.run())

    def test_no_rebalance_within_band(self):
        # 0.5% weight drift (target 50/50, current 50.5/49.5) -> small drift within no-trade band
        assets = [
            AssetWeight("SPY", target_weight=0.50, current_weight=0.505, asset_value_usd=505000.0),
            AssetWeight("TLT", target_weight=0.50, current_weight=0.495, asset_value_usd=495000.0)
        ]

        report = self.optimizer_engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "NO_REBALANCE_WITHIN_BAND")
        self.assertFalse(report.rebalance_recommended)
        self.assertEqual(len(report.proposed_trades), 0)

    def test_rebalance_triggered_max_drift_breached(self):
        # 10% weight drift (target 50/50, current 60/40) -> 10% drift > 5% max threshold
        assets = [
            AssetWeight("SPY", target_weight=0.50, current_weight=0.60, asset_value_usd=600000.0),
            AssetWeight("TLT", target_weight=0.50, current_weight=0.40, asset_value_usd=400000.0)
        ]

        report = self.optimizer_engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "REBALANCE_TRIGGERED_MAX_DRIFT")
        self.assertTrue(report.rebalance_recommended)
        self.assertEqual(len(report.proposed_trades), 2)
        # SPY SELL $100,000, TLT BUY $100,000
        spy_trade = next(t for t in report.proposed_trades if t.symbol == "SPY")
        self.assertEqual(spy_trade.action, "SELL")
        self.assertEqual(spy_trade.trade_amount_usd, 100000.0)

if __name__ == '__main__':
    unittest.main()
