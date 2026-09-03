"""
Unit tests for the schwab-api-oauth-pkce-flow skill.

Each group below pins a behaviour a naive implementation got wrong or did not
implement at all, so the suite fails against the old code and passes against the
corrected client:

1. Authorization URL: percent-encoded parameters, no PKCE parameters by default.
2. Callback handling: the percent-encoded Schwab code (`...%40`) decoded correctly.
3. Authorization-code exchange: strict response validation, no credential leakage
   into exception text, atomic + owner-only persistence.
4. Refresh grant (absent entirely before 2.0), including the rule that refreshing
   never extends the 7-day refresh window.
5. Lifetime inspection and the bearer-header guard.
6. RFC 7636 helper correctness and its 43-128 character bounds.
"""
import json
import logging
import os
import stat
import tempfile
import time
import unittest

from schwab_pkce_auth import (
    REFRESH_TOKEN_LIFETIME_SECONDS,
    SCHWAB_TOKEN_URL,
    SchwabAmbiguousTokenError,
    SchwabAuthError,
    SchwabOAuthManager,
    SchwabPKCEGenerator,
    SchwabRefreshTokenExpiredError,
    SchwabTokenExchangeError,
    SchwabTokenPersistenceError,
    SchwabTokenState,
)

REDIRECT_URI = "https://127.0.0.1:8182/callback"


def setUpModule():
    """Keep the module's expected warning logs out of the test report."""
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def read_token_file(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class RecordingTransport:
    """Records every token request and replays a scripted response."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload, headers):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def token_response(**overrides):
    resp = {
        "access_token": "ACCESS_ABC",
        "refresh_token": "REFRESH_XYZ",
        "expires_in": 1800,
        "token_type": "Bearer",
        "scope": "api",
        "id_token": "JWT_HERE",
    }
    resp.update(overrides)
    return resp


class SchwabTestBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_file = os.path.join(self.temp_dir.name, "schwab_tokens.json")
        self.mgr = SchwabOAuthManager(token_file_path=self.token_file)
        self.addCleanup(self.temp_dir.cleanup)

    def seed_state(self, **overrides):
        now = time.time()
        params = {
            "access_token": "ACCESS_SEED",
            "refresh_token": "REFRESH_SEED",
            "access_expires_at": now + 1800,
            "refresh_expires_at": now + REFRESH_TOKEN_LIFETIME_SECONDS,
        }
        params.update(overrides)
        self.mgr.state = SchwabTokenState(**params)
        return self.mgr.state


class TestAuthorizationUrl(SchwabTestBase):

    def test_parameters_are_percent_encoded(self):
        """A raw redirect_uri truncates the URL at its own '?'/'&' — encode it."""
        url = SchwabOAuthManager.get_authorization_url("APP KEY&x", REDIRECT_URI)
        self.assertIn("client_id=APP+KEY%26x", url)
        self.assertIn("redirect_uri=https%3A%2F%2F127.0.0.1%3A8182%2Fcallback", url)
        # The literal, unencoded form must not survive anywhere in the query.
        self.assertNotIn(f"redirect_uri={REDIRECT_URI}", url)

    def test_no_pkce_parameters_by_default(self):
        """Schwab publishes no PKCE support; nothing PKCE-shaped is sent unasked."""
        url = SchwabOAuthManager.get_authorization_url("APP_KEY", REDIRECT_URI)
        self.assertNotIn("code_challenge", url)
        self.assertNotIn("code_challenge_method", url)
        self.assertIn("response_type=code", url)
        self.assertTrue(url.startswith("https://api.schwabapi.com/v1/oauth/authorize?"))

    def test_pkce_parameters_included_only_when_explicitly_supplied(self):
        url = SchwabOAuthManager.get_authorization_url("APP_KEY", REDIRECT_URI, "CHALLENGE_123")
        self.assertIn("code_challenge=CHALLENGE_123", url)
        self.assertIn("code_challenge_method=S256", url)

    def test_padded_challenge_rejected(self):
        with self.assertRaises(SchwabAuthError):
            SchwabOAuthManager.get_authorization_url("APP_KEY", REDIRECT_URI, "PADDED=")

    def test_non_https_redirect_uri_rejected(self):
        """Schwab: 'Callback URLs must be HTTPS.'"""
        with self.assertRaises(SchwabAuthError):
            SchwabOAuthManager.get_authorization_url("APP_KEY", "http://127.0.0.1:8182")

    def test_over_length_redirect_uri_rejected(self):
        long_uri = "https://127.0.0.1/" + ("a" * 250)
        with self.assertRaises(SchwabAuthError):
            SchwabOAuthManager.get_authorization_url("APP_KEY", long_uri)

    def test_blank_app_key_rejected(self):
        with self.assertRaises(SchwabAuthError):
            SchwabOAuthManager.get_authorization_url("   ", REDIRECT_URI)


class TestCallbackExtraction(SchwabTestBase):

    def test_percent_encoded_code_is_decoded(self):
        """Schwab codes end in '%40'; the docs require URL-decoding before exchange."""
        callback = f"{REDIRECT_URI}?code=C0.abc-def%40&session=9f1"
        self.assertEqual(SchwabOAuthManager.extract_code_from_callback(callback), "C0.abc-def@")

    def test_code_containing_plus_is_not_turned_into_a_space(self):
        callback = f"{REDIRECT_URI}?code=abc%2Bdef%40"
        self.assertEqual(SchwabOAuthManager.extract_code_from_callback(callback), "abc+def@")

    def test_error_callback_raises(self):
        callback = f"{REDIRECT_URI}?error=access_denied"
        with self.assertRaises(SchwabAuthError):
            SchwabOAuthManager.extract_code_from_callback(callback)

    def test_missing_or_duplicated_code_raises(self):
        for callback in (f"{REDIRECT_URI}?state=1", f"{REDIRECT_URI}?code=a&code=b",
                         f"{REDIRECT_URI}?code="):
            with self.subTest(callback=callback):
                with self.assertRaises(SchwabAuthError):
                    SchwabOAuthManager.extract_code_from_callback(callback)


class TestCodeExchange(SchwabTestBase):

    def test_successful_exchange_persists_and_sets_windows(self):
        transport = RecordingTransport(token_response())
        before = time.time()
        state = self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)

        self.assertEqual(state.access_token, "ACCESS_ABC")
        self.assertEqual(state.refresh_token, "REFRESH_XYZ")
        self.assertGreaterEqual(state.access_expires_at, before + 1800)
        self.assertGreaterEqual(state.refresh_expires_at, before + REFRESH_TOKEN_LIFETIME_SECONDS)
        self.assertTrue(os.path.exists(self.token_file))

        on_disk = read_token_file(self.token_file)
        self.assertEqual(on_disk["access_token"], "ACCESS_ABC")
        self.assertEqual(on_disk["refresh_token"], "REFRESH_XYZ")

    def test_request_shape_matches_schwab_documentation(self):
        transport = RecordingTransport(token_response())
        self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)
        call = transport.calls[0]

        self.assertEqual(call["url"], SCHWAB_TOKEN_URL)
        self.assertEqual(call["payload"]["grant_type"], "authorization_code")
        self.assertEqual(call["payload"]["code"], "CODE@")
        self.assertEqual(call["payload"]["redirect_uri"], REDIRECT_URI)
        self.assertNotIn("code_verifier", call["payload"])
        self.assertEqual(call["headers"]["Content-Type"], "application/x-www-form-urlencoded")
        # Basic base64("KEY:SECRET")
        self.assertEqual(call["headers"]["Authorization"], "Basic S0VZOlNFQ1JFVA==")

    def test_code_verifier_sent_only_when_supplied(self):
        transport = RecordingTransport(token_response())
        self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, "V" * 64, transport)
        self.assertEqual(transport.calls[0]["payload"]["code_verifier"], "V" * 64)

    def test_colon_in_app_key_rejected_before_dispatch(self):
        transport = RecordingTransport(token_response())
        with self.assertRaises(SchwabAuthError):
            self.mgr.exchange_code("KE:Y", "SECRET", "CODE@", REDIRECT_URI, None, transport)
        self.assertEqual(transport.calls, [])

    def test_missing_expires_in_is_fatal_not_defaulted(self):
        """Inventing a 1800s lifetime the server never stated hides a dead token."""
        resp = token_response()
        del resp["expires_in"]
        transport = RecordingTransport(resp)
        with self.assertRaises(SchwabTokenExchangeError):
            self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)
        self.assertIsNone(self.mgr.state)

    def test_non_numeric_and_non_positive_expires_in_rejected(self):
        for bad in (None, "soon", 0, -5, True, float("inf"), float("nan")):
            with self.subTest(expires_in=bad):
                transport = RecordingTransport(token_response(expires_in=bad))
                with self.assertRaises(SchwabTokenExchangeError):
                    self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)

    def test_missing_refresh_token_is_fatal(self):
        resp = token_response()
        del resp["refresh_token"]
        transport = RecordingTransport(resp)
        with self.assertRaises(SchwabTokenExchangeError):
            self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)

    def test_rejection_message_does_not_leak_credentials(self):
        """an earlier client interpolated the whole response into the exception."""
        transport = RecordingTransport(
            {"error": "invalid_grant", "error_description": "bad code",
             "refresh_token": "LEAKED_REFRESH", "id_token": "LEAKED_JWT"}
        )
        with self.assertRaises(SchwabTokenExchangeError) as ctx:
            self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)
        message = str(ctx.exception)
        self.assertNotIn("LEAKED_REFRESH", message)
        self.assertNotIn("LEAKED_JWT", message)
        self.assertIn("invalid_grant", message)

    def test_transport_failure_is_ambiguous_and_preserves_state(self):
        """The authorization code is single-use; a lost response may have spent it."""
        seeded = self.seed_state()
        transport = RecordingTransport(TimeoutError("read timed out"))
        with self.assertRaises(SchwabAmbiguousTokenError):
            self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)
        self.assertIs(self.mgr.state, seeded)

    def test_non_mapping_response_is_ambiguous(self):
        transport = RecordingTransport("<html>502 Bad Gateway</html>")
        with self.assertRaises(SchwabAmbiguousTokenError):
            self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)


class TestRefreshGrant(SchwabTestBase):

    def test_refresh_does_not_extend_the_seven_day_window(self):
        """Re-anchoring the refresh deadline on every refresh silences the warning."""
        now = time.time()
        original_deadline = now + 3 * 86400
        self.seed_state(refresh_expires_at=original_deadline, access_expires_at=now + 10)

        transport = RecordingTransport(token_response(access_token="ACCESS_2"))
        state = self.mgr.refresh_access_token("KEY", "SECRET", transport)

        self.assertEqual(state.access_token, "ACCESS_2")
        self.assertEqual(state.refresh_expires_at, original_deadline)
        self.assertGreater(state.access_expires_at, now + 1700)

    def test_refresh_request_shape(self):
        self.seed_state()
        transport = RecordingTransport(token_response())
        self.mgr.refresh_access_token("KEY", "SECRET", transport)
        call = transport.calls[0]
        self.assertEqual(call["url"], SCHWAB_TOKEN_URL)
        self.assertEqual(call["payload"], {
            "grant_type": "refresh_token",
            "refresh_token": "REFRESH_SEED",
        })
        self.assertEqual(call["headers"]["Authorization"], "Basic S0VZOlNFQ1JFVA==")

    def test_rotated_refresh_token_is_stored(self):
        self.seed_state()
        transport = RecordingTransport(token_response(refresh_token="REFRESH_ROTATED"))
        state = self.mgr.refresh_access_token("KEY", "SECRET", transport)
        self.assertEqual(state.refresh_token, "REFRESH_ROTATED")
        self.assertEqual(read_token_file(self.token_file)["refresh_token"], "REFRESH_ROTATED")

    def test_absent_refresh_token_in_response_keeps_the_existing_one(self):
        self.seed_state()
        resp = token_response()
        del resp["refresh_token"]
        transport = RecordingTransport(resp)
        state = self.mgr.refresh_access_token("KEY", "SECRET", transport)
        self.assertEqual(state.refresh_token, "REFRESH_SEED")

    def test_expired_window_raises_before_any_network_call(self):
        self.seed_state(refresh_expires_at=time.time() - 1)
        transport = RecordingTransport(token_response())
        with self.assertRaises(SchwabRefreshTokenExpiredError):
            self.mgr.refresh_access_token("KEY", "SECRET", transport)
        self.assertEqual(transport.calls, [])

    def test_invalid_client_classified_as_expired_refresh_token(self):
        """Classify on the OAuth error field, not by substring-matching a message."""
        self.seed_state()
        transport = RecordingTransport({"error": "invalid_client"})
        with self.assertRaises(SchwabRefreshTokenExpiredError):
            self.mgr.refresh_access_token("KEY", "SECRET", transport)

    def test_refresh_without_stored_state_raises_expired(self):
        transport = RecordingTransport(token_response())
        with self.assertRaises(SchwabRefreshTokenExpiredError):
            self.mgr.refresh_access_token("KEY", "SECRET", transport)
        self.assertEqual(transport.calls, [])

    def test_transport_failure_during_refresh_keeps_old_state(self):
        seeded = self.seed_state()
        transport = RecordingTransport(ConnectionResetError("peer reset"))
        with self.assertRaises(SchwabAmbiguousTokenError):
            self.mgr.refresh_access_token("KEY", "SECRET", transport)
        self.assertIs(self.mgr.state, seeded)


class TestLifetimeInspection(SchwabTestBase):

    def test_access_expiry_respects_the_buffer(self):
        now = 1_000_000.0
        self.seed_state(access_expires_at=now + 301)
        self.assertFalse(self.mgr.is_access_token_expiring(now=now))
        self.seed_state(access_expires_at=now + 300)
        self.assertTrue(self.mgr.is_access_token_expiring(now=now))

    def test_no_state_counts_as_expiring(self):
        self.assertTrue(self.mgr.is_access_token_expiring())
        self.assertTrue(self.mgr.is_refresh_token_expiring_soon())
        self.assertTrue(self.mgr.is_refresh_token_expired())

    def test_refresh_warning_boundary(self):
        now = 1_000_000.0
        self.seed_state(refresh_expires_at=now + 86401)
        self.assertFalse(self.mgr.is_refresh_token_expiring_soon(now=now))
        self.seed_state(refresh_expires_at=now + 86400)
        self.assertTrue(self.mgr.is_refresh_token_expiring_soon(now=now))
        # Twelve hours out: inside the window, but not yet expired.
        self.seed_state(refresh_expires_at=now + 43200)
        self.assertTrue(self.mgr.is_refresh_token_expiring_soon(now=now))
        self.assertFalse(self.mgr.is_refresh_token_expired(now=now))

    def test_bearer_header_blocked_while_token_is_stale(self):
        now = time.time()
        self.seed_state(access_expires_at=now + 60)
        with self.assertRaises(SchwabAuthError):
            self.mgr.get_bearer_header()
        self.seed_state(access_expires_at=now + 1800)
        self.assertEqual(
            self.mgr.get_bearer_header(), {"Authorization": "Bearer ACCESS_SEED"}
        )

    def test_negative_buffers_rejected(self):
        with self.assertRaises(SchwabAuthError):
            SchwabOAuthManager(token_file_path=self.token_file, access_buffer_seconds=-1)


class TestPersistence(SchwabTestBase):

    def test_state_round_trips_through_a_new_manager(self):
        transport = RecordingTransport(token_response())
        self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)
        reloaded = SchwabOAuthManager(token_file_path=self.token_file)
        self.assertIsNotNone(reloaded.state)
        self.assertEqual(reloaded.state.access_token, "ACCESS_ABC")
        self.assertEqual(reloaded.state.refresh_token, "REFRESH_XYZ")

    def test_no_temp_files_left_behind(self):
        transport = RecordingTransport(token_response())
        self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)
        leftovers = [f for f in os.listdir(self.temp_dir.name) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    @unittest.skipUnless(os.name == "posix", "POSIX file modes only")
    def test_token_file_is_owner_only(self):
        transport = RecordingTransport(token_response())
        self.mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)
        mode = stat.S_IMODE(os.stat(self.token_file).st_mode)
        self.assertEqual(mode & (stat.S_IRWXG | stat.S_IRWXO), 0)

    def test_corrupt_token_file_does_not_crash_startup(self):
        with open(self.token_file, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        mgr = SchwabOAuthManager(token_file_path=self.token_file)
        self.assertIsNone(mgr.state)
        # The unreadable file is preserved for the operator, not silently removed.
        self.assertTrue(os.path.exists(self.token_file))

    def test_wrong_typed_token_file_does_not_crash_startup(self):
        with open(self.token_file, "w", encoding="utf-8") as handle:
            json.dump({"access_token": 42, "refresh_token": None}, handle)
        self.assertIsNone(SchwabOAuthManager(token_file_path=self.token_file).state)

    def test_persistence_failure_raises_but_keeps_usable_state(self):
        """A swallowed write makes the operator believe tokens survive a restart."""
        unwritable = os.path.join(self.temp_dir.name, "no_such_dir", "tokens.json")
        mgr = SchwabOAuthManager(token_file_path=unwritable)
        transport = RecordingTransport(token_response())
        with self.assertRaises(SchwabTokenPersistenceError):
            mgr.exchange_code("KEY", "SECRET", "CODE@", REDIRECT_URI, None, transport)
        self.assertIsNotNone(mgr.state)
        self.assertEqual(mgr.state.access_token, "ACCESS_ABC")


class TestSecretHygiene(SchwabTestBase):

    def test_repr_omits_tokens(self):
        state = self.seed_state()
        text = repr(state)
        self.assertNotIn("ACCESS_SEED", text)
        self.assertNotIn("REFRESH_SEED", text)
        self.assertIn("access_expires_at", text)

    def test_to_dict_still_carries_tokens_for_persistence(self):
        state = self.seed_state()
        self.assertEqual(state.to_dict()["access_token"], "ACCESS_SEED")


class TestPKCEHelper(unittest.TestCase):

    def test_verifier_length_and_alphabet(self):
        verifier = SchwabPKCEGenerator.generate_verifier(64)
        self.assertEqual(len(verifier), 64)
        allowed = set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
        )
        self.assertTrue(set(verifier) <= allowed)

    def test_verifier_length_bounds_enforced(self):
        """RFC 7636 s4.1 fixes the range at 43-128 characters."""
        for bad in (0, 42, 129, -1):
            with self.subTest(length=bad):
                with self.assertRaises(SchwabAuthError):
                    SchwabPKCEGenerator.generate_verifier(bad)
        self.assertEqual(len(SchwabPKCEGenerator.generate_verifier(43)), 43)
        self.assertEqual(len(SchwabPKCEGenerator.generate_verifier(128)), 128)

    def test_challenge_matches_rfc_7636_appendix_b_vector(self):
        """RFC 7636 Appendix B publishes this verifier/challenge pair."""
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        self.assertEqual(
            SchwabPKCEGenerator.derive_challenge(verifier),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        )

    def test_challenge_is_unpadded_and_deterministic(self):
        verifier = SchwabPKCEGenerator.generate_verifier(64)
        challenge = SchwabPKCEGenerator.derive_challenge(verifier)
        self.assertNotIn("=", challenge)
        self.assertEqual(len(challenge), 43)  # 32-byte digest, unpadded base64url
        self.assertEqual(challenge, SchwabPKCEGenerator.derive_challenge(verifier))

    def test_verifiers_are_unique(self):
        self.assertEqual(len({SchwabPKCEGenerator.generate_verifier(64) for _ in range(50)}), 50)

    def test_out_of_range_verifier_rejected_by_derive(self):
        with self.assertRaises(SchwabAuthError):
            SchwabPKCEGenerator.derive_challenge("tooshort")


if __name__ == "__main__":
    unittest.main()
