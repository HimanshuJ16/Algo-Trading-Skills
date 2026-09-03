"""
Unit tests for tastytrade-api-integration skill.

Expected values are derived independently of the implementation: OCC symbols are
spelled out literally against the published 21-character layout, and order
payloads are asserted field by field against Tastytrade's documented request
shape rather than by re-running the builder.
"""
import time
import unittest
from decimal import Decimal

from tastytrade_client import (
    DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS,
    MAX_PLAUSIBLE_TOKEN_LIFETIME_SECONDS,
    InstrumentType,
    LegAction,
    OptionLeg,
    OrderType,
    PriceEffect,
    TastytradeAPIError,
    TastytradeAmbiguousOrderError,
    TastytradeAuthDiscontinuedError,
    TastytradeAuthError,
    TastytradeClient,
    TastytradeCredentials,
    TastytradeOrderRejectedError,
    TastytradeOrderValidationError,
    TastytradeSessionExpiredError,
    TastytradeSymbolError,
    format_occ_symbol,
    parse_occ_symbol,
    price_effect_for_signed_price,
)

ACCOUNT = "5WT00001"
TOKEN_RESPONSE = {
    "access_token": "mock_access_token_999",
    "token_type": "Bearer",
    "expires_in": 900,
}


class RecordingTransport:
    """Injectable transport that records calls and replays scripted responses."""

    def __init__(self, responses=None, raise_on=None):
        self.calls = []
        self.responses = responses or {}
        self.raise_on = raise_on or set()
        self.token_calls = 0

    def __call__(self, method, url, headers, body):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        for fragment in self.raise_on:
            if fragment in url:
                raise ConnectionError("socket timed out")

        if url.endswith("/oauth/token"):
            self.token_calls += 1
            return self.responses.get("token", (200, dict(TOKEN_RESPONSE)))
        for fragment, response in self.responses.items():
            if fragment != "token" and fragment in url:
                return response
        return 404, {"error": {"code": "not_found", "message": "Not found"}}

    @property
    def last(self):
        return self.calls[-1]

    def calls_to(self, fragment):
        return [c for c in self.calls if fragment in c["url"]]


def order_ok(order_id="TT_ORD_554433", status="Routed", warnings=None):
    data = {"order": {"id": order_id, "status": status}}
    if warnings is not None:
        data["warnings"] = warnings
    return 201, {"data": data}


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def build_client(transport, clock=None, is_production=False, authenticated=True):
    client = TastytradeClient(
        is_production=is_production,
        http_fn=transport,
        user_agent="algo-trading-skills/2.0",
        clock=clock or FakeClock(),
    )
    if authenticated:
        client.authenticate(TastytradeCredentials("secret", "refresh"))
    return client


def vertical_spread():
    return [
        OptionLeg(
            occ_symbol=format_occ_symbol("AAPL", "240816", "C", 195),
            action=LegAction.BUY_TO_OPEN,
            quantity=1,
        ),
        OptionLeg(
            occ_symbol=format_occ_symbol("AAPL", "240816", "C", 200),
            action=LegAction.SELL_TO_OPEN,
            quantity=1,
        ),
    ]


# ==========================================================================
# OCC symbology
# ==========================================================================
class TestOccSymbolFormatting(unittest.TestCase):
    def test_known_symbols_match_published_layout(self):
        # 6-char space-padded root | YYMMDD | C/P | strike*1000 zero-padded to 8.
        self.assertEqual(format_occ_symbol("AAPL", "240816", "C", 200), "AAPL  240816C00200000")
        self.assertEqual(format_occ_symbol("SPY", "241220", "P", "500.50"), "SPY   241220P00500500")
        self.assertEqual(format_occ_symbol("GOOGL", "260116", "C", 5), "GOOGL 260116C00005000")
        # Exactly 6 characters: no padding at all.
        self.assertEqual(format_occ_symbol("BRKB12", "250117", "P", 12.5), "BRKB12250117P00012500")

    def test_every_symbol_is_21_characters(self):
        for root in ("A", "AB", "ABC", "ABCD", "ABCDE", "ABCDEF"):
            self.assertEqual(len(format_occ_symbol(root, "250117", "C", 1)), 21)

    def test_lowercase_and_whitespace_are_normalised(self):
        self.assertEqual(format_occ_symbol(" aapl ", "240816", "c", 200), "AAPL  240816C00200000")

    def test_root_longer_than_six_characters_is_rejected(self):
        # Regression: ljust(6) does not truncate, so this used to emit a silently
        # malformed 22-character symbol.
        with self.assertRaises(TastytradeSymbolError):
            format_occ_symbol("TOOLONG", "240816", "C", 200)

    def test_non_alphanumeric_root_is_rejected(self):
        for bad in ("BRK.B", "BRK/B", "", "   "):
            with self.assertRaises(TastytradeSymbolError):
                format_occ_symbol(bad, "240816", "C", 200)

    def test_spelled_out_option_type_is_rejected(self):
        # Regression: "CALL" used to be pasted straight in, yielding 24 chars.
        for bad in ("CALL", "PUT", "X", "", "CP"):
            with self.assertRaises(TastytradeSymbolError):
                format_occ_symbol("AAPL", "240816", bad, 200)

    def test_malformed_expiration_is_rejected(self):
        for bad in ("2024-08-16", "24816", "2408160", "abcdef", ""):
            with self.assertRaises(TastytradeSymbolError):
                format_occ_symbol("AAPL", bad, "C", 200)

    def test_impossible_calendar_date_is_rejected(self):
        for bad in ("240230", "241301", "240800"):
            with self.assertRaises(TastytradeSymbolError):
                format_occ_symbol("AAPL", bad, "C", 200)

    def test_leap_day_is_accepted(self):
        self.assertEqual(format_occ_symbol("AAPL", "240229", "C", 200), "AAPL  240229C00200000")

    def test_strike_overflowing_the_eight_digit_field_is_rejected(self):
        # 100000 * 1000 = 100000000, which is 9 digits.
        with self.assertRaises(TastytradeSymbolError):
            format_occ_symbol("AAPL", "240816", "C", 100000)
        # The boundary itself still fits.
        self.assertEqual(
            format_occ_symbol("AAPL", "240816", "C", "99999.999"), "AAPL  240816C99999999"
        )

    def test_non_positive_strike_is_rejected(self):
        for bad in (0, -1, "-0.5"):
            with self.assertRaises(TastytradeSymbolError):
                format_occ_symbol("AAPL", "240816", "C", bad)

    def test_sub_mill_strike_precision_is_rejected_not_rounded(self):
        # Regression: int(round(200.0001 * 1000)) == 200000, so a caller asking
        # for an unlisted strike used to be handed the 200 strike silently.
        with self.assertRaises(TastytradeSymbolError):
            format_occ_symbol("AAPL", "240816", "C", "200.0001")

    def test_strike_rounding_to_zero_is_rejected(self):
        # Regression: round() is banker's rounding, so int(round(0.0005 * 1000))
        # == 0 and the symbol used to come out with an 00000000 strike field.
        with self.assertRaises(TastytradeSymbolError):
            format_occ_symbol("AAPL", "240816", "C", 0.0005)

    def test_non_finite_and_non_numeric_strikes_are_rejected(self):
        for bad in (float("nan"), float("inf"), "abc", None, True, [200]):
            with self.assertRaises(TastytradeSymbolError):
                format_occ_symbol("AAPL", "240816", "C", bad)

    def test_exact_listed_strikes_survive_float_input(self):
        self.assertEqual(format_occ_symbol("AAPL", "240816", "C", 8.7), "AAPL  240816C00008700")
        self.assertEqual(format_occ_symbol("AAPL", "240816", "C", 2.675), "AAPL  240816C00002675")
        self.assertEqual(
            format_occ_symbol("AAPL", "240816", "C", Decimal("0.005")), "AAPL  240816C00000005"
        )


class TestOccSymbolParsing(unittest.TestCase):
    def test_parses_published_example(self):
        parsed = parse_occ_symbol("AAPL  240816C00200000")
        self.assertEqual(parsed.root, "AAPL")
        self.assertEqual(parsed.expiration, "240816")
        self.assertEqual(parsed.option_type, "C")
        self.assertEqual(parsed.strike, Decimal("200"))

    def test_round_trips_against_the_formatter(self):
        for root, exp, kind, strike in (
            ("AAPL", "240816", "C", "195"),
            ("SPY", "241220", "P", "500.5"),
            ("A", "250117", "C", "0.005"),
            ("ABCDEF", "251219", "P", "99999.999"),
        ):
            symbol = format_occ_symbol(root, exp, kind, strike)
            parsed = parse_occ_symbol(symbol)
            self.assertEqual(parsed.root, root)
            self.assertEqual(parsed.expiration, exp)
            self.assertEqual(parsed.option_type, kind)
            self.assertEqual(parsed.strike, Decimal(strike))

    def test_rejects_wrong_length_and_malformed_symbols(self):
        for bad in (
            "AAPL 240816C00200000",  # 20 chars
            "AAPL   240816C00200000",  # 22 chars
            "AAPL  240816X00200000",  # bad option type
            "AAPL  24081C000200000",  # digits shifted
            "aapl  240816C00200000",  # lowercase root
            "",
            None,
            12345,
        ):
            with self.assertRaises(TastytradeSymbolError):
                parse_occ_symbol(bad)

    def test_rejects_impossible_expiration(self):
        with self.assertRaises(TastytradeSymbolError):
            parse_occ_symbol("AAPL  240230C00200000")


class TestPriceEffectSign(unittest.TestCase):
    def test_negative_is_debit_and_positive_is_credit(self):
        self.assertEqual(price_effect_for_signed_price("-2.15"), PriceEffect.DEBIT)
        self.assertEqual(price_effect_for_signed_price(2.15), PriceEffect.CREDIT)
        self.assertEqual(price_effect_for_signed_price(Decimal("-0.01")), PriceEffect.DEBIT)

    def test_zero_has_no_direction(self):
        with self.assertRaises(TastytradeOrderValidationError):
            price_effect_for_signed_price(0)


# ==========================================================================
# Client construction and OAuth2
# ==========================================================================
class TestClientConstruction(unittest.TestCase):
    def test_expiry_clock_defaults_to_monotonic_not_wall_clock(self):
        # A wall-clock step must not be able to extend an access token's life.
        client = TastytradeClient(http_fn=RecordingTransport())
        self.assertIs(client._clock, time.monotonic)

    def test_missing_transport_is_rejected_at_construction(self):
        with self.assertRaises(TastytradeAPIError):
            TastytradeClient(http_fn=None)

    def test_user_agent_must_be_product_slash_version(self):
        # Tastytrade's edge proxy 401s any other shape.
        for bad in ("", "python-requests", "my bot/1.0", "/1.0", "bot/"):
            with self.assertRaises(TastytradeAPIError):
                TastytradeClient(http_fn=RecordingTransport(), user_agent=bad)
        TastytradeClient(http_fn=RecordingTransport(), user_agent="mybot/1.2.3")

    def test_environment_selects_base_url(self):
        transport = RecordingTransport()
        self.assertEqual(
            TastytradeClient(http_fn=transport).base_url, "https://api.cert.tastyworks.com"
        )
        self.assertEqual(
            TastytradeClient(is_production=True, http_fn=transport).base_url,
            "https://api.tastyworks.com",
        )


class TestOAuth2Authentication(unittest.TestCase):
    def test_retired_password_login_fails_loudly(self):
        client = build_client(RecordingTransport(), authenticated=False)
        with self.assertRaises(TastytradeAuthDiscontinuedError) as ctx:
            client.login("trader@example.com", "SecretPass123!")
        self.assertIn("oauth", str(ctx.exception).lower())

    def test_token_request_shape(self):
        transport = RecordingTransport()
        client = build_client(transport)
        call = transport.calls_to("/oauth/token")[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://api.cert.tastyworks.com/oauth/token")
        self.assertEqual(
            call["body"],
            {
                "grant_type": "refresh_token",
                "client_secret": "secret",
                "refresh_token": "refresh",
            },
        )
        self.assertEqual(call["headers"]["User-Agent"], "algo-trading-skills/2.0")
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        # No bearer header on the token request itself.
        self.assertNotIn("Authorization", call["headers"])
        self.assertEqual(client.session.access_token, "mock_access_token_999")

    def test_accept_version_only_on_production(self):
        cert = RecordingTransport()
        build_client(cert)
        self.assertNotIn("Accept-Version", cert.calls_to("/oauth/token")[0]["headers"])

        prod = RecordingTransport()
        build_client(prod, is_production=True)
        self.assertEqual(
            prod.calls_to("/oauth/token")[0]["headers"]["Accept-Version"],
            TastytradeClient.ACCEPT_VERSION,
        )

    def test_expiry_uses_server_supplied_lifetime(self):
        clock = FakeClock(1_000_000.0)
        transport = RecordingTransport(responses={"token": (200, {"access_token": "t", "expires_in": 900})})
        client = build_client(transport, clock=clock)
        self.assertEqual(client.session.expires_at, 1_000_900.0)

    def test_missing_expires_in_falls_back_to_documented_lifetime(self):
        clock = FakeClock(1_000_000.0)
        transport = RecordingTransport(responses={"token": (200, {"access_token": "t"})})
        client = build_client(transport, clock=clock)
        self.assertEqual(
            client.session.expires_at,
            1_000_000.0 + DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS,
        )

    def test_implausible_or_invalid_expires_in_is_fatal(self):
        for bad in (0, -5, "soon", True, MAX_PLAUSIBLE_TOKEN_LIFETIME_SECONDS + 1, [900]):
            transport = RecordingTransport(
                responses={"token": (200, {"access_token": "t", "expires_in": bad})}
            )
            with self.assertRaises(TastytradeAuthError):
                build_client(transport)

    def test_missing_or_blank_access_token_is_fatal(self):
        for body in ({}, {"access_token": ""}, {"access_token": None}, {"access_token": 123}):
            transport = RecordingTransport(responses={"token": (200, body)})
            with self.assertRaises(TastytradeAuthError):
                build_client(transport)

    def test_non_object_token_body_is_fatal(self):
        transport = RecordingTransport(responses={"token": (200, "<html>maintenance</html>")})
        with self.assertRaises(TastytradeAuthError):
            build_client(transport)

    def test_rejected_grant_reports_code_without_echoing_body(self):
        transport = RecordingTransport(
            responses={
                "token": (
                    401,
                    {
                        "error": {"code": "invalid_grant", "message": "revoked"},
                        "refresh_token": "LEAKED_SECRET",
                    },
                )
            }
        )
        with self.assertRaises(TastytradeAuthError) as ctx:
            build_client(transport)
        message = str(ctx.exception)
        self.assertIn("invalid_grant", message)
        self.assertNotIn("LEAKED_SECRET", message)

    def test_credentials_and_session_do_not_leak_through_repr(self):
        transport = RecordingTransport()
        client = build_client(transport)
        creds = TastytradeCredentials("super_secret", "super_refresh")
        self.assertNotIn("super_secret", repr(creds))
        self.assertNotIn("super_refresh", repr(creds))
        self.assertNotIn("mock_access_token_999", repr(client.session))

    def test_blank_credentials_are_rejected(self):
        for secret, refresh in (("", "r"), ("s", ""), ("  ", "r"), ("s", "  ")):
            with self.assertRaises(TastytradeAuthError):
                TastytradeCredentials(secret, refresh)


class TestTokenRefreshLifecycle(unittest.TestCase):
    def test_unauthenticated_client_refuses_to_build_headers(self):
        client = build_client(RecordingTransport(), authenticated=False)
        with self.assertRaises(TastytradeSessionExpiredError):
            client.auth_headers()

    def test_live_token_is_reused_without_a_second_token_call(self):
        clock = FakeClock(1_000_000.0)
        transport = RecordingTransport()
        client = build_client(transport, clock=clock)
        clock.now += 100  # 800s of a 900s token remain
        client.auth_headers()
        self.assertEqual(transport.token_calls, 1)

    def test_token_inside_the_buffer_is_refreshed_before_use(self):
        clock = FakeClock(1_000_000.0)
        transport = RecordingTransport()
        client = build_client(transport, clock=clock)
        # Boundary: refresh buffer is 60s, so 840s in is exactly the flip point.
        clock.now += 839
        client.auth_headers()
        self.assertEqual(transport.token_calls, 1)
        clock.now += 1
        client.auth_headers()
        self.assertEqual(transport.token_calls, 2)

    def test_bearer_scheme_is_used_on_authenticated_requests(self):
        transport = RecordingTransport(responses={"/positions": (200, {"data": {"items": []}})})
        client = build_client(transport)
        client.get_positions(ACCOUNT)
        self.assertEqual(
            transport.calls_to("/positions")[0]["headers"]["Authorization"],
            "Bearer mock_access_token_999",
        )


# ==========================================================================
# Order payload construction
# ==========================================================================
class TestOrderPayload(unittest.TestCase):
    def setUp(self):
        self.transport = RecordingTransport(
            responses={f"accounts/{ACCOUNT}/orders": order_ok()}
        )
        self.client = build_client(self.transport)

    def _placed_body(self):
        return self.transport.calls_to("/orders")[-1]["body"]

    def test_vertical_spread_payload_matches_documented_shape(self):
        order = self.client.place_complex_option_order(
            account_number=ACCOUNT,
            legs=vertical_spread(),
            order_type=OrderType.LIMIT,
            net_price="2.15",
            price_effect=PriceEffect.DEBIT,
        )
        self.assertEqual(
            self._placed_body(),
            {
                "order-type": "Limit",
                "time-in-force": "Day",
                "price": "2.15",
                "price-effect": "Debit",
                "legs": [
                    {
                        "instrument-type": "Equity Option",
                        "symbol": "AAPL  240816C00195000",
                        "action": "Buy to Open",
                        "quantity": 1,
                    },
                    {
                        "instrument-type": "Equity Option",
                        "symbol": "AAPL  240816C00200000",
                        "action": "Sell to Open",
                        "quantity": 1,
                    },
                ],
            },
        )
        self.assertEqual(order.order_id, "TT_ORD_554433")
        self.assertEqual(order.status, "Routed")
        self.assertEqual(order.price, Decimal("2.15"))
        self.assertEqual(order.price_effect, PriceEffect.DEBIT)

    def test_four_leg_iron_condor_sends_four_legs(self):
        legs = [
            OptionLeg(format_occ_symbol("SPY", "241220", "P", 480), LegAction.BUY_TO_OPEN, 1),
            OptionLeg(format_occ_symbol("SPY", "241220", "P", 490), LegAction.SELL_TO_OPEN, 1),
            OptionLeg(format_occ_symbol("SPY", "241220", "C", 510), LegAction.SELL_TO_OPEN, 1),
            OptionLeg(format_occ_symbol("SPY", "241220", "C", 520), LegAction.BUY_TO_OPEN, 1),
        ]
        self.client.place_complex_option_order(
            ACCOUNT, legs, OrderType.LIMIT, "1.35", PriceEffect.CREDIT
        )
        body = self._placed_body()
        self.assertEqual(len(body["legs"]), 4)
        self.assertEqual(body["price-effect"], "Credit")
        self.assertEqual(
            [leg["symbol"] for leg in body["legs"]],
            [
                "SPY   241220P00480000",
                "SPY   241220P00490000",
                "SPY   241220C00510000",
                "SPY   241220C00520000",
            ],
        )

    def test_market_order_carries_no_price_fields(self):
        # Regression: price/price-effect used to be sent unconditionally, which
        # Tastytrade's Market order model has no field for.
        self.client.place_complex_option_order(ACCOUNT, vertical_spread(), OrderType.MARKET)
        body = self._placed_body()
        self.assertNotIn("price", body)
        self.assertNotIn("price-effect", body)
        self.assertEqual(body["order-type"], "Market")

    def test_market_order_with_a_price_is_rejected_locally(self):
        for kwargs in (
            {"net_price": "2.15", "price_effect": PriceEffect.DEBIT},
            {"net_price": "2.15"},
            {"price_effect": PriceEffect.DEBIT},
        ):
            with self.assertRaises(TastytradeOrderValidationError):
                self.client.place_complex_option_order(
                    ACCOUNT, vertical_spread(), OrderType.MARKET, **kwargs
                )
        self.assertEqual(self.transport.calls_to("/orders"), [])

    def test_limit_order_requires_price_and_effect(self):
        with self.assertRaises(TastytradeOrderValidationError):
            self.client.place_complex_option_order(ACCOUNT, vertical_spread(), OrderType.LIMIT)
        with self.assertRaises(TastytradeOrderValidationError):
            self.client.place_complex_option_order(
                ACCOUNT, vertical_spread(), OrderType.LIMIT, net_price="2.15"
            )
        self.assertEqual(self.transport.calls_to("/orders"), [])

    def test_negative_net_price_is_rejected_with_a_pointer_to_the_sign_helper(self):
        with self.assertRaises(TastytradeOrderValidationError) as ctx:
            self.client.place_complex_option_order(
                ACCOUNT, vertical_spread(), OrderType.LIMIT, "-2.15", PriceEffect.DEBIT
            )
        self.assertIn("2.15", str(ctx.exception))
        self.assertEqual(self.transport.calls_to("/orders"), [])

    def test_zero_price_credit_order_is_permitted(self):
        self.client.place_complex_option_order(
            ACCOUNT, vertical_spread(), OrderType.LIMIT, 0, PriceEffect.CREDIT
        )
        self.assertEqual(self._placed_body()["price"], 0)

    def test_price_is_serialised_as_an_exact_decimal_string_not_a_float(self):
        # No float ever reaches the payload, so the wire value is exactly the
        # decimal the caller wrote.
        self.client.place_complex_option_order(
            ACCOUNT, vertical_spread(), OrderType.LIMIT, 0.07, PriceEffect.DEBIT
        )
        self.assertEqual(self._placed_body()["price"], "0.07")
        self.client.place_complex_option_order(
            ACCOUNT, vertical_spread(), OrderType.LIMIT, Decimal("2.150"), PriceEffect.DEBIT
        )
        self.assertEqual(self._placed_body()["price"], "2.15")

    def test_empty_leg_list_is_rejected(self):
        with self.assertRaises(TastytradeOrderValidationError):
            self.client.place_complex_option_order(
                ACCOUNT, [], OrderType.LIMIT, "1.00", PriceEffect.DEBIT
            )

    def test_duplicate_symbol_action_legs_are_rejected(self):
        leg = OptionLeg(format_occ_symbol("AAPL", "240816", "C", 200), LegAction.BUY_TO_OPEN, 1)
        duplicate = OptionLeg(
            format_occ_symbol("AAPL", "240816", "C", 200), LegAction.BUY_TO_OPEN, 1
        )
        with self.assertRaises(TastytradeOrderValidationError):
            self.client.place_complex_option_order(
                ACCOUNT, [leg, duplicate], OrderType.LIMIT, "1.00", PriceEffect.DEBIT
            )

    def test_same_symbol_opposite_actions_is_allowed(self):
        symbol = format_occ_symbol("AAPL", "240816", "C", 200)
        legs = [
            OptionLeg(symbol, LegAction.BUY_TO_OPEN, 1),
            OptionLeg(symbol, LegAction.SELL_TO_CLOSE, 1),
        ]
        self.client.place_complex_option_order(
            ACCOUNT, legs, OrderType.LIMIT, "0.05", PriceEffect.DEBIT
        )
        self.assertEqual(len(self._placed_body()["legs"]), 2)

    def test_external_identifier_is_forwarded_when_supplied(self):
        self.client.place_complex_option_order(
            ACCOUNT,
            vertical_spread(),
            OrderType.LIMIT,
            "2.15",
            PriceEffect.DEBIT,
            external_identifier="strat-a-0001",
        )
        self.assertEqual(self._placed_body()["external-identifier"], "strat-a-0001")

    def test_external_identifier_is_omitted_when_absent(self):
        self.client.place_complex_option_order(
            ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
        )
        self.assertNotIn("external-identifier", self._placed_body())

    def test_invalid_order_type_and_time_in_force_are_rejected(self):
        with self.assertRaises(TastytradeOrderValidationError):
            self.client.place_complex_option_order(
                ACCOUNT, vertical_spread(), "Limit", "2.15", PriceEffect.DEBIT
            )
        with self.assertRaises(TastytradeOrderValidationError):
            self.client.place_complex_option_order(
                ACCOUNT,
                vertical_spread(),
                OrderType.LIMIT,
                "2.15",
                PriceEffect.DEBIT,
                time_in_force="",
            )

    def test_gtc_time_in_force_is_forwarded(self):
        self.client.place_complex_option_order(
            ACCOUNT,
            vertical_spread(),
            OrderType.LIMIT,
            "2.15",
            PriceEffect.DEBIT,
            time_in_force="GTC",
        )
        self.assertEqual(self._placed_body()["time-in-force"], "GTC")

    def test_legs_supplied_as_a_generator_do_not_become_a_zero_leg_order(self):
        # A generator is exhausted by the validation pass; without materialising
        # it first the payload serialises with an empty legs array.
        self.client.place_complex_option_order(
            ACCOUNT,
            (leg for leg in vertical_spread()),
            OrderType.LIMIT,
            "2.15",
            PriceEffect.DEBIT,
        )
        self.assertEqual(len(self._placed_body()["legs"]), 2)

    def test_path_traversal_account_number_is_rejected(self):
        for bad in ("", "  ", "../5WT00001", "5WT/00001", 12345, None):
            with self.assertRaises(TastytradeOrderValidationError):
                self.client.place_complex_option_order(
                    bad, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
                )


class TestOptionLegValidation(unittest.TestCase):
    def test_malformed_equity_option_symbol_is_rejected_at_leg_construction(self):
        with self.assertRaises(TastytradeSymbolError):
            OptionLeg("AAPL240816C00200000", LegAction.BUY_TO_OPEN, 1)

    def test_future_option_symbol_is_not_forced_through_occ_validation(self):
        # Future options use Tastytrade's own symbology, not 21-char OCC.
        leg = OptionLeg(
            "./ESU4 EW4Q4 240823C5750",
            LegAction.BUY_TO_OPEN,
            1,
            instrument_type=InstrumentType.FUTURE_OPTION,
        )
        self.assertEqual(leg.to_payload()["instrument-type"], "Future Option")

    def test_non_positive_quantity_is_rejected(self):
        symbol = format_occ_symbol("AAPL", "240816", "C", 200)
        for bad in (0, -1, "-3", Decimal("0")):
            with self.assertRaises(TastytradeOrderValidationError):
                OptionLeg(symbol, LegAction.BUY_TO_OPEN, bad)

    def test_fractional_option_quantity_is_rejected(self):
        symbol = format_occ_symbol("AAPL", "240816", "C", 200)
        with self.assertRaises(TastytradeOrderValidationError):
            OptionLeg(symbol, LegAction.BUY_TO_OPEN, 1.5)

    def test_non_numeric_and_non_finite_quantities_are_rejected(self):
        symbol = format_occ_symbol("AAPL", "240816", "C", 200)
        for bad in ("abc", None, True, float("nan"), float("inf")):
            with self.assertRaises(TastytradeOrderValidationError):
                OptionLeg(symbol, LegAction.BUY_TO_OPEN, bad)

    def test_quantity_serialises_as_an_integer_not_a_float(self):
        symbol = format_occ_symbol("AAPL", "240816", "C", 200)
        self.assertEqual(OptionLeg(symbol, LegAction.BUY_TO_OPEN, 3.0).to_payload()["quantity"], 3)

    def test_unknown_instrument_type_and_action_are_rejected(self):
        symbol = format_occ_symbol("AAPL", "240816", "C", 200)
        with self.assertRaises(TastytradeOrderValidationError):
            OptionLeg(symbol, LegAction.BUY_TO_OPEN, 1, instrument_type="Crypto Option")
        with self.assertRaises(TastytradeOrderValidationError):
            OptionLeg(symbol, "Buy to Open", 1)

    def test_string_instrument_type_is_coerced(self):
        symbol = format_occ_symbol("AAPL", "240816", "C", 200)
        leg = OptionLeg(symbol, LegAction.BUY_TO_OPEN, 1, instrument_type="Equity Option")
        self.assertIs(leg.instrument_type, InstrumentType.EQUITY_OPTION)


# ==========================================================================
# Order outcome handling
# ==========================================================================
class TestOrderOutcomes(unittest.TestCase):
    def _client(self, response, raise_on=None):
        transport = RecordingTransport(
            responses={f"accounts/{ACCOUNT}/orders": response} if response else {},
            raise_on=raise_on,
        )
        return build_client(transport), transport

    def test_rejection_reports_error_code_and_leaves_no_order(self):
        client, _ = self._client(
            (
                422,
                {
                    "error": {
                        "code": "preflight_check_failure",
                        "message": "Account does not have enough buying power",
                    }
                },
            )
        )
        with self.assertRaises(TastytradeOrderRejectedError) as ctx:
            client.place_complex_option_order(
                ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
            )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("preflight_check_failure", ctx.exception.error_codes)

    def test_nested_error_list_is_flattened(self):
        client, _ = self._client(
            (
                422,
                {
                    "error": {
                        "code": "validation_error",
                        "errors": [
                            {"code": "invalid_symbol", "message": "bad leg 1"},
                            {"domain": "legs", "reason": "too many"},
                        ],
                    }
                },
            )
        )
        with self.assertRaises(TastytradeOrderRejectedError) as ctx:
            client.place_complex_option_order(
                ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
            )
        self.assertEqual(ctx.exception.error_codes, ("invalid_symbol", "legs"))

    def test_transport_failure_is_ambiguous_not_a_rejection(self):
        # Regression: a lost response must never be reported as "did not happen".
        client, _ = self._client(None, raise_on={"/orders"})
        with self.assertRaises(TastytradeAmbiguousOrderError) as ctx:
            client.place_complex_option_order(
                ACCOUNT,
                vertical_spread(),
                OrderType.LIMIT,
                "2.15",
                PriceEffect.DEBIT,
                external_identifier="strat-a-0001",
            )
        self.assertEqual(ctx.exception.account_number, ACCOUNT)
        self.assertEqual(ctx.exception.external_identifier, "strat-a-0001")
        self.assertIn("reconcile", str(ctx.exception).lower())

    def test_server_error_and_timeout_statuses_are_ambiguous(self):
        for status in (408, 425, 429, 500, 502, 503, 504):
            client, _ = self._client((status, {"error": {"code": "oops", "message": "x"}}))
            with self.assertRaises(TastytradeAmbiguousOrderError):
                client.place_complex_option_order(
                    ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
                )

    def test_client_errors_stay_rejections(self):
        for status in (400, 401, 403, 404, 422):
            client, _ = self._client((status, {"error": {"code": "bad", "message": "x"}}))
            with self.assertRaises(TastytradeOrderRejectedError):
                client.place_complex_option_order(
                    ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
                )

    def test_success_without_an_order_id_is_ambiguous_not_a_fabricated_id(self):
        # Regression: the id used to default to a hard-coded "TT_ORD_1001",
        # handing the caller a handle that can never cancel anything.
        for body in ({"data": {}}, {"data": {"order": {}}}, {"data": {"order": {"id": ""}}}, {}):
            client, _ = self._client((201, body))
            with self.assertRaises(TastytradeAmbiguousOrderError):
                client.place_complex_option_order(
                    ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
                )

    def test_success_without_a_status_is_ambiguous_not_a_fabricated_status(self):
        for order in ({"id": "1"}, {"id": "1", "status": ""}, {"id": "1", "status": None}):
            client, _ = self._client((201, {"data": {"order": order}}))
            with self.assertRaises(TastytradeAmbiguousOrderError):
                client.place_complex_option_order(
                    ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
                )

    def test_numeric_order_id_is_stringified(self):
        client, _ = self._client((201, {"data": {"order": {"id": 987654, "status": "Routed"}}}))
        order = client.place_complex_option_order(
            ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
        )
        self.assertEqual(order.order_id, "987654")

    def test_warnings_on_an_accepted_order_are_surfaced_not_discarded(self):
        client, _ = self._client(
            order_ok(warnings=[{"code": "wide_market", "message": "Bid-ask spread is wide"}])
        )
        order = client.place_complex_option_order(
            ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
        )
        self.assertEqual(len(order.warnings), 1)
        self.assertEqual(order.warnings[0].code, "wide_market")

    def test_rejected_status_is_reported_verbatim(self):
        client, _ = self._client(order_ok(status="Rejected"))
        order = client.place_complex_option_order(
            ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
        )
        self.assertEqual(order.status, "Rejected")


class TestDryRun(unittest.TestCase):
    def test_dry_run_targets_the_dry_run_path_with_the_same_payload(self):
        transport = RecordingTransport(
            responses={
                "/orders/dry-run": (
                    200,
                    {
                        "data": {
                            "order": {"status": "Received"},
                            "buying-power-effect": {"change-in-buying-power": "215.0"},
                            "fee-calculation": {"total-fees": "2.28"},
                            "warnings": [{"code": "wide_market", "message": "wide"}],
                        }
                    },
                )
            }
        )
        client = build_client(transport)
        preview = client.dry_run_option_order(
            ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
        )
        call = transport.calls_to("/orders/dry-run")[0]
        self.assertEqual(
            call["url"], f"https://api.cert.tastyworks.com/accounts/{ACCOUNT}/orders/dry-run"
        )
        self.assertEqual(call["body"]["price"], "2.15")
        self.assertEqual(call["body"]["price-effect"], "Debit")
        self.assertTrue(preview.is_acceptable)
        self.assertEqual(preview.buying_power_effect["change-in-buying-power"], "215.0")
        self.assertEqual(preview.fee_calculation["total-fees"], "2.28")
        self.assertEqual(preview.warnings[0].code, "wide_market")

    def test_dry_run_errors_mark_the_preview_unacceptable(self):
        transport = RecordingTransport(
            responses={
                "/orders/dry-run": (
                    200,
                    {
                        "data": {
                            "errors": [
                                {"code": "insufficient_buying_power", "message": "no bp"}
                            ]
                        }
                    },
                )
            }
        )
        client = build_client(transport)
        preview = client.dry_run_option_order(
            ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
        )
        self.assertFalse(preview.is_acceptable)
        self.assertEqual(preview.errors[0].code, "insufficient_buying_power")

    def test_dry_run_http_rejection_raises(self):
        transport = RecordingTransport(
            responses={"/orders/dry-run": (422, {"error": {"code": "bad", "message": "x"}})}
        )
        client = build_client(transport)
        with self.assertRaises(TastytradeOrderRejectedError):
            client.dry_run_option_order(
                ACCOUNT, vertical_spread(), OrderType.LIMIT, "2.15", PriceEffect.DEBIT
            )

    def test_dry_run_applies_the_same_local_validation_as_placement(self):
        client = build_client(RecordingTransport())
        with self.assertRaises(TastytradeOrderValidationError):
            client.dry_run_option_order(ACCOUNT, vertical_spread(), OrderType.MARKET, "2.15")


# ==========================================================================
# Reconciliation
# ==========================================================================
class TestReconciliation(unittest.TestCase):
    LIVE = (
        200,
        {
            "data": {
                "items": [
                    {"id": "1", "status": "Live", "external-identifier": "strat-a-0001"},
                    {"id": "2", "status": "Live", "external-identifier": "strat-a-0002"},
                    {"id": "3", "status": "Filled"},
                ]
            }
        },
    )

    def test_live_orders_unwrap_the_items_envelope(self):
        transport = RecordingTransport(responses={"/orders/live": self.LIVE})
        client = build_client(transport)
        orders = client.get_live_orders(ACCOUNT)
        self.assertEqual([o["id"] for o in orders], ["1", "2", "3"])
        self.assertEqual(
            transport.calls_to("/orders/live")[0]["url"],
            f"https://api.cert.tastyworks.com/accounts/{ACCOUNT}/orders/live",
        )

    def test_external_identifier_lookup_finds_the_matching_order(self):
        client = build_client(RecordingTransport(responses={"/orders/live": self.LIVE}))
        found = client.find_orders_by_external_identifier(ACCOUNT, "strat-a-0001")
        self.assertEqual([o["id"] for o in found], ["1"])

    def test_external_identifier_lookup_requires_a_tag(self):
        client = build_client(RecordingTransport(responses={"/orders/live": self.LIVE}))
        for bad in ("", "   ", None):
            with self.assertRaises(TastytradeOrderValidationError):
                client.find_orders_by_external_identifier(ACCOUNT, bad)

    def test_unreadable_live_orders_response_raises_instead_of_reading_as_empty(self):
        # "no orders" and "I could not tell" lead to opposite decisions, and only
        # one of them places a duplicate.
        for body in ({}, {"data": None}, {"data": {}}, {"data": {"items": None}}, "oops"):
            client = build_client(RecordingTransport(responses={"/orders/live": (200, body)}))
            with self.assertRaises(TastytradeAPIError):
                client.get_live_orders(ACCOUNT)

    def test_lookup_raises_when_no_live_order_echoes_an_external_identifier(self):
        # An empty match here would say nothing about whether the submission
        # landed, and would license exactly the duplicate it exists to prevent.
        transport = RecordingTransport(
            responses={
                "/orders/live": (200, {"data": {"items": [{"id": "1", "status": "Live"}]}})
            }
        )
        client = build_client(transport)
        with self.assertRaises(TastytradeAPIError):
            client.find_orders_by_external_identifier(ACCOUNT, "strat-a-0001")

    def test_lookup_returns_empty_when_the_field_is_echoed_but_unmatched(self):
        client = build_client(RecordingTransport(responses={"/orders/live": self.LIVE}))
        self.assertEqual(client.find_orders_by_external_identifier(ACCOUNT, "nope"), [])

    def test_lookup_returns_empty_for_an_empty_live_order_list(self):
        client = build_client(
            RecordingTransport(responses={"/orders/live": (200, {"data": {"items": []}})})
        )
        self.assertEqual(client.find_orders_by_external_identifier(ACCOUNT, "any"), [])

    def test_positions_and_accounts_unwrap_the_items_envelope(self):
        transport = RecordingTransport(
            responses={
                "/positions": (200, {"data": {"items": [{"symbol": "AAPL  240816C00200000"}]}}),
                "/customers/me/accounts": (
                    200,
                    {"data": {"items": [{"account": {"account-number": ACCOUNT}}]}},
                ),
            }
        )
        client = build_client(transport)
        self.assertEqual(len(client.get_positions(ACCOUNT)), 1)
        self.assertEqual(len(client.get_accounts()), 1)

    def test_malformed_position_envelopes_yield_an_empty_list(self):
        # Positions are read for reporting, where an empty list is a benign
        # answer; the strict path is reserved for reconciliation reads.
        for body in ({}, {"data": None}, {"data": {}}, {"data": {"items": None}}, "oops"):
            client = build_client(RecordingTransport(responses={"/positions": (200, body)}))
            self.assertEqual(client.get_positions(ACCOUNT), [])

    def test_failed_read_raises_without_echoing_the_body(self):
        client = build_client(
            RecordingTransport(
                responses={
                    "/positions": (
                        403,
                        {"error": {"code": "forbidden", "message": "no"}, "token": "LEAKED"},
                    )
                }
            )
        )
        with self.assertRaises(TastytradeAPIError) as ctx:
            client.get_positions(ACCOUNT)
        self.assertIn("forbidden", str(ctx.exception))
        self.assertNotIn("LEAKED", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
