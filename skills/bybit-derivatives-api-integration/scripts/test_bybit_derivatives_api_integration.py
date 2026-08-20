"""
Tests for the Bybit V5 signing layer.

Expected signatures are produced by ``_reference_hmac_sha256``, an RFC 2104
implementation written directly on top of ``hashlib`` and pinned to the RFC 4231
test vectors. It shares no code with ``hmac``, so a test asserting against it
verifies the concatenation order and the exact bytes signed - unlike a length
check, which passes for any 32-byte digest of anything.
"""
import json
import unittest

from bybit_derivatives_api_integration import (
    DEFAULT_RECV_WINDOW_MS,
    FORWARD_TOLERANCE_MS,
    MAINNET_BASE_URL,
    MAX_ORDER_LINK_ID_LEN,
    TESTNET_BASE_URL,
    BybitAuthError,
    BybitClockError,
    BybitConfig,
    BybitV5Authenticator,
    RateLimitSnapshot,
    new_order_link_id,
)

import hashlib

_BLOCK_SIZE = 64


def _reference_hmac_sha256(key: bytes, message: bytes) -> str:
    """HMAC-SHA256 per RFC 2104, built from hashlib alone. Returns lowercase hex."""
    if len(key) > _BLOCK_SIZE:
        key = hashlib.sha256(key).digest()
    key = key.ljust(_BLOCK_SIZE, b"\x00")
    inner_pad = bytes(b ^ 0x36 for b in key)
    outer_pad = bytes(b ^ 0x5C for b in key)
    inner = hashlib.sha256(inner_pad + message).digest()
    return hashlib.sha256(outer_pad + inner).hexdigest()


class TestReferenceOracle(unittest.TestCase):
    """The oracle the other tests rely on, checked against RFC 4231."""

    def test_rfc4231_case_1(self):
        self.assertEqual(
            _reference_hmac_sha256(b"\x0b" * 20, b"Hi There"),
            "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7",
        )

    def test_rfc4231_case_2(self):
        self.assertEqual(
            _reference_hmac_sha256(b"Jefe", b"what do ya want for nothing?"),
            "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
        )


class TestSignatureConstruction(unittest.TestCase):
    def setUp(self):
        self.config = BybitConfig(
            api_key="TEST_API_KEY",
            api_secret="TEST_API_SECRET",
            is_testnet=True,
        )
        self.auth = BybitV5Authenticator(self.config)

    def test_param_str_order_matches_bybit_specification(self):
        """timestamp + api_key + recv_window + payload, in that exact order."""
        timestamp = "1658384314791"
        payload = "category=option&symbol=BTC-29JUL22-25000-C"
        expected = _reference_hmac_sha256(
            b"TEST_API_SECRET",
            f"{timestamp}TEST_API_KEY{DEFAULT_RECV_WINDOW_MS}{payload}".encode("utf-8"),
        )
        self.assertEqual(self.auth._generate_signature(timestamp, payload), expected)

    def test_signature_is_lowercase_hex(self):
        signature = self.auth._generate_signature("1658384314791", "category=linear")
        self.assertEqual(len(signature), 64)
        self.assertEqual(signature, signature.lower())
        int(signature, 16)  # raises if not hex

    def test_recv_window_is_part_of_the_signed_string(self):
        """A different recv_window must change the signature, not just a header."""
        other = BybitV5Authenticator(
            BybitConfig(
                api_key="TEST_API_KEY",
                api_secret="TEST_API_SECRET",
                recv_window=8000,
            )
        )
        self.assertNotEqual(
            self.auth._generate_signature("1", "x=1"),
            other._generate_signature("1", "x=1"),
        )

    def test_signature_is_sensitive_to_field_boundaries(self):
        """
        Regression guard against a concatenation that shifts a boundary, e.g.
        signing api_key+timestamp instead of timestamp+api_key.
        """
        swapped = _reference_hmac_sha256(
            b"TEST_API_SECRET",
            f"TEST_API_KEY1658384314791{DEFAULT_RECV_WINDOW_MS}x=1".encode("utf-8"),
        )
        self.assertNotEqual(
            self.auth._generate_signature("1658384314791", "x=1"), swapped
        )


class TestSignedStringEqualsTransmittedString(unittest.TestCase):
    """The one property the whole scheme depends on."""

    def setUp(self):
        self.auth = BybitV5Authenticator(
            BybitConfig(api_key="K", api_secret="S", is_testnet=True)
        )

    def test_get_url_carries_exactly_the_signed_query_string(self):
        req = self.auth.sign_request(
            "GET", "/v5/order/realtime", {"symbol": "BTCUSDT", "category": "linear"}
        )
        self.assertEqual(req["payload"], req["query_string"])
        self.assertEqual(
            req["url"], f"{TESTNET_BASE_URL}/v5/order/realtime?{req['query_string']}"
        )
        expected = _reference_hmac_sha256(
            b"S",
            f"{req['headers']['X-BAPI-TIMESTAMP']}K{DEFAULT_RECV_WINDOW_MS}"
            f"{req['query_string']}".encode("utf-8"),
        )
        self.assertEqual(req["headers"]["X-BAPI-SIGN"], expected)

    def test_post_body_is_exactly_the_signed_body(self):
        params = {"category": "linear", "symbol": "BTCUSDT", "qty": "0.1"}
        req = self.auth.sign_request("POST", "/v5/order/create", params)
        self.assertEqual(req["body"], json.dumps(params, separators=(",", ":")))
        self.assertEqual(req["query_string"], "")
        self.assertEqual(req["url"], f"{TESTNET_BASE_URL}/v5/order/create")
        expected = _reference_hmac_sha256(
            b"S",
            f"{req['headers']['X-BAPI-TIMESTAMP']}K{DEFAULT_RECV_WINDOW_MS}"
            f"{req['body']}".encode("utf-8"),
        )
        self.assertEqual(req["headers"]["X-BAPI-SIGN"], expected)

    def test_get_request_carries_no_body(self):
        """Bybit answers HTTP 403 to a GET that carries a JSON body."""
        req = self.auth.sign_request("GET", "/v5/order/realtime", {"category": "linear"})
        self.assertEqual(req["body"], "")

    def test_empty_params_produce_an_empty_payload(self):
        get_req = self.auth.sign_request("GET", "/v5/market/time")
        post_req = self.auth.sign_request("POST", "/v5/order/cancel-all")
        self.assertEqual(get_req["payload"], "")
        self.assertEqual(post_req["payload"], "")
        self.assertEqual(get_req["url"], f"{TESTNET_BASE_URL}/v5/market/time")

    def test_mainnet_selection(self):
        auth = BybitV5Authenticator(
            BybitConfig(api_key="K", api_secret="S", is_testnet=False)
        )
        self.assertEqual(auth.sign_request("GET", "/v5/x")["url"], f"{MAINNET_BASE_URL}/v5/x")

    def test_required_headers_present(self):
        req = self.auth.sign_request("POST", "/v5/order/create", {"category": "linear"})
        for header in (
            "X-BAPI-API-KEY",
            "X-BAPI-SIGN",
            "X-BAPI-TIMESTAMP",
            "X-BAPI-RECV-WINDOW",
        ):
            self.assertIn(header, req["headers"])
        self.assertEqual(req["headers"]["X-BAPI-API-KEY"], "K")
        self.assertEqual(
            req["headers"]["X-BAPI-RECV-WINDOW"], str(DEFAULT_RECV_WINDOW_MS)
        )

    def test_unsupported_method_rejected(self):
        with self.assertRaises(BybitAuthError):
            self.auth.sign_request("DELETE", "/v5/order/cancel")

    def test_relative_endpoint_rejected(self):
        with self.assertRaises(BybitAuthError):
            self.auth.sign_request("GET", "v5/order/realtime")


class TestQueryStringRendering(unittest.TestCase):
    def setUp(self):
        self.auth = BybitV5Authenticator(BybitConfig(api_key="K", api_secret="S"))

    def test_none_values_are_dropped_not_stringified(self):
        """A dropped optional parameter must not become the literal 'None'."""
        query = self.auth.build_query_string({"symbol": "BTCUSDT", "cursor": None})
        self.assertEqual(query, "symbol=BTCUSDT")

    def test_booleans_render_lowercase(self):
        self.assertEqual(
            self.auth.build_query_string({"openOnly": True, "closed": False}),
            "closed=false&openOnly=true",
        )

    def test_float_values_rejected(self):
        with self.assertRaises(BybitAuthError):
            self.auth.build_query_string({"qty": 0.1})

    def test_values_rewritten_on_the_wire_are_rejected(self):
        """A value an HTTP client would re-encode cannot be signed as-is."""
        for bad in ("a b", "a&b", "a+b", "a#b", "50%off"):
            with self.subTest(value=bad):
                with self.assertRaises(BybitAuthError):
                    self.auth.build_query_string({"cursor": bad})

    def test_pagination_cursors_pass_through_unchanged(self):
        """
        Bybit's nextPageCursor carries '=' and well-formed '%XX' escapes, which
        survive transit untouched. Rejecting them would break pagination.
        """
        for cursor in ("page%3D2%26limit%3D50", "bF9pZD0x==", "a%3Db=="):
            with self.subTest(cursor=cursor):
                self.assertEqual(
                    self.auth.build_query_string({"cursor": cursor}),
                    f"cursor={cursor}",
                )

    def test_non_ascii_rejected(self):
        with self.assertRaises(BybitAuthError):
            self.auth.build_query_string({"symbol": "BTC–USDT"})

    def test_ordering_is_canonical_and_independent_of_insertion_order(self):
        a = self.auth.build_query_string({"symbol": "BTCUSDT", "category": "linear"})
        b = self.auth.build_query_string({"category": "linear", "symbol": "BTCUSDT"})
        self.assertEqual(a, b)


class TestOrderBodyValidation(unittest.TestCase):
    def setUp(self):
        self.auth = BybitV5Authenticator(BybitConfig(api_key="K", api_secret="S"))

    def test_numeric_qty_rejected(self):
        """0.1 + 0.2 serialises to '0.30000000000000004'; require a string."""
        with self.assertRaises(BybitAuthError):
            self.auth.build_json_body({"symbol": "BTCUSDT", "qty": 0.1 + 0.2})

    def test_numeric_price_rejected(self):
        with self.assertRaises(BybitAuthError):
            self.auth.build_json_body({"symbol": "BTCUSDT", "price": 65000})

    def test_decimal_strings_accepted(self):
        body = self.auth.build_json_body({"qty": "0.30", "price": "65000.5"})
        self.assertEqual(body, '{"qty":"0.30","price":"65000.5"}')

    def test_over_long_order_link_id_rejected(self):
        with self.assertRaises(BybitAuthError):
            self.auth.build_json_body({"orderLinkId": "x" * (MAX_ORDER_LINK_ID_LEN + 1)})

    def test_max_length_order_link_id_accepted(self):
        body = self.auth.build_json_body({"orderLinkId": "x" * MAX_ORDER_LINK_ID_LEN})
        self.assertIn("x" * MAX_ORDER_LINK_ID_LEN, body)

    def test_empty_order_link_id_rejected(self):
        with self.assertRaises(BybitAuthError):
            self.auth.build_json_body({"orderLinkId": ""})

    def test_unserialisable_body_rejected(self):
        with self.assertRaises(BybitAuthError):
            self.auth.build_json_body({"meta": object()})


class TestOrderLinkIdMinting(unittest.TestCase):
    def test_ids_are_unique_and_within_the_limit(self):
        ids = {new_order_link_id("strat-") for _ in range(1000)}
        self.assertEqual(len(ids), 1000)
        for link_id in ids:
            self.assertLessEqual(len(link_id), MAX_ORDER_LINK_ID_LEN)
            self.assertTrue(link_id.startswith("strat-"))

    def test_prefix_too_long_rejected_rather_than_truncated(self):
        """Truncating would collide across orders and defeat the whole point."""
        with self.assertRaises(BybitAuthError):
            new_order_link_id("x" * 11)


class TestConfigValidation(unittest.TestCase):
    def test_secret_absent_from_repr(self):
        config = BybitConfig(api_key="AK", api_secret="SUPER_SECRET")
        self.assertNotIn("SUPER_SECRET", repr(config))
        self.assertNotIn("SUPER_SECRET", str(config))
        self.assertNotIn("SUPER_SECRET", f"{config}")

    def test_secret_absent_from_container_repr(self):
        """The leak path that matters: a config logged inside a structure."""
        config = BybitConfig(api_key="AK", api_secret="SUPER_SECRET")
        self.assertNotIn("SUPER_SECRET", repr({"cfg": config}))

    def test_blank_credentials_rejected(self):
        with self.assertRaises(BybitAuthError):
            BybitConfig(api_key="", api_secret="S")
        with self.assertRaises(BybitAuthError):
            BybitConfig(api_key="K", api_secret="   ")

    def test_non_positive_recv_window_rejected(self):
        with self.assertRaises(BybitAuthError):
            BybitConfig(api_key="K", api_secret="S", recv_window=0)
        with self.assertRaises(BybitAuthError):
            BybitConfig(api_key="K", api_secret="S", recv_window=-1)

    def test_absurd_recv_window_rejected(self):
        with self.assertRaises(BybitAuthError):
            BybitConfig(api_key="K", api_secret="S", recv_window=600_000)


class TestAcceptanceWindow(unittest.TestCase):
    """server_time - recv_window <= ts < server_time + 1000."""

    def setUp(self):
        self.window = DEFAULT_RECV_WINDOW_MS
        self.server = 1_700_000_000_000

    def test_lower_bound_inclusive(self):
        self.assertTrue(
            BybitV5Authenticator.is_within_acceptance_window(
                self.server - self.window, self.server, self.window
            )
        )

    def test_just_below_lower_bound_rejected(self):
        self.assertFalse(
            BybitV5Authenticator.is_within_acceptance_window(
                self.server - self.window - 1, self.server, self.window
            )
        )

    def test_upper_bound_exclusive_at_1000ms(self):
        self.assertTrue(
            BybitV5Authenticator.is_within_acceptance_window(
                self.server + FORWARD_TOLERANCE_MS - 1, self.server, self.window
            )
        )
        self.assertFalse(
            BybitV5Authenticator.is_within_acceptance_window(
                self.server + FORWARD_TOLERANCE_MS, self.server, self.window
            )
        )

    def test_forward_tolerance_does_not_grow_with_recv_window(self):
        """A fast clock is not helped by a larger recv_window."""
        self.assertFalse(
            BybitV5Authenticator.is_within_acceptance_window(
                self.server + 4_000, self.server, 20_000
            )
        )


class TestServerTimeOffset(unittest.TestCase):
    def setUp(self):
        self.auth = BybitV5Authenticator(BybitConfig(api_key="K", api_secret="S"))

    def test_offset_is_zero_before_sync(self):
        self.assertEqual(self.auth.server_time_offset_ms, 0)

    def test_offset_applied_to_signed_timestamp(self):
        local = self.auth._local_time_ms()
        self.auth.sync_with_server_time(local + 2_500, local_time_ms=local)
        self.assertEqual(self.auth.server_time_offset_ms, 2_500)
        req = self.auth.sign_request("GET", "/v5/order/realtime")
        signed = int(req["headers"]["X-BAPI-TIMESTAMP"])
        self.assertGreaterEqual(signed - self.auth._local_time_ms(), 2_000)

    def test_negative_offset_supported(self):
        local = self.auth._local_time_ms()
        self.assertEqual(
            self.auth.sync_with_server_time(local - 1_200, local_time_ms=local), -1_200
        )

    def test_invalid_server_time_rejected(self):
        for bad in (0, -1, "1700000000000", 1.5, True):
            with self.subTest(value=bad):
                with self.assertRaises(BybitClockError):
                    self.auth.sync_with_server_time(bad)

    def test_stale_offset_blocks_signing(self):
        """A stale correction is worse than none: it hides ongoing drift."""
        auth = BybitV5Authenticator(
            BybitConfig(api_key="K", api_secret="S"), max_offset_age_s=-1.0
        )
        auth.sync_with_server_time(auth._local_time_ms() + 10)
        with self.assertRaises(BybitClockError):
            auth.sign_request("GET", "/v5/order/realtime")


class TestRateLimitSnapshot(unittest.TestCase):
    def test_parses_documented_header_example(self):
        snapshot = RateLimitSnapshot.from_headers(
            {
                "Content-Type": "application/json; charset=utf-8",
                "X-Bapi-Limit": "10",
                "X-Bapi-Limit-Status": "9",
                "X-Bapi-Limit-Reset-Timestamp": "1672738134824",
            }
        )
        self.assertEqual(snapshot.limit, 10)
        self.assertEqual(snapshot.remaining, 9)
        self.assertEqual(snapshot.reset_timestamp_ms, 1672738134824)

    def test_header_lookup_is_case_insensitive(self):
        snapshot = RateLimitSnapshot.from_headers(
            {"X-BAPI-LIMIT": "50", "x-bapi-limit-status": "25"}
        )
        self.assertEqual((snapshot.limit, snapshot.remaining), (50, 25))

    def test_missing_headers_return_none(self):
        self.assertIsNone(RateLimitSnapshot.from_headers({}))
        self.assertIsNone(RateLimitSnapshot.from_headers({"X-Bapi-Limit": "10"}))

    def test_unparseable_headers_return_none_rather_than_a_wrong_budget(self):
        self.assertIsNone(
            RateLimitSnapshot.from_headers(
                {"X-Bapi-Limit": "ten", "X-Bapi-Limit-Status": "9"}
            )
        )

    def test_unparseable_reset_timestamp_does_not_discard_the_budget(self):
        snapshot = RateLimitSnapshot.from_headers(
            {
                "X-Bapi-Limit": "10",
                "X-Bapi-Limit-Status": "3",
                "X-Bapi-Limit-Reset-Timestamp": "n/a",
            }
        )
        self.assertEqual(snapshot.remaining, 3)
        self.assertIsNone(snapshot.reset_timestamp_ms)

    def test_relative_threshold_does_not_misfire_on_a_10_per_second_endpoint(self):
        """
        The regression this replaces: an absolute 'back off below 10 remaining'
        rule fires on a fully healthy 10/s order endpoint.
        """
        healthy = RateLimitSnapshot(limit=10, remaining=9)
        self.assertLess(healthy.remaining, 10)
        self.assertFalse(healthy.should_throttle())

    def test_relative_threshold_protects_a_high_limit_endpoint(self):
        self.assertTrue(RateLimitSnapshot(limit=50, remaining=5).should_throttle())
        self.assertFalse(RateLimitSnapshot(limit=50, remaining=25).should_throttle())

    def test_threshold_boundary(self):
        self.assertFalse(RateLimitSnapshot(limit=10, remaining=2).should_throttle(0.2))
        self.assertTrue(RateLimitSnapshot(limit=10, remaining=1).should_throttle(0.2))

    def test_exhausted_budget_throttles(self):
        self.assertTrue(RateLimitSnapshot(limit=10, remaining=0).should_throttle())

    def test_utilisation(self):
        self.assertAlmostEqual(RateLimitSnapshot(limit=10, remaining=4).utilisation, 0.6)
        self.assertAlmostEqual(RateLimitSnapshot(limit=10, remaining=10).utilisation, 0.0)

    def test_nonsensical_limit_fails_closed(self):
        """An unusable budget must throttle, not divide by zero."""
        snapshot = RateLimitSnapshot(limit=0, remaining=0)
        self.assertTrue(snapshot.should_throttle())
        self.assertEqual(snapshot.utilisation, 1.0)

    def test_invalid_reserve_fraction_rejected(self):
        with self.assertRaises(ValueError):
            RateLimitSnapshot(limit=10, remaining=5).should_throttle(1.5)


if __name__ == "__main__":
    unittest.main()
