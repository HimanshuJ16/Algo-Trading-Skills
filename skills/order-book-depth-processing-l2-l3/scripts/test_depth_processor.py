"""
Unit tests for order-book-depth-processing-l2-l3 skill.

Tests:
1. L2 depth updates, weighted midprice, and book imbalance calculation.
2. Crossed-book detection and flagging.
3. L3 order addition, cancellation, and depth re-aggregation.
"""
import unittest
from depth_processor import DepthMetrics, DepthProcessorError, L2L3DepthProcessor


class TestL2L3DepthProcessor(unittest.TestCase):

    def setUp(self):
        self.processor = L2L3DepthProcessor(symbol="NIFTY")

    def test_l2_depth_updates_and_metrics(self):
        bids = [(100.0, 10.0), (99.5, 20.0)]
        asks = [(101.0, 5.0), (101.5, 15.0)]

        success = self.processor.update_l2_depth(bids, asks)
        self.assertTrue(success)

        metrics = self.processor.compute_metrics(depth_levels=2)
        self.assertFalse(metrics.is_crossed)
        self.assertEqual(metrics.best_bid, 100.0)
        self.assertEqual(metrics.best_ask, 101.0)
        self.assertEqual(metrics.mid_price, 100.5)

        # Imbalance ratio: (30 - 20) / (30 + 20) = 10 / 50 = +0.20
        self.assertAlmostEqual(metrics.imbalance_ratio, 0.20, delta=0.01)

    def test_crossed_book_detection(self):
        # Crossed book: Best Bid (102.0) >= Best Ask (101.0)
        bids = [(102.0, 10.0)]
        asks = [(101.0, 5.0)]

        success = self.processor.update_l2_depth(bids, asks)
        self.assertFalse(success)

        metrics = self.processor.compute_metrics()
        self.assertTrue(metrics.is_crossed)

    def test_l3_order_add_and_cancel(self):
        self.processor.add_l3_order(order_id="ORD_1", side="BUY", price=100.0, size=5.0)
        self.processor.add_l3_order(order_id="ORD_2", side="BUY", price=100.0, size=10.0)
        self.processor.add_l3_order(order_id="ORD_3", side="SELL", price=101.0, size=8.0)

        # 100.0 bid level should aggregate ORD_1 + ORD_2 = 15.0
        self.assertEqual(self.processor.bids[100.0], 15.0)

        # Cancel ORD_1 -> bid level should drop to 10.0
        self.processor.cancel_l3_order("ORD_1")
        self.assertEqual(self.processor.bids[100.0], 10.0)


if __name__ == "__main__":
    unittest.main()
