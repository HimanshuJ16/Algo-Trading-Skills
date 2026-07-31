import unittest
from opening_auction_imbalance_based_execution import (
    OpeningAuctionImbalanceBasedExecutionConfig,
    OpeningAuctionImbalanceBasedExecutionEngine,
    AuctionImbalanceData,
    AuctionExecutionReport
)

class TestOpeningAuctionImbalanceBasedExecution(unittest.TestCase):

    def setUp(self):
        self.config = OpeningAuctionImbalanceBasedExecutionConfig(
            enabled=True,
            threshold=100.0,
            size=5000,
            imbalance_ratio_threshold=0.20,
            min_imbalance_qty=10000,
            cutoff_seconds_to_open=120.0
        )
        self.engine = OpeningAuctionImbalanceBasedExecutionEngine(self.config)

    def test_legacy_evaluate(self):
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["qty"], 5000)

        market_data_low = {"symbol": "AAPL", "price": 95.0}
        orders_low = self.engine.evaluate(market_data_low)
        self.assertEqual(len(orders_low), 0)

    def test_auction_imbalance_order_generation(self):
        # 100,000 Buy Imbalance + 300,000 Paired = 400,000 total (25% ratio > 20% min) at 180s to open (> 120s cutoff)
        imb = AuctionImbalanceData(
            symbol="NVDA",
            paired_qty=300000,
            imbalance_qty=100000,
            imbalance_side="B",
            far_price=455.0,
            near_price=452.0,
            ref_price=450.0,
            seconds_to_open=180.0
        )
        report = self.engine.process_auction_imbalance(imb)
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertEqual(report.imbalance_ratio, 0.25)
        self.assertIsNotNone(report.order_generated)
        self.assertEqual(report.order_generated["side"], "SELL") # Contra-side against Buy imbalance
        self.assertEqual(report.order_generated["type"], "MOO")

    def test_auction_cutoff_lockout(self):
        # 60s to open < 120s cutoff -> CUTOFF_EXCEEDED
        imb = AuctionImbalanceData(
            symbol="NVDA",
            paired_qty=300000,
            imbalance_qty=100000,
            imbalance_side="B",
            far_price=455.0,
            near_price=452.0,
            ref_price=450.0,
            seconds_to_open=60.0
        )
        report = self.engine.process_auction_imbalance(imb)
        self.assertEqual(report.status, "CUTOFF_EXCEEDED")
        self.assertIsNone(report.order_generated)

if __name__ == '__main__':
    unittest.main()
