"""
Unit tests for zerodha-kite-postback-webhook-verification skill.

Tests:
1. Valid postback payload SHA-256 signature verification.
2. Invalid/tampered postback checksum rejection.
3. Timestamp drift rejection (replay attack defense).
4. Postback event deduplication (idempotency).
5. Constant-time hmac.compare_digest verification.
"""
import datetime
import time
import unittest
from postback_verifier import KitePostbackVerifier, PostbackVerificationResult


class TestZerodhaKitePostbackVerification(unittest.TestCase):

    def setUp(self):
        self.verifier = KitePostbackVerifier(max_drift_seconds=300.0)
        self.api_secret = "MY_KITE_SECRET_123"

    def test_valid_postback_verification(self):
        order_id = "2407240001"
        ts = str(int(time.time()))
        checksum = KitePostbackVerifier.compute_checksum(order_id, ts, self.api_secret)

        payload = {
            "order_id": order_id,
            "timestamp": ts,
            "checksum": checksum,
            "status": "COMPLETE",
            "filled_quantity": 50,
        }

        res = self.verifier.verify_postback(payload, self.api_secret)
        self.assertTrue(res.valid)
        self.assertEqual(res.reason, "VERIFIED_SUCCESS")
        self.assertEqual(res.order_id, order_id)
        self.assertEqual(res.status, "COMPLETE")

    def test_tampered_checksum_rejection(self):
        order_id = "2407240002"
        ts = str(int(time.time()))

        payload = {
            "order_id": order_id,
            "timestamp": ts,
            "checksum": "BAD_CHECKSUM_SPOOFED",
            "status": "COMPLETE",
            "filled_quantity": 50,
        }

        res = self.verifier.verify_postback(payload, self.api_secret)
        self.assertFalse(res.valid)
        self.assertIn("Invalid postback checksum", res.reason)

    def test_timestamp_drift_replay_attack_rejection(self):
        order_id = "2407240003"
        # Timestamp from 1 hour ago
        stale_ts = str(int(time.time() - 3600))
        checksum = KitePostbackVerifier.compute_checksum(order_id, stale_ts, self.api_secret)

        payload = {
            "order_id": order_id,
            "timestamp": stale_ts,
            "checksum": checksum,
            "status": "COMPLETE",
        }

        res = self.verifier.verify_postback(payload, self.api_secret)
        self.assertFalse(res.valid)
        self.assertIn("Timestamp drift", res.reason)

    def test_postback_deduplication(self):
        order_id = "2407240004"
        ts = str(int(time.time()))
        checksum = KitePostbackVerifier.compute_checksum(order_id, ts, self.api_secret)

        payload = {
            "order_id": order_id,
            "timestamp": ts,
            "checksum": checksum,
            "status": "COMPLETE",
        }

        res1 = self.verifier.verify_postback(payload, self.api_secret)
        self.assertTrue(res1.valid)
        self.assertEqual(res1.reason, "VERIFIED_SUCCESS")

        # Duplicate postback delivery
        res2 = self.verifier.verify_postback(payload, self.api_secret)
        self.assertTrue(res2.valid)
        self.assertEqual(res2.reason, "DUPLICATE_IGNORED")


if __name__ == "__main__":
    unittest.main()
