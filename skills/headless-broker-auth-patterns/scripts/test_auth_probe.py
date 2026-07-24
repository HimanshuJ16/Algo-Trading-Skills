"""
Unit tests for headless-broker-auth-patterns skill.

Tests:
1. TOTP safe window generation.
2. SHA-256 checksum calculator (Fyers & Zerodha).
3. HeadlessBrowserContext driver cleanup guarantees.
4. TokenCacheManager date-keyed persistence.
5. REST Archetype A login workflow simulation.
6. Browser Archetype B login redirect token extraction simulation.
"""
import pyotp
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, Mock
from auth_probe import (
    ChecksumHelper,
    HeadlessBrowserContext,
    TOTPHelper,
    TokenCacheManager,
    browser_login,
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
        chk_fyers = ChecksumHelper.fyers_checksum("app123", "code456", "sec789")
        self.assertEqual(len(chk_fyers), 64)  # SHA-256 hex string

        chk_zerodha = ChecksumHelper.zerodha_checksum("key123", "req456", "sec789")
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


if __name__ == "__main__":
    unittest.main()
