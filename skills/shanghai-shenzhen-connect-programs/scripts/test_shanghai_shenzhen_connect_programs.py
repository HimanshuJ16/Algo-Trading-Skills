import unittest
from shanghai_shenzhen_connect_programs import (
    Config, IntegrationEngine,
    ShanghaiShenzhenConnectEngine, ConnectOrder, StockConnectChannel,
    ConnectExecutionResult
)

class TestIntegrationEngineLegacy(unittest.TestCase):
    def setUp(self):
        self.config = Config(api_key="test_key")
        self.engine = IntegrationEngine(self.config)

    def test_connect(self):
        self.assertTrue(self.engine.connect())

    def test_process(self):
        self.assertEqual(self.engine.process(), "success")

class TestStockConnectEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = ShanghaiShenzhenConnectEngine()

    def test_valid_northbound_buy_order(self):
        order = ConnectOrder(
            order_id="ORD_SSE_001",
            symbol="600519.SH",  # Kweichow Moutai
            channel=StockConnectChannel.SHANGHAI_CONNECT,
            side="BUY",
            quantity=100.0,      # Round lot of 100
            price_cnh=1700.0,
            order_date_iso="2026-08-05"
        )
        res = self.engine.validate_and_route_order(order)
        self.assertTrue(res.is_executed)
        self.assertIsNone(res.rejection_reason)
        # Quota should decrease by 170,000 CNH
        expected_quota = 52_000_000_000.0 - 170000.0
        self.assertEqual(res.remaining_quota_cnh, expected_quota)

    def test_t_plus_1_day_trading_rejection(self):
        # Bought on 2026-08-05, attempting to sell on SAME DAY 2026-08-05 -> Rejected!
        order = ConnectOrder(
            order_id="ORD_SZSE_002",
            symbol="000858.SZ",  # Wuliangye
            channel=StockConnectChannel.SHENZHEN_CONNECT,
            side="SELL",
            quantity=100.0,
            price_cnh=150.0,
            order_date_iso="2026-08-05",
            purchased_date_iso="2026-08-05"  # Same day!
        )
        res = self.engine.validate_and_route_order(order)
        self.assertFalse(res.is_executed)
        self.assertIn("TPLUS1_VIOLATION", res.rejection_reason)

    def test_odd_lot_buy_rejection(self):
        # 150 shares (not a multiple of 100) -> Rejected!
        order = ConnectOrder(
            order_id="ORD_SSE_003",
            symbol="600519.SH",
            channel=StockConnectChannel.SHANGHAI_CONNECT,
            side="BUY",
            quantity=150.0,
            price_cnh=1700.0,
            order_date_iso="2026-08-05"
        )
        res = self.engine.validate_and_route_order(order)
        self.assertFalse(res.is_executed)
        self.assertIn("INVALID_BOARD_LOT", res.rejection_reason)

    def test_quota_exhaustion_rejection(self):
        # Manually exhaust Shanghai quota
        self.engine.channel_quotas[StockConnectChannel.SHANGHAI_CONNECT] = 50.0

        order = ConnectOrder(
            order_id="ORD_SSE_004",
            symbol="600519.SH",
            channel=StockConnectChannel.SHANGHAI_CONNECT,
            side="BUY",
            quantity=100.0,      # Notional $170,000 CNH > $50 CNH quota -> Rejected!
            price_cnh=1700.0,
            order_date_iso="2026-08-05"
        )
        res = self.engine.validate_and_route_order(order)
        self.assertFalse(res.is_executed)
        self.assertIn("QUOTA_EXCEEDED", res.rejection_reason)

if __name__ == '__main__':
    unittest.main()
