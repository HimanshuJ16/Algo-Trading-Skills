"""
Unit tests for token-lifecycle-live-probing skill.

Tests:
1. 3-outcome probe response classification (VALID, INVALID, AMBIGUOUS).
2. Documented broker status codes: Kite 403/429/500-504, Fyers 401/429, Breeze 408.
3. Body-envelope classification for brokers that report errors inside a 2xx.
4. Probe retry with capped, jittered backoff on AMBIGUOUS outcomes.
5. Automatic re-authentication trigger on INVALID (401/403) token probe.
6. The core safety invariant: AMBIGUOUS never triggers re-authentication.
7. Post-authentication verification, including token preservation on failure.
8. Empirical lifespan recording and proactive-refresh gating.
9. Backward compatibility of classify_probe_response and probe_with_backoff.
"""
import unittest
from unittest.mock import Mock

from token_probe import (
    AMBIGUOUS,
    INVALID,
    VALID,
    AmbiguousProbeError,
    LiveTokenProbeManager,
    TokenVerificationError,
    classify_probe_response,
    probe_with_backoff,
)


def _no_sleep(_seconds: float) -> None:
    """Injected in place of time.sleep so retry paths cost no wall-clock time."""


class TestClassifyProbeResponse(unittest.TestCase):

    def test_documented_valid_and_invalid_codes(self):
        self.assertEqual(classify_probe_response(200, False), VALID)
        # Fyers API v3 returns HTTP 401 for an unusable access token;
        # Kite Connect returns HTTP 403 TokenException for an expired session.
        self.assertEqual(classify_probe_response(401, False), INVALID)
        self.assertEqual(classify_probe_response(403, False), INVALID)

    def test_server_side_and_transport_failures_are_ambiguous(self):
        # Kite documents 500 (unexpected), 502 (OMS down), 503 (API down),
        # 504 (gateway timeout). None of these say anything about the token.
        for code in (500, 502, 503, 504):
            self.assertEqual(classify_probe_response(code, False), AMBIGUOUS, code)
        self.assertEqual(classify_probe_response(None, True), AMBIGUOUS)
        self.assertEqual(classify_probe_response(None, False), AMBIGUOUS)

    def test_rate_limit_is_ambiguous_not_invalid(self):
        # Regression: 429 previously classified as INVALID, so a rate-limited
        # probe triggered a full re-login -- against the endpoint most likely to
        # be throttled next. Kite documents 429 as "Too many requests to the API
        # (rate limiting)"; Fyers returns 429 "request limit reached".
        self.assertEqual(classify_probe_response(429, False), AMBIGUOUS)

    def test_request_timeout_status_is_ambiguous(self):
        # Breeze documents 408 Request Timeout. 425 Too Early is likewise a retry.
        self.assertEqual(classify_probe_response(408, False), AMBIGUOUS)
        self.assertEqual(classify_probe_response(425, False), AMBIGUOUS)

    def test_client_and_config_errors_do_not_trigger_reauth(self):
        # Regression: the previous catch-all returned INVALID for every status it
        # did not otherwise match. Kite documents 400 as bad parameters, 404 as a
        # missing resource, 405 as a wrong method and 410 as gone -- all client or
        # config defects that re-authentication cannot fix.
        for code in (400, 404, 405, 410, 302, 100):
            self.assertEqual(classify_probe_response(code, False), AMBIGUOUS, code)

    def test_invalid_codes_are_configurable(self):
        self.assertEqual(
            classify_probe_response(419, False, invalid_codes=(419,)), INVALID
        )
        # A broker whose auth failure is not 401 should not have 401 forced on it.
        self.assertEqual(
            classify_probe_response(401, False, invalid_codes=(419,)), AMBIGUOUS
        )


class TestBodyClassifier(unittest.TestCase):
    """ICICI Breeze wraps an HTTP-style code inside a 2xx envelope:
    {"Success": ..., "Status": <code>, "Error": ...}. A status-only classifier
    calls a dead Breeze session VALID."""

    @staticmethod
    def _breeze_envelope(body):
        if isinstance(body, dict) and body.get("Status") in (401, 403):
            return INVALID
        return None

    def test_envelope_error_under_http_200_is_invalid(self):
        body = {"Success": None, "Status": 401, "Error": "Session expired"}
        self.assertEqual(
            classify_probe_response(200, False, body=body, body_classifier=self._breeze_envelope),
            INVALID,
        )

    def test_envelope_success_under_http_200_is_valid(self):
        body = {"Success": {"idirect_user_name": "x"}, "Status": 200, "Error": None}
        self.assertEqual(
            classify_probe_response(200, False, body=body, body_classifier=self._breeze_envelope),
            VALID,
        )

    def test_body_classifier_cannot_upgrade_a_transport_auth_failure(self):
        # Consulted only for 2xx, so a buggy or over-eager classifier can never
        # turn a broker's 401 into "keep trading".
        self.assertEqual(
            classify_probe_response(401, False, body={}, body_classifier=lambda _b: VALID),
            INVALID,
        )

    def test_body_classifier_returning_garbage_is_a_wiring_bug(self):
        with self.assertRaises(ValueError):
            classify_probe_response(200, False, body={}, body_classifier=lambda _b: "OK")


class TestProbeWithBackoff(unittest.TestCase):

    def test_retries_ambiguous_then_succeeds(self):
        attempts = 0

        def flaky_probe():
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                return 503, False  # AMBIGUOUS
            return 200, False      # VALID

        res = probe_with_backoff(flaky_probe, max_attempts=3, base_delay=0.01, sleep_fn=_no_sleep)
        self.assertEqual(res, VALID)
        self.assertEqual(attempts, 2)

    def test_invalid_short_circuits_without_retrying(self):
        probe = Mock(return_value=(403, False))
        self.assertEqual(
            probe_with_backoff(probe, max_attempts=5, sleep_fn=_no_sleep), INVALID
        )
        probe.assert_called_once()

    def test_exhausted_retries_return_ambiguous(self):
        probe = Mock(return_value=(None, True))
        self.assertEqual(
            probe_with_backoff(probe, max_attempts=3, base_delay=0.01, sleep_fn=_no_sleep),
            AMBIGUOUS,
        )
        self.assertEqual(probe.call_count, 3)

    def test_backoff_is_jittered_and_capped(self):
        delays = []
        # rng returning 1.0 yields the upper end of the equal-jitter band, which is
        # the full capped delay -- the value to assert the cap against.
        probe_with_backoff(
            Mock(return_value=(503, False)),
            max_attempts=6,
            base_delay=1.0,
            max_delay=4.0,
            sleep_fn=delays.append,
            rng=lambda: 1.0,
        )
        self.assertEqual(delays, [1.0, 2.0, 4.0, 4.0, 4.0])

        lower = []
        probe_with_backoff(
            Mock(return_value=(503, False)),
            max_attempts=3,
            base_delay=1.0,
            sleep_fn=lower.append,
            rng=lambda: 0.0,
        )
        # Equal jitter keeps a floor of half the capped delay, so a single client
        # still backs off even when the random draw is minimal.
        self.assertEqual(lower, [0.5, 1.0])

    def test_rejects_invalid_retry_configuration(self):
        with self.assertRaises(ValueError):
            probe_with_backoff(Mock(return_value=(200, False)), max_attempts=0)
        with self.assertRaises(ValueError):
            probe_with_backoff(Mock(return_value=(200, False)), base_delay=-1.0)

    def test_rejects_malformed_probe_return(self):
        with self.assertRaises(ValueError):
            probe_with_backoff(Mock(return_value=200), sleep_fn=_no_sleep)


class TestVerifyAndRefreshToken(unittest.TestCase):

    def setUp(self):
        self.alerts = []
        self.mgr = LiveTokenProbeManager(
            alert_fn=self.alerts.append, base_delay=0.0, sleep_fn=_no_sleep
        )

    def test_cached_token_valid_no_reauth(self):
        probe_fn = Mock(return_value=(200, False))
        reauth_fn = Mock(return_value="NEW_TOKEN")

        token, refreshed = self.mgr.verify_and_refresh_token(
            "fyers", "CACHED_TOKEN", probe_fn, reauth_fn
        )

        self.assertEqual(token, "CACHED_TOKEN")
        self.assertFalse(refreshed)
        reauth_fn.assert_not_called()

    def test_invalid_token_triggers_reauth(self):
        def mock_probe(tok):
            if tok == "EXPIRED_TOKEN":
                return 401, False
            return 200, False

        reauth_fn = Mock(return_value="FRESH_TOKEN")

        token, refreshed = self.mgr.verify_and_refresh_token(
            "fyers", "EXPIRED_TOKEN", mock_probe, reauth_fn
        )

        self.assertEqual(token, "FRESH_TOKEN")
        self.assertTrue(refreshed)
        reauth_fn.assert_called_once()

    def test_missing_cached_token_authenticates(self):
        reauth_fn = Mock(return_value="FRESH_TOKEN")
        token, refreshed = self.mgr.verify_and_refresh_token(
            "zerodha", None, Mock(return_value=(200, False)), reauth_fn
        )
        self.assertEqual(token, "FRESH_TOKEN")
        self.assertTrue(refreshed)
        reauth_fn.assert_called_once()

    def test_ambiguous_probe_never_reauthenticates(self):
        # The central safety invariant. Previously an exhausted-retry AMBIGUOUS
        # fell through to reauth_fn(), so a broker outage produced a login storm
        # against the endpoint the skill warns is rate-limited.
        probe_fn = Mock(return_value=(None, True))
        reauth_fn = Mock(return_value="SHOULD_NOT_BE_ISSUED")

        with self.assertRaises(AmbiguousProbeError) as ctx:
            self.mgr.verify_and_refresh_token("fyers", "CACHED_TOKEN", probe_fn, reauth_fn)

        reauth_fn.assert_not_called()
        self.assertEqual(ctx.exception.outcome, AMBIGUOUS)
        # The cached token is handed back so the caller can keep it and retry.
        self.assertEqual(ctx.exception.token, "CACHED_TOKEN")
        self.assertEqual(len(self.alerts), 1)

    def test_rate_limited_probe_never_reauthenticates(self):
        probe_fn = Mock(return_value=(429, False))
        reauth_fn = Mock(return_value="SHOULD_NOT_BE_ISSUED")

        with self.assertRaises(AmbiguousProbeError):
            self.mgr.verify_and_refresh_token("zerodha", "CACHED_TOKEN", probe_fn, reauth_fn)

        reauth_fn.assert_not_called()

    def test_failed_post_auth_verification_preserves_new_token(self):
        # A transient 5xx right after a successful login must not throw away the
        # token the broker just issued -- discarding it spends another login on
        # the next start, for nothing.
        def mock_probe(tok):
            if tok == "EXPIRED_TOKEN":
                return 401, False
            return 503, False

        with self.assertRaises(TokenVerificationError) as ctx:
            self.mgr.verify_and_refresh_token(
                "fyers", "EXPIRED_TOKEN", mock_probe, Mock(return_value="FRESH_TOKEN")
            )

        self.assertEqual(ctx.exception.token, "FRESH_TOKEN")
        self.assertEqual(ctx.exception.outcome, AMBIGUOUS)

    def test_reauth_returning_nothing_is_an_error(self):
        with self.assertRaises(TokenVerificationError):
            self.mgr.verify_and_refresh_token(
                "fyers", None, Mock(return_value=(200, False)), Mock(return_value="")
            )

    def test_failing_alert_channel_does_not_mask_the_verdict(self):
        # A pager or webhook that raises must not replace AmbiguousProbeError with
        # its own exception -- the caller's "do not spend a login" branch is bound
        # to AmbiguousProbeError and would never run.
        mgr = LiveTokenProbeManager(
            alert_fn=Mock(side_effect=RuntimeError("pager down")),
            base_delay=0.0,
            sleep_fn=_no_sleep,
        )
        reauth_fn = Mock(return_value="SHOULD_NOT_BE_ISSUED")

        with self.assertRaises(AmbiguousProbeError):
            mgr.verify_and_refresh_token(
                "fyers", "CACHED_TOKEN", Mock(return_value=(None, True)), reauth_fn
            )
        reauth_fn.assert_not_called()

    def test_probe_fn_exception_is_not_swallowed(self):
        # probe_fn owns transport-error translation. If it raises anyway, that is a
        # wiring bug and must surface rather than be misread as an outcome.
        with self.assertRaises(ConnectionError):
            self.mgr.verify_and_refresh_token(
                "fyers",
                "CACHED_TOKEN",
                Mock(side_effect=ConnectionError("dns")),
                Mock(return_value="X"),
            )

    def test_error_messages_never_contain_token_material(self):
        probe_fn = Mock(return_value=(None, True))
        with self.assertRaises(AmbiguousProbeError) as ctx:
            self.mgr.verify_and_refresh_token(
                "fyers", "SECRET_TOKEN_VALUE", probe_fn, Mock(return_value="X")
            )
        self.assertNotIn("SECRET_TOKEN_VALUE", str(ctx.exception))
        self.assertNotIn("SECRET_TOKEN_VALUE", repr(ctx.exception))
        self.assertNotIn("SECRET_TOKEN_VALUE", " ".join(self.alerts))


class TestEmpiricalLifespan(unittest.TestCase):

    def setUp(self):
        self.mgr = LiveTokenProbeManager(sleep_fn=_no_sleep)

    def test_empirical_lifespan_recording(self):
        self.mgr.record_lifespan("fyers", 1000.0, 4600.0)
        self.assertIn("fyers", self.mgr.empirical_lifespans)
        self.assertEqual(self.mgr.empirical_lifespans["fyers"][0], 3600.0)

    def test_broker_name_is_normalised(self):
        self.mgr.record_lifespan("FYERS", 0.0, 60.0)
        self.assertEqual(self.mgr.empirical_lifespans["fyers"], [60.0])

    def test_negative_lifespan_is_rejected(self):
        # Regression: previously clamped to 0.0, which poisoned the baseline that
        # should_proactively_refresh reads -- one bad sample made every later
        # token look overdue.
        with self.assertRaises(ValueError):
            self.mgr.record_lifespan("fyers", 4600.0, 1000.0)
        self.assertNotIn("fyers", self.mgr.empirical_lifespans)

    def test_proactive_refresh_needs_enough_samples(self):
        self.mgr.record_lifespan("fyers", 0.0, 3600.0)
        # One observation is not a baseline, however overdue the token looks.
        self.assertFalse(
            self.mgr.should_proactively_refresh("fyers", 0.0, 100_000.0, min_samples=3)
        )

    def test_proactive_refresh_uses_shortest_observed_lifespan(self):
        for end in (36_000.0, 30_000.0, 33_000.0):
            self.mgr.record_lifespan("fyers", 0.0, end)
        # min = 30000s; with a 1800s margin the trigger point is 28200s.
        self.assertFalse(self.mgr.should_proactively_refresh("fyers", 0.0, 28_199.0))
        self.assertTrue(self.mgr.should_proactively_refresh("fyers", 0.0, 28_200.0))
        # The mean (33000s) would not have fired until 31200s -- 3000s after the
        # shortest lifespan already observed had ended.

    def test_unknown_broker_never_triggers_proactive_refresh(self):
        self.assertFalse(self.mgr.should_proactively_refresh("unseen", 0.0, 1e9))


if __name__ == "__main__":
    unittest.main()
