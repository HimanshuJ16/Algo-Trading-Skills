"""
Unit tests for the Bursa Malaysia BTS2 order-lifecycle state machine.

The expected values in the fill tests are taken from Bursa Malaysia's own
published BTS2 FIX Certification Test Logs (module A01.1), not recomputed from
this module's formula: SecurityID 1082 on board NM, 5000 shares, filled 2000 @
6.10 then 3000 @ 6.20, with the exchange reporting AvgPx(6)=6.16.
"""
import unittest

from bursa_malaysia_api_integration import (
    BEGIN_STRING,
    MAX_CLORDID_LENGTH,
    MAX_LOGON_ATTEMPTS,
    Board,
    BursaConfig,
    BursaMalaysiaFixEngine,
    ConnectionType,
    Environment,
    FIXOrder,
    OrderCapacity,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
    new_client_order_id,
)


def make_config(**overrides):
    """A valid production FIXTRADER config; override one field per test."""
    params = dict(
        sender_comp_id="0031",
        target_comp_id="XSTRMO",
        host="10.1.117.10",
        port=9999,
        username="BMIOE031901",
        password="Passw0rd1",
        connection_type=ConnectionType.FIXTRADER,
        broker_code="031901",
        environment=Environment.PRODUCTION,
    )
    params.update(overrides)
    return BursaConfig(**params)


def make_order(**overrides):
    params = dict(
        security_id="1082",
        board=Board.NORMAL,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=5000,
        account="1002",
        order_restrictions="E",
        price=6.10,
        order_capacity=OrderCapacity.PRINCIPAL,
    )
    params.update(overrides)
    return FIXOrder(**params)


class TestSessionConfiguration(unittest.TestCase):
    def test_begin_string_must_be_fixt_1_1(self):
        # The common misconfiguration: putting the application version in
        # BeginString. BTS2 speaks FIXT.1.1 with DefaultApplVerID=8 (FIX50SP1).
        self.assertEqual(make_config().begin_string, BEGIN_STRING)
        with self.assertRaises(ValueError):
            make_config(begin_string="FIX.5.0SP1")
        with self.assertRaises(ValueError):
            make_config(begin_string="FIX.4.2")

    def test_only_appl_ver_id_8_accepted(self):
        with self.assertRaises(ValueError):
            make_config(default_appl_ver_id="9")

    def test_heartbeat_must_be_within_gateway_range(self):
        # BTS2 accepts HeartBtInt 10-60 and silently substitutes its own value
        # outside that range, so an out-of-range setting must fail locally.
        for good in (10, 30, 60):
            self.assertEqual(make_config(heartbeat_interval=good).heartbeat_interval, good)
        for bad in (0, 9, 61, 120):
            with self.assertRaises(ValueError):
                make_config(heartbeat_interval=bad)

    def test_password_length_capped_and_never_echoed(self):
        with self.assertRaises(ValueError) as ctx:
            make_config(password="thirteenchars")
        self.assertNotIn("thirteenchars", str(ctx.exception))
        self.assertIn("<redacted>", str(ctx.exception))

    def test_password_absent_from_repr(self):
        self.assertNotIn("Passw0rd1", repr(make_config()))

    def test_credentials_and_endpoint_required(self):
        for override in (
            {"username": ""},
            {"password": ""},
            {"sender_comp_id": " "},
            {"target_comp_id": ""},
            {"host": ""},
            {"port": 0},
            {"port": 70000},
        ):
            with self.subTest(**override):
                with self.assertRaises(ValueError):
                    make_config(**override)


class TestBrokerCode(unittest.TestCase):
    def test_shape_is_six_digits(self):
        for bad in ("", "12345", "0319011", "03190A"):
            with self.subTest(broker_code=bad):
                with self.assertRaises(ValueError):
                    make_config(broker_code=bad)

    def test_production_branch_digit_must_match_connection_type(self):
        # '9' ordinary and '1' market maker are issued with FIXTRADER; '2'
        # (Direct Business Transactions) with FIXNEGDEAL.
        make_config(broker_code="068901")
        make_config(broker_code="068101")
        with self.assertRaises(ValueError):
            make_config(broker_code="068201")
        make_config(connection_type=ConnectionType.FIXNEGDEAL, broker_code="068201")
        with self.assertRaises(ValueError):
            make_config(connection_type=ConnectionType.FIXNEGDEAL, broker_code="068901")

    def test_certification_environment_does_not_apply_production_formats(self):
        # Bursa documents the branch formats as production-only; UAT issues its
        # own codes, so enforcing them there would block valid testing.
        cfg = make_config(broker_code="068201", environment=Environment.CERTIFICATION)
        self.assertEqual(cfg.broker_code, "068201")

    def test_market_maker_code_is_normal_board_only(self):
        engine = BursaMalaysiaFixEngine(make_config(broker_code="068101"))
        engine.connect()
        engine.submit_order(make_order(board=Board.NORMAL))
        with self.assertRaises(ValueError):
            engine.submit_order(make_order(board=Board.ODD_LOT))


class TestOrderValidation(unittest.TestCase):
    def setUp(self):
        self.engine = BursaMalaysiaFixEngine(make_config())
        self.engine.connect()

    def test_generated_cl_ord_id_fits_the_wire_limit(self):
        # A bare uuid4 string is 36 characters; BTS2 ClOrdID(11) is String(20).
        self.assertLessEqual(len(new_client_order_id()), MAX_CLORDID_LENGTH)
        self.assertLessEqual(len(make_order().client_order_id), MAX_CLORDID_LENGTH)

    def test_over_long_cl_ord_id_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(client_order_id="x" * 21))

    def test_duplicate_cl_ord_id_rejected_and_fills_preserved(self):
        order = make_order(client_order_id="ORD1")
        self.engine.submit_order(order)
        self.engine.simulate_execution_report("ORD1", 2000, 6.10, exec_id="E1")

        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(client_order_id="ORD1", quantity=100))

        # The original order's fill state must survive the refused duplicate.
        self.assertIs(self.engine.orders["ORD1"], order)
        self.assertEqual(order.filled_quantity, 2000)
        self.assertEqual(order.quantity, 5000)

    def test_cancel_request_id_cannot_collide_with_a_later_order_id(self):
        self.engine.submit_order(make_order(client_order_id="ORD1"))
        self.engine.cancel_order("ORD1", cancel_cl_ord_id="CXL1")
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(client_order_id="CXL1"))

    def test_nan_quantity_rejected(self):
        # NaN fails every comparison, so `quantity <= 0` alone would pass it.
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(quantity=float("nan")))
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(quantity=float("inf")))
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(quantity=0))

    def test_price_rules_by_order_type(self):
        # Limit needs a price; Market and Market-at-Best must not carry one.
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(order_type=OrderType.LIMIT, price=None))
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(order_type=OrderType.MARKET, price=6.10))
        with self.assertRaises(ValueError):
            self.engine.submit_order(
                make_order(order_type=OrderType.MARKET_AT_BEST, price=6.10)
            )
        self.engine.submit_order(make_order(order_type=OrderType.MARKET, price=None))
        self.engine.submit_order(make_order(order_type=OrderType.MARKET_AT_BEST, price=None))

    def test_trigger_price_rules_by_order_type(self):
        with self.assertRaises(ValueError):
            self.engine.submit_order(
                make_order(order_type=OrderType.STOP, price=None, trigger_price=None)
            )
        with self.assertRaises(ValueError):
            # Stop is activated as a Market order; Price(44) is not specified.
            self.engine.submit_order(
                make_order(order_type=OrderType.STOP, price=6.10, trigger_price=6.00)
            )
        with self.assertRaises(ValueError):
            self.engine.submit_order(
                make_order(order_type=OrderType.STOP_LIMIT, price=6.10, trigger_price=None)
            )
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(order_type=OrderType.LIMIT, trigger_price=6.0))

        self.engine.submit_order(
            make_order(order_type=OrderType.STOP, price=None, trigger_price=6.00)
        )
        self.engine.submit_order(
            make_order(order_type=OrderType.STOP_LIMIT, price=6.10, trigger_price=6.00)
        )

    def test_security_id_required(self):
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(security_id=""))

    def test_cds_account_must_be_digits_and_is_padded_to_nine(self):
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(account="ABC"))
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(account="1234567890"))
        order = make_order(account="1002")
        self.engine.submit_order(order)
        self.assertEqual(order.padded_account, "000001002")

    def test_order_restrictions_required_and_validated(self):
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(order_restrictions=""))
        with self.assertRaises(ValueError):
            self.engine.submit_order(make_order(order_restrictions="X"))
        with self.assertRaises(ValueError):
            # Capped at 5 characters, so at most three space-separated values.
            self.engine.submit_order(make_order(order_restrictions="E M R I"))
        self.engine.submit_order(make_order(order_restrictions="E M"))
        self.engine.submit_order(make_order(order_restrictions="E M R"))

    def test_short_sell_sides_are_distinct_from_sell(self):
        order = make_order(side=OrderSide.INTRADAY_SHORT_SELL)
        self.assertTrue(order.is_short_sell)
        self.assertFalse(make_order(side=OrderSide.SELL).is_short_sell)
        self.engine.submit_order(order)

    def test_used_order_object_cannot_be_resubmitted(self):
        order = make_order(client_order_id="ORD1")
        self.engine.submit_order(order)
        self.engine.simulate_execution_report("ORD1", 1000, 6.10, exec_id="E1")
        order.client_order_id = "ORD2"
        with self.assertRaises(ValueError):
            self.engine.submit_order(order)

    def test_submit_requires_a_session(self):
        engine = BursaMalaysiaFixEngine(make_config())
        with self.assertRaises(ConnectionError):
            engine.submit_order(make_order())


class TestConnectionTypeRouting(unittest.TestCase):
    def test_fixnegdeal_refuses_lit_board_order_entry(self):
        # FIXNEGDEAL carries Direct Business Transaction / off-market business;
        # Normal-board order flow belongs on FIXTRADER.
        engine = BursaMalaysiaFixEngine(
            make_config(connection_type=ConnectionType.FIXNEGDEAL, broker_code="068201")
        )
        engine.connect()
        with self.assertRaises(ValueError):
            engine.submit_order(make_order(board=Board.NORMAL))

    def test_fixtrader_accepts_its_three_boards(self):
        engine = BursaMalaysiaFixEngine(make_config(broker_code="068901"))
        engine.connect()
        for board in (Board.NORMAL, Board.ODD_LOT, Board.BUY_IN):
            with self.subTest(board=board):
                engine.submit_order(make_order(board=board))


class TestLogonLockout(unittest.TestCase):
    def test_connect_refuses_after_the_lockout_budget(self):
        # BTS2 locks the account after a defined number of failed logons
        # (default 3); unlocking needs exchange operations.
        engine = BursaMalaysiaFixEngine(make_config())
        for _ in range(MAX_LOGON_ATTEMPTS):
            engine.record_logon_failure("invalid password")
        with self.assertRaises(ConnectionError):
            engine.connect()

    def test_successful_connect_clears_the_counter(self):
        engine = BursaMalaysiaFixEngine(make_config())
        engine.record_logon_failure("transient")
        engine.connect()
        self.assertEqual(engine.failed_logon_attempts, 0)


class TestExecutionReports(unittest.TestCase):
    def setUp(self):
        self.engine = BursaMalaysiaFixEngine(make_config())
        self.engine.connect()
        self.order = make_order(client_order_id="ORD1")
        self.engine.submit_order(self.order)

    def test_certification_log_fill_sequence(self):
        # Expected values taken from Bursa's published certification test log.
        self.engine.simulate_execution_report("ORD1", 2000, 6.10, exec_id="1535B")
        self.assertEqual(self.order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(self.order.filled_quantity, 2000)
        self.assertEqual(self.order.remaining_quantity, 3000)
        self.assertAlmostEqual(self.order.average_price, 6.10, places=6)

        self.engine.simulate_execution_report("ORD1", 3000, 6.20, exec_id="1538B")
        self.assertEqual(self.order.status, OrderStatus.FILLED)
        self.assertEqual(self.order.filled_quantity, 5000)
        self.assertEqual(self.order.remaining_quantity, 0)
        self.assertAlmostEqual(self.order.average_price, 6.16, places=6)

    def test_resent_report_with_same_exec_id_is_ignored(self):
        self.engine.simulate_execution_report("ORD1", 2000, 6.10, exec_id="1535B")
        self.engine.simulate_execution_report("ORD1", 2000, 6.10, exec_id="1535B")
        self.assertEqual(self.order.filled_quantity, 2000)
        self.assertAlmostEqual(self.order.average_price, 6.10, places=6)

    def test_same_exec_id_on_a_different_order_still_applies(self):
        other = make_order(client_order_id="ORD2")
        self.engine.submit_order(other)
        self.engine.simulate_execution_report("ORD1", 1000, 6.10, exec_id="E1")
        self.engine.simulate_execution_report("ORD2", 1000, 6.10, exec_id="E1")
        self.assertEqual(other.filled_quantity, 1000)

    def test_overfill_is_refused(self):
        self.engine.simulate_execution_report("ORD1", 4000, 6.10, exec_id="E1")
        self.engine.simulate_execution_report("ORD1", 2000, 6.20, exec_id="E2")
        self.assertEqual(self.order.filled_quantity, 4000)
        self.assertEqual(self.order.status, OrderStatus.PARTIALLY_FILLED)

    def test_float_residue_does_not_strand_a_filled_order(self):
        engine = BursaMalaysiaFixEngine(make_config())
        engine.connect()
        order = make_order(client_order_id="ORD9", quantity=0.3)
        engine.submit_order(order)
        engine.simulate_execution_report("ORD9", 0.1, 6.10, exec_id="A")
        engine.simulate_execution_report("ORD9", 0.2, 6.10, exec_id="B")
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_unknown_order_returns_none(self):
        self.assertIsNone(self.engine.simulate_execution_report("NOPE", 1, 6.10, exec_id="E"))

    def test_invalid_fill_values_rejected(self):
        for qty, px in ((0, 6.10), (-1, 6.10), (float("nan"), 6.10), (100, 0), (100, float("inf"))):
            with self.subTest(qty=qty, px=px):
                with self.assertRaises(ValueError):
                    self.engine.simulate_execution_report("ORD1", qty, px, exec_id="E")


class TestCancelLifecycle(unittest.TestCase):
    def setUp(self):
        self.engine = BursaMalaysiaFixEngine(make_config())
        self.engine.connect()
        self.order = make_order(client_order_id="ORD1")
        self.engine.submit_order(self.order)

    def test_cancel_request_does_not_cancel(self):
        # Regression guard: an implementation that marks the order CANCELED on
        # the request loses every fill that lands in the race window.
        self.assertTrue(self.engine.cancel_order("ORD1"))
        self.assertEqual(self.order.status, OrderStatus.PENDING_CANCEL)
        self.assertNotIn(self.order.status, (OrderStatus.CANCELED,))
        self.assertIsNotNone(self.order.pending_cancel_cl_ord_id)

    def test_fill_during_pending_cancel_is_applied_and_state_preserved(self):
        self.engine.cancel_order("ORD1")
        self.engine.simulate_execution_report("ORD1", 2000, 6.10, exec_id="E1")
        self.assertEqual(self.order.filled_quantity, 2000)
        self.assertEqual(self.order.status, OrderStatus.PENDING_CANCEL)

    def test_full_fill_during_pending_cancel_becomes_filled(self):
        self.engine.cancel_order("ORD1")
        self.engine.simulate_execution_report("ORD1", 5000, 6.10, exec_id="E1")
        self.assertEqual(self.order.status, OrderStatus.FILLED)

    def test_confirm_cancel_finalises(self):
        self.engine.cancel_order("ORD1")
        self.engine.confirm_cancel("ORD1")
        self.assertEqual(self.order.status, OrderStatus.CANCELED)
        self.assertIsNone(self.order.pending_cancel_cl_ord_id)

    def test_reject_cancel_returns_the_order_to_working(self):
        self.engine.cancel_order("ORD1")
        self.engine.reject_cancel("ORD1", reason="order already inactive")
        self.assertEqual(self.order.status, OrderStatus.NEW)

        self.engine.simulate_execution_report("ORD1", 2000, 6.10, exec_id="E1")
        self.engine.cancel_order("ORD1")
        self.engine.reject_cancel("ORD1", reason="cannot withdraw")
        self.assertEqual(self.order.status, OrderStatus.PARTIALLY_FILLED)

    def test_cancel_request_ids_are_unique_and_distinct_from_order_ids(self):
        self.engine.cancel_order("ORD1")
        first = self.order.pending_cancel_cl_ord_id
        self.engine.reject_cancel("ORD1")
        # A fresh request must not reuse the previous request's ClOrdID: BTS2
        # requires it unique amongst order and replacement-order ClOrdIDs.
        with self.assertRaises(ValueError):
            self.engine.cancel_order("ORD1", cancel_cl_ord_id=first)
        self.engine.cancel_order("ORD1")
        second = self.order.pending_cancel_cl_ord_id
        self.assertNotEqual(first, second)
        self.assertNotIn("ORD1", (first, second))

    def test_second_cancel_request_is_not_sent_while_one_is_outstanding(self):
        self.assertTrue(self.engine.cancel_order("ORD1"))
        self.assertFalse(self.engine.cancel_order("ORD1"))

    def test_cancel_of_unknown_or_terminal_order_returns_false(self):
        self.assertFalse(self.engine.cancel_order("NOPE"))
        self.engine.simulate_execution_report("ORD1", 5000, 6.10, exec_id="E1")
        self.assertFalse(self.engine.cancel_order("ORD1"))

    def test_unsolicited_cancel_from_working_state(self):
        # A supervisor cancel or a cancel entered via the native protocol arrives
        # with no request of ours outstanding.
        self.engine.confirm_cancel("ORD1")
        self.assertEqual(self.order.status, OrderStatus.CANCELED)

    def test_reports_after_cancel_are_ignored(self):
        self.engine.cancel_order("ORD1")
        self.engine.confirm_cancel("ORD1")
        self.engine.simulate_execution_report("ORD1", 1000, 6.10, exec_id="E9")
        self.assertEqual(self.order.filled_quantity, 0)


class TestExpiry(unittest.TestCase):
    def test_ioc_remainder_expires(self):
        engine = BursaMalaysiaFixEngine(make_config())
        engine.connect()
        order = make_order(
            client_order_id="ORD1", time_in_force=TimeInForce.IMMEDIATE_OR_CANCEL
        )
        engine.submit_order(order)
        engine.simulate_execution_report("ORD1", 1000, 6.10, exec_id="E1")
        engine.expire_order("ORD1", reason="IOC remainder")
        self.assertEqual(order.status, OrderStatus.EXPIRED)
        self.assertEqual(order.filled_quantity, 1000)
        # Terminal: no further report may change it.
        engine.simulate_execution_report("ORD1", 1000, 6.10, exec_id="E2")
        self.assertEqual(order.filled_quantity, 1000)
        self.assertFalse(engine.cancel_order("ORD1"))


if __name__ == "__main__":
    unittest.main()
