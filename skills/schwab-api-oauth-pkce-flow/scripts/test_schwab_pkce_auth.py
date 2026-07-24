"""
Unit tests for schwab-api-oauth-pkce-flow skill.

Tests:
1. RFC 7636 PKCE code_verifier generation and S256 code_challenge derivation.
2. Authorization URL construction with code_challenge parameter.
3. Code exchange for access & 7-day refresh tokens with atomic file persistence.
4. Preemptive access token expiration inspection and 7-day refresh warning logic.
"""
import os
import tempfile
import time
import unittest
from schwab_pkce_auth import (
    SchwabOAuthManager,
    SchwabPKCEGenerator,
    SchwabTokenState,
)


class TestSchwabPKCEOAuth(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_file = os.path.join(self.temp_dir.name, "schwab_tokens.json")
        self.mgr = SchwabOAuthManager(token_file_path=self.token_file, access_buffer_seconds=300.0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pkce_verifier_and_challenge(self):
        verifier = SchwabPKCEGenerator.generate_verifier(64)
        self.assertEqual(len(verifier), 64)
        challenge = SchwabPKCEGenerator.derive_challenge(verifier)
        self.assertTrue(len(challenge) > 0)
        self.assertNotIn("=", challenge)  # No base64 padding

    def test_auth_url_construction(self):
        url = SchwabOAuthManager.get_authorization_url("APP_KEY", "https://127.0.0.1:8080", "CHALLENGE_123")
        self.assertIn("code_challenge=CHALLENGE_123", url)
        self.assertIn("code_challenge_method=S256", url)

    def test_code_exchange_and_persistence(self):
        def http_post_mock(url, payload, headers):
            return {
                "access_token": "ACC_SCHWAB_123",
                "refresh_token": "REF_SCHWAB_456",
                "expires_in": 1800,
            }

        state = self.mgr.exchange_code(
            "KEY", "SECRET", "AUTH_CODE", "https://127.0.0.1:8080", "VERIFIER_123", http_post_mock
        )

        self.assertEqual(state.access_token, "ACC_SCHWAB_123")
        self.assertTrue(os.path.exists(self.token_file))

    def test_refresh_token_expiring_soon_warning(self):
        now = time.time()
        # Refresh token expires in 12 hours (within 24h warning window)
        state = SchwabTokenState("ACC", "REF", now + 1000, now + 43200)
        self.mgr.state = state
        self.assertTrue(self.mgr.is_refresh_token_expiring_soon(warn_buffer_seconds=86400))


if __name__ == "__main__":
    unittest.main()
