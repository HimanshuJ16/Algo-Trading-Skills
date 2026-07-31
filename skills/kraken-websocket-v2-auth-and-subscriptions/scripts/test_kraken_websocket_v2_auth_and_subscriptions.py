import unittest
from kraken_websocket_v2_auth_and_subscriptions import (
    KrakenWsV2ManagerEngine, KrakenWsTokenState, KrakenWsV2SubscriptionSpec, KrakenWsV2Report
)

class TestKrakenWsV2ManagerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = KrakenWsV2ManagerEngine(
            api_key="TEST_API_KEY", api_secret_b64="bW9ja19zZWNyZXQ="
        )
        self.now = 1700000000.0

    def test_hmac_sha512_signature_generation(self):
        sig = self.engine.generate_kraken_rest_hmac_signature(
            url_path="/0/private/GetWebSocketsToken", nonce="1700000000000", post_data="nonce=1700000000000"
        )
        self.assertIsNotNone(sig)
        self.assertGreater(len(sig), 20)

    def test_public_book_subscription_frame(self):
        spec = KrakenWsV2SubscriptionSpec(channel="book", symbols=["BTC/USD", "ETH/USD"], depth=25)
        report = self.engine.build_v2_subscription_frame(spec, current_time_epoch=self.now)

        self.assertEqual(report.status, "SUBSCRIPTION_FRAME_CREATED")
        self.assertFalse(report.is_private_channel)
        self.assertEqual(report.ws_url, "wss://ws.kraken.com/v2")

        params = report.subscription_json_frame.get("params", {})
        self.assertEqual(params.get("channel"), "book")
        self.assertEqual(params.get("symbol"), ["BTC/USD", "ETH/USD"])
        self.assertEqual(params.get("depth"), 25)

    def test_private_executions_subscription_frame(self):
        token_state = KrakenWsTokenState(token="VALID_WS_TOKEN_123", created_timestamp_epoch=self.now - 300) # 5 mins old
        spec = KrakenWsV2SubscriptionSpec(channel="executions", snap_orders=True)
        report = self.engine.build_v2_subscription_frame(spec, token_state=token_state, current_time_epoch=self.now)

        self.assertEqual(report.status, "SUBSCRIPTION_FRAME_CREATED")
        self.assertTrue(report.is_private_channel)
        self.assertEqual(report.ws_url, "wss://ws-auth.kraken.com/v2")

        params = report.subscription_json_frame.get("params", {})
        self.assertEqual(params.get("channel"), "executions")
        self.assertEqual(params.get("token"), "VALID_WS_TOKEN_123")

    def test_expired_token_refresh_required(self):
        # 13 mins old token (> 720s limit) -> TOKEN_REFRESH_REQUIRED!
        token_state = KrakenWsTokenState(token="OLD_WS_TOKEN_999", created_timestamp_epoch=self.now - 780)
        spec = KrakenWsV2SubscriptionSpec(channel="executions")
        report = self.engine.build_v2_subscription_frame(spec, token_state=token_state, current_time_epoch=self.now)

        self.assertEqual(report.status, "TOKEN_REFRESH_REQUIRED")
        self.assertFalse(report.is_token_valid)

if __name__ == '__main__':
    unittest.main()
