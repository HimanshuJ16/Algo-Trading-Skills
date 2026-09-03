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


class TestSellAtomicity(unittest.TestCase):
    """A sell that cannot be covered must leave the ledger untouched.

    Regression tests for the partial-commit defect: the engine previously
    consumed lots and appended RealizedGainRecords as it walked the ledger, and
    only raised the oversell error afterwards. A caught exception then left a
    committed disposal with no corresponding sellable position.
    """

    def test_oversell_consumes_no_lots(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        engine.process_trade(TradeRecord("B1", "AAPL", TradeAction.BUY, 10.0, 100.0, 1000))

        with self.assertRaises(ValueError):
            engine.process_trade(TradeRecord("S1", "AAPL", TradeAction.SELL, 15.0, 110.0, 2000))

        # The 10-unit lot must be fully intact, not consumed.
        self.assertEqual(engine.get_total_open_lot_count(), 1)
        lot = engine.open_lots["AAPL"][0]
        self.assertEqual(lot.lot_id, "B1")
        self.assertEqual(lot.remaining_quantity, 10.0)

    def test_oversell_records_no_gains(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        engine.process_trade(TradeRecord("B1", "AAPL", TradeAction.BUY, 10.0, 100.0, 1000))

        with self.assertRaises(ValueError):
            engine.process_trade(TradeRecord("S1", "AAPL", TradeAction.SELL, 15.0, 110.0, 2000))

        # No phantom disposal may reach the realized-gain ledger (and hence a
        # Form 8949) for a sale that was rejected.
        self.assertEqual(engine.realized_gains, [])

    def test_oversell_spanning_multiple_lots_consumes_none(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        engine.process_trade(TradeRecord("B1", "AAPL", TradeAction.BUY, 10.0, 100.0, 1000))
        engine.process_trade(TradeRecord("B2", "AAPL", TradeAction.BUY, 10.0, 120.0, 2000))

        with self.assertRaises(ValueError):
            engine.process_trade(TradeRecord("S1", "AAPL", TradeAction.SELL, 25.0, 110.0, 3000))

        self.assertEqual(engine.get_total_open_lot_count(), 2)
        self.assertEqual([l.remaining_quantity for l in engine.open_lots["AAPL"]], [10.0, 10.0])
        self.assertEqual(engine.realized_gains, [])

    def test_ledger_still_usable_after_rejected_oversell(self):
        """The caller can correct the quantity and re-sell against the same lots."""
        engine = AutomatedTaxLotReportingPipelineEngine()
        engine.process_trade(TradeRecord("B1", "AAPL", TradeAction.BUY, 10.0, 100.0, 1000))

        with self.assertRaises(ValueError):
            engine.process_trade(TradeRecord("S1", "AAPL", TradeAction.SELL, 15.0, 110.0, 2000))

        gains = engine.process_trade(TradeRecord("S2", "AAPL", TradeAction.SELL, 10.0, 110.0, 3000))
        self.assertEqual(len(gains), 1)
        # 10 units * ($110 - $100) = $100
        self.assertEqual(gains[0].realized_pnl, 100.0)
        self.assertEqual(engine.get_total_open_lot_count(), 0)

    def test_oversell_error_states_no_lots_consumed(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        engine.process_trade(TradeRecord("B1", "AAPL", TradeAction.BUY, 10.0, 100.0, 1000))

        with self.assertRaises(ValueError) as context:
            engine.process_trade(TradeRecord("S1", "AAPL", TradeAction.SELL, 15.0, 110.0, 2000))
        self.assertIn("No lots were consumed", str(context.exception))


class TestFractionalQuantityPrecision(unittest.TestCase):
    """Float dust must not pin a depleted lot open.

    Regression tests for exhaustion being tested with ``== 0``: buying 0.3 and
    selling 0.1 three times left 2.78e-17 on the lot, so it was never removed and
    the third sell raised "Oversold ... Remaining 2.7755575615628914e-17 units"
    against a position the holder still fully owned.
    """

    def test_fractional_position_can_be_fully_liquidated(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        engine.process_trade(TradeRecord("B1", "BTC", TradeAction.BUY, 0.3, 50000.0, 1000))

        for i in range(3):
            engine.process_trade(
                TradeRecord("S%d" % i, "BTC", TradeAction.SELL, 0.1, 60000.0, 2000 + i)
            )

        self.assertEqual(engine.get_total_open_lot_count(), 0)

    def test_fractional_liquidation_totals_are_exact(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        engine.process_trade(TradeRecord("B1", "BTC", TradeAction.BUY, 0.3, 50000.0, 1000))

        total_pnl = 0.0
        total_qty = 0.0
        for i in range(3):
            for gain in engine.process_trade(
                TradeRecord("S%d" % i, "BTC", TradeAction.SELL, 0.1, 60000.0, 2000 + i)
            ):
                total_pnl += gain.realized_pnl
                total_qty += gain.quantity_sold

        # Independently derived: 0.3 units * ($60,000 - $50,000) = $3,000.
        self.assertAlmostEqual(total_pnl, 3000.0, places=6)
        self.assertAlmostEqual(total_qty, 0.3, places=12)

    def test_dust_remainder_does_not_strand_a_lot(self):
        """A lot depleted to sub-epsilon dust is treated as closed."""
        engine = AutomatedTaxLotReportingPipelineEngine()
        engine.process_trade(TradeRecord("B1", "BTC", TradeAction.BUY, 0.1, 50000.0, 1000))
        engine.process_trade(TradeRecord("B2", "BTC", TradeAction.BUY, 0.2, 50000.0, 2000))

        # 0.1 + 0.2 == 0.30000000000000004 in binary floating point, so the sell
        # quantity is very slightly larger than the sum of the two lots.
        engine.process_trade(TradeRecord("S1", "BTC", TradeAction.SELL, 0.1 + 0.2, 60000.0, 3000))
        self.assertEqual(engine.get_total_open_lot_count(), 0)


class TestNonFiniteAndBooleanInputs(unittest.TestCase):
    """Non-finite inputs must be rejected before they reach the gain records.

    Regression tests for infinities passing validation: an infinite price
    produced ``realized_pnl = nan`` (inf - inf), silently poisoning a tax report
    with no exception raised anywhere.
    """

    def _buy(self, **overrides):
        args = dict(trade_id="T1", symbol="AAPL", action=TradeAction.BUY,
                    quantity=100.0, price=10.0, timestamp_ms=1000)
        args.update(overrides)
        return TradeRecord(**args)

    def test_validation_rejects_infinite_price(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        for bad_price in (float('inf'), float('-inf')):
            with self.assertRaises(ValueError) as context:
                engine.process_trade(self._buy(price=bad_price))
            self.assertIn("price must be a valid number", str(context.exception))

    def test_validation_rejects_infinite_quantity(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        with self.assertRaises(ValueError) as context:
            engine.process_trade(self._buy(quantity=float('inf')))
        self.assertIn("quantity must be a valid number", str(context.exception))

    def test_no_nan_pnl_can_be_produced_from_accepted_input(self):
        """The previous failure path: inf in, nan realized_pnl out, no error."""
        engine = AutomatedTaxLotReportingPipelineEngine()
        with self.assertRaises(ValueError):
            engine.process_trade(self._buy(price=float('inf')))
        self.assertEqual(engine.realized_gains, [])

    def test_validation_rejects_boolean_quantity(self):
        """``True`` is an ``int`` in Python and previously booked a 1-unit lot."""
        engine = AutomatedTaxLotReportingPipelineEngine()
        with self.assertRaises(ValueError) as context:
            engine.process_trade(self._buy(quantity=True))
        self.assertIn("quantity must be a valid number", str(context.exception))

    def test_validation_rejects_boolean_timestamp(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        with self.assertRaises(ValueError) as context:
            engine.process_trade(self._buy(timestamp_ms=True))
        self.assertIn("timestamp_ms must be a non-negative integer", str(context.exception))


class TestForm8949Fields(unittest.TestCase):
    """Realized gain records must carry the dates Form 8949 requires.

    Form 8949 reports column (b) "Date acquired" and column (c) "Date sold or
    disposed of" per transaction, and separates Part I (short-term) from Part II
    (long-term) on the holding period. Without both timestamps on the record the
    documented Form 8949 mapping is not derivable from the engine's output.
    """

    def test_record_carries_acquisition_and_disposal_timestamps(self):
        engine = AutomatedTaxLotReportingPipelineEngine()
        engine.process_trade(TradeRecord("B1", "AAPL", TradeAction.BUY, 10.0, 100.0, 1600000000000))
        gains = engine.process_trade(TradeRecord("S1", "AAPL", TradeAction.SELL, 10.0, 120.0, 1700000000000))

        self.assertEqual(gains[0].acquired_timestamp_ms, 1600000000000)
        self.assertEqual(gains[0].disposed_timestamp_ms, 1700000000000)

    def test_each_leg_of_a_multi_lot_sell_carries_its_own_acquisition_date(self):
        """A sell spanning two lots is two Form 8949 rows with two acquired dates."""
        engine = AutomatedTaxLotReportingPipelineEngine(strategy=LotMatchingStrategy.FIFO)
        engine.process_trade(TradeRecord("B1", "AAPL", TradeAction.BUY, 100.0, 10.0, 1000))
        engine.process_trade(TradeRecord("B2", "AAPL", TradeAction.BUY, 100.0, 20.0, 2000))

        gains = engine.process_trade(TradeRecord("S1", "AAPL", TradeAction.SELL, 150.0, 15.0, 3000))

        self.assertEqual([g.acquired_timestamp_ms for g in gains], [1000, 2000])
        self.assertEqual([g.disposed_timestamp_ms for g in gains], [3000, 3000])

    def test_hifo_record_reports_the_matched_lots_acquisition_date(self):
        """Under HIFO the acquired date must follow the matched lot, not lot age."""
        engine = AutomatedTaxLotReportingPipelineEngine(strategy=LotMatchingStrategy.HIFO)
        engine.process_trade(TradeRecord("B1", "AAPL", TradeAction.BUY, 100.0, 10.0, 1000))
        engine.process_trade(TradeRecord("B2", "AAPL", TradeAction.BUY, 100.0, 20.0, 2000))

        gains = engine.process_trade(TradeRecord("S1", "AAPL", TradeAction.SELL, 50.0, 15.0, 3000))

        self.assertEqual(gains[0].buy_lot_id, "B2")
        self.assertEqual(gains[0].acquired_timestamp_ms, 2000)


if __name__ == '__main__':
    unittest.main()