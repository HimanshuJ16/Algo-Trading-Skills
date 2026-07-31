import unittest
from historical_order_book_reconstruction_from_message_logs import (
    HistoricalOrderBookReconstructEngine, L3OrderMessage, OrderBookReconstructionReport
)

class TestHistoricalOrderBookReconstructEngine(unittest.TestCase):

    def setUp(self):
        self.engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")

    def test_l3_message_replay_reconstructs_l2_book(self):
        # 1. Add Buy Order ID_1 @ 100.0 Qty 10
        self.engine.process_l3_message(L3OrderMessage("ID_1", "ADD", "BUY", 100.0, 10, 1000))
        # 2. Add Buy Order ID_2 @ 100.0 Qty 5 -> Total Buy Qty at 100.0 = 15
        self.engine.process_l3_message(L3OrderMessage("ID_2", "ADD", "BUY", 100.0, 5, 1001))
        # 3. Add Sell Order ID_3 @ 101.0 Qty 8 -> Best Ask 101.0 (8)
        self.engine.process_l3_message(L3OrderMessage("ID_3", "ADD", "SELL", 101.0, 8, 1002))
        # 4. Partial Cancel Buy Order ID_1 Qty 4 -> Total Buy Qty at 100.0 = 11
        self.engine.process_l3_message(L3OrderMessage("ID_1", "CANCEL", "BUY", 100.0, 4, 1003))

        snapshot = self.engine.get_l2_reconstructed_snapshot(top_n_levels=5)

        self.assertEqual(snapshot.best_bid_price, 100.0)
        self.assertEqual(snapshot.best_bid_qty, 11)
        self.assertEqual(snapshot.best_ask_price, 101.0)
        self.assertEqual(snapshot.best_ask_qty, 8)
        self.assertEqual(snapshot.mid_price, 100.50)
        self.assertEqual(snapshot.spread, 1.0)
        self.assertFalse(snapshot.is_crossed_book)
        self.assertEqual(snapshot.total_active_l3_orders, 3)

    def test_crossed_book_detection(self):
        # Add Buy Order @ 102.0 and Sell Order @ 101.0 -> CROSSED!
        self.engine.process_l3_message(L3OrderMessage("ID_1", "ADD", "BUY", 102.0, 10, 1000))
        self.engine.process_l3_message(L3OrderMessage("ID_2", "ADD", "SELL", 101.0, 5, 1001))

        snapshot = self.engine.get_l2_reconstructed_snapshot()

        self.assertTrue(snapshot.is_crossed_book)

if __name__ == '__main__':
    unittest.main()
