"""
Unit tests for headless-broker-auth-patterns skill.

Tests:
1. TOTP safe window generation.
2. SHA-256 checksum calculator (Fyers -- app_id+secret_key only, no auth_code -- & Zerodha).
3. HeadlessBrowserContext driver cleanup guarantees.
4. TokenCacheManager date-keyed persistence and restrictive file permissions.
5. REST Archetype A login workflow simulation.
6. Browser Archetype B login redirect token extraction simulation (WebDriverWait-based).
7. get_valid_session orchestration: cache hit + probe pass, cache hit + probe fail
   triggers re-login, cache miss triggers login.
"""
import os
import stat
import pyotp
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
from auth_probe import (
    AuthArchetype,
    ChecksumHelper,
    HeadlessBrowserContext,
    TOTPHelper,
    TokenCacheManager,
    browser_login,
    get_valid_session,
    rest_login,
)


class TestHeadlessBrokerAuthPatterns(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.totp_secret = pyotp.random_base32()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_totp_helper_safe_generation(self):
        code = TOTPHelper.get_totp_safe(self.totp_secret, min_remaining_sec=1.0)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

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
        cache_path = cache_mgr._get_cache_path(broker)
        mode = stat.S_IMODE(os.stat(cache_path).st_mode)
        self.assertEqual(mode, 0o600, f"Expected 0600 permissions, got {oct(mode)}")

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
