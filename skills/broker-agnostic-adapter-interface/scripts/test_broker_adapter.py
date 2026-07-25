import unittest
from decimal import Decimal
from broker_adapter import (
    AccountBalance,
    BrokerAdapterFactory,
    MockAlpacaAdapter,
    MockZerodhaAdapter,
    MockIBKRAdapter,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    OrderExecutionError,
)

class TestBrokerAgnosticAdapter(unittest.TestCase):

    def test_factory_creation(self):
        zerodha = BrokerAdapterFactory.create("zerodha")
        self.assertIsInstance(zerodha, MockZerodhaAdapter)
        self.assertEqual(zerodha.broker_name, "zerodha")

        alpaca = BrokerAdapterFactory.create("alpaca")
        self.assertIsInstance(alpaca, MockAlpacaAdapter)
        self.assertEqual(alpaca.broker_name, "alpaca")
        
        ibkr = BrokerAdapterFactory.create("ibkr")
        self.assertIsInstance(ibkr, MockIBKRAdapter)
        self.assertEqual(ibkr.broker_name, "ibkr")

    def test_unregistered_broker_raises_key_error(self):
        with self.assertRaises(KeyError):
            BrokerAdapterFactory.create("unsupported_broker")

    def test_zerodha_order_execution(self):
        adapter = BrokerAdapterFactory.create("zerodha")
        req = OrderRequest(symbol="INFY", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("10"))
        res = adapter.place_order(req)

        self.assertIsInstance(res, OrderResult)
        self.assertEqual(res.status, OrderStatus.FILLED)
        self.assertEqual(res.filled_quantity, Decimal("10"))
        self.assertEqual(res.broker_name, "zerodha")
        self.assertEqual(res.commission, Decimal("20.0"))

    def test_alpaca_order_execution(self):
        adapter = BrokerAdapterFactory.create("alpaca")
        req = OrderRequest(symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=Decimal("5"), price=Decimal("180.0"))
        res = adapter.place_order(req)

        self.assertIsInstance(res, OrderResult)
        self.assertEqual(res.status, OrderStatus.FILLED)
        self.assertEqual(res.filled_quantity, Decimal("5"))
        self.assertEqual(res.average_price, Decimal("180.0"))

    def test_ibkr_order_execution(self):
        adapter = BrokerAdapterFactory.create("ibkr")
        req = OrderRequest(symbol="TSLA", side=OrderSide.SELL, order_type=OrderType.MARKET, quantity=Decimal("15"))
        res = adapter.place_order(req)

        self.assertIsInstance(res, OrderResult)
        self.assertEqual(res.status, OrderStatus.PENDING)
        self.assertEqual(res.broker_name, "ibkr")

    def test_invalid_quantity_raises_error(self):
        adapter = BrokerAdapterFactory.create("zerodha")
        req = OrderRequest(symbol="INFY", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("-5"))
        with self.assertRaises(OrderExecutionError):
            adapter.place_order(req)

    def test_status_normalization(self):
        adapter = BrokerAdapterFactory.create("alpaca")
        self.assertEqual(adapter.normalize_status("filled"), OrderStatus.FILLED)
        self.assertEqual(adapter.normalize_status("rejected"), OrderStatus.REJECTED)
        self.assertEqual(adapter.normalize_status("unknown_status"), OrderStatus.PENDING)

    def test_positions_and_balance_normalization(self):
        adapter = BrokerAdapterFactory.create("zerodha")
        positions = adapter.get_positions()
        balance = adapter.get_account_balance()

        self.assertTrue(len(positions) > 0)
        self.assertIsInstance(positions[0], Position)
        self.assertIsInstance(balance, AccountBalance)
        self.assertIsInstance(balance.cash_available, Decimal)

if __name__ == "__main__":
    unittest.main()
