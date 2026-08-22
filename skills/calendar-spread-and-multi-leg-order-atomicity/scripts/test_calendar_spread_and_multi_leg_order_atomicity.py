import logging
import unittest

from calendar_spread_and_multi_leg_order_atomicity import (
    HedgeOrderOutcome,
    Leg,
    OrderSide,
    SpreadConfig,
    SpreadExecutionEngine,
    SpreadState,
)


class TestMultiLegAtomicity(unittest.TestCase):

    def setUp(self):
        self.anchor = Leg("BTC-JUN", OrderSide.SELL, ratio=1, limit_price=65000)
        self.hedge = Leg("BTC-JUL", OrderSide.BUY, ratio=1, limit_price=66000)
        self.config = SpreadConfig(
            anchor_leg=self.anchor, hedge_leg=self.hedge, max_hedge_slippage_bps=10.0
        )

        self.submitted_orders = []
        self.cancelled_symbols = []

        def mock_broker_submit(symbol, side, qty, price, order_type):
            self.submitted_orders.append(
                {"symbol": symbol, "side": side, "qty": qty, "price": price, "type": order_type}
            )

        def mock_broker_cancel(symbol):
            self.cancelled_symbols.append(symbol)

        self.engine = SpreadExecutionEngine(
            self.config, mock_broker_submit, broker_cancel_callback=mock_broker_cancel
        )

    # ----------------------------------------------------------- happy path

    def test_perfect_fill(self):
        self.engine.start_execution(1.0)
        self.assertEqual(self.engine.state, SpreadState.PENDING_ANCHOR)
        self.assertEqual(len(self.submitted_orders), 1)
        self.assertEqual(self.submitted_orders[0]["type"], "LIMIT")

        # Anchor fills entirely
        self.engine.on_anchor_fill(1.0, 65000)
        self.assertEqual(self.engine.state, SpreadState.HEDGING)
        self.assertEqual(len(self.submitted_orders), 2)

        # Verify slippage calculation (Buy at 66000 with 10 bps slippage limit -> 66066)
        self.assertAlmostEqual(self.submitted_orders[1]["price"], 66066.0, places=2)
        self.assertEqual(self.submitted_orders[1]["type"], "IOC")

        # Hedge fills entirely and the order terminates
        self.engine.on_hedge_fill(1.0, 66050)
        self.engine.on_hedge_order_done(HedgeOrderOutcome.FILLED)
        self.assertEqual(self.engine.state, SpreadState.COMPLETED)
        self.assertEqual(self.cancelled_symbols, [])

    def test_sell_hedge_slippage_moves_price_down(self):
        """A SELL hedge must be willing to sell LOWER, not higher."""
        cfg = SpreadConfig(
            anchor_leg=Leg("A", OrderSide.BUY, ratio=1, limit_price=100),
            hedge_leg=Leg("B", OrderSide.SELL, ratio=1, limit_price=200),
            max_hedge_slippage_bps=50.0,
        )
        engine = SpreadExecutionEngine(cfg, lambda **kw: self.submitted_orders.append(kw))
        engine.start_execution(1.0)
        engine.on_anchor_fill(1.0, 100)
        # 200 * (1 - 0.005) = 199.0, independently derived.
        self.assertAlmostEqual(self.submitted_orders[1]["price"], 199.0, places=6)

    # ------------------------------------------------------------ regression

    def test_multiple_partial_hedge_fills_do_not_break_spread(self):
        """
        REGRESSION: an IOC may emit several execution reports before its remainder
        is cancelled. Observing the first partial must not declare a broken spread.
        """
        self.engine.start_execution(1.0)
        self.engine.on_anchor_fill(1.0, 65000)

        self.engine.on_hedge_fill(0.5, 66000)
        self.assertEqual(self.engine.state, SpreadState.HEDGING)  # not BROKEN yet
        self.engine.on_hedge_fill(0.5, 66020)
        self.engine.on_hedge_order_done(HedgeOrderOutcome.FILLED)

        self.assertEqual(self.engine.state, SpreadState.COMPLETED)
        self.assertEqual(self.cancelled_symbols, [])

    def test_zero_fill_ioc_is_detected_as_broken(self):
        """
        REGRESSION: a hedge that fills nothing emits no fill report at all. The
        terminal callback is the only signal, and it must fire the break.
        """
        self.engine.start_execution(1.0)
        self.engine.on_anchor_fill(1.0, 65000)

        self.engine.on_hedge_order_done(HedgeOrderOutcome.CANCELLED)

        self.assertEqual(self.engine.state, SpreadState.BROKEN)
        self.assertAlmostEqual(self.engine.unhedged_qty(), 1.0)

    def test_broken_spread_cancels_resting_anchor_order(self):
        """Naked anchor exposure must stop growing once the spread has broken."""
        self.engine.start_execution(10.0)
        self.engine.on_anchor_fill(4.0, 65000)
        self.engine.on_hedge_order_done(HedgeOrderOutcome.REJECTED)

        self.assertEqual(self.engine.state, SpreadState.BROKEN)
        self.assertEqual(self.cancelled_symbols, ["BTC-JUN"])

    def test_anchor_fills_after_break_are_ignored_and_flagged(self):
        self.engine.start_execution(10.0)
        self.engine.on_anchor_fill(4.0, 65000)
        self.engine.on_hedge_order_done(HedgeOrderOutcome.CANCELLED)
        orders_at_break = len(self.submitted_orders)

        with self.assertLogs(
            "calendar_spread_and_multi_leg_order_atomicity", level=logging.WARNING
        ):
            self.engine.on_anchor_fill(6.0, 65000)

        # No further hedge routed on a spread already handed to the emergency protocol.
        self.assertEqual(len(self.submitted_orders), orders_at_break)
        self.assertAlmostEqual(self.engine.anchor_filled_qty, 4.0)

    def test_duplicate_start_execution_rejected(self):
        """Re-entry would duplicate a live anchor order on the venue."""
        self.engine.start_execution(1.0)
        with self.assertRaises(RuntimeError):
            self.engine.start_execution(1.0)
        self.assertEqual(len(self.submitted_orders), 1)

    def test_float_residue_does_not_fake_a_break(self):
        """0.1 + 0.2 != 0.3 in binary floats; tolerance must absorb it."""
        self.engine.start_execution(0.3)
        self.engine.on_anchor_fill(0.3, 65000)
        self.engine.on_hedge_fill(0.1, 66000)
        self.engine.on_hedge_fill(0.2, 66000)
        self.engine.on_hedge_order_done(HedgeOrderOutcome.FILLED)
        self.assertEqual(self.engine.state, SpreadState.COMPLETED)

    # --------------------------------------------------------- partial fills

    def test_partial_fill_handling(self):
        self.engine.start_execution(10.0)

        # Anchor fills 4 units out of 10
        self.engine.on_anchor_fill(4.0, 65000)
        self.assertEqual(self.engine.state, SpreadState.ANCHOR_PARTIAL)

        # Engine should route hedge for exactly 4 units
        self.assertEqual(self.submitted_orders[1]["qty"], 4.0)

        # Hedge fills 4 units and terminates cleanly
        self.engine.on_hedge_fill(4.0, 66000)
        self.engine.on_hedge_order_done(HedgeOrderOutcome.FILLED)

        # State remains partial because 6 units of anchor are still pending
        self.assertEqual(self.engine.state, SpreadState.ANCHOR_PARTIAL)
        self.assertEqual(self.cancelled_symbols, [])

    def test_multi_tranche_anchor_completes(self):
        self.engine.start_execution(10.0)
        self.engine.on_anchor_fill(4.0, 65000)
        self.engine.on_hedge_fill(4.0, 66000)
        self.engine.on_hedge_order_done(HedgeOrderOutcome.FILLED)

        self.engine.on_anchor_fill(6.0, 65100)
        self.assertEqual(self.engine.state, SpreadState.HEDGING)
        self.engine.on_hedge_fill(6.0, 66100)
        self.engine.on_hedge_order_done(HedgeOrderOutcome.FILLED)
        self.assertEqual(self.engine.state, SpreadState.COMPLETED)

    def test_non_unit_ratio_sizes_hedge_correctly(self):
        cfg = SpreadConfig(
            anchor_leg=Leg("A", OrderSide.SELL, ratio=1, limit_price=100),
            hedge_leg=Leg("B", OrderSide.BUY, ratio=2, limit_price=50),
        )
        engine = SpreadExecutionEngine(cfg, lambda **kw: self.submitted_orders.append(kw))
        engine.start_execution(3.0)
        self.assertEqual(self.submitted_orders[0]["qty"], 3.0)
        engine.on_anchor_fill(3.0, 100)
        self.assertEqual(self.submitted_orders[1]["qty"], 6.0)

    # ---------------------------------------------------------- verification

    def test_realized_net_spread(self):
        self.engine.start_execution(2.0)
        self.assertIsNone(self.engine.realized_net_spread())
        self.engine.on_anchor_fill(2.0, 65000)
        self.assertIsNone(self.engine.realized_net_spread())
        self.engine.on_hedge_fill(1.0, 66000)
        self.engine.on_hedge_fill(1.0, 66100)
        # anchor VWAP 65000, hedge VWAP 66050 -> -1050, derived by hand.
        self.assertAlmostEqual(self.engine.realized_net_spread(), -1050.0, places=6)

    # ------------------------------------------------------------ validation

    def test_invalid_leg_and_config_rejected(self):
        with self.assertRaises(ValueError):
            Leg("X", OrderSide.BUY, ratio=0, limit_price=100)
        with self.assertRaises(ValueError):
            Leg("X", OrderSide.BUY, ratio=1, limit_price=0)
        with self.assertRaises(ValueError):
            SpreadConfig(self.anchor, self.hedge, max_hedge_slippage_bps=-1.0)

    def test_invalid_quantities_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.start_execution(0.0)
        self.engine.start_execution(1.0)
        with self.assertRaises(ValueError):
            self.engine.on_anchor_fill(0.0, 65000)
        with self.assertRaises(ValueError):
            self.engine.on_hedge_fill(-1.0)

    def test_missing_cancel_callback_still_breaks_loudly(self):
        engine = SpreadExecutionEngine(self.config, lambda **kw: None)
        engine.start_execution(1.0)
        engine.on_anchor_fill(1.0, 65000)
        with self.assertLogs(
            "calendar_spread_and_multi_leg_order_atomicity", level=logging.CRITICAL
        ) as cm:
            engine.on_hedge_order_done(HedgeOrderOutcome.CANCELLED)
        self.assertEqual(engine.state, SpreadState.BROKEN)
        self.assertTrue(any("MANUALLY" in m for m in cm.output))

    def test_failing_cancel_does_not_mask_broken_state(self):
        def exploding_cancel(symbol):
            raise ConnectionError("broker unreachable")

        engine = SpreadExecutionEngine(self.config, lambda **kw: None, exploding_cancel)
        engine.start_execution(1.0)
        engine.on_anchor_fill(1.0, 65000)
        with self.assertLogs(
            "calendar_spread_and_multi_leg_order_atomicity", level=logging.CRITICAL
        ):
            engine.on_hedge_order_done(HedgeOrderOutcome.CANCELLED)
        self.assertEqual(engine.state, SpreadState.BROKEN)


if __name__ == "__main__":
    unittest.main()
