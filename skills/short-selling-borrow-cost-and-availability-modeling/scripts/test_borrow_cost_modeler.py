import unittest
from borrow_cost_modeler import BorrowCostModeler, BorrowStatus, ShortTrade

class TestBorrowCostModeler(unittest.TestCase):
    def setUp(self):
        self.modeler = BorrowCostModeler(gc_rate=0.0025, htb_base_rate=0.05, max_htb_rate=0.30)
        self.modeler.update_status(BorrowStatus("AAPL", 0.1, available_shares=100000))
        self.modeler.update_status(BorrowStatus("GME", 0.9, available_shares=5000))
        self.modeler.update_status(BorrowStatus("MEME", 1.0, available_shares=0))
        
    def test_can_short(self):
        # Can short GC
        self.assertTrue(self.modeler.can_short("AAPL", 1000))
        
        # Can short HTB if available
        self.assertTrue(self.modeler.can_short("GME", 1000))
        
        # Cannot short if not enough shares
        self.assertFalse(self.modeler.can_short("GME", 10000))
        
        # Cannot short if utilization is 1.0
        self.assertFalse(self.modeler.can_short("MEME", 100))
        
        # Assume unknown tickers are GC
        self.assertTrue(self.modeler.can_short("UNKNOWN", 10000))

    def test_calculate_annualized_rate(self):
        # GC rate
        self.assertEqual(self.modeler.calculate_annualized_rate("AAPL"), 0.0025)
        
        # Unknown ticker uses GC rate
        self.assertEqual(self.modeler.calculate_annualized_rate("UNKNOWN"), 0.0025)
        
        # HTB rate scales with utilization (0.9 util = 0.05 + 0.5 * (0.3 - 0.05) = 0.175)
        rate = self.modeler.calculate_annualized_rate("GME")
        self.assertAlmostEqual(rate, 0.175)

    def test_calculate_borrow_cost(self):
        trade_gc = ShortTrade(ticker="AAPL", shares=100, entry_price=150.0, days_held=30)
        # 15000 * 0.0025 / 365 * 30
        expected_cost_gc = (15000 * 0.0025 / 365.0) * 30
        self.assertAlmostEqual(self.modeler.calculate_borrow_cost(trade_gc), expected_cost_gc)
        
        trade_htb = ShortTrade(ticker="GME", shares=100, entry_price=20.0, days_held=10)
        rate_htb = self.modeler.calculate_annualized_rate("GME") # 0.175
        expected_cost_htb = (2000 * rate_htb / 365.0) * 10
        self.assertAlmostEqual(self.modeler.calculate_borrow_cost(trade_htb), expected_cost_htb)

if __name__ == '__main__':
    unittest.main()
