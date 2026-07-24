"""
Unit tests for token-lifecycle-live-probing skill.

Tests:
1. 3-outcome probe response classification (VALID, INVALID, AMBIGUOUS).
2. Probe retry with exponential backoff on AMBIGUOUS outcomes.
3. Automatic re-authentication trigger on INVALID (401/403) token probe.
4. Empirical lifespan recording.
5. Backward compatibility with classify_probe_response and probe_with_backoff.
"""
import unittest
from unittest.mock import Mock
from token_probe import (
    AMBIGUOUS,
    INVALID,
    VALID,
    LiveTokenProbeManager,
    classify_probe_response,
    probe_with_backoff,
)


class TestTokenLifecycleLiveProbing(unittest.TestCase):

    def setUp(self):
        self.mgr = LiveTokenProbeManager()

    def test_classify_probe_response(self):
        self.assertEqual(classify_probe_response(200, False), VALID)
        self.assertEqual(classify_probe_response(401, False), INVALID)
        self.assertEqual(classify_probe_response(403, False), INVALID)
        self.assertEqual(classify_probe_response(502, False), AMBIGUOUS)
        self.assertEqual(classify_probe_response(None, True), AMBIGUOUS)

    def test_probe_backoff_retries_ambiguous(self):
        attempts = 0

        def flaky_probe():
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                return 503, False  # AMBIGUOUS
            return 200, False  # VALID

        res = probe_with_backoff(flaky_probe, max_attempts=3, base_delay=0.01)
        self.assertEqual(res, VALID)
        self.assertEqual(attempts, 2)

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

    def test_empirical_lifespan_recording(self):
        self.mgr.record_lifespan("fyers", 1000.0, 4600.0)
        self.assertIn("fyers", self.mgr.empirical_lifespans)
        self.assertEqual(self.mgr.empirical_lifespans["fyers"][0], 3600.0)


if __name__ == "__main__":
    unittest.main()
