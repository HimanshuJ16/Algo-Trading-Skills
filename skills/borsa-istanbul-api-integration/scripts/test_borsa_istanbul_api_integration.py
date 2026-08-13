import datetime
import unittest

from borsa_istanbul_api_integration import (
    BISTConfig,
    BISTIntegrationEngine,
    FIXOrder,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)


def _limit_order(**kwargs) -> FIXOrder:
    params = dict(
        symbol="THYAO.E",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=100,
        price=250.50,
    )
    params.update(kwargs)
    return FIXOrder(**params)


class TestSession(unittest.TestCase):
    def setUp(self):
        self.config = BISTConfig(
            sender_comp_id="MYFIRM",
            target_comp_id="BIST",
            host="192.168.1.100",
            port=9800,
        )
        self.engine = BISTIntegrationEngine(self.config)

    def test_connection_sequence(self):
        self.assertFalse(self.engine.is_connected)
        self.assertTrue(self.engine.connect())
        self.assertTrue(self.engine.is_connected)
        self.assertTrue(self.engine.disconnect())
        self.assertFalse(self.engine.is_connected)

    def test_invalid_host_rejected(self):
        bad_engine = BISTIntegrationEngine(BISTConfig("A", "B", "", 9800))
        with self.assertRaises(ValueError):
            bad_engine.connect()

    def test_invalid_port_rejected(self):
        """Regression: port 0 was caught only because it is falsy; -1 and 70000 passed."""
        for bad_port in (0, -1, 70000, 65536):
            engine = BISTIntegrationEngine(BISTConfig("A", "B", "host", bad_port))
            with self.assertRaises(ValueError, msg=f"port {bad_port} should be rejected"):
                engine.connect()

    def test_empty_comp_ids_rejected(self):
        """FIX requires both CompIDs on every message; an empty one cannot log on."""
        for sender, target in (("", "BIST"), ("MYFIRM", ""), ("   ", "BIST")):
            engine = BISTIntegrationEngine(BISTConfig(sender, target, "host", 9800))
            with self.assertRaises(ValueError):
                engine.connect()

    def test_double_connect_is_idempotent(self):
        self.assertTrue(self.engine.connect())
        self.assertTrue(self.engine.connect())
        self.assertTrue(self.engine.is_connected)


class TestOrderEntry(unittest.TestCase):
    def setUp(self):
        self.engine = BISTIntegrationEngine(
            BISTConfig("MYFIRM", "BIST", "192.168.1.100", 9800)
        )
        self.engine.connect()

    def test_submit_order(self):
        order = _limit_order()
        order_id = self.engine.submit_order(order)
        self.assertIn(order_id, self.engine.orders)
        self.assertEqual(self.engine.orders[order_id].status, OrderStatus.NEW)
        self.assertEqual(self.engine.orders[order_id].time_in_force, TimeInForce.DAY)

    def test_submit_order_disconnected(self):
        self.engine.disconnect()
        order = FIXOrder(
            symbol="GARAN.E", side=OrderSide.SELL,
            order_type=OrderType.MARKET, quantity=50,
        )
        with self.assertRaises(ConnectionError):
            self.engine.submit_order(order)

    def test_limit_order_without_price_rejected(self):
        order = FIXOrder(
            symbol="GARAN.E", side=OrderSide.SELL,
            order_type=OrderType.LIMIT, quantity=50,
        )
        with self.assertRaises(ValueError):
            self.engine.submit_order(order)

    def test_nan_quantity_rejected(self):
        """
        Regression: validation was `quantity <= 0`, and NaN fails every
        comparison, so a NaN quantity was accepted and routed.
        """
        with self.assertRaises(ValueError):
            self.engine.submit_order(_limit_order(quantity=float("nan")))

    def test_non_positive_and_infinite_quantity_rejected(self):
        for bad in (0, -1, float("inf")):
            with self.assertRaises(ValueError, msg=f"quantity {bad} should be rejected"):
                self.engine.submit_order(_limit_order(quantity=bad))

    def test_invalid_limit_price_rejected(self):
        for bad in (0, -5.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=f"price {bad} should be rejected"):
                self.engine.submit_order(_limit_order(price=bad))

    def test_market_order_with_price_rejected(self):
        """FIX: NewOrderSingle with OrdType=Market must not carry Price."""
        with self.assertRaises(ValueError):
            self.engine.submit_order(
                _limit_order(order_type=OrderType.MARKET, price=250.0)
            )

    def test_empty_symbol_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.submit_order(_limit_order(symbol="  "))

    def test_duplicate_clordid_rejected(self):
        """
        Regression: a second submit with the same ClOrdID silently overwrote the
        first order's record, discarding its accumulated fill state.
        """
        first = _limit_order(client_order_id="DUP-1")
        self.engine.submit_order(first)
        self.engine.simulate_execution_report("DUP-1", 40, 250.0, exec_id="E1")

        with self.assertRaises(ValueError):
            self.engine.submit_order(_limit_order(client_order_id="DUP-1"))

        # The original order and its fills survived the refused submission.
        self.assertEqual(self.engine.orders["DUP-1"].filled_quantity, 40)

    def test_resubmitting_a_used_order_object_rejected(self):
        """A FIXOrder carrying fills would import stale state into a new order."""
        order = _limit_order(client_order_id="USED-1")
        self.engine.submit_order(order)
        self.engine.simulate_execution_report("USED-1", 40, 250.0, exec_id="E1")

        order.client_order_id = "USED-2"  # same object, fresh ID
        with self.assertRaises(ValueError):
            self.engine.submit_order(order)

    def test_order_timestamp_is_timezone_aware(self):
        """Naive UTC timestamps silently misalign against venue timestamps."""
        order = _limit_order()
        self.assertIsNotNone(order.timestamp.tzinfo)
        self.assertEqual(order.timestamp.utcoffset(), datetime.timedelta(0))


class TestCancelLifecycle(unittest.TestCase):
    """
    A FIX Order Cancel Request (MsgType=F) requests cancellation; the order stays
    live at the venue until an ExecutionReport with ExecType=Canceled arrives, or
    an Order Cancel Reject (MsgType=9) refuses it.
    """

    def setUp(self):
        self.engine = BISTIntegrationEngine(
            BISTConfig("MYFIRM", "BIST", "192.168.1.100", 9800)
        )
        self.engine.connect()
        self.order_id = self.engine.submit_order(
            _limit_order(symbol="AKBNK.E", quantity=200, price=30.0,
                         client_order_id="C-1")
        )

    def test_cancel_request_does_not_cancel_the_order(self):
        """
        Regression: the engine marked the order CANCELED the instant the request
        was sent, before the venue had answered.
        """
        self.assertTrue(self.engine.cancel_order(self.order_id))
        self.assertEqual(
            self.engine.orders[self.order_id].status, OrderStatus.PENDING_CANCEL
        )

    def test_fill_arriving_during_pending_cancel_is_applied(self):
        """
        Regression, and the reason the bug cost money: the order was still
        resting at the venue, so a fill in the cancel race window is real. The
        old code had already marked it terminal and silently discarded it,
        leaving the local position short of the venue's.
        """
        self.engine.cancel_order(self.order_id)

        updated = self.engine.simulate_execution_report(
            self.order_id, 80, 30.0, exec_id="E-RACE"
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated.filled_quantity, 80)
        # Still pending: a fill does not answer the cancel request.
        self.assertEqual(updated.status, OrderStatus.PENDING_CANCEL)

    def test_venue_confirmation_cancels_the_order(self):
        self.engine.cancel_order(self.order_id)
        updated = self.engine.confirm_cancel(self.order_id)
        self.assertEqual(updated.status, OrderStatus.CANCELED)

    def test_cancel_reject_returns_order_to_working_state(self):
        """An Order Cancel Reject means the order was never canceled."""
        self.engine.cancel_order(self.order_id)
        updated = self.engine.reject_cancel(self.order_id, reason="Too late to cancel")
        self.assertEqual(updated.status, OrderStatus.NEW)
        self.assertEqual(updated.remaining_quantity, 200)

    def test_cancel_reject_restores_partially_filled_state(self):
        self.engine.simulate_execution_report(self.order_id, 50, 30.0, exec_id="E-1")
        self.engine.cancel_order(self.order_id)
        updated = self.engine.reject_cancel(self.order_id, reason="Order already inactive")
        self.assertEqual(updated.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(updated.remaining_quantity, 150)

    def test_duplicate_cancel_request_refused(self):
        self.assertTrue(self.engine.cancel_order(self.order_id))
        self.assertFalse(self.engine.cancel_order(self.order_id))
        self.assertEqual(
            self.engine.orders[self.order_id].status, OrderStatus.PENDING_CANCEL
        )

    def test_cannot_cancel_terminal_order(self):
        self.engine.simulate_execution_report(self.order_id, 200, 30.0, exec_id="E-FULL")
        self.assertEqual(self.engine.orders[self.order_id].status, OrderStatus.FILLED)
        self.assertFalse(self.engine.cancel_order(self.order_id))

    def test_cancel_unknown_order_returns_false(self):
        self.assertFalse(self.engine.cancel_order("no-such-id"))

    def test_unsolicited_cancel_is_accepted(self):
        """Venues cancel orders without being asked (session end, risk action)."""
        updated = self.engine.confirm_cancel(self.order_id)
        self.assertEqual(updated.status, OrderStatus.CANCELED)

    def test_cancel_reject_without_outstanding_request_is_ignored(self):
        updated = self.engine.reject_cancel(self.order_id, reason="stray")
        self.assertEqual(updated.status, OrderStatus.NEW)

    def test_confirm_cancel_on_filled_order_is_ignored(self):
        self.engine.simulate_execution_report(self.order_id, 200, 30.0, exec_id="E-FULL")
        updated = self.engine.confirm_cancel(self.order_id)
        self.assertEqual(updated.status, OrderStatus.FILLED)


class TestExecutionReports(unittest.TestCase):
    def setUp(self):
        self.engine = BISTIntegrationEngine(
            BISTConfig("MYFIRM", "BIST", "192.168.1.100", 9800)
        )
        self.engine.connect()
        self.order_id = self.engine.submit_order(
            _limit_order(symbol="TUPRS.E", quantity=100, price=150.0,
                         client_order_id="X-1")
        )

    def test_partial_then_complete_fill(self):
        updated = self.engine.simulate_execution_report(
            self.order_id, 40, 149.5, exec_id="E1"
        )
        self.assertEqual(updated.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(updated.filled_quantity, 40)
        self.assertEqual(updated.remaining_quantity, 60)

        updated = self.engine.simulate_execution_report(
            self.order_id, 60, 150.0, exec_id="E2"
        )
        self.assertEqual(updated.status, OrderStatus.FILLED)
        self.assertEqual(updated.filled_quantity, 100)
        self.assertEqual(updated.remaining_quantity, 0)
        # Hand-computed: (149.5*40 + 150.0*60) / 100 = 14980 / 100 = 149.8
        self.assertAlmostEqual(updated.average_price, 149.8)

    def test_average_price_across_three_fills(self):
        self.engine.simulate_execution_report(self.order_id, 40, 149.5, exec_id="A")
        self.engine.simulate_execution_report(self.order_id, 35, 150.25, exec_id="B")
        updated = self.engine.simulate_execution_report(self.order_id, 25, 151.0, exec_id="C")
        # Hand-computed: (5980 + 5258.75 + 3775) / 100 = 15013.75 / 100 = 150.1375
        self.assertAlmostEqual(updated.average_price, 150.1375)
        self.assertEqual(updated.status, OrderStatus.FILLED)

    def test_duplicate_exec_id_is_not_double_counted(self):
        """
        Regression: after a sequence gap the counterparty resends messages. A
        resent ExecutionReport was applied a second time, double-counting the
        fill and corrupting the average price.
        """
        self.engine.simulate_execution_report(self.order_id, 40, 149.5, exec_id="DUP")
        updated = self.engine.simulate_execution_report(self.order_id, 40, 149.5, exec_id="DUP")

        self.assertEqual(updated.filled_quantity, 40)
        self.assertEqual(updated.status, OrderStatus.PARTIALLY_FILLED)
        self.assertAlmostEqual(updated.average_price, 149.5)

    def test_same_exec_id_on_different_orders_is_not_suppressed(self):
        """
        Deduplication is keyed by (ClOrdID, ExecID). Keying on ExecID alone would
        let a fill on one order silently suppress a fill on another whenever the
        caller synthesises ExecIDs per order rather than globally.
        """
        second_id = self.engine.submit_order(
            _limit_order(symbol="GARAN.E", quantity=50, price=30.0, client_order_id="X-2")
        )

        self.engine.simulate_execution_report(self.order_id, 10, 150.0, exec_id="SEQ-1")
        other = self.engine.simulate_execution_report(second_id, 20, 30.0, exec_id="SEQ-1")

        self.assertEqual(other.filled_quantity, 20)
        self.assertEqual(self.engine.orders[self.order_id].filled_quantity, 10)

    def test_overfill_is_rejected(self):
        """
        Regression: cumulative filled quantity was incremented unchecked, so a
        report could take it past the order quantity, marking the order FILLED
        on a quantity that was never ordered.
        """
        self.engine.simulate_execution_report(self.order_id, 90, 150.0, exec_id="O1")
        updated = self.engine.simulate_execution_report(self.order_id, 50, 150.0, exec_id="O2")

        self.assertEqual(updated.filled_quantity, 90)
        self.assertEqual(updated.status, OrderStatus.PARTIALLY_FILLED)

    def test_exact_fill_to_quantity_completes_order(self):
        updated = self.engine.simulate_execution_report(
            self.order_id, 100, 150.0, exec_id="E-EXACT"
        )
        self.assertEqual(updated.status, OrderStatus.FILLED)

    def test_invalid_fill_quantity_rejected(self):
        for bad in (0, -10, float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=f"filled_qty {bad} should be rejected"):
                self.engine.simulate_execution_report(self.order_id, bad, 150.0, exec_id="Z")

    def test_invalid_exec_price_rejected(self):
        for bad in (0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=f"exec_price {bad} should be rejected"):
                self.engine.simulate_execution_report(self.order_id, 10, bad, exec_id="Z")

    def test_unknown_order_returns_none(self):
        self.assertIsNone(
            self.engine.simulate_execution_report("no-such-id", 10, 150.0, exec_id="Z")
        )

    def test_report_after_terminal_state_is_ignored(self):
        self.engine.simulate_execution_report(self.order_id, 100, 150.0, exec_id="F1")
        updated = self.engine.simulate_execution_report(self.order_id, 10, 150.0, exec_id="F2")
        self.assertEqual(updated.filled_quantity, 100)
        self.assertEqual(updated.status, OrderStatus.FILLED)


if __name__ == "__main__":
    unittest.main()
