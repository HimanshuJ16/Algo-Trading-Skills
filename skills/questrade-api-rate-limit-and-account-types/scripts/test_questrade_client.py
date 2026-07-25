"""
Unit tests for questrade-api-rate-limit-and-account-types skill.
"""
import unittest
from questrade_client import AccountType, QuestradeAPIError, QuestradeClient


def mock_http_transport(method, url, headers, body):
    """Mock HTTP transport for Questrade API."""
    if "login.questrade.com" in url:
        return 200, {
            "access_token": "mock_access_token_123",
            "refresh_token": "mock_next_refresh_token_456",
            "api_server": "https://api01.iq.questrade.com/",
            "token_type": "Bearer",
            "expires_in": 1800,
        }
    elif "v1/accounts" in url:
        return 200, {
            "accounts": [
                {"number": "12345678", "type": "Margin", "isPrimary": True, "status": "Active"},
                {"number": "87654321", "type": "TFSA", "isPrimary": False, "status": "Active"},
                {"number": "99998888", "type": "RRSP", "isPrimary": False, "status": "Active"},
            ]
        }
    return 404, {"detail": "Not found"}


class TestQuestradeClient(unittest.TestCase):

    def setUp(self):
        self.client = QuestradeClient(rate_limit_capacity=30, http_fn=mock_http_transport)

    def test_oauth2_token_rotation(self):
        token = self.client.refresh_access_token("initial_refresh_token")
        self.assertEqual(token.access_token, "mock_access_token_123")
        self.assertEqual(token.refresh_token, "mock_next_refresh_token_456")
        self.assertEqual(token.api_server, "https://api01.iq.questrade.com/")

    def test_fetch_accounts(self):
        self.client.refresh_access_token("token_abc")
        accounts = self.client.fetch_accounts()
        self.assertEqual(len(accounts), 3)
        self.assertEqual(accounts[0].account_type, AccountType.MARGIN)
        self.assertEqual(accounts[1].account_type, AccountType.TFSA)

    def test_registered_account_short_sale_restriction(self):
        self.client.refresh_access_token("token_abc")
        self.client.fetch_accounts()

        # Margin account permits short selling
        self.assertTrue(self.client.validate_order_for_account("12345678", "Short"))
        # TFSA registered account forbids short selling
        self.assertFalse(self.client.validate_order_for_account("87654321", "Short"))
        # RRSP registered account forbids short selling
        self.assertFalse(self.client.validate_order_for_account("99998888", "SellShort"))

    def test_rate_limiter(self):
        client = QuestradeClient(rate_limit_capacity=2, http_fn=mock_http_transport)
        client.refresh_access_token("token_abc")

        # 2 calls exhaust capacity
        client.fetch_accounts()
        client.fetch_accounts()

        # 3rd rapid call triggers rate limit error
        with self.assertRaises(QuestradeAPIError):
            client.fetch_accounts()


if __name__ == "__main__":
    unittest.main()
