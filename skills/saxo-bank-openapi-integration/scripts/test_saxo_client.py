"""
Unit tests for saxo-bank-openapi-integration skill.

Fixtures reproduce Saxo's documented response shapes rather than a convenient shape:
- the instrument UIC arrives as ``Identifier`` (EURUSD FxSpot is UIC 21);
- ``PositionId``/``NetPositionId`` sit at the top level of a positions row, not in
  ``PositionBase``;
- the position ``Symbol`` is only present inside ``DisplayAndFormat``;
- the order-placement response carries ``OrderId``/``Orders`` and no status field.
"""
import unittest
from saxo_client import (
    MAX_EXTERNAL_REFERENCE_LENGTH,
    SaxoAPIError,
    SaxoAssetType,
    SaxoAuthError,
    SaxoBankOpenAPIClient,
    SaxoOrderDuration,
    SaxoOrderType,
    SaxoRateLimitError,
)

# Real EURUSD FxSpot position numbers from Saxo's documented example response.
EURUSD_OPEN_PRICE = 1.13715
EURUSD_CURRENT_PRICE = 1.13273
EURUSD_AMOUNT = -100000.0
EURUSD_CONVERSION_RATE = 0.882905


def _instrument_payload():
    return {
        "Data": [
            {
                "AssetType": "FxSpot",
                "CurrencyCode": "USD",
                "Description": "Euro/US Dollar",
                "ExchangeId": "SBFX",
                "Identifier": 21,
                "SummaryType": "Instrument",
                "Symbol": "EURUSD",
                "TradableAs": ["FxSpot", "FxForwards", "FxVanillaOption"],
            }
        ]
    }


def _positions_payload():
    return {
        "__count": 1,
        "Data": [
            {
                "NetPositionId": "EURUSD__FxSpot",
                "PositionId": "212561926",
                "PositionBase": {
                    "AccountId": "9226397",
                    "Amount": EURUSD_AMOUNT,
                    "AssetType": "FxSpot",
                    "CanBeClosed": True,
                    "OpenPrice": EURUSD_OPEN_PRICE,
                    "Status": "Open",
                    "Uic": 21,
                },
                "PositionView": {
                    "CalculationReliability": "Ok",
                    "ConversionRateCurrent": EURUSD_CONVERSION_RATE,
                    "CurrentPrice": EURUSD_CURRENT_PRICE,
                    "Exposure": EURUSD_AMOUNT,
                    "ExposureCurrency": "EUR",
                    "ProfitLossOnTrade": 442.0,
                    "ProfitLossOnTradeInBaseCurrency": 390.24,
                },
                "DisplayAndFormat": {
                    "Currency": "USD",
                    "Decimals": 5,
                    "Description": "Euro/US Dollar",
                    "Symbol": "EURUSD",
                },
            }
        ],
    }


def mock_http_transport(method, url, headers, body):
    """Mock HTTP transport for Saxo Bank OpenAPI. Records the last request for assertions."""
    mock_http_transport.last_request = (method, url, headers, body)
    if "ref/v1/instruments" in url:
        return 200, _instrument_payload()
    if "trade/v2/orders" in url:
        response = {"OrderId": 100200300, "Orders": [{"OrderId": 100200301}]}
        if isinstance(body, dict) and body.get("ExternalReference"):
            response["ExternalReference"] = body["ExternalReference"]
        return 201, response
    if "port/v1/orders" in url:
        return 200, {"Data": [{"OrderId": "100200300", "ExternalReference": "ats-abc"}]}
    if "port/v1/positions" in url:
        return 200, _positions_payload()
    return 404, {"Detail": "Not found"}


def _status_transport(status, body=None, resp_headers=None):
    """Transport that always answers with a fixed status/body (optionally with headers)."""

    def _fn(method, url, headers, request_body):
        payload = body if body is not None else {"Message": "error"}
        if resp_headers is None:
            return status, payload
        return status, payload, resp_headers

    return _fn


class TestSaxoBankOpenAPIClient(unittest.TestCase):

    def setUp(self):
        mock_http_transport.last_request = None
        self.client = SaxoBankOpenAPIClient(
            access_token="mock_token_saxo_123",
            account_key="ACC_KEY_MOCK_456",
            is_simulation=True,
            http_fn=mock_http_transport,
        )

    def _client_with(self, http_fn):
        return SaxoBankOpenAPIClient(
            access_token="mock_token_saxo_123",
            account_key="ACC_KEY_MOCK_456",
            is_simulation=True,
            http_fn=http_fn,
        )

    # -------------------------------------------------------------- construction --

    def test_simulation_flag_selects_gateway(self):
        self.assertEqual(self.client.base_url, SaxoBankOpenAPIClient.SIM_BASE_URL)
        live = SaxoBankOpenAPIClient("t", "a", is_simulation=False, http_fn=mock_http_transport)
        self.assertEqual(live.base_url, SaxoBankOpenAPIClient.LIVE_BASE_URL)
        self.assertNotIn("/sim/", live.base_url)

    def test_empty_credentials_rejected(self):
        with self.assertRaises(ValueError):
            SaxoBankOpenAPIClient("", "ACC", http_fn=mock_http_transport)
        with self.assertRaises(ValueError):
            SaxoBankOpenAPIClient("token", "", http_fn=mock_http_transport)

    def test_missing_transport_raises(self):
        client = SaxoBankOpenAPIClient("token", "ACC")
        with self.assertRaises(SaxoAPIError):
            client.get_positions()

    # ---------------------------------------------------------- instrument search --

    def test_search_instrument(self):
        instruments = self.client.search_instrument("EURUSD", SaxoAssetType.FX_SPOT)
        self.assertEqual(len(instruments), 1)
        # EURUSD FxSpot resolves to UIC 21 in Saxo's reference data.
        self.assertEqual(instruments[0].uic, 21)
        self.assertEqual(instruments[0].symbol, "EURUSD")
        self.assertEqual(instruments[0].asset_type, SaxoAssetType.FX_SPOT)
        self.assertEqual(instruments[0].currency, "USD")
        self.assertEqual(instruments[0].exchange_id, "SBFX")
        self.assertIn("FxForwards", instruments[0].tradable_as)

    def test_search_instrument_percent_encodes_keywords(self):
        """A keyword with a space or '&' must not corrupt the query string."""
        self.client.search_instrument("Vestas Wind & Co", SaxoAssetType.STOCK)
        _, url, _, _ = mock_http_transport.last_request
        self.assertIn("Keywords=Vestas+Wind+%26+Co", url)
        self.assertNotIn(" ", url)

    def test_search_instrument_rejects_blank_keywords(self):
        with self.assertRaises(ValueError):
            self.client.search_instrument("   ", SaxoAssetType.FX_SPOT)

    def test_search_instrument_skips_rows_without_identifier(self):
        transport = _status_transport(200, {"Data": [{"Symbol": "BAD"}, {"Identifier": 21}]})
        instruments = self._client_with(transport).search_instrument("X", SaxoAssetType.FX_SPOT)
        self.assertEqual([i.uic for i in instruments], [21])

    # ------------------------------------------------------------- order placement --

    def test_place_order(self):
        order = self.client.place_order(
            uic=21,
            asset_type=SaxoAssetType.FX_SPOT,
            buy_sell="Buy",
            amount=100000.0,
            order_type=SaxoOrderType.LIMIT,
            price=1.0850,
            duration=SaxoOrderDuration.DAY_ORDER,
        )
        self.assertEqual(order.order_id, "100200300")
        self.assertEqual(order.related_order_ids, ["100200301"])
        self.assertEqual(order.uic, 21)
        self.assertEqual(order.buy_sell, "Buy")
        # Saxo's placement response carries no status field; nothing may be invented.
        self.assertIsNone(order.status)

    def test_place_order_targets_v2_endpoint(self):
        self.client.place_order(21, SaxoAssetType.FX_SPOT, "Buy", 1000.0)
        _, url, _, _ = mock_http_transport.last_request
        self.assertIn("/trade/v2/orders", url)
        self.assertNotIn("/trade/v1/orders", url)

    def test_place_order_always_sends_manual_order_flag(self):
        self.client.place_order(21, SaxoAssetType.FX_SPOT, "Buy", 1000.0)
        _, _, _, body = mock_http_transport.last_request
        self.assertIn("ManualOrder", body)
        self.assertIs(body["ManualOrder"], False)

        self.client.place_order(21, SaxoAssetType.FX_SPOT, "Buy", 1000.0, manual_order=True)
        _, _, _, body = mock_http_transport.last_request
        self.assertIs(body["ManualOrder"], True)

    def test_market_order_omits_order_price(self):
        self.client.place_order(21, SaxoAssetType.FX_SPOT, "Sell", 1000.0)
        _, _, _, body = mock_http_transport.last_request
        self.assertNotIn("OrderPrice", body)
        self.assertEqual(body["OrderDuration"], {"DurationType": "DayOrder"})

    def test_non_market_order_types_require_a_price(self):
        for order_type in (
            SaxoOrderType.LIMIT,
            SaxoOrderType.STOP,
            SaxoOrderType.STOP_IF_TRADED,
            SaxoOrderType.STOP_LIMIT,
            SaxoOrderType.TRAILING_STOP,
            SaxoOrderType.TRAILING_STOP_IF_TRADED,
        ):
            with self.subTest(order_type=order_type):
                with self.assertRaises(ValueError):
                    self.client.place_order(
                        21, SaxoAssetType.FX_SPOT, "Buy", 1000.0, order_type=order_type
                    )

    def test_priced_order_sends_order_price(self):
        self.client.place_order(
            21, SaxoAssetType.FX_SPOT, "Buy", 1000.0, SaxoOrderType.STOP_LIMIT, price=1.1
        )
        _, _, _, body = mock_http_transport.last_request
        self.assertAlmostEqual(body["OrderPrice"], 1.1)

    def test_invalid_order_inputs_rejected(self):
        bad_cases = [
            dict(uic=0),
            dict(uic=-21),
            dict(buy_sell="BUY"),
            dict(buy_sell="buy"),
            dict(buy_sell="Long"),
            dict(amount=0.0),
            dict(amount=-100.0),
            dict(amount=float("nan")),
            dict(amount=float("inf")),
        ]
        for override in bad_cases:
            kwargs = dict(uic=21, asset_type=SaxoAssetType.FX_SPOT, buy_sell="Buy", amount=1000.0)
            kwargs.update(override)
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    self.client.place_order(**kwargs)

    def test_extra_fields_cannot_override_computed_payload(self):
        self.client.place_order(
            21,
            SaxoAssetType.FX_SPOT,
            "Buy",
            1000.0,
            extra_fields={"Uic": 999999, "TrailingStopStep": 0.01},
        )
        _, _, _, body = mock_http_transport.last_request
        self.assertEqual(body["Uic"], 21)
        self.assertAlmostEqual(body["TrailingStopStep"], 0.01)

    def test_missing_order_id_raises_instead_of_fabricating_one(self):
        """A 201 with no OrderId leaves order state UNKNOWN; never synthesise an id."""
        transport = _status_transport(201, {"Orders": []})
        with self.assertRaises(SaxoAPIError) as ctx:
            self._client_with(transport).place_order(21, SaxoAssetType.FX_SPOT, "Buy", 1000.0)
        self.assertIn("UNKNOWN", str(ctx.exception))

    # ------------------------------------------------------- external reference --

    def test_external_reference_is_sent_and_echoed(self):
        order = self.client.place_order(
            21, SaxoAssetType.FX_SPOT, "Buy", 1000.0, external_reference="ats-abc"
        )
        _, _, _, body = mock_http_transport.last_request
        self.assertEqual(body["ExternalReference"], "ats-abc")
        self.assertEqual(order.external_reference, "ats-abc")

    def test_external_reference_length_limit_enforced(self):
        too_long = "x" * (MAX_EXTERNAL_REFERENCE_LENGTH + 1)
        with self.assertRaises(ValueError):
            self.client.place_order(
                21, SaxoAssetType.FX_SPOT, "Buy", 1000.0, external_reference=too_long
            )
        ok = "x" * MAX_EXTERNAL_REFERENCE_LENGTH
        self.client.place_order(21, SaxoAssetType.FX_SPOT, "Buy", 1000.0, external_reference=ok)

    def test_generated_external_reference_fits_and_is_unique(self):
        refs = {SaxoBankOpenAPIClient.generate_external_reference() for _ in range(200)}
        self.assertEqual(len(refs), 200)
        for ref in refs:
            self.assertLessEqual(len(ref), MAX_EXTERNAL_REFERENCE_LENGTH)

    def test_find_open_orders_by_external_reference(self):
        matches = self.client.find_open_orders_by_external_reference("ats-abc")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["OrderId"], "100200300")
        self.assertEqual(self.client.find_open_orders_by_external_reference("ats-other"), [])

    # -------------------------------------------------------------- positions --

    def test_get_positions(self):
        positions = self.client.get_positions()
        self.assertEqual(len(positions), 1)
        pos = positions[0]
        # PositionId lives at the row top level, not inside PositionBase.
        self.assertEqual(pos.position_id, "212561926")
        self.assertEqual(pos.net_position_id, "EURUSD__FxSpot")
        # Symbol only exists inside DisplayAndFormat.
        self.assertEqual(pos.symbol, "EURUSD")
        self.assertEqual(pos.uic, 21)
        self.assertEqual(pos.status, "Open")
        self.assertTrue(pos.valuation_is_reliable)

    def test_get_positions_requests_field_groups(self):
        self.client.get_positions()
        _, url, _, _ = mock_http_transport.last_request
        for group in ("PositionBase", "PositionView", "DisplayAndFormat"):
            self.assertIn(group, url)

    def test_unrealized_pnl_matches_independently_computed_value(self):
        """(open - current) * |amount| for a short EURUSD position, in quote currency."""
        pos = self.client.get_positions()[0]
        expected = (EURUSD_OPEN_PRICE - EURUSD_CURRENT_PRICE) * abs(EURUSD_AMOUNT)
        self.assertAlmostEqual(pos.unrealized_pnl, expected, places=6)
        self.assertEqual(pos.pnl_currency, "USD")

    def test_base_currency_pnl_is_converted_and_distinct(self):
        pos = self.client.get_positions()[0]
        self.assertIsNotNone(pos.unrealized_pnl_base_currency)
        self.assertNotAlmostEqual(pos.unrealized_pnl_base_currency, pos.unrealized_pnl)
        self.assertAlmostEqual(
            pos.unrealized_pnl_base_currency,
            pos.unrealized_pnl * EURUSD_CONVERSION_RATE,
            places=2,
        )

    def test_symbol_empty_when_display_and_format_absent(self):
        """No fabricated 'UNKNOWN' placeholder that could be mistaken for a ticker."""
        payload = _positions_payload()
        del payload["Data"][0]["DisplayAndFormat"]
        pos = self._client_with(_status_transport(200, payload)).get_positions()[0]
        self.assertEqual(pos.symbol, "")
        self.assertEqual(pos.pnl_currency, "")

    def test_unreliable_calculation_is_surfaced(self):
        payload = _positions_payload()
        payload["Data"][0]["PositionView"]["CalculationReliability"] = "ApproximatedPrice"
        pos = self._client_with(_status_transport(200, payload)).get_positions()[0]
        self.assertFalse(pos.valuation_is_reliable)
        self.assertEqual(pos.calculation_reliability, "ApproximatedPrice")

    def test_missing_calculation_reliability_fails_closed(self):
        payload = _positions_payload()
        del payload["Data"][0]["PositionView"]["CalculationReliability"]
        pos = self._client_with(_status_transport(200, payload)).get_positions()[0]
        self.assertFalse(pos.valuation_is_reliable)

    def test_unknown_asset_type_does_not_crash(self):
        payload = _positions_payload()
        payload["Data"][0]["PositionBase"]["AssetType"] = "CfdOnIndex"
        pos = self._client_with(_status_transport(200, payload)).get_positions()[0]
        self.assertEqual(pos.asset_type, SaxoAssetType.STOCK)

    def test_paged_position_result_is_flagged(self):
        """A truncated page must warn: silently short position lists understate exposure."""
        payload = _positions_payload()
        payload["__count"] = 4  # four positions exist, one row returned
        client = self._client_with(_status_transport(200, payload))
        with self.assertLogs("saxo_client", level="WARNING") as logs:
            self.assertEqual(len(client.get_positions()), 1)
        self.assertTrue(any("PAGED" in line for line in logs.output))

    def test_complete_page_does_not_warn(self):
        payload = _positions_payload()
        payload["__count"] = 1
        client = self._client_with(_status_transport(200, payload))
        with self.assertNoLogs("saxo_client", level="WARNING"):
            client.get_positions()

    def test_next_link_is_flagged(self):
        payload = _positions_payload()
        payload["__next"] = "https://gateway.saxobank.com/sim/openapi/port/v1/positions?$skip=1"
        client = self._client_with(_status_transport(200, payload))
        with self.assertLogs("saxo_client", level="WARNING") as logs:
            client.get_positions()
        self.assertTrue(any("PAGED" in line for line in logs.output))

    def test_malformed_position_rows_are_skipped(self):
        payload = {"Data": ["not-a-row", {"PositionBase": "bad", "PositionView": {}}]}
        self.assertEqual(self._client_with(_status_transport(200, payload)).get_positions(), [])

    # ------------------------------------------------------------ error handling --

    def test_401_raises_auth_error(self):
        client = self._client_with(_status_transport(401, {"Message": "expired"}))
        with self.assertRaises(SaxoAuthError) as ctx:
            client.get_positions()
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIsInstance(ctx.exception, SaxoAPIError)

    def test_429_raises_rate_limit_error_with_reset_from_exhausted_dimension(self):
        client = self._client_with(
            _status_transport(
                429,
                {"Message": "too many"},
                {
                    "X-RateLimit-AppDay-Remaining": "500",
                    "X-RateLimit-AppDay-Reset": "3600",
                    "X-RateLimit-SessionOrders-Remaining": "0",
                    "X-RateLimit-SessionOrders-Reset": "1",
                },
            )
        )
        with self.assertRaises(SaxoRateLimitError) as ctx:
            client.place_order(21, SaxoAssetType.FX_SPOT, "Buy", 1000.0)
        # The exhausted bucket (SessionOrders) drives the wait, not the largest window.
        self.assertAlmostEqual(ctx.exception.retry_after_seconds, 1.0)

    def test_429_without_headers_has_no_retry_hint(self):
        client = self._client_with(_status_transport(429))
        with self.assertRaises(SaxoRateLimitError) as ctx:
            client.get_positions()
        self.assertIsNone(ctx.exception.retry_after_seconds)

    def test_generic_error_status_raises_api_error(self):
        client = self._client_with(_status_transport(400, {"ErrorCode": "InvalidModelState"}))
        with self.assertRaises(SaxoAPIError) as ctx:
            client.get_positions()
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertNotIsInstance(ctx.exception, SaxoAuthError)

    def test_malformed_transport_result_raises(self):
        for bad in (None, "200", (200,), (200, {}, {}, {}), ("200", {})):
            with self.subTest(bad=bad):
                client = self._client_with(lambda m, u, h, b, _r=bad: _r)
                with self.assertRaises(SaxoAPIError):
                    client.get_positions()

    def test_non_object_body_raises(self):
        client = self._client_with(_status_transport(200, ["unexpected"]))
        with self.assertRaises(SaxoAPIError):
            client.get_positions()

    # ----------------------------------------------------------------- headers --

    def test_bearer_token_sent_and_not_in_url(self):
        self.client.get_positions()
        _, url, headers, _ = mock_http_transport.last_request
        self.assertEqual(headers["Authorization"], "Bearer mock_token_saxo_123")
        self.assertNotIn("mock_token_saxo_123", url)


class TestSaxoEnums(unittest.TestCase):

    def test_option_root_is_not_a_tradable_asset_type(self):
        """OptionRoot is a search concept, not an orderable AssetType."""
        with self.assertRaises(ValueError):
            SaxoAssetType("OptionRoot")

    def test_asset_type_values_match_saxo_enum_strings(self):
        self.assertEqual(SaxoAssetType.FX_SPOT.value, "FxSpot")
        self.assertEqual(SaxoAssetType.STOCK.value, "Stock")
        self.assertEqual(SaxoAssetType.CONTRACT_FUTURES.value, "ContractFutures")
        self.assertEqual(SaxoAssetType.STOCK_OPTION.value, "StockOption")
        # 'Equity' is the common mistake; Saxo's value is 'Stock'.
        with self.assertRaises(ValueError):
            SaxoAssetType("Equity")


if __name__ == "__main__":
    unittest.main()
