import unittest
from portfolio_construction_with_transaction_cost_awareness import (
    Config, Engine, PortfolioConstructionEngine,
    AssetAlphaSpec, CostSpec, TCAwarePortfolioReport
)

class TestPortfolioConstructionWithTransactionCostAwareness(unittest.TestCase):

    def setUp(self):
        self.config = Config(rebalance_threshold=0.02)
        self.legacy_engine = Engine(self.config)
        self.tc_engine = PortfolioConstructionEngine(self.config)

    def test_legacy_init_and_run(self):
        self.assertTrue(self.legacy_engine.config.enabled)
        self.assertTrue(self.legacy_engine.run())

    def test_no_trade_buffer_band_suppression(self):
        # AAPL proposed shift: 40% -> 41% (1% shift <= 2% threshold => Suppressed)
        # MSFT proposed shift: 30% -> 40% (10% shift > 2% threshold => Traded)
        assets = [
            AssetAlphaSpec("AAPL", expected_return=0.10, current_weight=0.40, target_weight=0.41),
            AssetAlphaSpec("MSFT", expected_return=0.15, current_weight=0.30, target_weight=0.40),
        ]
        report = self.tc_engine.construct_portfolio(assets)

        self.assertEqual(report.status, "REBALANCED_COST_OPTIMIZED")
        self.assertIn("AAPL", report.suppressed_symbols)
        self.assertIn("MSFT", report.traded_symbols)
        self.assertEqual(report.total_turnover, 0.10) # 10% MSFT shift
        self.assertGreater(report.total_transaction_cost, 0.0)

if __name__ == '__main__':
    unittest.main()
