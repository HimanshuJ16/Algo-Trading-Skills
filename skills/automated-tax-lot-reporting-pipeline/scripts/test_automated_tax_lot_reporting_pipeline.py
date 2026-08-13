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

    def test_validation_rejects_invalid_trade_id(self):
        """Test that validation rejects invalid trade IDs."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Empty trade ID
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord("", "AAPL", TradeAction.BUY, 100.0, 10.0, 1000)
            engine.process_trade(invalid_trade)
        self.assertIn("trade_id must be a non-empty string", str(context.exception))

        # None trade ID (would cause TypeError but we test for ValueError from validation)
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord(None, "AAPL", TradeAction.BUY, 100.0, 10.0, 1000)  # type: ignore
            engine.process_trade(invalid_trade)
        self.assertIn("trade_id must be a non-empty string", str(context.exception))

    def test_validation_rejects_invalid_symbol(self):
        """Test that validation rejects invalid symbols."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Empty symbol
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord("T1", "", TradeAction.BUY, 100.0, 10.0, 1000)
            engine.process_trade(invalid_trade)
        self.assertIn("symbol must be a non-empty string", str(context.exception))

    def test_validation_rejects_invalid_action(self):
        """Test that validation rejects invalid actions."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Invalid action (not a TradeAction)
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord("T1", "AAPL", "INVALID", 100.0, 10.0, 1000)  # type: ignore
            engine.process_trade(invalid_trade)
        self.assertIn("action must be a valid TradeAction", str(context.exception))

    def test_validation_rejects_non_positive_quantity(self):
        """Test that validation rejects non-positive quantities."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Zero quantity
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord("T1", "AAPL", TradeAction.BUY, 0.0, 10.0, 1000)
            engine.process_trade(invalid_trade)
        self.assertIn("quantity must be positive", str(context.exception))

        # Negative quantity
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord("T1", "AAPL", TradeAction.BUY, -100.0, 10.0, 1000)
            engine.process_trade(invalid_trade)
        self.assertIn("quantity must be positive", str(context.exception))

    def test_validation_rejects_nan_quantity(self):
        """Test that validation rejects NaN quantities."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # NaN quantity
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord("T1", "AAPL", TradeAction.BUY, float('nan'), 10.0, 1000)
            engine.process_trade(invalid_trade)
        self.assertIn("quantity must be a valid number", str(context.exception))

    def test_validation_rejects_negative_price(self):
        """Test that validation rejects negative prices."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Negative price
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord("T1", "AAPL", TradeAction.BUY, 100.0, -10.0, 1000)
            engine.process_trade(invalid_trade)
        self.assertIn("price must be non-negative", str(context.exception))

    def test_validation_rejects_nan_price(self):
        """Test that validation rejects NaN prices."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # NaN price
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord("T1", "AAPL", TradeAction.BUY, 100.0, float('nan'), 1000)
            engine.process_trade(invalid_trade)
        self.assertIn("price must be a valid number", str(context.exception))

    def test_validation_rejects_negative_timestamp(self):
        """Test that validation rejects negative timestamps."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Negative timestamp
        with self.assertRaises(ValueError) as context:
            invalid_trade = TradeRecord("T1", "AAPL", TradeAction.BUY, 100.0, 10.0, -1000)
            engine.process_trade(invalid_trade)
        self.assertIn("timestamp_ms must be a non-negative integer", str(context.exception))

    def test_validation_allows_valid_trades(self):
        """Test that validation allows valid trades to proceed."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Valid trade should not raise an exception
        valid_trade = TradeRecord("T1", "AAPL", TradeAction.BUY, 100.0, 10.0, 1000)
        try:
            engine.process_trade(valid_trade)
            # Should have created a lot
            self.assertEqual(len(engine.open_lots["AAPL"]), 1)
            self.assertEqual(engine.open_lots["AAPL"][0].remaining_quantity, 100.0)
        except ValueError:
            self.fail("Valid trade raised ValueError unexpectedly")

    def test_sell_without_matching_buy_raises_error(self):
        """Test that selling without matching buy raises an error instead of returning empty list."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Try to sell without buying first
        sell_trade = TradeRecord("S1", "AAPL", TradeAction.SELL, 50.0, 15.0, 3000)

        with self.assertRaises(ValueError) as context:
            engine.process_trade(sell_trade)
        self.assertIn("Cannot sell 50.0 of AAPL. No open lots found", str(context.exception))

    def test_oversell_raises_error(self):
        """Test that overselling raises an error instead of just warning."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Buy 100 shares
        buy_trade = TradeRecord("B1", "AAPL", TradeAction.BUY, 100.0, 10.0, 1000)
        engine.process_trade(buy_trade)

        # Try to sell 150 shares (more than we have)
        sell_trade = TradeRecord("S1", "AAPL", TradeAction.SELL, 150.0, 15.0, 2000)

        with self.assertRaises(ValueError) as context:
            engine.process_trade(sell_trade)
        self.assertIn("Oversold AAPL! Remaining 50.0 units had no matching buy lots", str(context.exception))

    def test_memory_management_settled_lots_removed(self):
        """Test that fully settled lots are removed from memory to prevent leaks."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Buy 100 shares
        buy_trade = TradeRecord("B1", "AAPL", TradeAction.BUY, 100.0, 10.0, 1000)
        engine.process_trade(buy_trade)

        # Verify we have one open lot
        self.assertEqual(engine.get_total_open_lot_count(), 1)
        self.assertEqual(engine.get_open_lot_count()["AAPL"], 1)

        # Sell exactly 100 shares (should fully settle the lot)
        sell_trade = TradeRecord("S1", "AAPL", TradeAction.SELL, 100.0, 15.0, 2000)
        engine.process_trade(sell_trade)

        # Verify the lot has been removed from memory
        self.assertEqual(engine.get_total_open_lot_count(), 0)
        self.assertEqual(engine.get_open_lot_count()["AAPL"], 0)

    def test_memory_management_partial_settlement(self):
        """Test that partially settled lots remain in memory."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Buy 100 shares
        buy_trade = TradeRecord("B1", "AAPL", TradeAction.BUY, 100.0, 10.0, 1000)
        engine.process_trade(buy_trade)

        # Verify we have one open lot
        self.assertEqual(engine.get_total_open_lot_count(), 1)

        # Sell only 50 shares (should leave 50 remaining)
        sell_trade = TradeRecord("S1", "AAPL", TradeAction.SELL, 50.0, 15.0, 2000)
        engine.process_trade(sell_trade)

        # Verify we still have one open lot (partially settled)
        self.assertEqual(engine.get_total_open_lot_count(), 1)
        self.assertEqual(engine.get_open_lot_count()["AAPL"], 1)

        # Verify the remaining quantity is correct
        lots = engine.open_lots["AAPL"]
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].remaining_quantity, 50.0)

    def test_memory_management_multiple_lots(self):
        """Test memory management with multiple lots."""
        engine = AutomatedTaxLotReportingPipelineEngine()

        # Buy 100 shares at $10
        buy1 = TradeRecord("B1", "AAPL", TradeAction.BUY, 100.0, 10.0, 1000)
        engine.process_trade(buy1)

        # Buy 100 shares at $20
        buy2 = TradeRecord("B2", "AAPL", TradeAction.BUY, 100.0, 20.0, 2000)
        engine.process_trade(buy2)

        # Verify we have two open lots
        self.assertEqual(engine.get_total_open_lot_count(), 2)

        # Sell 100 shares (should fully settle the first lot)
        sell_trade = TradeRecord("S1", "AAPL", TradeAction.SELL, 100.0, 15.0, 3000)
        engine.process_trade(sell_trade)

        # Verify we now have one open lot (the second one)
        self.assertEqual(engine.get_total_open_lot_count(), 1)
        self.assertEqual(engine.get_open_lot_count()["AAPL"], 1)

        # Verify the remaining lot is the second one (with original quantity 100)
        lots = engine.open_lots["AAPL"]
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].lot_id, "B2")
        self.assertEqual(lots[0].remaining_quantity, 100.0)


if __name__ == '__main__':
    unittest.main()