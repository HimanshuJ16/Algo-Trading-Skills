"""
webhook-based-order-fill-notifications: a broker fill-webhook ingress guard.

Scope note
----------
This module verifies and *classifies* an inbound webhook. It deliberately does
not mutate a position ledger. Its whole output is a
:class:`WebhookIngestionResult` whose ``apply_to_ledger`` flag is the single
boolean the caller acts on, because the historical failure mode in this area is
a caller that reads ``status == "SUCCESS"`` on a redelivered webhook and adds
the same fill twice.

Trust model
-----------
A verified signature proves the bytes came from someone holding the secret. It
does **not** prove the payload reflects broker state, and several real fill
webhooks are weaker than they look:

* DhanHQ postbacks carry no signature or shared secret at all.
* Zerodha Kite Connect postbacks carry ``sha256(order_id + order_timestamp +
  api_secret)`` -- a checksum over three fields, not over the body, so
  ``filled_quantity``/``status``/``average_price`` are unauthenticated.
* Interactive Brokers republishes an execution *correction* as a fresh
  ``execDetails`` whose ``execId`` differs from the original only in the digits
  after the final period, so a correction does not look like a duplicate.

So a webhook is a *hint that something changed*, never the authority on what
changed. Reconcile against the broker's authenticated order/fill endpoint before
the position ledger moves. See ``references/standards.md`` for sources.

Deployment limits
-----------------
The dedup and sequence stores are in-memory and process-local. They are correct
for a single-process consumer and are guarded by a lock for multi-threaded
servers, but two web workers do not share them: with more than one process, a
redelivery routed to the other worker is ingested again. Production needs a
shared atomic claim (a unique index on ``(order_id, exec_id)``, or Redis
``SET NX``); :meth:`WebhookConsumerManager.claim_execution` is the seam to
replace.
"""
from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import hmac
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Set, Tuple, Union

logger = logging.getLogger(__name__)

#: OWASP's webhook guidance and the Standard Webhooks specification both put the
#: replay tolerance at five minutes. Widen it only with a measured reason.
DEFAULT_MAX_DRIFT_SECONDS = 300.0

#: How long a claimed ``order_id:exec_id`` stays remembered. Must comfortably
#: exceed the publisher's retry horizon, or a late retry is re-ingested.
DEFAULT_RETENTION_SECONDS = 86_400.0

#: Hard ceiling on remembered executions, so a hostile or runaway publisher
#: cannot grow the process out of memory between retention sweeps.
DEFAULT_MAX_TRACKED_EXECUTIONS = 500_000

#: Most a caller may send in one signature header. A rotation window needs two
#: or three tokens; anything beyond this is an attempt to make the endpoint do
#: unbounded HMAC work on unauthenticated input.
MAX_SIGNATURE_TOKENS = 8

#: Longest signature token that will be decoded. A SHA-256 digest is 64 hex or
#: 44 base64 characters, so this is generous; it exists to stop a multi-megabyte
#: token being hex-decoded before it can be rejected.
MAX_SIGNATURE_TOKEN_CHARS = 512

SecretLike = Union[str, bytes]


class WebhookError(ValueError):
    """Raised when a webhook payload cannot be parsed into a fill event."""


class WebhookStatus:
    """Terminal classifications for an inbound webhook delivery."""

    SUCCESS = "SUCCESS"      # first verified delivery of this execution
    DUPLICATE = "DUPLICATE"  # already claimed; acknowledge, do not re-apply
    REJECTED = "REJECTED"    # failed verification or is unparseable


@dataclass
class WebhookIngestionResult:
    """
    Outcome of one webhook delivery.

    ``apply_to_ledger`` is the only field a ledger writer should branch on. It
    is true exactly once per ``(order_id, exec_id)``. ``filled_quantity`` is
    forced to ``0.0`` whenever ``apply_to_ledger`` is false, so that a caller
    that ignores the flag and blindly accumulates the quantity still cannot
    double-count a redelivery.
    """

    status: str
    reason: str
    order_id: Optional[str] = None
    exec_id: Optional[str] = None
    filled_quantity: float = 0.0
    apply_to_ledger: bool = False
    http_status: int = 200
    sequence_num: Optional[int] = None
    out_of_order: bool = False
    requires_reconciliation: bool = False

    def __post_init__(self) -> None:
        if not self.apply_to_ledger:
            self.filled_quantity = 0.0


@dataclass
class ClaimRecord:
    """What was recorded the first time an execution key was claimed."""

    claimed_at: float
    body_digest: str
    sequence_num: Optional[int] = None


@dataclass
class _OrderState:
    """Per-order sequencing state."""

    highest_sequence: int = -1
    seen_sequences: Set[int] = field(default_factory=set)


def _normalize_secret(secret: SecretLike) -> bytes:
    if isinstance(secret, bytes):
        return secret
    if isinstance(secret, str):
        return secret.encode("utf-8")
    raise TypeError(f"secret must be str or bytes, got {type(secret).__name__}")


def _strip_signature_prefix(candidate: str) -> str:
    """
    Removes the algorithm label publishers put in front of the digest.

    Handles the GitHub-style ``sha256=<hex>`` and the Standard Webhooks
    ``v1,<base64>`` forms. The prefix is only stripped from the *start*: a naive
    ``replace("sha256=", "")`` would also mangle a digest that happens to
    contain the literal elsewhere.
    """
    text = candidate.strip()
    for prefix in ("sha256=", "sha-256=", "v1,", "v1="):
        if text.lower().startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _candidate_digests(candidate: str) -> Tuple[bytes, ...]:
    """Decodes one signature token as hex and/or base64, ignoring what fails."""
    decoded = []
    try:
        decoded.append(bytes.fromhex(candidate))
    except ValueError:
        pass
    try:
        decoded.append(base64.b64decode(candidate, validate=True))
    except (binascii.Error, ValueError):
        pass
    return tuple(decoded)


class WebhookConsumerManager:
    """
    Verifies broker fill webhooks and classifies each delivery exactly once.

    Args:
        max_drift_seconds: Reject an event whose timestamp is older than this.
        max_future_drift_seconds: Reject an event timestamped further ahead than
            this. Defaults to ``max_drift_seconds`` so behaviour matches the
            symmetric window publishers document, but it is a separate knob
            because a far-future timestamp is a clock fault or a forgery, not a
            late delivery, and some operators want it tighter.
        retention_seconds: How long claimed execution keys are remembered.
        max_tracked_executions: Hard ceiling on remembered execution keys.
        clock: Injectable time source, for deterministic tests.
    """

    def __init__(
        self,
        max_drift_seconds: float = DEFAULT_MAX_DRIFT_SECONDS,
        max_future_drift_seconds: Optional[float] = None,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
        max_tracked_executions: int = DEFAULT_MAX_TRACKED_EXECUTIONS,
        clock: Any = time.time,
    ) -> None:
        if max_drift_seconds <= 0:
            raise ValueError("max_drift_seconds must be positive")
        if max_future_drift_seconds is not None and max_future_drift_seconds < 0:
            raise ValueError("max_future_drift_seconds must be non-negative")
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        if max_tracked_executions <= 0:
            raise ValueError("max_tracked_executions must be positive")

        self.max_drift_seconds = float(max_drift_seconds)
        self.max_future_drift_seconds = (
            float(max_drift_seconds)
            if max_future_drift_seconds is None
            else float(max_future_drift_seconds)
        )
        self.retention_seconds = float(retention_seconds)
        self.max_tracked_executions = int(max_tracked_executions)
        self._clock = clock

        self._lock = threading.Lock()
        self._claims: Dict[str, ClaimRecord] = {}
        self._orders: Dict[str, _OrderState] = {}
        # Expiry is decided per key on lookup, which is exact and O(1). The full
        # scan is only reclaiming memory, so it runs on an interval: sweeping
        # the whole store on every claim makes ingestion quadratic in the number
        # of live claims.
        self._sweep_interval = max(1.0, self.retention_seconds / 10.0)
        self._next_sweep_at = float(self._clock()) + self._sweep_interval

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------

    @staticmethod
    def compute_hmac_signature(raw_body: bytes, secret: SecretLike) -> str:
        """
        HMAC-SHA256 hex digest over the *raw* request bytes.

        The bytes must be the ones received on the wire. Re-serialising a parsed
        dict changes key order and whitespace and will not reproduce the
        publisher's digest.
        """
        if not isinstance(raw_body, (bytes, bytearray)):
            raise TypeError(
                "raw_body must be bytes; sign the raw request body, not a parsed dict"
            )
        return hmac.new(_normalize_secret(secret), bytes(raw_body), hashlib.sha256).hexdigest()

    def verify_signature(
        self,
        raw_body: bytes,
        signature: str,
        secret: Union[SecretLike, Sequence[SecretLike]],
    ) -> bool:
        """
        Constant-time HMAC-SHA256 verification.

        Accepts a sequence of secrets so a dual-secret rotation window works
        without downtime, and a space-delimited list of signature tokens because
        a rotating publisher sends one token per active key. Hex and base64
        digests are both accepted; every candidate is compared with
        :func:`hmac.compare_digest`.
        """
        if not isinstance(raw_body, (bytes, bytearray)):
            return False
        if not signature or not isinstance(signature, str):
            return False

        if isinstance(secret, (str, bytes)):
            secrets: Sequence[SecretLike] = [secret]
        else:
            secrets = list(secret or [])
        secrets = [s for s in secrets if s]
        if not secrets:
            return False

        expected = [
            hmac.new(_normalize_secret(s), bytes(raw_body), hashlib.sha256).digest()
            for s in secrets
        ]

        matched = False
        for token in signature.split()[:MAX_SIGNATURE_TOKENS]:
            if len(token) > MAX_SIGNATURE_TOKEN_CHARS:
                continue
            for digest in _candidate_digests(_strip_signature_prefix(token)):
                for exp in expected:
                    # No short-circuit: every candidate is compared so the total
                    # work does not depend on which one matched.
                    if hmac.compare_digest(exp, digest):
                        matched = True
        return matched

    # ------------------------------------------------------------------
    # Timestamp / replay defence
    # ------------------------------------------------------------------

    def parse_timestamp(self, ts_val: Any) -> float:
        """
        Coerces a publisher timestamp to epoch seconds.

        Raises:
            WebhookError: if the value is absent, of an unusable type, or not a
                finite instant. There is no "assume now" fallback: defaulting a
                missing timestamp to the current time silently disables replay
                defence for exactly the payloads that omit it.
        """
        if ts_val is None:
            raise WebhookError("timestamp is missing")
        if isinstance(ts_val, bool):
            raise WebhookError("timestamp must not be a boolean")

        if isinstance(ts_val, (int, float)):
            ts = float(ts_val)
        elif isinstance(ts_val, str):
            text = ts_val.strip()
            if not text:
                raise WebhookError("timestamp is empty")
            try:
                ts = float(text)
            except ValueError:
                try:
                    dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise WebhookError(f"unparseable timestamp {ts_val!r}") from exc
                if dt.tzinfo is None:
                    # A naive ISO-8601 stamp is ambiguous. Publishers that send
                    # one mean UTC in every case surveyed; assuming local time
                    # would reject valid events on any non-UTC host.
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                ts = dt.timestamp()
        else:
            raise WebhookError(f"timestamp of unsupported type {type(ts_val).__name__}")

        if not math.isfinite(ts):
            raise WebhookError("timestamp is not finite")

        # Millisecond stamps are common and are three orders of magnitude out of
        # range; read as seconds they would land tens of thousands of years out.
        if ts > 1e11:
            ts /= 1000.0
        return ts

    def verify_timestamp(self, ts_val: Any) -> bool:
        """
        True when ``ts_val`` is a parseable instant inside the freshness window.

        A missing or unparseable timestamp is False, not "fresh".
        """
        try:
            ts = self.parse_timestamp(ts_val)
        except WebhookError:
            return False
        delta = float(self._clock()) - ts
        if delta >= 0:
            return delta <= self.max_drift_seconds
        return -delta <= self.max_future_drift_seconds

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def execution_key(order_id: str, exec_id: str) -> str:
        """The composite idempotency key. Both halves are required."""
        return f"{order_id}:{exec_id}"

    def is_duplicate(self, order_id: str, exec_id: str) -> bool:
        """
        Pure read: has this execution key already been claimed?

        This does not claim the key. Use :meth:`claim_execution` for the
        check-and-set; a predicate that mutates hides the fact that the claim
        happens before the ledger write.
        """
        with self._lock:
            self._maybe_sweep_locked()
            return self._live_claim_locked(self.execution_key(order_id, exec_id)) is not None

    def claim_execution(
        self,
        order_id: str,
        exec_id: str,
        body_digest: str = "",
        sequence_num: Optional[int] = None,
    ) -> Tuple[bool, Optional[ClaimRecord]]:
        """
        Atomically claims an execution key.

        Returns ``(True, None)`` for the first claim and ``(False, prior)`` for a
        redelivery, where ``prior`` is the record written by the first claim.
        The check and the set are taken under one lock: separating them lets two
        request threads both conclude "not a duplicate".

        This is the seam to override for a multi-process deployment. Back it
        with a unique constraint or ``SET NX`` and the rest of this class works
        unchanged.
        """
        with self._lock:
            self._maybe_sweep_locked()
            key = self.execution_key(order_id, exec_id)
            prior = self._live_claim_locked(key)
            if prior is not None:
                return False, prior
            self._claims[key] = ClaimRecord(
                claimed_at=float(self._clock()),
                body_digest=body_digest,
                sequence_num=sequence_num,
            )
            self._enforce_capacity_locked()
            return True, None

    def _live_claim_locked(self, key: str) -> Optional[ClaimRecord]:
        """
        The claim for ``key`` if it is still inside the retention window.

        Expiry is decided here rather than relying on the sweep, so a key is
        never reported as a duplicate past its retention window just because the
        sweep has not run yet.
        """
        record = self._claims.get(key)
        if record is None:
            return None
        if record.claimed_at < float(self._clock()) - self.retention_seconds:
            del self._claims[key]
            return None
        return record

    def _maybe_sweep_locked(self) -> None:
        """Reclaims memory from expired claims, at most once per sweep interval."""
        now = float(self._clock())
        if now < self._next_sweep_at:
            return
        self._next_sweep_at = now + self._sweep_interval
        cutoff = now - self.retention_seconds
        expired = [k for k, rec in self._claims.items() if rec.claimed_at < cutoff]
        for key in expired:
            del self._claims[key]
        if expired:
            logger.debug("Evicted %d execution claims past the retention window.", len(expired))

    def _enforce_capacity_locked(self) -> None:
        overflow = len(self._claims) - self.max_tracked_executions
        if overflow <= 0:
            return
        # dict preserves insertion order, so the head is the oldest claim.
        for key in list(self._claims)[:overflow]:
            del self._claims[key]
        logger.warning(
            "Execution claim store hit its %d-entry ceiling; dropped %d oldest keys. "
            "Redeliveries of dropped executions will no longer be recognised as duplicates.",
            self.max_tracked_executions,
            overflow,
        )

    # ------------------------------------------------------------------
    # Payload field extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _reject_json_constant(name: str) -> Any:
        raise WebhookError(f"payload contains the non-standard JSON constant {name}")

    @staticmethod
    def _require_identifier(payload: Mapping[str, Any], *names: str) -> str:
        """
        Pulls the first present identifier and rejects non-scalar or blank ones.

        ``str(payload.get(...))`` is not sufficient: a JSON ``null`` stringifies
        to the truthy ``"None"`` and sails through an emptiness check as if it
        were a real order id.
        """
        for name in names:
            if name not in payload:
                continue
            value = payload[name]
            if value is None or isinstance(value, (dict, list, bool)):
                raise WebhookError(f"field {name!r} is not a usable identifier")
            text = str(value).strip()
            if text:
                return text
            raise WebhookError(f"field {name!r} is empty")
        raise WebhookError(f"none of {names} present in payload")

    @staticmethod
    def _require_quantity(payload: Mapping[str, Any], *names: str) -> float:
        for name in names:
            if name not in payload:
                continue
            value = payload[name]
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise WebhookError(f"field {name!r} is not a number")
            try:
                qty = float(value)
            except (TypeError, ValueError) as exc:
                raise WebhookError(f"field {name!r} is not a number: {value!r}") from exc
            if not math.isfinite(qty):
                # NaN would propagate silently through every later comparison and
                # poison the position ledger without raising anything.
                raise WebhookError(f"field {name!r} is not finite: {value!r}")
            if qty < 0:
                raise WebhookError(f"field {name!r} is negative: {value!r}")
            return qty
        raise WebhookError(f"none of {names} present in payload")

    @staticmethod
    def _optional_sequence(payload: Mapping[str, Any], *names: str) -> Optional[int]:
        for name in names:
            if name not in payload:
                continue
            value = payload[name]
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise WebhookError(f"field {name!r} is not an integer")
            try:
                seq = int(value)
            except (TypeError, ValueError) as exc:
                raise WebhookError(f"field {name!r} is not an integer: {value!r}") from exc
            if seq < 0:
                raise WebhookError(f"field {name!r} is negative: {value!r}")
            return seq
        return None

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def process_webhook(
        self,
        raw_body: bytes,
        signature_header: str,
        secret: Union[SecretLike, Sequence[SecretLike]],
    ) -> WebhookIngestionResult:
        """
        Verifies one webhook delivery and classifies it.

        Order of checks is deliberate: signature first, so unauthenticated bytes
        are never parsed; then freshness, so a replay is dropped before it can
        consume an idempotency claim; then the claim; then sequencing.

        Returns:
            A :class:`WebhookIngestionResult`. Apply the fill only when
            ``apply_to_ledger`` is true, and only after reconciling against the
            broker's authenticated order endpoint -- see the module docstring.
        """
        # 1. Signature over the raw bytes, before any parsing.
        if not self.verify_signature(raw_body, signature_header, secret):
            logger.error("Rejected webhook: HMAC signature did not verify.")
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED, reason="INVALID_SIGNATURE", http_status=401
            )

        # 2. Decode. NaN/Infinity are non-standard JSON that Python accepts by
        #    default; refuse them rather than let a NaN quantity through.
        try:
            payload = json.loads(
                bytes(raw_body).decode("utf-8"), parse_constant=self._reject_json_constant
            )
        except UnicodeDecodeError as exc:
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED, reason=f"INVALID_ENCODING: {exc}", http_status=400
            )
        except WebhookError as exc:
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED, reason=f"INVALID_JSON: {exc}", http_status=400
            )
        except json.JSONDecodeError as exc:
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED, reason=f"INVALID_JSON: {exc}", http_status=400
            )

        if not isinstance(payload, dict):
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED, reason="PAYLOAD_NOT_OBJECT", http_status=400
            )

        # 3. Required fields. Every coercion is guarded: an uncaught ValueError
        #    here becomes a 500, and a publisher reading a 500 retries forever.
        try:
            order_id = self._require_identifier(payload, "order_id", "orderId")
            exec_id = self._require_identifier(
                payload, "exec_id", "executionId", "execution_id", "tradeId"
            )
        except WebhookError as exc:
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED,
                reason=f"MISSING_ORDER_OR_EXEC_ID: {exc}",
                http_status=400,
            )

        try:
            filled_qty = self._require_quantity(
                payload, "filled_qty", "filled_quantity", "quantity"
            )
        except WebhookError as exc:
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED,
                reason=f"MALFORMED_QUANTITY: {exc}",
                order_id=order_id,
                exec_id=exec_id,
                http_status=400,
            )

        try:
            seq_num = self._optional_sequence(payload, "sequence_num", "seq", "sequence")
        except WebhookError as exc:
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED,
                reason=f"MALFORMED_SEQUENCE: {exc}",
                order_id=order_id,
                exec_id=exec_id,
                http_status=400,
            )

        # 4. Replay defence. A payload with no timestamp is rejected outright.
        ts_val = payload.get("timestamp", payload.get("time"))
        if ts_val is None:
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED,
                reason="MISSING_TIMESTAMP",
                order_id=order_id,
                exec_id=exec_id,
                http_status=400,
            )
        if not self.verify_timestamp(ts_val):
            logger.warning(
                "Rejected webhook for order %s exec %s: timestamp outside the freshness window.",
                order_id,
                exec_id,
            )
            return WebhookIngestionResult(
                status=WebhookStatus.REJECTED,
                reason="TIMESTAMP_DRIFT_EXCEEDED",
                order_id=order_id,
                exec_id=exec_id,
                http_status=400,
            )

        # 5. Atomic idempotency claim.
        body_digest = hashlib.sha256(bytes(raw_body)).hexdigest()
        claimed, prior = self.claim_execution(order_id, exec_id, body_digest, seq_num)
        if not claimed:
            content_changed = bool(
                prior and prior.body_digest and prior.body_digest != body_digest
            )
            if content_changed:
                # Same key, different bytes. Either the publisher amended the
                # execution or something is replaying a mutated payload. Neither
                # is safe to drop silently.
                logger.warning(
                    "Redelivery of order %s exec %s carries a different body; flagging for "
                    "reconciliation.",
                    order_id,
                    exec_id,
                )
            else:
                logger.info("Duplicate delivery of order %s exec %s skipped.", order_id, exec_id)
            return WebhookIngestionResult(
                status=WebhookStatus.DUPLICATE,
                reason="DUPLICATE_CONTENT_MISMATCH" if content_changed else "DUPLICATE_SKIPPED",
                order_id=order_id,
                exec_id=exec_id,
                apply_to_ledger=False,
                http_status=200,
                sequence_num=seq_num,
                requires_reconciliation=content_changed,
            )

        # 6. Sequencing. This records and reports; it does not reorder. Holding
        #    a fill back to wait for its predecessor would need a durable buffer
        #    and a timeout policy, which is the caller's decision, not this
        #    module's -- so the out-of-order fact is surfaced instead.
        out_of_order = False
        if seq_num is not None:
            with self._lock:
                state = self._orders.setdefault(order_id, _OrderState())
                out_of_order = seq_num < state.highest_sequence
                state.highest_sequence = max(state.highest_sequence, seq_num)
                state.seen_sequences.add(seq_num)
                highest = state.highest_sequence
            if out_of_order:
                logger.warning(
                    "Out-of-order fill for order %s: sequence %d arrived after %d. "
                    "Reconcile order state before applying.",
                    order_id,
                    seq_num,
                    highest,
                )

        logger.info(
            "Webhook fill accepted: order=%s exec=%s qty=%s seq=%s",
            order_id,
            exec_id,
            filled_qty,
            seq_num,
        )
        return WebhookIngestionResult(
            status=WebhookStatus.SUCCESS,
            reason="VERIFIED_INGESTED",
            order_id=order_id,
            exec_id=exec_id,
            filled_quantity=filled_qty,
            apply_to_ledger=True,
            http_status=200,
            sequence_num=seq_num,
            out_of_order=out_of_order,
            requires_reconciliation=out_of_order,
        )

    def missing_sequences(self, order_id: str) -> Tuple[int, ...]:
        """
        Sequence numbers not yet seen below the highest one seen for an order.

        A non-empty result means a delivery is outstanding: at-least-once
        delivery carries no at-most-once and no in-order guarantee, so a gap is
        the signal to reconcile rather than to assume the order is complete.
        """
        with self._lock:
            state = self._orders.get(order_id)
            if state is None or state.highest_sequence < 0:
                return ()
            seen = state.seen_sequences
            low = min(seen)
            return tuple(s for s in range(low, state.highest_sequence) if s not in seen)
