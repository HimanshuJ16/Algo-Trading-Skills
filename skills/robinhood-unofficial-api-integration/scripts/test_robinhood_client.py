"""
Unit tests for robinhood-unofficial-api-integration skill.
"""
import unittest
from robinhood_client import (
    OrderSide, OrderType, RobinhoodAuthError, RobinhoodUnofficialClient,
)


def mock_http_success(method, url, headers, payload):
    """Mock HTTP transport that returns success responses."""
    if "oauth2/token" in url:
        return 200, {
            "access_token": "mock_token_abc123",
            "refresh_token": "mock_refresh_xyz",
            "expires_in": 86400,
        }
    if "orders" in url and method == "POST":
        return 201, {"id": "order_001", "state": "queued"}
    if "positions" in url:
        return 200, {"results": [
            {"symbol": "AAPL", "quantity": "100.0", "average_buy_price": "150.00"},
            {"symbol": "TSLA", "quantity": "0.0", "average_buy_price": "0.00"},
        ]}
    return 200, {}


def mock_http_mfa_required(method, url, headers, payload):
    """Mock HTTP transport that requires MFA."""
    if "oauth2/token" in url:
        if "mfa_code" not in payload:
            return 400, {"mfa_required": True, "mfa_type": "sms"}
        return 200, {
            "access_token": "mock_token_mfa",
            "refresh_token": "mock_refresh_mfa",
            "expires_in": 86400,
        }
    return 200, {}


class TestRobinhoodUnofficialClient(unittest.TestCase):

    def test_successful_authentication(self):
        """Auth with valid credentials should return a token."""
        client = RobinhoodUnofficialClient(http_fn=mock_http_success)
        token = client.authenticate("user@email.com", "password123")
        self.assertEqual(token.access_token, "mock_token_abc123")
        self.assertFalse(token.is_expired)

    def test_mfa_challenge_and_resolution(self):
        """MFA-required flow should raise, then succeed with MFA code."""
        client = RobinhoodUnofficialClient(http_fn=mock_http_mfa_required)

        with self.assertRaises(RobinhoodAuthError) as ctx:
            client.authenticate("user@email.com", "password123")
        self.assertIn("MFA_REQUIRED", str(ctx.exception))

        # Retry with MFA code
        token = client.authenticate("user@email.com", "password123", mfa_code="123456")
        self.assertEqual(token.access_token, "mock_token_mfa")

    def test_order_placement(self):
        """Order placement should construct correct payload and return order."""
        client = RobinhoodUnofficialClient(http_fn=mock_http_success)
        client.authenticate("user@email.com", "password123")

        order = client.place_order("AAPL", OrderSide.BUY, 10, OrderType.MARKET)
        self.assertEqual(order.symbol, "AAPL")
        self.assertEqual(order.side, "buy")
        self.assertEqual(order.quantity, 10)
        self.assertEqual(order.status, "queued")

    def test_position_polling(self):
        """Position query should return only non-zero positions."""
        client = RobinhoodUnofficialClient(http_fn=mock_http_success)
        client.authenticate("user@email.com", "password123")

        positions = client.get_positions()
        self.assertEqual(len(positions), 1)  # TSLA has qty=0, filtered out
        self.assertEqual(positions[0].symbol, "AAPL")
        self.assertEqual(positions[0].quantity, 100.0)


if __name__ == "__main__":
    unittest.main()
