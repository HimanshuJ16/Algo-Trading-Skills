"""
Unit tests for order-book-imbalance-signal-pipeline skill.
"""
import time
import unittest
from imbalance_pipeline import FastPathOBIPipelineEngine, ImbalanceSignalType, L2OrderBookTop


class TestFastPathOBIPipelineEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)

    def test_high_buy_pressure_signal(self):
        # 800 bid vol vs 200 ask vol -> Imbalance = (800 - 200) / 1000 = +0.60
        book = L2OrderBookTop(
            symbol="NIFTY",
            bid_price=100.0,
            bid_volume=800.0,
            ask_price=101.0,
            ask_volume=200.0,
            timestamp_ns=time.time_ns(),
        )

        res = self.engine.process_l2_update(book)

        self.assertEqual(res.signal_type, ImbalanceSignalType.HIGH_BUY_PRESSURE)
        self.assertEqual(res.imbalance, 0.60)
        # Micro price = (800*101 + 200*100) / 1000 = 100.80
        self.assertAlmostEqual(res.micro_price, 100.80)
        self.assertLess(res.calculation_latency_ns, 500000)  # Sub-millisecond calculation

    def test_high_sell_pressure_signal(self):
        # 100 bid vol vs 900 ask vol -> Imbalance = (100 - 900) / 1000 = -0.80
        book = L2OrderBookTop(
            symbol="BTCUSDT",
            bid_price=50000.0,
            bid_volume=100.0,
            ask_price=50001.0,
            ask_volume=900.0,
            timestamp_ns=time.time_ns(),
        )

        res = self.engine.process_l2_update(book)

        self.assertEqual(res.signal_type, ImbalanceSignalType.HIGH_SELL_PRESSURE)
        self.assertEqual(res.imbalance, -0.80)


if __name__ == "__main__":
    unittest.main()
