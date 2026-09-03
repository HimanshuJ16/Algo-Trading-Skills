"""
zerodha-kite-postback-webhook-verification: verification engine for Zerodha Kite
Connect v3 order postbacks.

Kite Connect delivers order updates as a raw JSON HTTP POST body to the app's
registered postback URL. The body carries a "checksum" field which the Kite
Connect v3 documentation defines as:

    checksum = SHA-256(order_id + order_timestamp + api_secret)

    -- https://kite.trade/docs/connect/v3/postbacks/

Two properties of that scheme drive the design of this module:

1. It is a plain SHA-256 digest over a concatenated string, *not* an HMAC.
   Comparison must still be constant-time, but no HMAC construction is involved.

2. It authenticates `order_id` and `order_timestamp` *only*. It does not cover
   `status`, `filled_quantity`, `average_price`, or any other body field. An
   attacker who captures one postback can alter those fields and the checksum
   still matches. A verified postback is therefore a trustworthy notification
   that *order X changed at instant T* -- and nothing more.

   Consequence: never mutate a position or order ledger from postback body
   fields. Use the verified postback as a trigger to re-fetch authoritative
   state from `GET /orders/:order_id`. PostbackVerificationResult exposes
   `requires_reconciliation` to keep that step visible at the call site.

Kite publishes no retry, ordering, or delivery guarantee for postbacks, so a
consumer must tolerate missing, duplicated, and out-of-order deliveries, and
must not treat postback silence as evidence that an order did not change.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
import json
import logging
import threading
from typing import Any, Callable, FrozenSet, Mapping, Optional

logger = logging.getLogger(__name__)

#: Kite Connect emits timezone-naive local exchange time. India observes no DST,
#: so a fixed UTC+05:30 offset is exact for every value Kite can emit. The
#: official pykiteconnect client parses these strings and leaves them naive,
#: which is why the offset has to be supplied by the consumer.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

#: Documented order_timestamp shape, e.g. "2022-03-03 09:24:25".
KITE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_CHECKSUM_HEX_LENGTH = 64
_DEFAULT_MAX_TRACKED_EVENTS = 10_000


class PostbackVerificationError(ValueError):
    """Raised for caller/configuration errors, not for untrusted-input rejection.

    Untrusted input is never signalled by an exception -- a malformed or forged
    postback must not be able to raise out of the request handler. Those produce
    a PostbackVerificationResult carrying a rejecting outcome.
    """


class PostbackOutcome(str, Enum):
    """Terminal classification of a single postback delivery."""

    #: Authentic, fresh, and not seen before -- apply exactly once.
    ACCEPTED = "ACCEPTED"
    #: Authentic and fresh, but an identical state update was already applied.
    DUPLICATE = "DUPLICATE"
    #: Structurally unusable (missing or unparseable required fields).
    REJECTED_MALFORMED = "REJECTED_MALFORMED"
    #: No checksum present. Fails closed -- an unsigned postback is untrusted.
    REJECTED_MISSING_CHECKSUM = "REJECTED_MISSING_CHECKSUM"
    #: Checksum present but does not match. Possible spoofing attempt.
    REJECTED_BAD_CHECKSUM = "REJECTED_BAD_CHECKSUM"
    #: Authentic but older than the accepted freshness window.
    REJECTED_STALE = "REJECTED_STALE"
    #: Authentic but dated further into the future than clock skew allows.
    REJECTED_FUTURE_DATED = "REJECTED_FUTURE_DATED"


#: Outcomes reachable only after the checksum matched, i.e. sender is authentic.
_AUTHENTICATED_OUTCOMES = frozenset(
    {
        PostbackOutcome.ACCEPTED,
        PostbackOutcome.DUPLICATE,
        PostbackOutcome.REJECTED_STALE,
        PostbackOutcome.REJECTED_FUTURE_DATED,
    }
)


@dataclass(frozen=True)
class PostbackVerificationResult:
    """Outcome of verifying one postback delivery."""

    outcome: PostbackOutcome
    reason: str
    order_id: Optional[str] = None
    status: Optional[str] = None
    order_timestamp: Optional[str] = None
    filled_quantity: int = 0

    @property
    def valid(self) -> bool:
        """True only when this delivery should be applied, exactly once.

        Deliberately False for PostbackOutcome.DUPLICATE: a caller written as
        `if result.valid: apply(result)` must not double-apply a redelivered
        postback.
        """
        return self.outcome is PostbackOutcome.ACCEPTED

    @property
    def authenticated(self) -> bool:
        """True when the checksum matched, regardless of freshness/duplication.

        Use this to separate "a stale message from Zerodha" (operational: clock
        skew, delayed delivery) from "a forged message" (security incident).
        """
        return self.outcome in _AUTHENTICATED_OUTCOMES

    @property
    def requires_reconciliation(self) -> bool:
        """True whenever the caller intends to act on this postback.

        The Kite checksum does not cover `status`, `filled_quantity` or
        `average_price`, so those fields are unauthenticated. Re-fetch
        `GET /orders/:order_id` and act on that response instead.
        """
        return self.valid


def _coerce_quantity(value: Any) -> Optional[int]:
    """Coerce a Kite quantity field to a non-negative int, or None if invalid.

    Kite quantities are integral. Anything fractional, negative, or unparseable
    is treated as malformed rather than silently truncated.
    """
    if value is None:
        return 0
    if isinstance(value, bool):  # bool is an int subclass; never a quantity
        return None
    number: float
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / Inf
        return None
    if not number.is_integer() or number < 0:
        return None
    return int(number)


def _redact_checksum(checksum: str) -> str:
    """Return a short, non-reusable fragment of a checksum for log lines."""
    return f"{checksum[:8]}..." if len(checksum) > 8 else "<short>"


class KitePostbackVerifier:
    """Verifies Zerodha Kite Connect order postbacks before they touch state.

    Checks run in this order, each stage reached only if the previous passed:

    1. Structural -- `order_id` and `order_timestamp` must be present and
       usable, because both are checksum inputs.
    2. Authenticity -- constant-time comparison against
       `SHA-256(order_id + order_timestamp + api_secret)`. Fails closed when
       the `checksum` field is absent.
    3. Freshness -- bounded age and bounded future-dating of `order_timestamp`.
    4. Idempotency -- an atomic claim on the full state fingerprint.

    Authenticity is checked *before* freshness so that an unauthenticated
    payload can never influence timing-observable or order_id-keyed behaviour,
    and so that "stale" and "forged" remain distinguishable in the audit log.

    Instances are safe to share across the threads of a webhook server; the
    duplicate-claim step is guarded by a lock.
    """

    def __init__(
        self,
        max_drift_seconds: float = 300.0,
        max_future_seconds: float = 60.0,
        timestamp_tz: timezone = IST,
        max_tracked_events: int = _DEFAULT_MAX_TRACKED_EVENTS,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        """
        Args:
            max_drift_seconds: Maximum accepted age of `order_timestamp`.
            max_future_seconds: Maximum accepted future-dating, to absorb clock
                skew between Zerodha's host and this one without accepting an
                arbitrarily post-dated payload.
            timestamp_tz: Timezone that Kite's naive `order_timestamp` is
                expressed in. Defaults to IST (UTC+05:30). Override only with
                evidence that the account's postbacks differ.
            max_tracked_events: Upper bound on remembered event fingerprints.
                Oldest entries are evicted first, bounding memory in a
                long-lived receiver at the cost of forgetting very old events.
            clock: Returns the current timezone-aware time. Injectable for
                deterministic tests.
        """
        if max_drift_seconds <= 0:
            raise PostbackVerificationError("max_drift_seconds must be positive")
        if max_future_seconds < 0:
            raise PostbackVerificationError("max_future_seconds must be non-negative")
        if max_tracked_events <= 0:
            raise PostbackVerificationError("max_tracked_events must be positive")

        self.max_drift_seconds = float(max_drift_seconds)
        self.max_future_seconds = float(max_future_seconds)
        self.timestamp_tz = timestamp_tz
        self.max_tracked_events = int(max_tracked_events)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._seen: "OrderedDict[str, None]" = OrderedDict()

    # ------------------------------------------------------------------
    # Signature
    # ------------------------------------------------------------------
    @staticmethod
    def compute_checksum(order_id: str, order_timestamp: str, api_secret: str) -> str:
        """Return SHA-256(order_id + order_timestamp + api_secret) as hex.

        `order_timestamp` must be the raw string from the payload. Parsing and
        re-formatting it (normalising the separator, adding a timezone) changes
        the digest pre-image and breaks verification.
        """
        if not api_secret:
            raise PostbackVerificationError(
                "api_secret is empty; refusing to compute a postback checksum "
                "(an empty secret makes the digest forgeable by anyone)"
            )
        raw = f"{order_id}{order_timestamp}{api_secret}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _checksums_match(expected_hex: str, received: str) -> bool:
        """Constant-time compare of a received checksum against the expected one.

        Compares bytes rather than str: hmac.compare_digest raises TypeError on
        non-ASCII str input, which an attacker fully controls. Non-hex and
        wrong-length values are rejected before the comparison is reached.
        """
        candidate = received.strip().lower()
        if len(candidate) != _CHECKSUM_HEX_LENGTH:
            return False
        try:
            candidate_bytes = bytes.fromhex(candidate)
        except ValueError:
            return False
        return hmac.compare_digest(bytes.fromhex(expected_hex), candidate_bytes)

    # ------------------------------------------------------------------
    # Timestamp
    # ------------------------------------------------------------------
    def parse_order_timestamp(self, timestamp_str: str) -> datetime:
        """Parse a Kite order_timestamp into a timezone-aware datetime.

        Accepts the documented "YYYY-MM-DD HH:MM:SS" form (interpreted in
        `timestamp_tz`) and ISO-8601 strings carrying an explicit offset. A bare
        epoch number is rejected: Kite does not emit one, and accepting it lets
        a test suite pass without ever exercising the real wire format.

        Raises:
            PostbackVerificationError: if the value cannot be parsed.
        """
        text = (timestamp_str or "").strip()
        if not text:
            raise PostbackVerificationError("order_timestamp is empty")
        try:
            return datetime.strptime(text, KITE_TIMESTAMP_FORMAT).replace(
                tzinfo=self.timestamp_tz
            )
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PostbackVerificationError(
                f"Unparseable order_timestamp {text!r}; expected "
                f"{KITE_TIMESTAMP_FORMAT!r} or ISO-8601 with offset"
            ) from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=self.timestamp_tz)
        return parsed

    def timestamp_age_seconds(self, timestamp_str: str) -> float:
        """Signed age of timestamp_str: positive = past, negative = future."""
        return (self._clock() - self.parse_order_timestamp(timestamp_str)).total_seconds()

    def verify_timestamp(self, timestamp_str: str) -> bool:
        """True when timestamp_str is inside the accepted freshness window."""
        try:
            age = self.timestamp_age_seconds(timestamp_str)
        except PostbackVerificationError as exc:
            logger.error("Rejecting postback with unusable order_timestamp: %s", exc)
            return False
        return -self.max_future_seconds <= age <= self.max_drift_seconds

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------
    @staticmethod
    def event_fingerprint(payload: Mapping[str, Any]) -> str:
        """Fingerprint identifying one distinct order-state update.

        Keyed on the fields that determine the ledger mutation, not on
        `order_id` + `status` alone: Kite emits an UPDATE postback for *every*
        partial fill, and consecutive partial fills of one order all carry
        `status == "OPEN"`. Deduplicating on the status alone silently discards
        every fill after the first.
        """
        parts = (
            str(payload.get("order_id", "")),
            str(payload.get("order_timestamp", "")),
            str(payload.get("status", "")),
            str(payload.get("filled_quantity", "")),
            str(payload.get("average_price", "")),
        )
        return "|".join(parts)

    def claim_event(self, fingerprint: str) -> bool:
        """Atomically record fingerprint; True if it had not been seen before.

        Combining the test and the record under one lock is what makes
        concurrent redeliveries safe: two worker threads entering together
        cannot both be told "not a duplicate".
        """
        with self._lock:
            if fingerprint in self._seen:
                self._seen.move_to_end(fingerprint)
                return False
            self._seen[fingerprint] = None
            while len(self._seen) > self.max_tracked_events:
                self._seen.popitem(last=False)
            return True

    @property
    def tracked_event_count(self) -> int:
        """Number of fingerprints currently remembered."""
        with self._lock:
            return len(self._seen)

    @property
    def processed_postbacks(self) -> FrozenSet[str]:
        """Snapshot of remembered fingerprints. For introspection/tests only."""
        with self._lock:
            return frozenset(self._seen)

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------
    def verify_raw_body(self, body: bytes, api_secret: str) -> PostbackVerificationResult:
        """Verify a raw Kite postback HTTP body.

        Kite posts the payload as a raw JSON body with no signature headers, so
        this is the shape a receiver actually handles.
        """
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("Rejecting postback with undecodable body: %s", exc)
            return PostbackVerificationResult(
                outcome=PostbackOutcome.REJECTED_MALFORMED,
                reason="Postback body is not valid UTF-8 JSON",
            )
        if not isinstance(payload, dict):
            return PostbackVerificationResult(
                outcome=PostbackOutcome.REJECTED_MALFORMED,
                reason="Postback body is not a JSON object",
            )
        return self.verify_postback(payload, api_secret)

    def verify_postback(
        self, payload: Mapping[str, Any], api_secret: str
    ) -> PostbackVerificationResult:
        """Verify one decoded postback payload.

        Never raises on untrusted input; every rejection is returned as a result
        so a webhook handler can answer without a 500.

        Raises:
            PostbackVerificationError: only if api_secret is empty, which is a
                deployment defect rather than a bad request.
        """
        if not api_secret:
            raise PostbackVerificationError(
                "api_secret is empty; every postback would verify against a "
                "publicly derivable digest"
            )

        order_id = str(payload.get("order_id") or "").strip()
        # Kite's field is `order_timestamp`; `timestamp` is accepted only as a
        # fallback for receivers that renamed it upstream.
        raw_timestamp = payload.get("order_timestamp")
        if raw_timestamp is None:
            raw_timestamp = payload.get("timestamp")
        order_timestamp = str(raw_timestamp or "").strip()
        status = str(payload.get("status") or "").strip()
        received_checksum = str(payload.get("checksum") or "").strip()

        # 1. Structural validation -- both checksum inputs must exist.
        if not order_id or not order_timestamp:
            return PostbackVerificationResult(
                outcome=PostbackOutcome.REJECTED_MALFORMED,
                reason="Missing required order_id or order_timestamp field",
                order_id=order_id or None,
                status=status or None,
            )
        filled_quantity = _coerce_quantity(
            payload.get("filled_quantity", payload.get("quantity", 0))
        )
        if filled_quantity is None:
            return PostbackVerificationResult(
                outcome=PostbackOutcome.REJECTED_MALFORMED,
                reason="filled_quantity is not a non-negative integer",
                order_id=order_id,
                status=status or None,
                order_timestamp=order_timestamp,
            )

        # 2. Authenticity -- fails closed when no checksum was supplied.
        if not received_checksum:
            logger.critical(
                "Postback for order %s carries no checksum; rejecting. "
                "An unsigned postback is indistinguishable from a forged one.",
                order_id,
            )
            return PostbackVerificationResult(
                outcome=PostbackOutcome.REJECTED_MISSING_CHECKSUM,
                reason="Postback has no checksum field",
                order_id=order_id,
                status=status or None,
                order_timestamp=order_timestamp,
                filled_quantity=filled_quantity,
            )
        expected_checksum = self.compute_checksum(order_id, order_timestamp, api_secret)
        if not self._checksums_match(expected_checksum, received_checksum):
            logger.critical(
                "INVALID POSTBACK CHECKSUM for order %s (received %s); "
                "possible webhook spoofing attempt.",
                order_id,
                _redact_checksum(received_checksum),
            )
            return PostbackVerificationResult(
                outcome=PostbackOutcome.REJECTED_BAD_CHECKSUM,
                reason="Invalid postback checksum signature",
                order_id=order_id,
                status=status or None,
                order_timestamp=order_timestamp,
                filled_quantity=filled_quantity,
            )

        # 3. Freshness. Past and future are bounded separately: an old payload
        #    is a stale or replayed delivery, a post-dated one is clock skew or
        #    tampering, and the two need different operational responses.
        try:
            age_seconds = self.timestamp_age_seconds(order_timestamp)
        except PostbackVerificationError as exc:
            logger.error("Authentic postback with unusable timestamp: %s", exc)
            return PostbackVerificationResult(
                outcome=PostbackOutcome.REJECTED_MALFORMED,
                reason=f"Unparseable order_timestamp: {exc}",
                order_id=order_id,
                status=status or None,
                order_timestamp=order_timestamp,
                filled_quantity=filled_quantity,
            )
        if age_seconds > self.max_drift_seconds:
            logger.warning(
                "Stale postback for order %s: age %.1fs > %.1fs limit.",
                order_id,
                age_seconds,
                self.max_drift_seconds,
            )
            return PostbackVerificationResult(
                outcome=PostbackOutcome.REJECTED_STALE,
                reason=(
                    f"Postback rejected: order_timestamp is {age_seconds:.1f}s old, "
                    f"beyond the {self.max_drift_seconds:.0f}s freshness window"
                ),
                order_id=order_id,
                status=status or None,
                order_timestamp=order_timestamp,
                filled_quantity=filled_quantity,
            )
        if -age_seconds > self.max_future_seconds:
            logger.warning(
                "Future-dated postback for order %s: %.1fs ahead of local clock.",
                order_id,
                -age_seconds,
            )
            return PostbackVerificationResult(
                outcome=PostbackOutcome.REJECTED_FUTURE_DATED,
                reason=(
                    f"Postback rejected: order_timestamp is {-age_seconds:.1f}s in the "
                    f"future, beyond the {self.max_future_seconds:.0f}s skew allowance"
                ),
                order_id=order_id,
                status=status or None,
                order_timestamp=order_timestamp,
                filled_quantity=filled_quantity,
            )

        # 4. Idempotency.
        if not self.claim_event(self.event_fingerprint(payload)):
            logger.info(
                "Duplicate postback ignored for order %s (status %s, filled %d).",
                order_id,
                status,
                filled_quantity,
            )
            return PostbackVerificationResult(
                outcome=PostbackOutcome.DUPLICATE,
                reason="DUPLICATE_IGNORED",
                order_id=order_id,
                status=status or None,
                order_timestamp=order_timestamp,
                filled_quantity=filled_quantity,
            )

        return PostbackVerificationResult(
            outcome=PostbackOutcome.ACCEPTED,
            reason="VERIFIED_SUCCESS",
            order_id=order_id,
            status=status or None,
            order_timestamp=order_timestamp,
            filled_quantity=filled_quantity,
        )
