"""
Unit tests for headless-broker-auth-patterns skill.

Tests:
1. TOTP safe window generation and argument validation.
2. SHA-256 checksum calculator (Fyers -- app_id+secret_key only, no auth_code -- & Zerodha).
3. HeadlessBrowserContext driver cleanup guarantees.
4. TokenCacheManager session-date keying (tz + rollover hour), restrictive file and
   directory permissions, and stale-token purging.
5. REST Archetype A login workflow simulation, including HTTP-200-with-error-body.
6. Browser Archetype B redirect token extraction -- including the ICICI Breeze
   ``API_Session`` parameter, which the previous string-slicing implementation could not
   extract at all (regression test).
7. Archetype C: documented Fyers refresh-token exchange.
8. get_valid_session orchestration: cache hit + probe pass, cache hit + probe fail
   triggers re-login, cache miss triggers login.
"""
import datetime
import os
import stat
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

try:
    import pyotp
except ImportError:
    pyotp = None

from auth_probe import (
    AuthArchetype,
    BrokerAuthError,
    ChecksumHelper,
    HeadlessBrowserContext,
    TOTPHelper,
    TokenCacheManager,
    browser_login,
    extract_session_token,
    fyers_refresh_token_login,
    get_valid_session,
    rest_login,
)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


class _FakeWait:
    """Minimal WebDriverWait stand-in that *actually invokes* the predicate.

    The pre-existing Archetype B test patches WebDriverWait wholesale with a mock whose
    ``until`` returns canned values, so the real redirect-detection predicate is never
    executed. This fake runs it against a real driver double, which is what lets the
    Breeze regression test below fail against the old substring logic.
    """

    def __init__(self, driver, timeout):
        self.driver = driver
        self.timeout = timeout

    def until(self, method):
        result = method(self.driver)
        if not result:
            raise AssertionError(f"predicate never satisfied: {method}")
        return result


class TestHeadlessBrokerAuthPatterns(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.totp_secret = pyotp.random_base32() if pyotp is not None else "JBSWY3DPEHPK3PXP"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_totp_helper_safe_generation(self):
        code = TOTPHelper.get_totp_safe(self.totp_secret, min_remaining_sec=1.0)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_totp_helper_rejects_unsatisfiable_window(self):
        """A min_remaining_sec >= the 30s time step can never be satisfied; sleeping and
        then returning a code that still violates the caller's requirement is worse than
        refusing the argument."""
        with self.assertRaises(ValueError):
            TOTPHelper.get_totp_safe(self.totp_secret, min_remaining_sec=30.0)
        with self.assertRaises(ValueError):
            TOTPHelper.get_totp_safe(self.totp_secret, min_remaining_sec=-1.0)
        with self.assertRaises(ValueError):
            TOTPHelper.get_totp_safe("   ", min_remaining_sec=1.0)

    def test_checksum_helper(self):
        # Fyers appIdHash = sha256(app_id:secret_key) -- auth_code is NOT part of the
        # hash (verified against Fyers' -371 error resolution and community-confirmed
        # working implementations). This is a regression test for that exact bug.
        chk_fyers = ChecksumHelper.fyers_checksum("app123", "sec789")
        expected_fyers = __import__("hashlib").sha256(b"app123:sec789").hexdigest()
        self.assertEqual(chk_fyers, expected_fyers)
        self.assertEqual(len(chk_fyers), 64)  # SHA-256 hex string

        chk_zerodha = ChecksumHelper.zerodha_checksum("key123", "req456", "sec789")
        expected_zerodha = __import__("hashlib").sha256(b"key123req456sec789").hexdigest()
        self.assertEqual(chk_zerodha, expected_zerodha)
        self.assertEqual(len(chk_zerodha), 64)

    def test_headless_browser_context_cleanup(self):
        mock_driver = Mock()
        driver_factory = Mock(return_value=mock_driver)

        with HeadlessBrowserContext(driver_factory) as driver:
            self.assertEqual(driver, mock_driver)

        # Confirm quit was called on context exit
        mock_driver.quit.assert_called_once()

    def test_token_cache_manager(self):
        cache_mgr = TokenCacheManager(cache_dir=self.temp_dir)
        broker = "fyers_test"

        self.assertIsNone(cache_mgr.get_cached_token(broker))

        cache_mgr.save_token(broker, "access_token_12345")
        cached = cache_mgr.get_cached_token(broker)
        self.assertEqual(cached, "access_token_12345")

        # Cache file must not be world/group readable -- it holds a plaintext bearer token.
        if os.name != "nt":
            cache_path = cache_mgr._get_cache_path(broker)
            mode = stat.S_IMODE(os.stat(cache_path).st_mode)
            self.assertEqual(mode, 0o600, f"Expected 0600 permissions, got {oct(mode)}")

    def test_cache_dir_is_owner_only(self):
        """The files are 0600, but a world-traversable cache dir still discloses which
        brokers this host authenticates to, and on which session dates."""
        TokenCacheManager(cache_dir=os.path.join(self.temp_dir, "nested_cache"))
        if os.name != "nt":
            mode = stat.S_IMODE(os.stat(os.path.join(self.temp_dir, "nested_cache")).st_mode)
            self.assertEqual(mode, 0o700, f"Expected 0700 on cache dir, got {oct(mode)}")

    def test_session_date_uses_broker_rollover_not_local_midnight(self):
        """Kite Connect access tokens expire at 06:00 IST the next day, so 01:30 IST still
        belongs to the *previous* session. Expected values are derived from that documented
        boundary, not from re-running the implementation's own arithmetic."""
        cache_mgr = TokenCacheManager(
            cache_dir=self.temp_dir, session_tz=IST, rollover_hour=6
        )

        # 01:30 IST on the 25th -- before the 06:00 flush, so still the 24th's session.
        before_flush = datetime.datetime(2026, 8, 25, 1, 30, tzinfo=IST)
        self.assertEqual(cache_mgr.session_date(before_flush), datetime.date(2026, 8, 24))

        # 06:30 IST on the 25th -- after the flush, a new session.
        after_flush = datetime.datetime(2026, 8, 25, 6, 30, tzinfo=IST)
        self.assertEqual(cache_mgr.session_date(after_flush), datetime.date(2026, 8, 25))

        # Exactly 06:00 is the boundary and belongs to the new session.
        at_flush = datetime.datetime(2026, 8, 25, 6, 0, tzinfo=IST)
        self.assertEqual(cache_mgr.session_date(at_flush), datetime.date(2026, 8, 25))

    def test_session_date_converts_foreign_timezone_instant(self):
        """A UTC-hosted bot must key on the broker's zone. 20:00 UTC on the 24th is
        01:30 IST on the 25th, which (rollover 06:00) is still the 24th's session --
        whereas naive local-date keying would have called it the 24th only by accident of
        the host's zone, and would have said the 25th for a host in IST."""
        cache_mgr = TokenCacheManager(
            cache_dir=self.temp_dir, session_tz=IST, rollover_hour=6
        )
        utc_instant = datetime.datetime(2026, 8, 24, 20, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(cache_mgr.session_date(utc_instant), datetime.date(2026, 8, 24))

    def test_rollover_hour_is_validated(self):
        with self.assertRaises(ValueError):
            TokenCacheManager(cache_dir=self.temp_dir, rollover_hour=24)

    def test_purge_stale_removes_previous_session_tokens(self):
        """Yesterday's file holds a plaintext bearer token the broker has already
        invalidated (NSE/INVG/67858 A.8 mandates a daily logout), so it is leak surface
        with no operational value."""
        cache_mgr = TokenCacheManager(cache_dir=self.temp_dir)
        stale_date = cache_mgr.session_date() - datetime.timedelta(days=3)
        stale_path = os.path.join(self.temp_dir, f"fyers_test_{stale_date.isoformat()}.json")
        with open(stale_path, "w", encoding="utf-8") as f:
            f.write('{"access_token": "yesterdays_token"}')
        unrelated = os.path.join(self.temp_dir, "notes.txt")
        with open(unrelated, "w", encoding="utf-8") as f:
            f.write("leave me alone")

        cache_mgr.save_token("fyers_test", "todays_token")

        self.assertFalse(os.path.exists(stale_path), "stale token file was not purged")
        self.assertTrue(os.path.exists(unrelated), "purge must not touch foreign files")
        self.assertEqual(cache_mgr.get_cached_token("fyers_test"), "todays_token")

    def test_save_token_is_atomic_and_leaves_no_temp_file(self):
        """Two instances starting together (multi-account fan-out, systemd restart) must
        never let a reader observe a half-written cache file and conclude the token is
        corrupt -- that would trigger a needless re-login."""
        cache_mgr = TokenCacheManager(cache_dir=self.temp_dir)
        cache_mgr.save_token("fyers_test", "tok_one")
        cache_mgr.save_token("fyers_test", "tok_two")

        self.assertEqual(cache_mgr.get_cached_token("fyers_test"), "tok_two")
        leftovers = [f for f in os.listdir(self.temp_dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"temp files left behind: {leftovers}")

    def test_save_token_refuses_empty_token(self):
        cache_mgr = TokenCacheManager(cache_dir=self.temp_dir)
        with self.assertRaises(ValueError):
            cache_mgr.save_token("fyers_test", "")

    def test_token_cache_manager_metadata_roundtrip(self):
        cache_mgr = TokenCacheManager(cache_dir=self.temp_dir)
        cache_mgr.save_token("fyers_test", "tok_abc", metadata={"archetype": "ARCHETYPE_A_REST"})
        # get_cached_token only returns the token by design (callers shouldn't need to
        # parse the cache file directly), so verify metadata via the raw file.
        import json
        path = cache_mgr._get_cache_path("fyers_test")
        with open(path) as f:
            payload = json.load(f)
        self.assertEqual(payload["metadata"]["archetype"], "ARCHETYPE_A_REST")

    def test_rest_login_archetype_a(self):
        mock_session = Mock()
        resp1 = Mock()
        resp1.json.return_value = {"auth_code": "code_xyz"}
        resp1.raise_for_status.return_value = None

        resp2 = Mock()
        resp2.json.return_value = {"access_token": "token_abc"}
        resp2.raise_for_status.return_value = None

        mock_session.post.side_effect = [resp1, resp2]

        token = rest_login("https://api.fyers.in", "client_1", "secret_1", self.totp_secret, mock_session)
        self.assertEqual(token, "token_abc")

    def test_rest_login_sends_auth_code_separately_not_in_checksum(self):
        """Regression test for the auth_code-in-checksum bug: the checksum sent to the
        broker must depend only on client_id+secret, and auth_code must appear as its
        own field, never concatenated into the checksum payload's `checksum` value."""
        mock_session = Mock()
        resp1 = Mock()
        resp1.json.return_value = {"auth_code": "code_xyz"}
        resp1.raise_for_status.return_value = None
        resp2 = Mock()
        resp2.json.return_value = {"access_token": "token_abc"}
        resp2.raise_for_status.return_value = None
        mock_session.post.side_effect = [resp1, resp2]

        rest_login("https://api.fyers.in", "client_1", "secret_1", self.totp_secret, mock_session)

        second_call_kwargs = mock_session.post.call_args_list[1].kwargs
        sent_payload = second_call_kwargs["json"]
        expected_checksum = ChecksumHelper.fyers_checksum("client_1", "secret_1")
        self.assertEqual(sent_payload["checksum"], expected_checksum)
        self.assertEqual(sent_payload["code"], "code_xyz")
        self.assertNotIn("secret", sent_payload)  # secret must not be sent in plaintext alongside the hash

    def test_rest_login_raises_diagnosable_error_on_http_200_error_body(self):
        """Fyers returns HTTP 200 with {"s":"error","code":-371,...}. raise_for_status()
        passes, so without explicit unwrapping the caller sees KeyError('access_token')
        and has no pointer back to the checksum -- the exact misdiagnosis this skill's
        references warn about."""
        mock_session = Mock()
        resp1 = Mock()
        resp1.json.return_value = {"auth_code": "code_xyz"}
        resp1.raise_for_status.return_value = None
        resp2 = Mock()
        resp2.json.return_value = {
            "s": "error",
            "code": -371,
            "message": "Please provide sha256 hash of appId and app secret",
        }
        resp2.raise_for_status.return_value = None
        mock_session.post.side_effect = [resp1, resp2]

        with self.assertRaises(BrokerAuthError) as ctx:
            rest_login("https://api.fyers.in", "client_1", "secret_1", self.totp_secret, mock_session)

        message = str(ctx.exception)
        self.assertIn("-371", message)
        self.assertIn("sha256 hash of appId", message)

    def test_extract_session_token_handles_breeze_api_session_param(self):
        """REGRESSION: ICICI Breeze -- the skill's flagship Archetype B broker -- returns
        the token as `API_Session`. The previous implementation waited on the substrings
        'session_token=' / 'api_session=' (case-sensitive, so no match) and, if reached,
        sliced on 'session_token=' and returned the entire URL prefix as the 'token'."""
        url = "https://mybot.example/callback?API_Session=51234567&status=success"

        # What the old string-slicing did, asserted explicitly so this test documents the
        # defect rather than merely avoiding it.
        old_behaviour = url.split("session_token=")[-1].split("&")[0]
        self.assertEqual(old_behaviour, "https://mybot.example/callback?API_Session=51234567")

        self.assertEqual(extract_session_token(url), "51234567")

    def test_extract_session_token_variants_and_failures(self):
        self.assertEqual(
            extract_session_token("https://x/cb?session_token=abc&s=1"), "abc"
        )
        self.assertEqual(extract_session_token("https://x/cb?apisession=abc"), "abc")
        # URL-encoded values must be decoded, not handed back raw.
        self.assertEqual(extract_session_token("https://x/cb?session_token=a%2Bb"), "a+b")
        # A present-but-empty parameter is a failure, not an empty token.
        with self.assertRaises(BrokerAuthError):
            extract_session_token("https://x/cb?session_token=")
        # A changed login page must fail loudly at the point of breakage.
        with self.assertRaises(BrokerAuthError):
            extract_session_token("https://x/cb?error=access_denied")
        with self.assertRaises(BrokerAuthError):
            extract_session_token("")

    def test_browser_login_extracts_breeze_api_session_end_to_end(self):
        """Same regression, driven through browser_login with a wait double that actually
        runs the redirect predicate (unlike the fully-mocked test below)."""
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mybot.example/cb?API_Session=51234567"
        driver_factory = Mock(return_value=mock_driver)

        with patch("auth_probe.WebDriverWait", _FakeWait), \
             patch("auth_probe.EC"), patch("auth_probe.By"):
            token = browser_login(
                "https://api.icicidirect.com/apiuser/login?api_key=k",
                "user1",
                "pass1",
                None,
                driver_factory,
            )

        self.assertEqual(token, "51234567")
        mock_driver.quit.assert_called_once()

    def test_browser_login_raises_when_redirect_never_carries_token(self):
        """A login page redesign must surface as a clear failure, never as a bogus token."""
        mock_driver = MagicMock()
        mock_driver.current_url = "https://mybot.example/cb?error=login_failed"
        driver_factory = Mock(return_value=mock_driver)

        with patch("auth_probe.WebDriverWait", _FakeWait), \
             patch("auth_probe.EC"), patch("auth_probe.By"):
            with self.assertRaises(AssertionError):  # _FakeWait's stand-in for TimeoutException
                browser_login("https://x/login", "u", "p", None, driver_factory)

        mock_driver.quit.assert_called_once()  # cleanup still guaranteed on the failure path

    def test_fyers_refresh_token_login_uses_documented_payload(self):
        """Archetype C: the documented, broker-supported unattended path. Verifies the
        grant_type/appIdHash/refresh_token/pin payload and that appIdHash is
        sha256('app_id:secret'), independently recomputed here."""
        mock_session = Mock()
        resp = Mock()
        resp.json.return_value = {"access_token": "fresh_access_tok"}
        resp.raise_for_status.return_value = None
        mock_session.post.return_value = resp

        token = fyers_refresh_token_login(
            refresh_token="refresh_abc",
            app_id="APPID-100",
            secret_key="sec789",
            pin="1234",
            session=mock_session,
        )

        self.assertEqual(token, "fresh_access_tok")
        url, kwargs = mock_session.post.call_args[0][0], mock_session.post.call_args.kwargs
        self.assertTrue(url.endswith("/validate-refresh-token"))
        payload = kwargs["json"]
        self.assertEqual(payload["grant_type"], "refresh_token")
        self.assertEqual(payload["refresh_token"], "refresh_abc")
        self.assertEqual(payload["pin"], "1234")
        expected_hash = __import__("hashlib").sha256(b"APPID-100:sec789").hexdigest()
        self.assertEqual(payload["appIdHash"], expected_hash)
        # The raw secret must never travel alongside its own hash.
        self.assertNotIn("sec789", str(payload))

    def test_fyers_refresh_token_login_validates_inputs(self):
        with self.assertRaises(ValueError):
            fyers_refresh_token_login("", "a", "b", "1234", Mock())
        with self.assertRaises(ValueError):
            fyers_refresh_token_login("tok", "a", "b", "", Mock())

    def test_browser_login_archetype_b(self):
        """Simulates the full Archetype B redirect-token-extraction flow with a mocked
        Selenium driver, using WebDriverWait rather than fixed sleeps."""
        mock_driver = MagicMock()
        mock_driver.current_url = "https://broker.example/callback?session_token=sess_abc123&state=xyz"
        driver_factory = Mock(return_value=mock_driver)

        with patch("auth_probe.WebDriverWait") as mock_wait_cls, \
             patch("auth_probe.EC"), patch("auth_probe.By"):
            mock_wait_instance = MagicMock()
            mock_wait_cls.return_value = mock_wait_instance
            # Each .until() call should return an element-like mock supporting send_keys/click,
            # except the final redirect-poll call which just needs to return truthy.
            element_mock = MagicMock()
            mock_wait_instance.until.side_effect = [
                element_mock,  # username field
                element_mock,  # password field
                element_mock,  # submit button
                element_mock,  # totp field
                element_mock,  # totp submit button
                True,          # redirect-url poll
            ]

            token = browser_login(
                "https://broker.example/login",
                "user1",
                "pass1",
                self.totp_secret,
                driver_factory,
            )

        self.assertEqual(token, "sess_abc123")
        mock_driver.quit.assert_called_once()  # cleanup guaranteed even on the happy path

    def test_browser_login_requires_selenium(self):
        """If selenium isn't installed, browser_login should fail fast with a clear
        error rather than an opaque AttributeError deep in the call stack."""
        with patch("auth_probe.WebDriverWait", None):
            with self.assertRaises(ImportError):
                browser_login("https://x", "u", "p", None, Mock())

    def test_get_valid_session_reuses_token_on_probe_pass(self):
        cache_mgr = TokenCacheManager(cache_dir=self.temp_dir)
        cache_mgr.save_token("fyers_test", "cached_tok")
        login_fn = Mock(return_value="should_not_be_called")
        probe_fn = Mock(return_value=True)

        token = get_valid_session(
            "fyers_test", AuthArchetype.ARCHETYPE_A_REST, login_fn, probe_fn, cache_mgr
        )

        self.assertEqual(token, "cached_tok")
        login_fn.assert_not_called()
        probe_fn.assert_called_once_with("cached_tok")

    def test_get_valid_session_relogins_on_probe_fail(self):
        cache_mgr = TokenCacheManager(cache_dir=self.temp_dir)
        cache_mgr.save_token("fyers_test", "stale_tok")
        login_fn = Mock(return_value="fresh_tok")
        probe_fn = Mock(return_value=False)

        token = get_valid_session(
            "fyers_test", AuthArchetype.ARCHETYPE_A_REST, login_fn, probe_fn, cache_mgr
        )

        self.assertEqual(token, "fresh_tok")
        login_fn.assert_called_once()
        self.assertEqual(cache_mgr.get_cached_token("fyers_test"), "fresh_tok")

    def test_get_valid_session_logs_in_on_cache_miss(self):
        cache_mgr = TokenCacheManager(cache_dir=self.temp_dir)
        login_fn = Mock(return_value="new_tok")
        probe_fn = Mock(return_value=True)  # should never be called -- nothing to probe

        token = get_valid_session(
            "fyers_test", AuthArchetype.ARCHETYPE_A_REST, login_fn, probe_fn, cache_mgr
        )

        self.assertEqual(token, "new_tok")
        login_fn.assert_called_once()
        probe_fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
