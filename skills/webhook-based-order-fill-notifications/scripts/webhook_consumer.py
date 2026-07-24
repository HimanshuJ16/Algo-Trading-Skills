"""
webhook-based-order-fill-notifications: Production-grade broker webhook consumer,
HMAC-SHA256 signature verifier, at-least-once delivery deduplicator, and sequence order manager.
"""
from dataclasses import dataclass
import datetime
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class WebhookError(ValueError):
    """Raised when webhook signature, timestamp, or payload fails verification."""
    pass


@dataclass
class WebhookIngestionResult:
    status: str
    reason: str
    order_id: Optional[str] = None
    exec_id: Optional[str] = None
    filled_quantity: float = 0.0


class WebhookConsumerManager:
    """
    Manages broker fill webhook ingestion, HMAC-SHA256 signature verification,
    replay defense, deduplication, and sequence tracking.
    """

    def __init__(self, max_drift_seconds: float = 300.0):
        self.max_drift_seconds = max_drift_seconds
        self.processed_executions: Set[str] = set()
        self.order_sequence_tracker: Dict[str, int] = {}

    @staticmethod
    def compute_hmac_signature(raw_body: bytes, secret: str) -> str:
        """Computes HMAC-SHA256 hex digest over raw body."""
        return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    def verify_signature(self, raw_body: bytes, signature: str, secret: str) -> bool:
        """Constant-time HMAC-SHA256 signature verification."""
        if not signature or not secret:
            return False
        clean_sig = signature.replace("sha256=", "").strip().lower()
        expected_sig = self.compute_hmac_signature(raw_body, secret).lower()
        return hmac.compare_digest(expected_sig, clean_sig)

    def verify_timestamp(self, ts_val: Any) -> bool:
        """Verifies timestamp freshness within max_drift_seconds."""
        try:
            if isinstance(ts_val, (int, float)):
                ts = float(ts_val)
            elif isinstance(ts_val, str) and ts_val.isdigit():
                ts = float(ts_val)
            elif isinstance(ts_val, str) and "T" in ts_val:
                dt = datetime.datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                ts = dt.timestamp()
            else:
                return False

            return abs(time.time() - ts) <= self.max_drift_seconds
        except Exception:
            return False

    def is_duplicate(self, order_id: str, exec_id: str) -> bool:
        sig = f"{order_id}:{exec_id}"
        if sig in self.processed_executions:
            return True
        self.processed_executions.add(sig)
        return False

    def process_webhook(
        self, raw_body: bytes, signature_header: str, secret: str
    ) -> WebhookIngestionResult:
        """
        Ingests and validates incoming broker fill webhook.
        """
        # 1. HMAC-SHA256 Signature Verification
        if not self.verify_signature(raw_body, signature_header, secret):
            logger.critical("INVALID WEBHOOK SIGNATURE! Rejected unauthenticated request.")
            return WebhookIngestionResult(status="REJECTED", reason="INVALID_SIGNATURE")

        # 2. JSON Decoding
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            return WebhookIngestionResult(status="REJECTED", reason=f"INVALID_JSON: {e}")

        order_id = str(payload.get("order_id", payload.get("orderId", "")))
        exec_id = str(payload.get("exec_id", payload.get("executionId", payload.get("tradeId", ""))))
        filled_qty = float(payload.get("filled_qty", payload.get("quantity", 0.0)))
        ts_val = payload.get("timestamp", payload.get("time", time.time()))
        seq_num = int(payload.get("sequence_num", payload.get("seq", 0)))

        if not order_id or not exec_id:
            return WebhookIngestionResult(status="REJECTED", reason="MISSING_ORDER_OR_EXEC_ID")

        # 3. Timestamp Replay Defense
        if not self.verify_timestamp(ts_val):
            return WebhookIngestionResult(
                status="REJECTED", reason="TIMESTAMP_DRIFT_EXCEEDED", order_id=order_id, exec_id=exec_id
            )

        # 4. At-Least-Once Deduplication
        if self.is_duplicate(order_id, exec_id):
            logger.info(f"Duplicate webhook execution '{exec_id}' for order '{order_id}' skipped.")
            return WebhookIngestionResult(
                status="SUCCESS", reason="DUPLICATE_SKIPPED", order_id=order_id, exec_id=exec_id, filled_quantity=filled_qty
            )

        # 5. Sequence Monotonic Ordering
        last_seq = self.order_sequence_tracker.get(order_id, -1)
        if seq_num > 0 and seq_num < last_seq:
            logger.warning(f"Out-of-order webhook sequence for order '{order_id}': recv {seq_num} < last {last_seq}")

        if seq_num > 0:
            self.order_sequence_tracker[order_id] = max(last_seq, seq_num)

        logger.info(f"Webhook fill ingested: Order '{order_id}' Exec '{exec_id}' Qty {filled_qty}")
        return WebhookIngestionResult(
            status="SUCCESS", reason="VERIFIED_INGESTED", order_id=order_id, exec_id=exec_id, filled_quantity=filled_qty
        )
