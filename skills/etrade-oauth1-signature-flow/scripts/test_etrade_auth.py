"""
Unit tests for etrade-oauth1-signature-flow skill.
"""
import unittest
from etrade_auth import ETradeOAuth1Client


class TestETradeOAuth1Client(unittest.TestCase):

    def setUp(self):
        self.client = ETradeOAuth1Client(
            consumer_key="test_consumer_key",
            consumer_secret="test_consumer_secret",
            use_sandbox=True,
        )

    def test_hmac_sha1_signature(self):
        """HMAC-SHA1 signature should produce a valid base64 encoded string."""
        base_string = "GET&https%3A%2F%2Fapi.example.com%2Ftest&oauth_consumer_key%3Dtest"
        sig = self.client.sign_hmac_sha1(base_string, "consumer_secret", "token_secret")
        self.assertIsInstance(sig, str)
        self.assertGreater(len(sig), 10)  # Base64 encoded HMAC should be substantial

    def test_base_string_construction(self):
        """Base string should follow RFC 5849 format: METHOD&URL&PARAMS."""
        params = {"oauth_consumer_key": "key1", "oauth_nonce": "abc123"}
        base = self.client.build_base_string(
            "GET", "https://api.etrade.com/v1/accounts", params
        )
        self.assertTrue(base.startswith("GET&"))
        self.assertIn("oauth_consumer_key", base)
        self.assertIn("oauth_nonce", base)

    def test_auth_header_format(self):
        """Authorization header should start with 'OAuth ' and contain required params."""
        header = self.client.build_auth_header(
            "GET", "https://api.etrade.com/v1/accounts",
            token="access_token_123",
            token_secret="access_secret_456",
        )
        self.assertTrue(header.startswith("OAuth "))
        self.assertIn("oauth_consumer_key", header)
        self.assertIn("oauth_signature", header)
        self.assertIn("oauth_nonce", header)
        self.assertIn("oauth_timestamp", header)
        self.assertIn("oauth_token", header)

    def test_sign_request_requires_access_token(self):
        """sign_request should raise if access token is not set."""
        with self.assertRaises(ValueError):
            self.client.sign_request("GET", "https://api.etrade.com/v1/accounts")

    def test_sign_request_with_access_token(self):
        """sign_request should return valid Authorization header after setting token."""
        self.client.set_access_token("token_abc", "secret_xyz")
        headers = self.client.sign_request("GET", "https://api.etrade.com/v1/accounts")
        self.assertIn("Authorization", headers)
        self.assertTrue(headers["Authorization"].startswith("OAuth "))


if __name__ == "__main__":
    unittest.main()
