import unittest
import time
import json
from bybit_derivatives_api_integration import BybitV5Authenticator, BybitConfig

class TestBybitDerivativesApiIntegration(unittest.TestCase):
    def setUp(self):
        self.config = BybitConfig(
            api_key="TEST_API_KEY",
            api_secret="TEST_API_SECRET",
            is_testnet=True
        )
        self.auth = BybitV5Authenticator(self.config)

    def test_get_signature_generation(self):
        # Mocking time to ensure deterministic tests
        test_timestamp = "1675240200000"
        
        # Override the actual sign request to inject mocked timestamp for testing
        params = {"category": "linear", "symbol": "BTCUSDT"}
        sorted_params = "category=linear&symbol=BTCUSDT"
        
        signature = self.auth._generate_signature(test_timestamp, sorted_params)
        
        # Expected param_str: "1675240200000TEST_API_KEY5000category=linear&symbol=BTCUSDT"
        self.assertIsInstance(signature, str)
        self.assertEqual(len(signature), 64) # SHA256 hex string is 64 chars

    def test_post_signature_generation(self):
        test_timestamp = "1675240200000"
        params = {"category": "linear", "symbol": "BTCUSDT", "side": "Buy", "qty": "0.1"}
        
        # Test exact whitespace elimination
        json_payload = json.dumps(params, separators=(',', ':'))
        self.assertNotIn(" ", json_payload)
        
        signature = self.auth._generate_signature(test_timestamp, json_payload)
        self.assertIsInstance(signature, str)
        self.assertEqual(len(signature), 64)

    def test_sign_request_integration(self):
        req = self.auth.sign_request("POST", "/v5/order/create", {"symbol": "BTCUSDT"})
        
        self.assertTrue(req["url"].startswith("https://api-testnet.bybit.com"))
        self.assertIn("X-BAPI-API-KEY", req["headers"])
        self.assertIn("X-BAPI-SIGN", req["headers"])
        self.assertIn("X-BAPI-TIMESTAMP", req["headers"])
        self.assertEqual(req["headers"]["X-BAPI-API-KEY"], "TEST_API_KEY")

if __name__ == '__main__':
    unittest.main()
