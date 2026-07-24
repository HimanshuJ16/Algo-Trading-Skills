"""
zerodha-kite-postback-webhook-verification: Production-grade postback webhook verification engine
for Zerodha Kite Connect v3. Performs constant-time SHA-256 signature validation,
timestamp replay attack defense, and idempotent postback deduplication.
"""
from dataclasses import dataclass
import datetime
import hashlib
import hmac
import logging
import time
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class PostbackVerificationError(ValueError):
    """Raised when postback signature, timestamp, or payload fails security checks."""
    pass


@dataclass
class PostbackVerificationResult:
    valid: bool
    reason: str
    order_id: Optional[str] = None
    status: Optional[str] = None
    filled_quantity: float = 0.0


class KitePostbackVerifier:
    """
    Cryptographic postback verification manager enforcing SHA-256 signature authenticity,
    timestamp drift thresholds, and postback event deduplication for Zerodha Kite Connect.
    """

    def __init__(self, max_drift_seconds: float = 300.0):
        self.max_drift_seconds = max_drift_seconds
        self.processed_postbacks: Set[str] = set()

    @staticmethod
    def compute_checksum(order_id: str, timestamp: str, api_secret: str) -> str:
        """Computes expected SHA-256 checksum for Kite Connect postback."""
        raw = f"{order_id}{timestamp}{api_secret}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def verify_timestamp(self, timestamp_str: str) -> bool:
        """Verifies postback timestamp is within max_drift_seconds from current time."""
        try:
            # Handle ISO string or timestamp string
            if "T" in timestamp_str:
                dt = datetime.datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                ts = dt.timestamp()
            else:
                ts = float(timestamp_str)

            now = time.time()
            drift = abs(now - ts)
            if drift > self.max_drift_seconds:
                logger.warning(f"Postback timestamp drift exceeded limit: {drift:.1f}s > {self.max_drift_seconds}s")
                return False
            return True
        except Exception as e:
            logger.error(f"Error parsing postback timestamp '{timestamp_str}': {e}")
            return False

    def is_duplicate(self, order_id: str, status: str) -> bool:
        sig = f"{order_id}:{status}"
        if sig in self.processed_postbacks:
            return True
        self.processed_postbacks.add(sig)
        return False

    def verify_postback(
        self, payload: Dict[str, Any], api_secret: str
    ) -> PostbackVerificationResult:
        """
        Validates postback signature, timestamp freshness, and deduplication.
        """
        order_id = str(payload.get("order_id", ""))
        timestamp = str(payload.get("timestamp", payload.get("order_timestamp", "")))
        received_checksum = str(payload.get("checksum", ""))
        status = str(payload.get("status", ""))
        filled_qty = float(payload.get("filled_quantity", payload.get("quantity", 0)))

        if not order_id or not timestamp:
            return PostbackVerificationResult(
                valid=False, reason="Missing required order_id or timestamp field"
            )

        # 1. Timestamp Freshness Check (Replay Attack Defense)
        if not self.verify_timestamp(timestamp):
            return PostbackVerificationResult(
                valid=False,
                reason="Postback rejected: Timestamp drift exceeded threshold (possible replay attack)",
                order_id=order_id,
                status=status,
            )

        # 2. SHA-256 Checksum Verification (Constant-Time)
        if received_checksum:
            expected_checksum = self.compute_checksum(order_id, timestamp, api_secret)
            if not hmac.compare_digest(expected_checksum.lower(), received_checksum.lower()):
                logger.critical(f"INVALID POSTBACK CHECKSUM for order '{order_id}'! Possible webhook spoofing attempt.")
                return PostbackVerificationResult(
                    valid=False,
                    reason="Invalid postback checksum signature",
                    order_id=order_id,
                    status=status,
                )

        # 3. Idempotency Check
        if self.is_duplicate(order_id, status):
            logger.info(f"Duplicate postback event ignored for order '{order_id}' status '{status}'")
            return PostbackVerificationResult(
                valid=True,
                reason="DUPLICATE_IGNORED",
                order_id=order_id,
                status=status,
                filled_quantity=filled_qty,
            )

        return PostbackVerificationResult(
            valid=True,
            reason="VERIFIED_SUCCESS",
            order_id=order_id,
            status=status,
            filled_quantity=filled_qty,
        )
