"""
Unit tests for broker-agnostic-adapter-interface skill.

Tests:
1. Instantiating adapters via BrokerAdapterFactory.
2. Standardized OrderRequest execution returning normalized OrderResult across Zerodha and Alpaca adapters.
3. Querying positions and account balances with normalized data models.
4. Attempting factory creation for unregistered broker raises KeyError.
"""
import unittest
from broker_adapter import (
    AccountBalance,
    BaseBrokerAdapter,
    BrokerAdapterFactory,
    MockAlpacaAdapter,
    MockZerodhaAdapter,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)


class TestBrokerAgnosticAdapter(unittest.TestCase):

    def test_factory_creation(self):
        zerodha = BrokerAdapterFactory.create("zerodha")
        self.assertIsInstance(zerodha, MockZerodhaAdapter)
        self.assertEqual(zerodha.broker_name, "zerodha")

        alpaca = BrokerAdapterFactory.create("alpaca")
        self.assertIsInstance(alpaca, MockAlpacaAdapter)
        self.assertEqual(alpaca.broker_name, "alpaca")

    def test_unregistered_broker_raises_key_error(self):
        with self.assertRaises(KeyError):
            BrokerAdapterFactory.create("unsupported_broker")

    def test_zerodha_order_execution(self):
        adapter = BrokerAdapterFactory.create("zerodha")
        req = OrderRequest(symbol="INFY", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=10)
        res = adapter.place_order(req)

        self.assertIsInstance(res, OrderResult)
        self.assertEqual(res.status, OrderStatus.FILLED)
        self.assertEqual(res.filled_quantity, 10)
        self.assertEqual(res.broker_name, "zerodha")

    def test_alpaca_order_execution(self):
        adapter = BrokerAdapterFactory.create("alpaca")
        req = OrderRequest(symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=5, price=180.0)
        res = adapter.place_order(req)

        self.assertIsInstance(res, OrderResult)
        self.assertEqual(res.status, OrderStatus.FILLED)
        self.assertEqual(res.filled_quantity, 5)
        self.assertEqual(res.average_price, 180.0)

    def test_positions_and_balance_normalization(self):
        adapter = BrokerAdapterFactory.create("zerodha")
        positions = adapter.get_positions()
        balance = adapter.get_account_balance()

        self.assertTrue(len(positions) > 0)
        self.assertIsInstance(positions[0], Position)
        self.assertIsInstance(balance, AccountBalance)


if __name__ == "__main__":
    unittest.main()
