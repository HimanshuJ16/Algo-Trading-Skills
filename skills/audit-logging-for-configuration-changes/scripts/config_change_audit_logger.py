"""
audit-logging-for-configuration-changes:
Compliance engine for tracking modifications to trading configurations to satisfy
FINRA Rule 3110 / Regulatory Notice 15-09 and SEC Rule 17a-4 recordkeeping, with
tamper-evident hash chaining for forensic integrity.

Regulatory applicability:
  * FINRA Rule 3110 (Supervision) and Regulatory Notice 15-09 require a documented
    change-management process for algorithmic trading code and risk-control
    parameter settings, including records of testing, approvals, and reasoning.
  * SEC Rule 17a-4 imposes WORM-style retention periods for broker-dealer records.
  * SEC Regulation SCI applies directly only to "SCI entities" (SROs, certain ATSs,
    plan processors, certain exempt clearing agencies); a 2023 SEC proposal would
    extend it to certain large broker-dealers. This skill is useful for any firm
    that *voluntarily* adopts SCI-style controls even where SCI is not mandated.

Integrity model (NIST SP 800-92):
  Each emitted record carries a monotonically increasing ``sequence_number`` and a
  ``record_hash`` (SHA-256 over the canonical JSON of every other field, including
  ``prev_hash``). Deletion, reordering, or in-place modification of any record is
  therefore independently detectable during an examination. The hash layer detects
  tampering; true immutability is provided by the downstream WORM/SIEM sink.
"""
import dataclasses
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List

logger = logging.getLogger(__name__)

# Minimum character length for a compliant justification. FINRA Notice 15-09
# expects documented reasoning for risk-control parameter changes; a bare token
# such as "ok" does not constitute supervisory documentation.
MIN_JUSTIFICATION_LENGTH = 5

# Fields excluded from the integrity hash. ``record_hash`` is computed over the
# canonical (sorted-key) JSON of every *other* field, so any tampering with a
# serialized record is independently detectable.
_HASH_EXCLUDED_FIELDS = ("record_hash",)


@dataclass
class ConfigChangeRequest:
    """A requested mutation to a trading-system configuration parameter."""

    parameter_name: str
    old_value: Any
    new_value: Any
    user_id: str
    justification: str
    # Originating environment (e.g. "production", "staging"). Config changes in
    # any environment are auditable; recording the source aids forensic scoping.
    environment: str = "production"


@dataclass
class ConfigChangeRecord:
    """An immutable, tamper-evident audit entry for one change request."""

    sequence_number: int
    timestamp_utc: str
    environment: str
    parameter_name: str
    old_value: Any
    new_value: Any
    user_id: str
    justification: str
    is_approved: bool
    rejection_reason: str
    prev_hash: str
    record_hash: str

    def to_json(self) -> str:
        """Canonical (sorted-key) JSON for append-only SIEM ingestion.

        ``default=str`` ensures non-JSON-serializable config values (sets,
        datetimes, custom objects) serialize deterministically rather than
        raising and dropping the audit record.
        """
        return json.dumps(dataclasses.asdict(self), sort_keys=True, default=str)

    def _hashing_view(self) -> str:
        """Canonical JSON of the record excluding ``record_hash`` itself."""
        full = dataclasses.asdict(self)
        for excluded in _HASH_EXCLUDED_FIELDS:
            full.pop(excluded, None)
        return json.dumps(full, sort_keys=True, default=str)

    def compute_hash(self) -> str:
        """SHA-256 over the canonical hashing view."""
        return hashlib.sha256(self._hashing_view().encode("utf-8")).hexdigest()


class ConfigurationAuditLogger:
    """
    Enforces regulatory audit logging for trading-system configuration changes.

    Each processed request -- approved *or* rejected -- is assigned a monotonically
    increasing sequence number and chained to the previous record via its SHA-256
    ``record_hash``, so that deletion, reordering, or in-place modification of any
    emitted record is independently detectable during an examination. Rejected
    attempts are emitted at WARNING level because a failed change attempt (for
    example, a missing justification) is itself an auditable supervisory event.
    """

    def __init__(self, environment: str = "production") -> None:
        self._environment = environment
        self._sequence: int = 0
        self._prev_hash: str = ""
        self._records: List[ConfigChangeRecord] = []

    def process_change_request(self, request: ConfigChangeRequest) -> ConfigChangeRecord:
        """Validate, record, and emit an audit entry for ``request``."""
        rejection_reason = self._validate(request)
        is_approved = rejection_reason == ""
        record = self._create_record(
            request,
            is_approved=is_approved,
            rejection_reason=rejection_reason,
        )
        self._records.append(record)
        if is_approved:
            logger.info("AUDIT_LOG_ENTRY: %s", record.to_json())
        else:
            logger.warning(
                "AUDIT_LOG_REJECTED: %s reason=%s",
                record.to_json(),
                rejection_reason,
            )
        return record

    @property
    def records(self) -> List[ConfigChangeRecord]:
        """A defensive copy of all emitted records, in sequence order."""
        return list(self._records)

    def _validate(self, request: ConfigChangeRequest) -> str:
        """Returns ``""`` when the request is approvable, else a short reason."""
        # FINRA Notice 15-09: risk-control parameter changes require documented reasoning.
        justification = request.justification.strip() if request.justification else ""
        if len(justification) < MIN_JUSTIFICATION_LENGTH:
            return "missing or insufficient justification"
        # An audit record without an authenticated principal cannot support supervisory review.
        if not request.user_id or not request.user_id.strip():
            return "missing user_id"
        # No-op changes are not approved, but are still recorded for forensic completeness.
        if request.old_value == request.new_value:
            return "new value identical to old value"
        return ""

    def _create_record(
        self,
        request: ConfigChangeRequest,
        is_approved: bool,
        rejection_reason: str,
    ) -> ConfigChangeRecord:
        self._sequence += 1
        # High-precision UTC timestamp as expected for CAT / Reg SCI-style reconstruction.
        now_utc = datetime.now(timezone.utc).isoformat()
        record = ConfigChangeRecord(
            sequence_number=self._sequence,
            timestamp_utc=now_utc,
            environment=request.environment or self._environment,
            parameter_name=request.parameter_name,
            old_value=request.old_value,
            new_value=request.new_value,
            user_id=request.user_id,
            justification=request.justification,
            is_approved=is_approved,
            rejection_reason=rejection_reason,
            prev_hash=self._prev_hash,
            record_hash="",
        )
        record.record_hash = record.compute_hash()
        self._prev_hash = record.record_hash
        return record
