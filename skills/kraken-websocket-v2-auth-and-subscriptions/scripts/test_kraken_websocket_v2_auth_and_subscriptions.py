"""Unit tests for kraken-websocket-v2-auth-and-subscriptions."""
import logging
import threading
import unittest

from kraken_websocket_v2_auth_and_subscriptions import (
    AUTH_WS_URL_V2,
    BOOK_DEPTHS,
    LEVEL3_DEPTHS,
    LEVEL3_WS_URL_V2,
    PUBLIC_WS_URL_V2,
    STATUS_FRAME_CREATED,
    STATUS_INVALID_CHANNEL,
    STATUS_INVALID_DEPTH,
    STATUS_MISSING_SYMBOL,
    STATUS_MISSING_WS_TOKEN,
    STATUS_TOKEN_CLOCK_SKEW,
    STATUS_TOKEN_EXPIRED,
    STATUS_TOKEN_INACTIVE,
    STATUS_TOKEN_REFRESH_REQUIRED,
    KrakenNonceGenerator,
    KrakenWsTokenState,
    KrakenWsV2Error,
    KrakenWsV2ManagerEngine,
    KrakenWsV2SubscriptionSpec,
    redact_ws_token,
)

logging.getLogger("kraken_websocket_v2_auth_and_subscriptions").setLevel(logging.CRITICAL)

# Kraken's own published API-Sign example. Key, nonce, payload and the expected
# signature are all taken from the vendor's Spot REST Authentication guide, so
# this is an independently derived expected value rather than a restatement of
# the implementation's own arithmetic.
KRAKEN_EXAMPLE_SECRET = (
    "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
)
KRAKEN_EXAMPLE_NONCE = "1616492376594"
KRAKEN_EXAMPLE_PATH = "/0/private/AddOrder"
KRAKEN_EXAMPLE_PAYLOAD = (
    "nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25"
)
KRAKEN_EXAMPLE_SIGNATURE = (
    "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
)

NOW = 1_700_000_000.0
TOKEN = "VALID_WS_TOKEN_123"


def make_engine(**kwargs) -> KrakenWsV2ManagerEngine:
    return KrakenWsV2ManagerEngine(
        api_key="TEST_API_KEY", api_secret_b64=KRAKEN_EXAMPLE_SECRET, **kwargs
    )


def fresh_token(age_seconds: float = 300.0, **kwargs) -> KrakenWsTokenState:
    return KrakenWsTokenState(
        token=TOKEN, created_timestamp_epoch=NOW - age_seconds, **kwargs
    )


class TestRestSignature(unittest.TestCase):
    """The signature is the whole of REST auth; it is checked against the vendor
    vector, not against a re-implementation of the same formula."""

    def setUp(self):
        self.engine = make_engine()

    def test_matches_kraken_published_vector(self):
        self.assertEqual(
            self.engine.generate_kraken_rest_hmac_signature(
                url_path=KRAKEN_EXAMPLE_PATH,
                nonce=KRAKEN_EXAMPLE_NONCE,
                post_data=KRAKEN_EXAMPLE_PAYLOAD,
            ),
            KRAKEN_EXAMPLE_SIGNATURE,
        )

    def test_signature_covers_the_path(self):
        """Swapping only the path must change the signature - proof the path is
        actually prefixed to the digest rather than ignored."""
        token_sig = self.engine.generate_kraken_rest_hmac_signature(
            "/0/private/GetWebSocketsToken", KRAKEN_EXAMPLE_NONCE, KRAKEN_EXAMPLE_PAYLOAD
        )
        self.assertNotEqual(token_sig, KRAKEN_EXAMPLE_SIGNATURE)

    def test_whitespace_wrapped_secret_still_signs_correctly(self):
        """A secret pasted from a wrapped config file must not change the key."""
        wrapped = KRAKEN_EXAMPLE_SECRET[:40] + "\n  " + KRAKEN_EXAMPLE_SECRET[40:] + "\n"
        engine = KrakenWsV2ManagerEngine(api_key="K", api_secret_b64=wrapped)
        self.assertEqual(
            engine.generate_kraken_rest_hmac_signature(
                KRAKEN_EXAMPLE_PATH, KRAKEN_EXAMPLE_NONCE, KRAKEN_EXAMPLE_PAYLOAD
            ),
            KRAKEN_EXAMPLE_SIGNATURE,
        )

    def test_corrupt_secret_raises_instead_of_signing_with_the_wrong_key(self):
        """Regression: the engine used to fall back to the raw string as the
        HMAC key, emitting a well-formed signature that Kraken always rejects
        with EAPI:Invalid signature and no local signal."""
        engine = KrakenWsV2ManagerEngine(
            api_key="K", api_secret_b64=KRAKEN_EXAMPLE_SECRET.replace("/8p", "!8p")
        )
        with self.assertRaises(KrakenWsV2Error):
            engine.generate_kraken_rest_hmac_signature(
                KRAKEN_EXAMPLE_PATH, KRAKEN_EXAMPLE_NONCE, KRAKEN_EXAMPLE_PAYLOAD
            )

    def test_body_without_the_signed_nonce_raises(self):
        with self.assertRaises(KrakenWsV2Error):
            self.engine.generate_kraken_rest_hmac_signature(
                KRAKEN_EXAMPLE_PATH, KRAKEN_EXAMPLE_NONCE, "nonce=9999999999999"
            )

    def test_full_url_instead_of_path_raises(self):
        with self.assertRaises(KrakenWsV2Error):
            self.engine.generate_kraken_rest_hmac_signature(
                "https://api.kraken.com/0/private/AddOrder",
                KRAKEN_EXAMPLE_NONCE,
                KRAKEN_EXAMPLE_PAYLOAD,
            )

    def test_non_integer_nonce_raises(self):
        with self.assertRaises(KrakenWsV2Error):
            self.engine.generate_kraken_rest_hmac_signature(
                KRAKEN_EXAMPLE_PATH, "not-a-nonce", "nonce=not-a-nonce"
            )

    def test_blank_credentials_rejected_at_construction(self):
        with self.assertRaises(KrakenWsV2Error):
            KrakenWsV2ManagerEngine(api_key="", api_secret_b64=KRAKEN_EXAMPLE_SECRET)
        with self.assertRaises(KrakenWsV2Error):
            KrakenWsV2ManagerEngine(api_key="K", api_secret_b64="   ")


class TestNonceGenerator(unittest.TestCase):

    def test_strictly_increasing_under_rapid_calls(self):
        gen = KrakenNonceGenerator()
        nonces = [gen.next_nonce() for _ in range(500)]
        self.assertEqual(nonces, sorted(set(nonces)))

    def test_never_regresses_below_a_seeded_high_water_mark(self):
        """Simulates an NTP step backwards: a nonce already issued must never be
        reissued, because Kraken bans on repeated EAPI:Invalid nonce."""
        gen = KrakenNonceGenerator(start_nonce=99_999_999_999_999)
        self.assertGreater(gen.next_nonce(), 99_999_999_999_999)

    def test_concurrent_callers_never_collide(self):
        gen = KrakenNonceGenerator()
        collected = []
        lock = threading.Lock()

        def worker():
            local = [gen.next_nonce() for _ in range(200)]
            with lock:
                collected.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(collected), len(set(collected)))


class TestChannelRouting(unittest.TestCase):

    def setUp(self):
        self.engine = make_engine()

    def test_public_book_subscription_frame(self):
        spec = KrakenWsV2SubscriptionSpec(
            channel="book", symbols=["BTC/USD", "ETH/USD"], depth=25, req_id=77
        )
        report = self.engine.build_v2_subscription_frame(spec, current_time_epoch=NOW)

        self.assertEqual(report.status, STATUS_FRAME_CREATED)
        self.assertFalse(report.is_private_channel)
        self.assertEqual(report.ws_url, PUBLIC_WS_URL_V2)
        self.assertEqual(report.subscription_json_frame["req_id"], 77)

        params = report.subscription_json_frame["params"]
        self.assertEqual(params["channel"], "book")
        self.assertEqual(params["symbol"], ["BTC/USD", "ETH/USD"])
        self.assertEqual(params["depth"], 25)
        self.assertNotIn("token", params)

    def test_private_executions_subscription_frame(self):
        spec = KrakenWsV2SubscriptionSpec(channel="executions", snap_orders=True)
        report = self.engine.build_v2_subscription_frame(
            spec, token_state=fresh_token(300.0), current_time_epoch=NOW
        )

        self.assertEqual(report.status, STATUS_FRAME_CREATED)
        self.assertTrue(report.is_private_channel)
        self.assertEqual(report.ws_url, AUTH_WS_URL_V2)

        params = report.subscription_json_frame["params"]
        self.assertEqual(params["channel"], "executions")
        self.assertEqual(params["token"], TOKEN)
        self.assertNotIn("symbol", params)
        self.assertEqual(report.token_expires_in_seconds, 600.0)

    def test_level3_requires_a_token_and_its_own_host(self):
        """Regression: level3 is order book data but authenticated, and it does
        not live on ws-auth. Routing it as a public channel produced a frame with
        no token pointed at the wrong host."""
        spec = KrakenWsV2SubscriptionSpec(channel="level3", symbols=["BTC/USD"], depth=100)

        unauthenticated = self.engine.build_v2_subscription_frame(
            spec, current_time_epoch=NOW
        )
        self.assertEqual(unauthenticated.status, STATUS_MISSING_WS_TOKEN)

        report = self.engine.build_v2_subscription_frame(
            spec, token_state=fresh_token(), current_time_epoch=NOW
        )
        self.assertEqual(report.status, STATUS_FRAME_CREATED)
        self.assertEqual(report.ws_url, LEVEL3_WS_URL_V2)
        self.assertNotEqual(report.ws_url, AUTH_WS_URL_V2)
        self.assertEqual(report.subscription_json_frame["params"]["token"], TOKEN)

    def test_order_entry_methods_are_not_subscribable_channels(self):
        """Regression: add_order/cancel_order used to be treated as private
        channels, producing {"method":"subscribe","params":{"channel":"add_order"}},
        which Kraken rejects."""
        for method in ("add_order", "cancel_order"):
            with self.subTest(method=method):
                report = self.engine.build_v2_subscription_frame(
                    KrakenWsV2SubscriptionSpec(channel=method),
                    token_state=fresh_token(),
                    current_time_epoch=NOW,
                )
                self.assertEqual(report.status, STATUS_INVALID_CHANNEL)
                self.assertEqual(report.subscription_json_frame, {})
                self.assertIn("request method", report.audit_notes)

    def test_unknown_channel_is_rejected_not_forwarded(self):
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="not_a_channel"), current_time_epoch=NOW
        )
        self.assertEqual(report.status, STATUS_INVALID_CHANNEL)
        self.assertEqual(report.subscription_json_frame, {})

    def test_channel_name_is_case_and_whitespace_insensitive(self):
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="  BOOK ", symbols=["BTC/USD"]),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_FRAME_CREATED)
        self.assertEqual(report.channel, "book")


class TestSubscriptionParameterValidation(unittest.TestCase):

    def setUp(self):
        self.engine = make_engine()

    def test_symbol_required_channels_reject_an_empty_list(self):
        for channel in ("book", "ticker", "trade", "ohlc"):
            with self.subTest(channel=channel):
                report = self.engine.build_v2_subscription_frame(
                    KrakenWsV2SubscriptionSpec(channel=channel), current_time_epoch=NOW
                )
                self.assertEqual(report.status, STATUS_MISSING_SYMBOL)
                self.assertEqual(report.subscription_json_frame, {})

    def test_blank_symbols_do_not_satisfy_the_requirement(self):
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="ticker", symbols=["   ", ""]),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_MISSING_SYMBOL)

    def test_instrument_needs_no_symbol(self):
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="instrument", depth=None),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_FRAME_CREATED)
        self.assertNotIn("symbol", report.subscription_json_frame["params"])

    def test_rest_altname_symbol_is_warned_about(self):
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="ticker", symbols=["XXBTZUSD"]),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_FRAME_CREATED)
        self.assertTrue(any("BASE/QUOTE" in w for w in report.warnings))

    def test_book_depth_must_be_one_of_the_venue_values(self):
        for depth in sorted(BOOK_DEPTHS):
            with self.subTest(depth=depth):
                report = self.engine.build_v2_subscription_frame(
                    KrakenWsV2SubscriptionSpec(
                        channel="book", symbols=["BTC/USD"], depth=depth
                    ),
                    current_time_epoch=NOW,
                )
                self.assertEqual(report.status, STATUS_FRAME_CREATED)

        rejected = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="book", symbols=["BTC/USD"], depth=7),
            current_time_epoch=NOW,
        )
        self.assertEqual(rejected.status, STATUS_INVALID_DEPTH)

    def test_level3_depth_set_is_narrower_than_book(self):
        """25 and 500 are valid book depths but not level3 depths."""
        self.assertNotIn(25, LEVEL3_DEPTHS)
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="level3", symbols=["BTC/USD"], depth=25),
            token_state=fresh_token(),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_INVALID_DEPTH)

    def test_depth_is_not_emitted_for_channels_that_reject_it(self):
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="ticker", symbols=["BTC/USD"], depth=500),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_FRAME_CREATED)
        self.assertNotIn("depth", report.subscription_json_frame["params"])
        self.assertTrue(report.warnings)

    def test_ohlc_interval_is_emitted_and_validated(self):
        ok = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(
                channel="ohlc", symbols=["BTC/USD"], depth=None, interval=60
            ),
            current_time_epoch=NOW,
        )
        self.assertEqual(ok.subscription_json_frame["params"]["interval"], 60)
        self.assertFalse(ok.warnings)

        odd = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(
                channel="ohlc", symbols=["BTC/USD"], depth=None, interval=7
            ),
            current_time_epoch=NOW,
        )
        self.assertTrue(any("interval 7" in w for w in odd.warnings))

    def test_default_depth_does_not_warn_on_depthless_channels(self):
        """depth defaults to 10, so a private subscribe must not emit a spurious
        warning about a parameter the caller never set."""
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(), current_time_epoch=NOW,
        )
        self.assertEqual(report.warnings, [])

    def test_optional_flags_are_omitted_unless_set(self):
        params = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(),
            current_time_epoch=NOW,
        ).subscription_json_frame["params"]
        self.assertNotIn("order_status", params)
        self.assertNotIn("snapshot", params)

        params = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(
                channel="executions", order_status=False, snapshot=False
            ),
            token_state=fresh_token(),
            current_time_epoch=NOW,
        ).subscription_json_frame["params"]
        self.assertIs(params["order_status"], False)
        self.assertIs(params["snapshot"], False)


class TestTokenLifecycle(unittest.TestCase):

    def setUp(self):
        self.engine = make_engine()

    def test_private_channel_without_a_token_is_rejected(self):
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="balances"), current_time_epoch=NOW
        )
        self.assertEqual(report.status, STATUS_MISSING_WS_TOKEN)
        self.assertFalse(report.is_token_valid)

    def test_blank_token_string_counts_as_missing(self):
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=KrakenWsTokenState(token="   ", created_timestamp_epoch=NOW),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_MISSING_WS_TOKEN)

    def test_inactive_token_is_honoured(self):
        """is_active was a declared field the engine never read."""
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(10.0, is_active=False),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_TOKEN_INACTIVE)

    def test_refresh_margin_boundary_is_inclusive(self):
        just_inside = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(719.9), current_time_epoch=NOW,
        )
        self.assertEqual(just_inside.status, STATUS_FRAME_CREATED)

        at_boundary = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(720.0), current_time_epoch=NOW,
        )
        self.assertEqual(at_boundary.status, STATUS_TOKEN_REFRESH_REQUIRED)

    def test_expired_token_is_distinguished_from_one_merely_needing_refresh(self):
        """780s is inside the 900s use-by window but past the 720s margin; 900s
        is past the window itself. Collapsing both into one status hides the
        difference between 'refresh now' and 'this token cannot work'."""
        needs_refresh = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(780.0), current_time_epoch=NOW,
        )
        self.assertEqual(needs_refresh.status, STATUS_TOKEN_REFRESH_REQUIRED)
        self.assertFalse(needs_refresh.is_token_valid)
        self.assertEqual(needs_refresh.token_expires_in_seconds, 120.0)

        expired = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(901.0), current_time_epoch=NOW,
        )
        self.assertEqual(expired.status, STATUS_TOKEN_EXPIRED)

    def test_future_dated_token_is_rejected_rather_than_looking_fresh(self):
        """Regression: a negative age passed every freshness comparison."""
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(-3_600.0), current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_TOKEN_CLOCK_SKEW)

    def test_nan_timestamp_raises_rather_than_passing_the_gate(self):
        """NaN comparisons are all False, so a NaN age would fall through every
        rejection branch to approval."""
        with self.assertRaises(KrakenWsV2Error):
            self.engine.build_v2_subscription_frame(
                KrakenWsV2SubscriptionSpec(channel="executions"),
                token_state=KrakenWsTokenState(
                    token=TOKEN, created_timestamp_epoch=float("nan")
                ),
                current_time_epoch=NOW,
            )

    def test_shorter_venue_expiry_shortens_the_window(self):
        """If Kraken ever returns expires < 900, the smaller value must win."""
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(400.0, expires_in_seconds=300.0),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_TOKEN_EXPIRED)

    def test_short_venue_expiry_still_leaves_a_refresh_margin(self):
        """With expires=300 the 720s threshold would never fire, taking a token
        straight from valid to expired with no chance to refresh. The 180s
        margin is what is held constant, not the threshold."""
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(200.0, expires_in_seconds=300.0),
            current_time_epoch=NOW,
        )
        self.assertEqual(report.status, STATUS_TOKEN_REFRESH_REQUIRED)

    def test_symbol_is_not_emitted_on_channels_that_reject_it(self):
        """executions/balances take no symbol filter; passing one must not end up
        in the frame as a malformed parameter."""
        for channel in ("executions", "balances"):
            with self.subTest(channel=channel):
                report = self.engine.build_v2_subscription_frame(
                    KrakenWsV2SubscriptionSpec(channel=channel, symbols=["BTC/USD"]),
                    token_state=fresh_token(), current_time_epoch=NOW,
                )
                self.assertEqual(report.status, STATUS_FRAME_CREATED)
                self.assertNotIn("symbol", report.subscription_json_frame["params"])
                self.assertTrue(any("no symbol parameter" in w for w in report.warnings))

    def test_refresh_threshold_must_sit_inside_the_use_by_window(self):
        with self.assertRaises(KrakenWsV2Error):
            make_engine(refresh_threshold_seconds=1_200.0)
        with self.assertRaises(KrakenWsV2Error):
            make_engine(refresh_threshold_seconds=0.0)


class TestTokenRedaction(unittest.TestCase):

    def setUp(self):
        self.engine = make_engine()

    def test_audit_notes_never_carry_the_live_token(self):
        """Regression: audit_notes embedded json.dumps(frame), writing a live
        bearer credential into every log line and stored audit record."""
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="executions"),
            token_state=fresh_token(), current_time_epoch=NOW,
        )
        self.assertNotIn(TOKEN, report.audit_notes)
        self.assertIn("<ws_token:", report.audit_notes)
        # The frame that actually goes on the wire still carries the real token.
        self.assertEqual(report.subscription_json_frame["params"]["token"], TOKEN)

    def test_rejection_notes_are_redacted_too(self):
        report = self.engine.build_v2_subscription_frame(
            KrakenWsV2SubscriptionSpec(channel="add_order"),
            token_state=fresh_token(), current_time_epoch=NOW,
        )
        self.assertNotIn(TOKEN, report.audit_notes)

    def test_redaction_is_stable_and_distinguishes_tokens(self):
        """The fingerprint must be reproducible for one token - it is how events
        are correlated across a session - and different for a different token."""
        self.assertEqual(
            redact_ws_token("token=" + TOKEN, TOKEN),
            redact_ws_token("token=" + TOKEN, TOKEN),
        )
        other = TOKEN + "_OTHER"
        self.assertNotEqual(
            redact_ws_token(TOKEN, TOKEN), redact_ws_token(other, other)
        )

    def test_redaction_of_an_absent_token_is_a_no_op(self):
        self.assertEqual(redact_ws_token("nothing here", None), "nothing here")


if __name__ == "__main__":
    unittest.main()
