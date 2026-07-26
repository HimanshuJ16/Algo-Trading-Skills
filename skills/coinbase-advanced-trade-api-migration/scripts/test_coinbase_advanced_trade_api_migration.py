import unittest
from coinbase_advanced_trade_api_migration import (
    CoinbaseAdvancedTradeAdapter, LegacyProOrderRequest
)

class TestCoinbaseAdvancedTradeAdapter(unittest.TestCase):

    def setUp(self):
        self.adapter = CoinbaseAdvancedTradeAdapter()

    def test_translate_limit_order(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD",
            side="buy",
            type="limit",
            size="0.25",
            price="65000.50",
            post_only=True,
            client_oid="MOCK_CLIENT_OID_123"
        )
        
        v3_payload = self.adapter.translate_order_request(legacy_req)
        
        self.assertEqual(v3_payload["client_order_id"], "MOCK_CLIENT_OID_123")
        self.assertEqual(v3_payload["product_id"], "BTC-USD")
        self.assertEqual(v3_payload["side"], "BUY") # Converted to upper
        self.assertIn("limit_limit_gtc", v3_payload["order_configuration"])
        
        limit_config = v3_payload["order_configuration"]["limit_limit_gtc"]
        self.assertEqual(limit_config["base_size"], "0.25")
        self.assertEqual(limit_config["limit_price"], "65000.50")
        self.assertTrue(limit_config["post_only"])

    def test_translate_market_order(self):
        legacy_req = LegacyProOrderRequest(
            product_id="ETH-USD",
            side="sell",
            type="market",
            size="1.5"
        )
        v3_payload = self.adapter.translate_order_request(legacy_req)
        
        self.assertEqual(v3_payload["side"], "SELL")
        self.assertIn("market_market_ioc", v3_payload["order_configuration"])
        self.assertEqual(v3_payload["order_configuration"]["market_market_ioc"]["base_size"], "1.5")

    def test_parse_v3_response_success(self):
        mock_response = {
            "success": True,
            "success_response": {
                "order_id": "V3_ORDER_999",
                "client_order_id": "CLIENT_123",
                "product_id": "BTC-USD",
                "side": "BUY"
            }
        }
        res = self.adapter.parse_v3_response(mock_response)
        
        self.assertEqual(res.order_id, "V3_ORDER_999")
        self.assertEqual(res.client_order_id, "CLIENT_123")
        self.assertEqual(res.side, "BUY")
        self.assertEqual(res.status, "PENDING")

    def test_parse_v3_response_failure(self):
        mock_response = {
            "success": False,
            "error_response": {
                "message": "INSUFFICIENT_FUNDS"
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            self.adapter.parse_v3_response(mock_response)
        self.assertIn("INSUFFICIENT_FUNDS", str(ctx.exception))

if __name__ == '__main__':
    unittest.main()
