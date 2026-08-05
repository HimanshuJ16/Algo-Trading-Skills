import unittest
from singapore_exchange_sgx_api_integration import (
    Config, IntegrationEngine,
    SingaporeExchangeSGXAPIClient, SGXOrderType, SGXOrder, SGXAssetCategory
)

class TestIntegrationLegacy(unittest.TestCase):
    def setUp(self):
        self.config = Config("key", "secret")
        self.engine = IntegrationEngine(self.config)

    def test_connect(self):
        self.assertTrue(self.engine.connect())

    def test_disconnect(self):
        self.assertTrue(self.engine.disconnect())

class TestSingaporeExchangeSGXAPIClient(unittest.TestCase):

    def setUp(self):
        self.client = SingaporeExchangeSGXAPIClient(
            sender_comp_id="SENDER_MOCK_123",
            target_comp_id="SGX_TITAN_GW",
            environment="SIMULATION"
        )
        self.client.connect()

    def test_valid_futures_limit_order(self):
        # FTSE China A50 Futures (CN) - Tick size 2.5
        order = self.client.place_order(
            order_id="ORD_SGX_001",
            account_id="ACC_SGX_99",
            symbol="CN",
            side="BUY",
            quantity=10.0,
            order_type=SGXOrderType.LIMIT,
            price=12500.0  # Multiple of 2.5
        )
        self.assertEqual(order.order_id, "ORD_SGX_001")
        self.assertEqual(order.symbol, "CN")
        self.assertEqual(order.status, "NEW")

    def test_invalid_tick_size_rejection(self):
        # Nikkei 225 Futures (NK) - Tick size 5.0 (Price 38002.0 is NOT a multiple of 5.0)
        with self.assertRaises(ValueError) as ctx:
            self.client.place_order(
                order_id="ORD_SGX_002",
                account_id="ACC_SGX_99",
                symbol="NK",
                side="SELL",
                quantity=5.0,
                order_type=SGXOrderType.LIMIT,
                price=38002.0
            )
        self.assertIn("violates SGX tick size 5.0", str(ctx.exception))

    def test_disconnected_order_rejection(self):
        self.client.disconnect()
        with self.assertRaises(RuntimeError):
            self.client.place_order(
                order_id="ORD_SGX_003",
                account_id="ACC_SGX_99",
                symbol="CN",
                side="BUY",
                quantity=1.0
            )

if __name__ == '__main__':
    unittest.main()
