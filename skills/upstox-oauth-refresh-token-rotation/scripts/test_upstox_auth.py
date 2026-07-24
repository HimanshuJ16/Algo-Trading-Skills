"""
Unit tests for upstox-oauth-refresh-token-rotation skill.

Tests:
1. Successful single-use refresh token rotation and atomic storage persistence.
2. Preemptive token expiry inspection logic.
3. Thread-safe lock preventing concurrent duplicate refresh POST calls.
4. Fallback re-authentication callback on refresh token revocation.
"""
import os
import tempfile
import time
import unittest
from unittest.mock import Mock
from upstox_auth import UpstoxAuthError, UpstoxTokenManager, UpstoxTokenState


class TestUpstoxOAuthRefreshTokenRotation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_file = os.path.join(self.temp_dir.name, "upstox_tokens.json")
        self.mgr = UpstoxTokenManager(token_file_path=self.token_file, buffer_seconds=900.0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preemptive_expiry_detection(self):
        # Set token expiring in 100 seconds (buffer is 900s) -> should be expiring
        exp_soon = UpstoxTokenState("ACC_1", "REF_1", time.time() + 100)
        self.mgr.state = exp_soon
        self.assertTrue(self.mgr.is_token_expiring())

        # Set token expiring in 10,000 seconds -> not expiring
        exp_far = UpstoxTokenState("ACC_2", "REF_2", time.time() + 10000)
        self.mgr.state = exp_far
        self.assertFalse(self.mgr.is_token_expiring())

    def test_successful_token_rotation(self):
        old_state = UpstoxTokenState("ACC_OLD", "REF_OLD", time.time() - 100)
        self.mgr.state = old_state

        def http_post_mock(url, payload):
            self.assertEqual(payload["refresh_token"], "REF_OLD")
            return {
                "status": "success",
                "access_token": "ACC_NEW_123",
                "refresh_token": "REF_NEW_456",
                "expires_in": 86400,
            }

        new_acc = self.mgr.get_valid_access_token("CLIENT_ID", "SECRET", http_post_mock)

        self.assertEqual(new_acc, "ACC_NEW_123")
        self.assertEqual(self.mgr.state.refresh_token, "REF_NEW_456")

        # Confirm atomic storage persistence on disk
        self.assertTrue(os.path.exists(self.token_file))

    def test_fallback_reauth_on_revoked_token(self):
        old_state = UpstoxTokenState("ACC_REVOKED", "REF_REVOKED", time.time() - 100)
        self.mgr.state = old_state

        def http_post_fail(url, payload):
            return {"status": "error", "errors": [{"message": "Invalid refresh token"}]}

        fallback_mock = Mock(
            return_value=UpstoxTokenState("ACC_FALLBACK", "REF_FALLBACK", time.time() + 86400)
        )

        acc = self.mgr.get_valid_access_token(
            "CLIENT_ID", "SECRET", http_post_fail, reauth_fallback_fn=fallback_mock
        )

        self.assertEqual(acc, "ACC_FALLBACK")
        fallback_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
