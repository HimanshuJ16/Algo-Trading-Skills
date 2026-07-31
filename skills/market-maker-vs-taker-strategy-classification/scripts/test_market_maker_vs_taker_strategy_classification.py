import unittest
from market_maker_vs_taker_strategy_classification import (
    MarketMakerVsTakerClassifierEngine, ExecutedTradeLog, StrategyClassificationReport
)

class TestMarketMakerVsTakerClassifierEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MarketMakerVsTakerClassifierEngine(
            pure_maker_threshold_ratio=0.80, pure_taker_threshold_ratio=0.20
        )

    def test_pure_maker_strategy_classification(self):
        # 90 Maker trades (Rebate -$1.00 each), 10 Taker trades (Fee +$5.00 each)
        # Maker Ratio = 90 / 100 = 0.90 >= 0.80 -> PURE_MAKER_STRATEGY!
        trades = [
            ExecutedTradeLog(f"M_{i}", "AAPL", is_maker=True, executed_price=150.0, quantity=100.0, fee_paid_usd=-1.0)
            for i in range(90)
        ] + [
            ExecutedTradeLog(f"T_{i}", "AAPL", is_maker=False, executed_price=150.0, quantity=100.0, fee_paid_usd=5.0)
            for i in range(10)
        ]

        report = self.engine.classify_strategy_executions("HFT_MAKER_01", trades)

        self.assertEqual(report.status, "STRATEGY_CLASSIFICATION_SUCCESS")
        self.assertEqual(report.classification, "PURE_MAKER_STRATEGY")
        self.assertEqual(report.maker_volume_ratio, 0.90)
        self.assertEqual(report.net_fees_paid_usd, -40.0) # (-90) + (+50) = -$40.00 Net Rebate
        self.assertLess(report.effective_fee_rate_bps, 0.0)

    def test_pure_taker_strategy_classification(self):
        # 100 Taker trades -> Maker Ratio = 0.0 -> PURE_TAKER_STRATEGY!
        trades = [
            ExecutedTradeLog(f"T_{i}", "BTC-USD", is_maker=False, executed_price=50000.0, quantity=1.0, fee_paid_usd=25.0)
            for i in range(100)
        ]
        report = self.engine.classify_strategy_executions("MOMENTUM_TAKER_02", trades)

        self.assertEqual(report.classification, "PURE_TAKER_STRATEGY")
        self.assertEqual(report.maker_volume_ratio, 0.0)
        self.assertEqual(report.net_fees_paid_usd, 2500.0)
        self.assertEqual(report.effective_fee_rate_bps, 5.0) # 2500 / 5,000,000 * 10000 = 5 bps

if __name__ == '__main__':
    unittest.main()
