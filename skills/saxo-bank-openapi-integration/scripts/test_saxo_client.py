"""
Unit tests for saxo-bank-openapi-integration skill.
"""
import unittest
from saxo_client import (
    SaxoAPIError, SaxoAssetType, SaxoBankOpenAPIClient, SaxoOrderDuration, SaxoOrderType,
)


def mock_http_transport(method, url, headers, body):
    """Mock HTTP transport for Saxo Bank OpenAPI."""
    if "ref/v1/instruments" in url:
        return 200, {
            "Data": [
                {
                    "Identifier": 211,
                    "Symbol": "EURUSD",
                    "Description": "Euro / US Dollar",
                    "AssetType": "FxSpot",
                    "CurrencyCode": "USD",
                }
            ]
        }
    elif "trade/v1/orders" in url:
        return 201, {
            "OrderId": "100200300",
            "Status": "Placed",
        }
    elif "port/v1/positions" in url:
        return 200, {
            "Data": [
                {
                    "PositionBase": {
                        "PositionId": "POS_991",
                        "Uic": 211,
                        "Symbol": "EURUSD",
                        "AssetType": "FxSpot",
                        "Amount": 100000.0,
                        "OpenPrice": 1.0850,
                    },
                    "PositionView": {
                        "CurrentPrice": 1.0890,
                        "ProfitLossOnTrade": 400.0,
                    },
                }
            ]
        }
    return 404, {"Detail": "Not found"}


class TestSaxoBankOpenAPIClient(unittest.TestCase):

    def setUp(self):
        self.client = SaxoBankOpenAPIClient(
            access_token="mock_token_saxo_123",
            account_key="ACC_KEY_MOCK_456",
            is_simulation=True,
            http_fn=mock_http_transport,
        )

    def test_search_instrument(self):
        instruments = self.client.search_instrument("EURUSD", SaxoAssetType.FX_SPOT)
        self.assertEqual(len(instruments), 1)
        self.assertEqual(instruments[0].uic, 211)
        self.assertEqual(instruments[0].symbol, "EURUSD")
        self.assertEqual(instruments[0].asset_type, SaxoAssetType.FX_SPOT)

    def test_place_order(self):
        order = self.client.place_order(
            uic=211,
            asset_type=SaxoAssetType.FX_SPOT,
            buy_sell="Buy",
            amount=100000.0,
            order_type=SaxoOrderType.LIMIT,
            price=1.0850,
            duration=SaxoOrderDuration.DAY_ORDER,
        )
        self.assertEqual(order.order_id, "100200300")
        self.assertEqual(order.uic, 211)
        self.assertEqual(order.buy_sell, "Buy")
        self.assertEqual(order.status, "Placed")

    def test_get_positions(self):
        positions = self.client.get_positions()
        self.assertEqual(len(positions), 1)
        pos = positions[0]
        self.assertEqual(pos.position_id, "POS_991")
        self.assertEqual(pos.uic, 211)
        self.assertEqual(pos.symbol, "EURUSD")
        self.assertAlmostEqual(pos.unrealized_pnl, 400.0)


if __name__ == "__main__":
    unittest.main()
