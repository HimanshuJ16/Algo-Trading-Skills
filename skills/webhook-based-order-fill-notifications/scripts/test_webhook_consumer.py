"""
Unit tests for webhook-based-order-fill-notifications skill.

Tests:
1. Valid HMAC-SHA256 signature verification and payload processing.
2. Tampered signature rejection.
3. At-least-once duplicate webhook delivery skipping.
4. Timestamp drift rejection (replay defense).
"""
import json
import time
import unittest
from webhook_consumer import WebhookConsumerManager, WebhookIngestionResult


class TestWebhookConsumerManager(unittest.TestCase):

    def setUp(self):
        self.mgr = WebhookConsumerManager(max_drift_seconds=300.0)
        self.secret = "WEBHOOK_SECRET_999"

    def test_valid_webhook_processing(self):
        payload_dict = {
            "order_id": "ORD_100",
            "exec_id": "EXEC_500",
            "filled_qty": 50.0,
            "timestamp": time.time(),
            "sequence_num": 1,
        }
        body_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = WebhookConsumerManager.compute_hmac_signature(body_bytes, self.secret)

        res = self.mgr.process_webhook(body_bytes, f"sha256={sig}", self.secret)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.reason, "VERIFIED_INGESTED")
        self.assertEqual(res.order_id, "ORD_100")
        self.assertEqual(res.exec_id, "EXEC_500")

    def test_tampered_signature_rejection(self):
        payload_dict = {"order_id": "ORD_101", "exec_id": "EXEC_501", "filled_qty": 10.0}
        body_bytes = json.dumps(payload_dict).encode("utf-8")

        res = self.mgr.process_webhook(body_bytes, "sha256=BAD_SIGNATURE", self.secret)
        self.assertEqual(res.status, "REJECTED")
        self.assertEqual(res.reason, "INVALID_SIGNATURE")

    def test_duplicate_webhook_skipped(self):
        payload_dict = {
            "order_id": "ORD_102",
            "exec_id": "EXEC_502",
            "filled_qty": 20.0,
            "timestamp": time.time(),
        }
        body_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = WebhookConsumerManager.compute_hmac_signature(body_bytes, self.secret)

        # First delivery -> INGESTED
        res1 = self.mgr.process_webhook(body_bytes, sig, self.secret)
        self.assertEqual(res1.reason, "VERIFIED_INGESTED")

        # Second delivery (retry) -> SKIPPED
        res2 = self.mgr.process_webhook(body_bytes, sig, self.secret)
        self.assertEqual(res2.reason, "DUPLICATE_SKIPPED")

    def test_timestamp_drift_rejection(self):
        stale_ts = time.time() - 3600  # 1 hour ago
        payload_dict = {
            "order_id": "ORD_103",
            "exec_id": "EXEC_503",
            "filled_qty": 15.0,
            "timestamp": stale_ts,
        }
        body_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = WebhookConsumerManager.compute_hmac_signature(body_bytes, self.secret)

        res = self.mgr.process_webhook(body_bytes, sig, self.secret)
        self.assertEqual(res.status, "REJECTED")
        self.assertEqual(res.reason, "TIMESTAMP_DRIFT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
