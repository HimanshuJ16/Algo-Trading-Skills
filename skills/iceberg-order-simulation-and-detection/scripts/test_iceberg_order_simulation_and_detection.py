import unittest
from iceberg_order_simulation_and_detection import (
    IcebergDetectorEngine, Level2DepthSnapshot, TradePrint, IcebergDetectionReport
)

class TestIcebergDetectorEngine(unittest.TestCase):

    def setUp(self):
        self.detector = IcebergDetectorEngine(symbol="AAPL", min_volume_ratio=1.5, min_refill_count=2)

    def test_bullish_hidden_buy_iceberg_detection(self):
        # 1. Register Initial BID Depth at $100.00 = 500 shares
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1000))

        # 2. Trade 1: Aggressor SELL 400 shares (hitting Bid) -> Display drops to 100
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 100, 1001))
        self.detector.process_trade_print(TradePrint("T1", 100.00, 400, "SELL", 1001))

        # 3. Refill 1: Display refilled back to 500
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1002))

        # 4. Trade 2: Aggressor SELL 400 shares
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 100, 1003))
        self.detector.process_trade_print(TradePrint("T2", 100.00, 400, "SELL", 1003))

        # 5. Refill 2: Display refilled back to 500
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1004))

        # 6. Trade 3: Aggressor SELL 400 shares -> Total traded = 1,200 shares (2.4x initial 500), Refills = 2
        report = self.detector.process_trade_print(TradePrint("T3", 100.00, 400, "SELL", 1005))

        self.assertIsNotNone(report)
        self.assertEqual(report.signal_classification, "BULLISH_HIDDEN_BUY")
        self.assertEqual(report.iceberg_side, "BUY")
        self.assertEqual(report.cumulative_traded_quantity, 1200)
        self.assertEqual(report.estimated_hidden_quantity, 700) # 1200 - 500
        self.assertGreaterEqual(report.confidence_score, 0.90)

    def test_no_iceberg_detected_under_low_volume(self):
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1000))
        report = self.detector.process_trade_print(TradePrint("T1", 100.00, 100, "SELL", 1001))

        self.assertIsNone(report)

if __name__ == '__main__':
    unittest.main()
