"""
Unit tests for tastytrade-api-integration skill.
"""
import unittest
from tastytrade_client import (
    LegAction, OptionLeg, OrderType, PriceEffect, TastytradeAPIError, TastytradeClient,
)


def mock_http_transport(method, url, headers, body):
    """Mock HTTP transport for Tastytrade API."""
    if "sessions" in url:
        return 201, {
            "data": {
                "session-token": "mock_session_token_tt_999",
                "remember-token": "mock_remember_token_888",
            }
        }
    elif "accounts/55512345/orders" in url:
        return 201, {
            "data": {
                "order": {
                    "id": "TT_ORD_554433",
                    "status": "Received",
                }
            }
        }
    return 404, {"detail": "Not found"}


class TestTastytradeClient(unittest.TestCase):

    def setUp(self):
        self.client = TastytradeClient(is_production=False, http_fn=mock_http_transport)

    def test_occ_symbol_formatting(self):
        # AAPL 200 Call expiring 2024-08-16
        occ = self.client.format_occ_symbol("AAPL", "240816", "C", 200.0)
        self.assertEqual(len(occ), 21)
        self.assertEqual(occ, "AAPL  240816C00200000")

        # SPY 500 Put expiring 2024-12-20
        occ_put = self.client.format_occ_symbol("SPY", "241220", "P", 500.50)
        self.assertEqual(len(occ_put), 21)
        self.assertEqual(occ_put, "SPY   241220P00500500")

    def test_login_authentication(self):
        session = self.client.login("trader@example.com", "SecretPass123!")
        self.assertEqual(session.session_token, "mock_session_token_tt_999")

    def test_place_vertical_spread_option_order(self):
        self.client.login("trader@example.com", "SecretPass123!")

        leg1 = OptionLeg(
            occ_symbol=self.client.format_occ_symbol("AAPL", "240816", "C", 195.0),
            action=LegAction.BUY_TO_OPEN,
            quantity=1.0,
        )
        leg2 = OptionLeg(
            occ_symbol=self.client.format_occ_symbol("AAPL", "240816", "C", 200.0),
            action=LegAction.SELL_TO_OPEN,
            quantity=1.0,
        )

        order = self.client.place_complex_option_order(
            account_number="55512345",
            legs=[leg1, leg2],
            order_type=OrderType.LIMIT,
            net_price=2.15,
            price_effect=PriceEffect.DEBIT,
        )

        self.assertEqual(order.order_id, "TT_ORD_554433")
        self.assertEqual(order.status, "Received")
        self.assertEqual(len(order.legs), 2)
        self.assertEqual(order.price_effect, PriceEffect.DEBIT)


if __name__ == "__main__":
    unittest.main()
