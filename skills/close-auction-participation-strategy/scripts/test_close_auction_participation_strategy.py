import unittest
from datetime import datetime, time
from close_auction_participation_strategy import CloseAuctionParticipationStrategy, NoiiMessage

class TestCloseAuctionParticipationStrategy(unittest.TestCase):

    def setUp(self):
        self.strategy = CloseAuctionParticipationStrategy(
            max_participation_pct=0.10,
            cutoff_time=time(15, 55, 0)
        )

    def test_contra_sell_order_on_buy_imbalance(self):
        # 100,000 shares excess buy imbalance -> 10% = 10,000 shares SELL
        noii = NoiiMessage(
            symbol="AAPL",
            timestamp=datetime(2025, 1, 15, 15, 52, 0), # 3:52 PM (Before cutoff)
            paired_shares=500000,
            imbalance_shares=100000,
            imbalance_direction='B',
            far_price=150.50,
            near_price=150.25,
            reference_price=150.00
        )
        
        order = self.strategy.generate_auction_order(noii)
        self.assertIsNotNone(order)
        self.assertEqual(order.symbol, "AAPL")
        self.assertEqual(order.side, "SELL")
        self.assertEqual(order.quantity, 10000)
        self.assertEqual(order.limit_price, 150.50) # max(150.50, 150.25)

    def test_contra_buy_order_on_sell_imbalance(self):
        # 50,000 shares excess sell imbalance -> 10% = 5,000 shares BUY
        noii = NoiiMessage(
            symbol="MSFT",
            timestamp=datetime(2025, 1, 15, 15, 54, 30), # 3:54:30 PM (Before cutoff)
            paired_shares=300000,
            imbalance_shares=50000,
            imbalance_direction='S',
            far_price=400.00,
            near_price=401.00,
            reference_price=402.00
        )
        
        order = self.strategy.generate_auction_order(noii)
        self.assertIsNotNone(order)
        self.assertEqual(order.symbol, "MSFT")
        self.assertEqual(order.side, "BUY")
        self.assertEqual(order.quantity, 5000)
        self.assertEqual(order.limit_price, 400.00) # min(400.00, 401.00)

    def test_cutoff_time_enforcement(self):
        # Timestamp at 3:55:01 PM (Past cutoff)
        noii = NoiiMessage(
            symbol="TSLA",
            timestamp=datetime(2025, 1, 15, 15, 55, 1),
            paired_shares=100000,
            imbalance_shares=50000,
            imbalance_direction='B',
            far_price=200.00,
            near_price=200.00,
            reference_price=200.00
        )
        
        order = self.strategy.generate_auction_order(noii)
        self.assertIsNone(order)

    def test_no_imbalance_returns_none(self):
        noii = NoiiMessage(
            symbol="NVDA",
            timestamp=datetime(2025, 1, 15, 15, 50, 0),
            paired_shares=200000,
            imbalance_shares=0,
            imbalance_direction='N',
            far_price=100.00,
            near_price=100.00,
            reference_price=100.00
        )
        
        order = self.strategy.generate_auction_order(noii)
        self.assertIsNone(order)

if __name__ == '__main__':
    unittest.main()
