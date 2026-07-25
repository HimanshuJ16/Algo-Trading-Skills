import unittest
from automated_tax_lot_reporting_pipeline import (
    AutomatedTaxLotReportingPipelineEngine,
    TradeRecord,
    TradeAction,
    LotMatchingStrategy
)

class TestTaxLotReporting(unittest.TestCase):
    def setUp(self):
        # We buy 100 shares at $10, then 100 shares at $20.
        self.buy1 = TradeRecord("B1", "AAPL", TradeAction.BUY, 100.0, 10.0, 1000)
        self.buy2 = TradeRecord("B2", "AAPL", TradeAction.BUY, 100.0, 20.0, 2000)
        
        # We sell 50 shares at $15
        self.sell = TradeRecord("S1", "AAPL", TradeAction.SELL, 50.0, 15.0, 3000)

    def test_fifo_strategy(self):
        engine = AutomatedTaxLotReportingPipelineEngine(strategy=LotMatchingStrategy.FIFO)
        engine.process_trade(self.buy1)
        engine.process_trade(self.buy2)
        
        # FIFO should match against B1 ($10 cost basis)
        gains = engine.process_trade(self.sell)
        
        self.assertEqual(len(gains), 1)
        self.assertEqual(gains[0].buy_lot_id, "B1")
        # PnL: 50 shares * ($15 sell - $10 buy) = $250 profit
        self.assertEqual(gains[0].realized_pnl, 250.0)
        
        # Verify ledger state (B1 should have 50 remaining, B2 100 remaining)
        lots = engine.open_lots["AAPL"]
        self.assertEqual(len(lots), 2)
        self.assertEqual(lots[0].remaining_quantity, 50.0)
        self.assertEqual(lots[1].remaining_quantity, 100.0)

    def test_hifo_strategy(self):
        engine = AutomatedTaxLotReportingPipelineEngine(strategy=LotMatchingStrategy.HIFO)
        engine.process_trade(self.buy1)
        engine.process_trade(self.buy2)
        
        # HIFO should match against B2 ($20 cost basis, because it's highest)
        gains = engine.process_trade(self.sell)
        
        self.assertEqual(len(gains), 1)
        self.assertEqual(gains[0].buy_lot_id, "B2")
        # PnL: 50 shares * ($15 sell - $20 buy) = -$250 loss (tax optimization)
        self.assertEqual(gains[0].realized_pnl, -250.0)
        
        # Verify ledger state (B2 should be the one partially consumed)
        lots = engine.open_lots["AAPL"]
        # HIFO doesn't change the list order until we pop, but B2's quantity should drop
        b1_lot = next(l for l in lots if l.lot_id == "B1")
        b2_lot = next(l for l in lots if l.lot_id == "B2")
        self.assertEqual(b1_lot.remaining_quantity, 100.0)
        self.assertEqual(b2_lot.remaining_quantity, 50.0)

    def test_split_across_lots(self):
        engine = AutomatedTaxLotReportingPipelineEngine(strategy=LotMatchingStrategy.FIFO)
        engine.process_trade(self.buy1) # 100 @ 10
        engine.process_trade(self.buy2) # 100 @ 20
        
        # Sell 150 shares
        sell_large = TradeRecord("S_L", "AAPL", TradeAction.SELL, 150.0, 15.0, 4000)
        gains = engine.process_trade(sell_large)
        
        self.assertEqual(len(gains), 2)
        
        # First 100 shares from B1
        self.assertEqual(gains[0].buy_lot_id, "B1")
        self.assertEqual(gains[0].quantity_sold, 100.0)
        self.assertEqual(gains[0].realized_pnl, 500.0) # 100 * (15 - 10)
        
        # Remaining 50 shares from B2
        self.assertEqual(gains[1].buy_lot_id, "B2")
        self.assertEqual(gains[1].quantity_sold, 50.0)
        self.assertEqual(gains[1].realized_pnl, -250.0) # 50 * (15 - 20)

if __name__ == '__main__':
    unittest.main()
