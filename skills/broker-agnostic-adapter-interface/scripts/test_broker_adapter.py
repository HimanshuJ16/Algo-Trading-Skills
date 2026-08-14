import unittest
from decimal import Decimal

from broker_adapter import (
    AccountBalance,
    AdapterRegistrationError,
    BaseBrokerAdapter,
    BrokerAdapterFactory,
    MockAlpacaAdapter,
    MockIBKRAdapter,
    MockZerodhaAdapter,
    OrderExecutionError,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)


class FactoryTestCase(unittest.TestCase):
    """Registry is process-wide shared state; isolate every test from it."""

    def setUp(self):
        BrokerAdapterFactory.reset()
        BrokerAdapterFactory.register_simulated_adapters()

    def tearDown(self):
        BrokerAdapterFactory.reset()


class TestFactory(FactoryTestCase):
    def test_factory_creation(self):
        for name, cls in (("zerodha", MockZerodhaAdapter),
                          ("alpaca", MockAlpacaAdapter),
                          ("ibkr", MockIBKRAdapter)):
            adapter = BrokerAdapterFactory.create(name)
            self.assertIsInstance(adapter, cls)
            self.assertEqual(adapter.broker_name, name)

    def test_unregistered_broker_raises_key_error(self):
        with self.assertRaises(KeyError):
            BrokerAdapterFactory.create("unsupported_broker")

    def test_registry_starts_empty_so_a_config_string_cannot_pick_up_a_mock(self):
        """
        Regression: the shipped registry bound production broker names to
        simulated adapters, so `create(config["broker"])` returned a mock that
        reports every order FILLED at an invented price.
        """
        BrokerAdapterFactory.reset()
        self.assertEqual(BrokerAdapterFactory.available(), [])
        with self.assertRaises(KeyError) as ctx:
            BrokerAdapterFactory.create("zerodha")
        self.assertIn("registry starts empty", str(ctx.exception))

    def test_register_rejects_a_class_that_is_not_an_adapter(self):
        """Regression: any object could be registered and only failed at call time."""
        class NotAnAdapter:
            pass

        with self.assertRaises(AdapterRegistrationError):
            BrokerAdapterFactory.register("bogus", NotAnAdapter)
        self.assertNotIn("bogus", BrokerAdapterFactory.available())

    def test_register_rejects_empty_name(self):
        with self.assertRaises(AdapterRegistrationError):
            BrokerAdapterFactory.register("   ", MockZerodhaAdapter)

    def test_registration_is_case_insensitive(self):
        BrokerAdapterFactory.register("MyBroker", MockAlpacaAdapter)
        self.assertIsInstance(BrokerAdapterFactory.create("mybroker"), MockAlpacaAdapter)


class TestStatusNormalization(FactoryTestCase):
    """
    An unmapped status must never be reported as PENDING. Saying "this order is
    live" about an order you cannot classify is how a terminal state gets treated
    as working — the strategy waits forever, or re-sends an order that already
    filled.
    """

    def test_unknown_status_is_unknown_not_pending(self):
        for name in ("zerodha", "alpaca", "ibkr"):
            adapter = BrokerAdapterFactory.create(name)
            with self.subTest(broker=name):
                self.assertEqual(
                    adapter.normalize_status("some_status_invented_next_quarter"),
                    OrderStatus.UNKNOWN,
                )

    def test_non_string_status_is_unknown(self):
        adapter = BrokerAdapterFactory.create("alpaca")
        for value in (None, 42, object()):
            with self.subTest(value=value):
                self.assertEqual(adapter.normalize_status(value), OrderStatus.UNKNOWN)

    def test_zerodha_terminal_lapsed_is_not_reported_as_working(self):
        """Regression: LAPSED is terminal at Kite but fell through to PENDING."""
        adapter = BrokerAdapterFactory.create("zerodha")
        self.assertEqual(adapter.normalize_status("LAPSED"), OrderStatus.EXPIRED)

    def test_zerodha_documented_statuses(self):
        adapter = BrokerAdapterFactory.create("zerodha")
        cases = {
            "COMPLETE": OrderStatus.FILLED,
            "REJECTED": OrderStatus.REJECTED,
            "CANCELLED": OrderStatus.CANCELLED,
            "OPEN": OrderStatus.PENDING,
            "TRIGGER PENDING": OrderStatus.PENDING,
            "VALIDATION PENDING": OrderStatus.PENDING,
            "PUT ORDER REQ RECEIVED": OrderStatus.PENDING,
            "CANCEL PENDING": OrderStatus.PENDING,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(adapter.normalize_status(raw), expected)

    def test_ibkr_apicancelled_is_cancelled_not_pending(self):
        """Regression: a real IBKR terminal status was unmapped and read as live."""
        adapter = BrokerAdapterFactory.create("ibkr")
        self.assertEqual(adapter.normalize_status("ApiCancelled"), OrderStatus.CANCELLED)

    def test_ibkr_lookup_is_case_insensitive(self):
        """
        Regression: IBKR used a case-sensitive lookup while the other adapters
        upper-cased, so "FILLED" fell through to the PENDING default while
        "Filled" resolved — a filled order reported as still working.
        """
        adapter = BrokerAdapterFactory.create("ibkr")
        for raw in ("Filled", "FILLED", "filled"):
            with self.subTest(raw=raw):
                self.assertEqual(adapter.normalize_status(raw), OrderStatus.FILLED)

    def test_ibkr_documented_statuses(self):
        adapter = BrokerAdapterFactory.create("ibkr")
        cases = {
            "Filled": OrderStatus.FILLED,
            "Cancelled": OrderStatus.CANCELLED,
            "ApiCancelled": OrderStatus.CANCELLED,
            "Inactive": OrderStatus.REJECTED,
            "Submitted": OrderStatus.PENDING,
            "PreSubmitted": OrderStatus.PENDING,
            "PendingSubmit": OrderStatus.PENDING,
            "PendingCancel": OrderStatus.PENDING,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(adapter.normalize_status(raw), expected)

    def test_alpaca_documented_statuses(self):
        adapter = BrokerAdapterFactory.create("alpaca")
        cases = {
            "filled": OrderStatus.FILLED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "rejected": OrderStatus.REJECTED,
            "canceled": OrderStatus.CANCELLED,
            "expired": OrderStatus.EXPIRED,
            "done_for_day": OrderStatus.EXPIRED,
            "new": OrderStatus.PENDING,
            "pending_new": OrderStatus.PENDING,
            "accepted": OrderStatus.PENDING,
            "pending_cancel": OrderStatus.PENDING,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(adapter.normalize_status(raw), expected)

    def test_unknown_is_not_treated_as_terminal(self):
        result = OrderResult(
            order_id="X", broker_name="alpaca", status=OrderStatus.UNKNOWN,
            filled_quantity=Decimal("0"), average_price=Decimal("0"),
        )
        self.assertFalse(result.is_terminal)

    def test_terminal_statuses_report_terminal(self):
        for status in (OrderStatus.FILLED, OrderStatus.REJECTED,
                       OrderStatus.CANCELLED, OrderStatus.EXPIRED):
            with self.subTest(status=status):
                result = OrderResult(
                    order_id="X", broker_name="alpaca", status=status,
                    filled_quantity=Decimal("0"), average_price=Decimal("0"),
                )
                self.assertTrue(result.is_terminal)


class TestOrderExecution(FactoryTestCase):
    def test_zerodha_order_execution(self):
        adapter = BrokerAdapterFactory.create("zerodha")
        req = OrderRequest(symbol="INFY", side=OrderSide.BUY,
                           order_type=OrderType.MARKET, quantity=Decimal("10"))
        res = adapter.place_order(req)

        self.assertIsInstance(res, OrderResult)
        self.assertEqual(res.status, OrderStatus.FILLED)
        self.assertEqual(res.filled_quantity, Decimal("10"))
        self.assertEqual(res.broker_name, "zerodha")
        self.assertEqual(res.commission, Decimal("20.0"))

    def test_alpaca_order_execution(self):
        adapter = BrokerAdapterFactory.create("alpaca")
        req = OrderRequest(symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.LIMIT,
                           quantity=Decimal("5"), price=Decimal("180.0"))
        res = adapter.place_order(req)

        self.assertEqual(res.status, OrderStatus.FILLED)
        self.assertEqual(res.filled_quantity, Decimal("5"))
        self.assertEqual(res.average_price, Decimal("180.0"))

    def test_ibkr_order_execution(self):
        adapter = BrokerAdapterFactory.create("ibkr")
        req = OrderRequest(symbol="TSLA", side=OrderSide.SELL,
                           order_type=OrderType.MARKET, quantity=Decimal("15"))
        res = adapter.place_order(req)

        self.assertEqual(res.status, OrderStatus.PENDING)
        self.assertEqual(res.broker_name, "ibkr")

    def test_result_echoes_client_order_id(self):
        """Without the echo the caller cannot correlate a response to its request."""
        adapter = BrokerAdapterFactory.create("zerodha")
        req = OrderRequest(symbol="INFY", side=OrderSide.BUY,
                           order_type=OrderType.MARKET, quantity=Decimal("1"),
                           client_order_id="my-idempotency-key")
        self.assertEqual(adapter.place_order(req).client_order_id, "my-idempotency-key")


class TestRequestValidation(FactoryTestCase):
    """Validation lives on the base class so a new adapter cannot omit it."""

    def setUp(self):
        super().setUp()
        self.adapters = [BrokerAdapterFactory.create(n) for n in ("zerodha", "alpaca", "ibkr")]

    def _assert_all_reject(self, req_factory):
        for adapter in self.adapters:
            with self.subTest(broker=adapter.broker_name):
                with self.assertRaises(OrderExecutionError):
                    adapter.place_order(req_factory())

    def test_invalid_quantity_rejected(self):
        for bad in (Decimal("-5"), Decimal("0")):
            self._assert_all_reject(
                lambda b=bad: OrderRequest(symbol="INFY", side=OrderSide.BUY,
                                           order_type=OrderType.MARKET, quantity=b)
            )

    def test_non_finite_quantity_rejected_without_leaking_decimal_errors(self):
        """
        Regression: Decimal('NaN') raised decimal.InvalidOperation straight through
        the adapter boundary — the exact leaky abstraction this skill warns about.
        """
        for bad in (Decimal("NaN"), Decimal("Infinity")):
            self._assert_all_reject(
                lambda b=bad: OrderRequest(symbol="INFY", side=OrderSide.BUY,
                                           order_type=OrderType.MARKET, quantity=b)
            )

    def test_float_quantity_rejected(self):
        """
        Regression: a float passed validation and propagated into OrderResult,
        defeating the module's stated precision guarantee and only surfacing
        later as a TypeError against a Decimal.
        """
        self._assert_all_reject(
            lambda: OrderRequest(symbol="INFY", side=OrderSide.BUY,
                                 order_type=OrderType.MARKET, quantity=0.1)
        )

    def test_float_price_rejected(self):
        self._assert_all_reject(
            lambda: OrderRequest(symbol="INFY", side=OrderSide.BUY,
                                 order_type=OrderType.LIMIT, quantity=Decimal("1"),
                                 price=180.5)
        )

    def test_int_quantity_is_widened_losslessly(self):
        adapter = BrokerAdapterFactory.create("zerodha")
        req = OrderRequest(symbol="INFY", side=OrderSide.BUY,
                           order_type=OrderType.MARKET, quantity=7)
        res = adapter.place_order(req)
        self.assertEqual(res.filled_quantity, Decimal("7"))
        self.assertIsInstance(res.filled_quantity, Decimal)

    def test_limit_order_without_price_rejected(self):
        """Regression: the price fell back to a fabricated constant (100.50)."""
        self._assert_all_reject(
            lambda: OrderRequest(symbol="INFY", side=OrderSide.BUY,
                                 order_type=OrderType.LIMIT, quantity=Decimal("1"))
        )

    def test_zero_limit_price_rejected(self):
        """
        Regression: `if request.price` treats Decimal('0') as absent, so a zero
        limit price was silently replaced by the fabricated default.
        """
        self._assert_all_reject(
            lambda: OrderRequest(symbol="INFY", side=OrderSide.BUY,
                                 order_type=OrderType.LIMIT, quantity=Decimal("1"),
                                 price=Decimal("0"))
        )

    def test_market_order_with_price_rejected(self):
        self._assert_all_reject(
            lambda: OrderRequest(symbol="INFY", side=OrderSide.BUY,
                                 order_type=OrderType.MARKET, quantity=Decimal("1"),
                                 price=Decimal("100"))
        )

    def test_stop_order_without_stop_price_rejected(self):
        self._assert_all_reject(
            lambda: OrderRequest(symbol="INFY", side=OrderSide.BUY,
                                 order_type=OrderType.STOP, quantity=Decimal("1"))
        )

    def test_stop_limit_requires_both_prices(self):
        self._assert_all_reject(
            lambda: OrderRequest(symbol="INFY", side=OrderSide.BUY,
                                 order_type=OrderType.STOP_LIMIT, quantity=Decimal("1"),
                                 price=Decimal("100"))
        )

    def test_empty_symbol_rejected(self):
        self._assert_all_reject(
            lambda: OrderRequest(symbol="   ", side=OrderSide.BUY,
                                 order_type=OrderType.MARKET, quantity=Decimal("1"))
        )

    def test_wrong_enum_types_rejected(self):
        self._assert_all_reject(
            lambda: OrderRequest(symbol="INFY", side="BUY",
                                 order_type=OrderType.MARKET, quantity=Decimal("1"))
        )

    def test_valid_stop_limit_accepted(self):
        adapter = BrokerAdapterFactory.create("alpaca")
        req = OrderRequest(symbol="AAPL", side=OrderSide.SELL,
                           order_type=OrderType.STOP_LIMIT, quantity=Decimal("3"),
                           price=Decimal("175.00"), stop_price=Decimal("176.00"))
        self.assertEqual(adapter.place_order(req).filled_quantity, Decimal("3"))


class TestInterfaceContract(FactoryTestCase):
    def test_incomplete_adapter_cannot_be_instantiated(self):
        class Incomplete(BaseBrokerAdapter):
            @property
            def broker_name(self) -> str:
                return "incomplete"

        with self.assertRaises(TypeError):
            Incomplete()

    def test_positions_and_balance_normalization(self):
        adapter = BrokerAdapterFactory.create("zerodha")
        positions = adapter.get_positions()
        balance = adapter.get_account_balance()

        self.assertTrue(len(positions) > 0)
        self.assertIsInstance(positions[0], Position)
        self.assertIsInstance(balance, AccountBalance)
        self.assertIsInstance(balance.cash_available, Decimal)

    def test_all_adapters_return_decimal_metrics(self):
        for name in ("zerodha", "alpaca", "ibkr"):
            adapter = BrokerAdapterFactory.create(name)
            with self.subTest(broker=name):
                req = OrderRequest(symbol="X", side=OrderSide.BUY,
                                   order_type=OrderType.MARKET, quantity=Decimal("2"))
                res = adapter.place_order(req)
                self.assertIsInstance(res.filled_quantity, Decimal)
                self.assertIsInstance(res.average_price, Decimal)
                self.assertIsInstance(res.commission, Decimal)
                balance = adapter.get_account_balance()
                self.assertIsInstance(balance.total_equity, Decimal)


if __name__ == "__main__":
    unittest.main()
