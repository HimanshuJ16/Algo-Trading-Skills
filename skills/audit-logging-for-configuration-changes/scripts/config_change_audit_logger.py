"""
audit-logging-for-configuration-changes:
Compliance engine for tracking modifications to trading configurations, with
tamper-evident hash chaining so the record set can be checked for edits,
deletions and reordering during an examination.

Whose obligation this serves
----------------------------
Applicability is entity- and jurisdiction-dependent. The engine cannot tell
which regime a firm is in scope of, so it makes no compliance assertion of its
own:

  * **FINRA member broker-dealers** are subject to Rule 3110 (Supervision):
    written supervisory procedures under 3110(b)(1), a record of supervisory
    designations preserved at least three years under 3110(b)(6)(B), and
    internal inspection reports kept at least three years under 3110(c)(2).
  * **FINRA Regulatory Notice 15-09** (March 2015) is *guidance* -- "suggested
    effective practices" that "complement, rather than supplant, obligations
    firms have under existing or future rules". It describes "a development and
    change management process that tracks the development of new trading code or
    material changes to existing code", including "a review of test results and a
    set of approval protocols", and "archiving code versions in a retrievable
    manner". It does **not** impose a rule-level requirement, and it does not
    require that the *reason* for a change be documented.
  * **US broker-dealers with market access** are subject to SEA Rule 15c3-5.
    SEC Division of Trading and Markets FAQ No. 18 is the authority for
    documenting *reasons*: where a threshold is raised in accordance with
    supervisory procedures, "the reasons for any such modification should be
    appropriately documented and retained as part of the broker-dealer's books
    and records". This is the obligation the ``justification`` field serves, and
    it is specific to risk-control thresholds at US broker-dealers.
  * **SEC Rule 17a-4** governs preservation. Since the 2022 amendments (Release
    34-96034, effective 3 January 2023, compliance date 3 May 2023 for
    broker-dealers) an electronic recordkeeping system may satisfy 17a-4(f) by
    **either** the WORM format **or** an audit-trail arrangement that permits
    recreation of an original record if it is modified or deleted, recording the
    date and time of each creation, modification or deletion and the identity of
    the person who performed it. WORM is no longer the only permitted approach.
  * **SEC Regulation SCI** applies only to "SCI entities" -- SROs, certain ATSs,
    plan processors and certain exempt clearing agencies. The March 2023 proposal
    to extend it to certain large broker-dealers and SBSDRs (88 FR 23146) was
    **formally withdrawn** by the Commission on 12 June 2025 (effective 17 June
    2025). Firms outside the SCI perimeter may adopt SCI-style controls
    voluntarily, but should not describe them as SCI compliance.

A firm outside all of the above should treat this as operational hygiene, not as
compliance evidence.

Integrity model (NIST SP 800-92 Section 3.1)
--------------------------------------------
Each record carries a monotonically increasing ``sequence_number`` and a
``record_hash`` (SHA-256 over the canonical JSON of every other field, including
``prev_hash``). Editing, deleting or reordering any record in the middle of the
chain is therefore detectable by :func:`verify_chain`.

The chain has two limits an examiner should know about, and neither is fixed by
adding more hashing:

  * **Truncation of the newest records is not detectable from the chain alone.**
    Removing the last N records leaves a chain that verifies perfectly. Only an
    externally held chain head reveals the loss -- which is why SP 800-92 says
    the digest must be "protected from alteration through FIPS-approved
    encryption algorithms, storage on read-only media, or other suitable means".
    Publish :attr:`ConfigurationAuditLogger.chain_head_hash` to storage the
    trading host cannot rewrite.
  * **Anything that can rewrite the whole log can recompute the whole chain.**
    The hash layer provides tamper *detection* in a process that has not itself
    been subverted; tamper *prevention* is the downstream WORM or audit-trail
    recordkeeping system's job.
"""
import dataclasses
import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

#: Engineering default with **no regulatory basis**. A character count cannot
#: judge whether a justification is adequate -- adequacy is a human review
#: question -- it only catches an empty or placeholder field such as "ok".
#: Neither FINRA Rule 3110 nor Regulatory Notice 15-09 prescribes a length.
#: Override per firm policy via ``ConfigurationAuditLogger(min_justification_chars=...)``.
MIN_JUSTIFICATION_LENGTH = 5

#: ``prev_hash`` of the first record in a chain. Retained as the empty string so
#: that chains emitted by earlier versions of this module still verify.
GENESIS_PREV_HASH = ""

# Fields excluded from the integrity hash. ``record_hash`` is computed over the
# canonical (sorted-key) JSON of every *other* field, so any tampering with a
# serialized record is independently detectable.
_HASH_EXCLUDED_FIELDS = ("record_hash",)

# Rejection reasons, exposed as constants so callers can branch on them without
# matching prose.
REASON_PARAMETER_NAME = "missing parameter_name"
REASON_JUSTIFICATION = "missing or insufficient justification"
REASON_USER_ID = "missing user_id"
REASON_NO_OP = "new value identical to old value"


def _coerce_unserializable(value: Any) -> str:
    """``json.dumps`` fallback that cannot itself raise.

    Equivalent to ``str`` for every value with a working ``__str__``, so records
    emitted by earlier versions of this module hash identically. The fallback
    exists because dropping an audit record is a worse outcome than recording a
    degraded rendering of one config value.
    """
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - an arbitrary config object's __str__ may raise anything
        return "<unserializable {}>".format(type(value).__name__)


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
    """A tamper-evident audit entry for one change request.

    The dataclass is deliberately mutable: it is the emitted JSON line, held in
    append-only storage, that is the record of authority. Mutating an instance
    in memory is exactly the tampering that :meth:`compute_hash` detects.
    """

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

        Config values that are not JSON-native (sets, datetimes, custom objects)
        are coerced to their string form rather than raising and dropping the
        audit record. That coercion is one-way, and the string form of an
        unordered container is not stable between processes, so prefer
        JSON-native config values where the record must be reproducible.
        """
        return json.dumps(
            dataclasses.asdict(self), sort_keys=True, default=_coerce_unserializable
        )

    def _hashing_view(self) -> str:
        """Canonical JSON of the record excluding ``record_hash`` itself."""
        full = dataclasses.asdict(self)
        for excluded in _HASH_EXCLUDED_FIELDS:
            full.pop(excluded, None)
        return json.dumps(full, sort_keys=True, default=_coerce_unserializable)

    def compute_hash(self) -> str:
        """SHA-256 over the canonical hashing view."""
        return hashlib.sha256(self._hashing_view().encode("utf-8")).hexdigest()

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "ConfigChangeRecord":
        """Rebuild a record from a parsed audit line, for offline verification.

        An examiner works from emitted JSON, not from live objects, so this is
        the entry point to :func:`verify_chain` for an archived log.

        Raises:
            ValueError: if a field is missing, or if unrecognised fields are
                present. Silently ignoring extra keys would let an examiner
                verify a chain while unaware that the archive carries data the
                hash does not cover.
        """
        expected = {f.name for f in dataclasses.fields(cls)}
        provided = set(mapping)
        missing = expected - provided
        if missing:
            raise ValueError("audit record is missing fields: {}".format(sorted(missing)))
        unknown = provided - expected
        if unknown:
            raise ValueError(
                "audit record has unrecognised fields: {}".format(sorted(unknown))
            )
        return cls(**{name: mapping[name] for name in expected})

    @classmethod
    def from_json(cls, line: str) -> "ConfigChangeRecord":
        """Rebuild a record from one emitted JSON line."""
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError("audit line must decode to a JSON object")
        return cls.from_mapping(parsed)


def verify_chain(
    records: Iterable[ConfigChangeRecord],
    *,
    expect_genesis: bool = True,
) -> Tuple[bool, Optional[str]]:
    """Walk a chain in sequence order and report the first inconsistency.

    Detects in-place edits, deletions from the middle, reordering, and gaps in
    the sequence. It cannot detect truncation of the newest records, nor an
    attacker who recomputed the entire chain -- see the module docstring.

    Args:
        records: Records in emission order, live or rebuilt via
            :meth:`ConfigChangeRecord.from_json`.
        expect_genesis: When ``True`` the first record must be sequence 1 with
            an empty ``prev_hash``. Set ``False`` to verify a window of a longer
            chain, which then proves internal consistency only -- it says
            nothing about the records preceding the window.

    Returns:
        ``(True, None)`` when the chain is consistent, otherwise
        ``(False, reason)`` naming the first record that fails.
    """
    previous_hash = GENESIS_PREV_HASH
    expected_sequence = 1 if expect_genesis else None

    for index, record in enumerate(records):
        if expected_sequence is None:
            # Anchor an unanchored window on its own first record.
            expected_sequence = record.sequence_number
            previous_hash = record.prev_hash
        if record.sequence_number != expected_sequence:
            return False, (
                "record at position {} has sequence_number {}, expected {} "
                "(a record is missing, duplicated or out of order)".format(
                    index, record.sequence_number, expected_sequence
                )
            )
        if record.prev_hash != previous_hash:
            return False, "record {} does not link to its predecessor".format(
                record.sequence_number
            )
        recomputed = record.compute_hash()
        if record.record_hash != recomputed:
            return False, "record {} hash does not match its content".format(
                record.sequence_number
            )
        previous_hash = recomputed
        expected_sequence += 1

    return True, None


class ConfigurationAuditLogger:
    """
    Records trading-system configuration changes as a tamper-evident chain.

    Each processed request -- approved *or* rejected -- is assigned a
    monotonically increasing sequence number and chained to the previous record
    via its SHA-256 ``record_hash``. Rejected attempts are emitted at WARNING
    because a failed change attempt (a missing justification, an unauthenticated
    principal) is itself a supervisory event worth retaining.

    This class records; it does not authorise. Deciding whether a principal may
    change a given parameter belongs upstream -- see
    ``risk-control-configuration-change-approval-workflow``.

    Sequence assignment, chain linkage and record emission happen under a single
    lock, so concurrent callers (per-request web handlers, worker threads) cannot
    duplicate a sequence number or fork the chain, and the emitted log order
    matches the chain order.
    """

    def __init__(
        self,
        environment: str = "production",
        min_justification_chars: int = MIN_JUSTIFICATION_LENGTH,
    ) -> None:
        if min_justification_chars < 0:
            raise ValueError("min_justification_chars must be non-negative")
        self._environment = environment
        self._min_justification_chars = min_justification_chars
        self._sequence = 0
        self._prev_hash: str = GENESIS_PREV_HASH
        self._records: List[ConfigChangeRecord] = []
        self._lock = threading.RLock()

    def process_change_request(self, request: ConfigChangeRequest) -> ConfigChangeRecord:
        """Validate, record, and emit an audit entry for ``request``.

        Returns the record. The caller must apply the underlying configuration
        change only when ``record.is_approved`` is ``True``.
        """
        rejection_reason = self._validate(request)
        is_approved = rejection_reason == ""
        with self._lock:
            record = self._create_record(
                request,
                is_approved=is_approved,
                rejection_reason=rejection_reason,
            )
            self._records.append(record)
            # Emitted under the lock so the log order matches the chain order;
            # an out-of-order log is an examiner's false tamper alarm.
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
        """All emitted records, in sequence order.

        The *list* is a copy, so a caller cannot add or drop entries. The records
        in it are the live objects, not clones: mutating one corrupts the
        in-memory chain, which is precisely what :meth:`verify_integrity`
        reports. The emitted JSON line, not this list, is the record of authority.
        """
        with self._lock:
            return list(self._records)

    @property
    def chain_head_hash(self) -> str:
        """Hash of the most recent record, or ``""`` before the first.

        Publish this to storage the trading host cannot rewrite. It is the only
        thing that reveals truncation of the newest records (NIST SP 800-92
        Section 3.1: the digest must be protected from alteration).
        """
        with self._lock:
            return self._prev_hash

    def verify_integrity(self) -> Tuple[bool, Optional[str]]:
        """Verify the in-memory chain. See :func:`verify_chain`."""
        with self._lock:
            records = list(self._records)
            head = self._prev_hash
        is_intact, reason = verify_chain(records)
        if not is_intact:
            return is_intact, reason
        if records and records[-1].record_hash != head:
            return False, "chain head does not match the last record"
        return True, None

    def _validate(self, request: ConfigChangeRequest) -> str:
        """Returns ``""`` when the request is approvable, else a short reason."""
        # A record that cannot name what changed cannot support supervisory
        # reconstruction, whatever else it carries.
        if not request.parameter_name or not request.parameter_name.strip():
            return REASON_PARAMETER_NAME
        # SEC Rule 15c3-5 FAQ No. 18: for US broker-dealers with market access,
        # the reasons for a risk-control threshold modification should be
        # documented and retained. The length floor itself is an engineering
        # default -- see MIN_JUSTIFICATION_LENGTH.
        justification = request.justification.strip() if request.justification else ""
        if len(justification) < self._min_justification_chars:
            return REASON_JUSTIFICATION
        # An audit record without an authenticated principal cannot support
        # supervisory attribution, nor the identity element of the Rule 17a-4(f)
        # audit-trail alternative.
        if not request.user_id or not request.user_id.strip():
            return REASON_USER_ID
        # No-op changes are not approved, but are still recorded for forensic
        # completeness.
        if _values_are_equal(request.old_value, request.new_value):
            return REASON_NO_OP
        return ""

    def _create_record(
        self,
        request: ConfigChangeRequest,
        is_approved: bool,
        rejection_reason: str,
    ) -> ConfigChangeRecord:
        """Assign sequence and chain linkage. Caller must hold ``self._lock``."""
        self._sequence += 1
        # High-precision UTC timestamp. Ordering evidence comes from
        # sequence_number, not from this clock, which can step backwards under
        # an NTP correction.
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


def _values_are_equal(old_value: Any, new_value: Any) -> bool:
    """Whether the change is a no-op, treating an unanswerable comparison as no.

    ``==`` on an arbitrary config value can raise, or return a non-boolean whose
    truth value is ambiguous (a numpy array does both). Letting that escape would
    drop the audit record entirely, which is the one outcome this engine exists
    to prevent, so an undecidable comparison is treated as a real change and
    recorded.
    """
    try:
        return bool(old_value == new_value)
    except Exception:  # noqa: BLE001 - an arbitrary __eq__/__bool__ may raise anything
        return False
