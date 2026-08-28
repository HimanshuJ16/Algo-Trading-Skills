"""
risk-control-bypass-audit-logging: tamper-evident audit trail for manual overrides
of pre-trade and intra-trade risk controls.

What this module is and is not
------------------------------
It is an **append-only, hash-chained record** of every occasion on which a risk
control was manually bypassed, together with a severity classification and a set
of suspicion flags. It is *not* an enforcement gate: it records that a bypass
happened, it does not decide whether one may happen. Authorisation enforcement
belongs in the risk control itself, upstream of this logger.

Tamper-evident, not tamper-proof
--------------------------------
Each entry commits to its predecessor through a SHA-256 chain, so any edit,
deletion or reordering of an in-memory entry is *detectable* via
``verify_integrity()``. That is not the same as immutability: an attacker who can
rewrite the process memory or the persisted file can recompute the whole chain.
Genuine immutability requires the records to be written to storage the trading
system cannot rewrite. Under SEA Rule 17a-4(f)(2)(i)(A) a US broker-dealer's
electronic records must be preserved either in a non-rewriteable, non-erasable
(WORM) format or under the audit-trail alternative, which requires a complete
time-stamped trail of all modifications and deletions, the date and time of each
create/modify/delete action, and the identity of the individual responsible.
Publish the chain head to that storage; do not treat this object as the system of
record. See ``references/standards.md``.

Jurisdiction
------------
The obligations that make this record necessary attach to *regulated entities*,
not to everyone running an algorithm:

* **US** -- SEA Rule 15c3-5 applies to broker-dealers with market access. SEC
  Division of Trading and Markets FAQ No. 18 states that where a threshold is
  raised after orders were rejected, "the reasons for such modifications should
  be documented and retained as part of the broker-dealer's books and records."
* **EU/UK** -- RTS 6 (Commission Delegated Regulation (EU) 2017/589, assimilated
  in the UK) Article 15(6) governs orders blocked by pre-trade controls that the
  firm nevertheless wishes to submit.

Neither regime is universal. Do not present this engine's output as satisfying an
obligation the firm is not actually subject to.

Determinism
-----------
``log_bypass`` accepts ``recorded_at``. It defaults to the current UTC time only
as a convenience; pass it explicitly for reproducible output and in tests.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"

STATUS_NO_BYPASSES = "NO_BYPASSES"
STATUS_BYPASSES_LOGGED = "BYPASSES_LOGGED"
STATUS_SUSPICIOUS_BYPASSES = "SUSPICIOUS_BYPASSES_DETECTED"

#: Controls whose bypass removes a capital-protection or halt mechanism. Any
#: bypass of these is CRITICAL regardless of size or duration.
CRITICAL_CONTROLS: FrozenSet[str] = frozenset({
    "MAX_POSITION_SIZE", "DAILY_LOSS_LIMIT", "PORTFOLIO_VAR_LIMIT",
    "KILL_SWITCH", "MARGIN_CALL_HALT",
})

#: The pre-trade control types RTS 6 Article 15(1) requires an in-scope firm to
#: operate: price collars, maximum order values, maximum order volumes and
#: maximum message limits. Bypassing a *mandated* control is never routine, so
#: these are classified HIGH explicitly rather than left to the name heuristic --
#: "MAX_ORDER_VALUE" contains neither "LIMIT" nor "CAP" and would otherwise fall
#: through to MEDIUM.
HIGH_SEVERITY_CONTROLS: FrozenSet[str] = frozenset({
    "PRICE_COLLAR", "MAX_ORDER_VALUE", "MAX_ORDER_VOLUME",
    "MAX_MESSAGE_RATE", "MAX_MESSAGE_LIMIT", "MAX_ORDER_RATE",
    "REPEATED_EXECUTION_THROTTLE",
})

#: Example allowlist. Every deployment must replace this with the firm's own
#: designated individuals -- see RTS 6 Article 15(6).
DEFAULT_AUTHORIZED_PRINCIPALS: FrozenSet[str] = frozenset({
    "risk_officer", "cro", "head_of_trading", "system_admin",
})

#: Engineering default with **no regulatory basis**. A length check cannot judge
#: whether a justification is adequate; it only catches an empty or placeholder
#: field. Adequacy is a human review question.
DEFAULT_MIN_JUSTIFICATION_CHARS = 5

#: Tolerance for an event timestamp that sits ahead of the recording clock.
#: Beyond this the entry is flagged: either the clocks disagree or the record is
#: forward-dated, and both matter to a forensic reader.
DEFAULT_CLOCK_SKEW_TOLERANCE = timedelta(seconds=5)

_GENESIS_HASH = "0" * 64


class RiskBypassAuditError(ValueError):
    """Raised when a bypass record is structurally unusable or contradicts the chain.

    Structural defects -- no event id, an unparseable or timezone-naive timestamp,
    a duplicate id carrying different content -- make a record unaddressable or
    unorderable, which defeats the purpose of keeping it. The engine refuses the
    record so the caller fixes and resubmits.

    Never swallow this exception and drop the event: a bypass that happened but
    was not recorded is precisely the gap the audit trail exists to close.
    """


@dataclass
class RiskBypassEvent:
    """A single manual override of a risk control, as reported by the caller.

    ``authorized_by`` is the individual who authorised the override. Under RTS 6
    Article 15(6) an in-scope firm additionally needs the override verified by the
    risk management function, and RTS 6 Article 1(c) requires trading desks to be
    separated from risk control and compliance "to ensure that unauthorised
    trading activity cannot be concealed" -- so ``requested_by`` and
    ``risk_function_verifier`` are recorded separately and checked against each
    other. They default to empty for backward compatibility; supply them.

    ``expires_at_iso`` records that the override was temporary. RTS 6 Article
    15(6) permits override procedures only "in relation to a specific trade on a
    temporary basis and in exceptional circumstances"; an override with no expiry
    is a permanent disablement wearing an override's clothes.
    """

    event_id: str
    timestamp_iso: str                        # when the bypass occurred (tz-aware ISO-8601)
    bypassed_control: str                     # e.g. 'MAX_POSITION_SIZE', 'KILL_SWITCH'
    original_limit_value: str
    override_value: str
    authorized_by: str                        # individual who authorised the override
    justification: str
    strategy_id: str = ""
    instrument: str = ""
    requested_by: str = ""                    # who asked for the override
    risk_function_verifier: str = ""          # RTS 6 Art. 15(6) risk management verification
    expires_at_iso: Optional[str] = None      # when the override lapses; None = open-ended


@dataclass
class BypassAuditEntry:
    """The engine's classification of one event, and its position in the chain.

    Produced once, at log time, and returned unchanged by
    :meth:`RiskControlBypassAuditEngine.generate_audit_report`. The report must
    never re-derive severity or suspicion: two different verdicts on one event in
    the same regulatory record is worse than either verdict alone.
    """

    event_id: str
    timestamp_iso: str
    bypassed_control: str
    authorized_by: str
    severity: str                             # SEVERITY_CRITICAL / _HIGH / _MEDIUM
    is_suspicious: bool
    flag_reason: Optional[str]                # flag_reasons joined, for display
    flag_reasons: List[str] = field(default_factory=list)
    sequence_number: int = 0
    recorded_at_iso: str = ""                 # when the engine recorded it
    previous_hash: str = _GENESIS_HASH
    record_hash: str = ""


@dataclass
class RiskBypassAuditReport:
    total_bypass_events: int
    critical_count: int
    suspicious_count: int
    entries: List[BypassAuditEntry]
    status: str                               # STATUS_* constant
    audit_notes: str
    severity_counts: Dict[str, int] = field(default_factory=dict)
    integrity_verified: bool = True
    chain_head_hash: str = _GENESIS_HASH


def _parse_timestamp(value: str, field_name: str) -> datetime:
    """Parse a timezone-aware ISO-8601 timestamp, or raise.

    A naive timestamp is rejected rather than assumed to be UTC. An audit trail
    whose entries carry ambiguous local times cannot be reliably ordered across a
    DST transition, and the ordering is the evidence.
    """
    if not isinstance(value, str) or not value.strip():
        raise RiskBypassAuditError(
            f"{field_name} is required and must be a non-empty string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RiskBypassAuditError(
            f"{field_name} {value!r} is not a parseable ISO-8601 timestamp: {exc}"
        ) from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise RiskBypassAuditError(
            f"{field_name} {value!r} is timezone-naive; supply an explicit UTC offset "
            "so the audit trail can be ordered unambiguously"
        )
    return parsed


def _normalize_principal(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _copy_entry(entry: BypassAuditEntry) -> BypassAuditEntry:
    """A copy safe to hand out.

    ``dataclasses.replace`` copies field *references*, so a bare ``replace`` would
    share the ``flag_reasons`` list with the stored entry and let a caller mutate
    the trail through what looks like an exported copy.
    """
    return replace(entry, flag_reasons=list(entry.flag_reasons))


class RiskControlBypassAuditEngine:
    """Append-only, hash-chained audit trail of manual risk control bypasses.

    The engine classifies each bypass by severity, flags the patterns that make a
    bypass suspicious on its face, and chains the entries so later tampering is
    detectable. It records; it does not authorise. Enforcement of who may bypass
    what belongs in the risk control, upstream of this object.

    Args:
        authorized_principals: Individuals permitted to authorise an override.
            Compared case-insensitively after stripping. The module default is an
            example; replace it with the firm's designated individuals.
        critical_controls: Controls whose bypass is always CRITICAL.
        high_severity_controls: Controls whose bypass is at least HIGH. Defaults
            to the RTS 6 Article 15(1) mandated control types.
        min_justification_chars: Minimum justification length. An engineering
            default with no regulatory basis; it catches empty and placeholder
            fields, nothing more.
        require_risk_function_verification: Flag a bypass carrying no
            ``risk_function_verifier``. Enable for firms in scope of RTS 6
            Article 15(6); off by default because the requirement is
            jurisdiction-specific.
        require_expiry_for_critical: Flag a CRITICAL bypass carrying no
            ``expires_at_iso``. Off by default for the same reason; RTS 6
            Article 15(6) is where the "temporary basis" wording comes from.
        clock_skew_tolerance: How far an event timestamp may lead the recording
            clock before the entry is flagged as forward-dated.
    """

    def __init__(
        self,
        authorized_principals: Optional[Iterable[str]] = None,
        critical_controls: Optional[Iterable[str]] = None,
        high_severity_controls: Optional[Iterable[str]] = None,
        min_justification_chars: int = DEFAULT_MIN_JUSTIFICATION_CHARS,
        require_risk_function_verification: bool = False,
        require_expiry_for_critical: bool = False,
        clock_skew_tolerance: timedelta = DEFAULT_CLOCK_SKEW_TOLERANCE,
    ) -> None:
        if min_justification_chars < 0:
            raise RiskBypassAuditError("min_justification_chars must be non-negative")
        if clock_skew_tolerance < timedelta(0):
            raise RiskBypassAuditError("clock_skew_tolerance must be non-negative")

        principals = (DEFAULT_AUTHORIZED_PRINCIPALS if authorized_principals is None
                      else authorized_principals)
        self.authorized_principals: FrozenSet[str] = frozenset(
            _normalize_principal(p) for p in principals
        )
        self.critical_controls: FrozenSet[str] = frozenset(
            c.strip().upper() for c in
            (CRITICAL_CONTROLS if critical_controls is None else critical_controls)
        )
        self.high_severity_controls: FrozenSet[str] = frozenset(
            c.strip().upper() for c in
            (HIGH_SEVERITY_CONTROLS if high_severity_controls is None
             else high_severity_controls)
        )
        self.min_justification_chars = min_justification_chars
        self.require_risk_function_verification = require_risk_function_verification
        self.require_expiry_for_critical = require_expiry_for_critical
        self.clock_skew_tolerance = clock_skew_tolerance

        # Reentrant so generate_audit_report can hold the lock across its own
        # integrity check -- a report may not claim integrity_verified for a set
        # of entries that changed between the check and the summary.
        self._lock = threading.RLock()
        self._log: List[RiskBypassEvent] = []
        self._entries: List[BypassAuditEntry] = []
        self._by_event_id: Dict[str, int] = {}
        self._chain_head: str = _GENESIS_HASH

    # ------------------------------------------------------------------ logging

    def log_bypass(
        self,
        event: RiskBypassEvent,
        recorded_at: Optional[datetime] = None,
    ) -> BypassAuditEntry:
        """Record one bypass event, classify it, and append it to the chain.

        Re-submitting an id already in the chain with identical content is an
        idempotent no-op that returns the original entry -- a retried write must
        not create a second record of one bypass. Re-submitting an id with
        *different* content raises: that is either an id collision or an attempt
        to restate history, and both need a human.

        Args:
            event: The bypass to record.
            recorded_at: Timezone-aware recording time. Defaults to now (UTC).

        Returns:
            The classified, chained entry.

        Raises:
            RiskBypassAuditError: The record is structurally unusable, or an
                existing id was resubmitted with different content.
        """
        if not isinstance(event, RiskBypassEvent):
            raise RiskBypassAuditError(
                f"event must be a RiskBypassEvent, got {type(event)!r}")
        if not isinstance(event.event_id, str) or not event.event_id.strip():
            raise RiskBypassAuditError(
                "event_id is required: an unaddressable audit record is unusable")
        if not isinstance(event.bypassed_control, str) or not event.bypassed_control.strip():
            raise RiskBypassAuditError(
                f"bypassed_control is required for event {event.event_id!r}")

        event_ts = _parse_timestamp(event.timestamp_iso, "timestamp_iso")
        expires_ts = (
            _parse_timestamp(event.expires_at_iso, "expires_at_iso")
            if event.expires_at_iso is not None else None
        )

        if recorded_at is None:
            recorded_at = datetime.now(timezone.utc)
        elif not isinstance(recorded_at, datetime):
            raise RiskBypassAuditError(
                f"recorded_at must be a datetime, got {recorded_at!r}")
        elif recorded_at.tzinfo is None or recorded_at.tzinfo.utcoffset(recorded_at) is None:
            raise RiskBypassAuditError("recorded_at must be timezone-aware")

        event_id = event.event_id.strip()
        severity = self._classify_severity(event.bypassed_control)
        flag_reasons = self._detect_suspicious(
            event, severity, event_ts, expires_ts, recorded_at)

        with self._lock:
            existing_index = self._by_event_id.get(event_id)
            if existing_index is not None:
                if self._log[existing_index] == event:
                    logger.info(
                        "DUPLICATE BYPASS SUBMISSION ignored (idempotent): event_id=%s",
                        event_id)
                    return _copy_entry(self._entries[existing_index])
                raise RiskBypassAuditError(
                    f"event_id {event_id!r} already recorded with different content; "
                    "an audit record is never restated -- log a new corrective event instead"
                )

            sequence_number = len(self._entries)
            previous_hash = self._chain_head
            recorded_at_iso = recorded_at.isoformat()
            record_hash = self._compute_hash(
                event=event,
                severity=severity,
                flag_reasons=flag_reasons,
                sequence_number=sequence_number,
                recorded_at_iso=recorded_at_iso,
                previous_hash=previous_hash,
            )

            entry = BypassAuditEntry(
                event_id=event_id,
                timestamp_iso=event.timestamp_iso,
                bypassed_control=event.bypassed_control,
                authorized_by=event.authorized_by,
                severity=severity,
                is_suspicious=bool(flag_reasons),
                flag_reason=" ".join(flag_reasons) if flag_reasons else None,
                flag_reasons=list(flag_reasons),
                sequence_number=sequence_number,
                recorded_at_iso=recorded_at_iso,
                previous_hash=previous_hash,
                record_hash=record_hash,
            )

            # Store a copy: an audit record must not change because the caller
            # reused or mutated their own event object afterwards.
            self._log.append(replace(event))
            self._entries.append(entry)
            self._by_event_id[event_id] = sequence_number
            self._chain_head = record_hash

        if entry.is_suspicious:
            logger.warning(
                "SUSPICIOUS BYPASS [%s] seq=%d: %s by '%s' (%s)",
                severity, entry.sequence_number, event.bypassed_control,
                event.authorized_by, entry.flag_reason,
            )
        else:
            logger.info(
                "BYPASS LOGGED [%s] seq=%d: %s by '%s'",
                severity, entry.sequence_number, event.bypassed_control,
                event.authorized_by,
            )
        return _copy_entry(entry)

    # ------------------------------------------------------------ classification

    def _classify_severity(self, bypassed_control: str) -> str:
        control = bypassed_control.strip().upper()
        if control in self.critical_controls:
            return SEVERITY_CRITICAL
        if control in self.high_severity_controls:
            return SEVERITY_HIGH
        # Fallback heuristic for control names the caller has not registered. It
        # is a convenience, not a classification policy: register the firm's own
        # control names in critical_controls / high_severity_controls.
        if "LIMIT" in control or "CAP" in control:
            return SEVERITY_HIGH
        return SEVERITY_MEDIUM

    def _detect_suspicious(
        self,
        event: RiskBypassEvent,
        severity: str,
        event_ts: datetime,
        expires_ts: Optional[datetime],
        recorded_at: datetime,
    ) -> List[str]:
        reasons: List[str] = []

        principal = _normalize_principal(event.authorized_by)
        if not principal:
            reasons.append("No authorising principal recorded.")
        elif principal not in self.authorized_principals:
            reasons.append(
                f"Unauthorized principal '{event.authorized_by}' bypassed control.")

        justification = (event.justification.strip()
                         if isinstance(event.justification, str) else "")
        if len(justification) < self.min_justification_chars:
            reasons.append("Missing or insufficient justification.")

        requester = _normalize_principal(event.requested_by)
        if requester and principal and requester == principal:
            reasons.append(
                "Self-authorised: requester and authoriser are the same individual.")

        if self.require_risk_function_verification and not _normalize_principal(
                event.risk_function_verifier):
            reasons.append("No risk management function verification recorded.")

        if expires_ts is not None and expires_ts <= event_ts:
            reasons.append("Override expiry is not after the bypass timestamp.")
        elif (expires_ts is None and self.require_expiry_for_critical
                and severity == SEVERITY_CRITICAL):
            reasons.append("Open-ended bypass of a critical control (no expiry recorded).")

        if event_ts - recorded_at > self.clock_skew_tolerance:
            reasons.append(
                "Event timestamp is ahead of the recording clock "
                "(forward-dated or clock skew).")

        return reasons

    # ------------------------------------------------------------------ integrity

    @staticmethod
    def _compute_hash(
        event: RiskBypassEvent,
        severity: str,
        flag_reasons: Sequence[str],
        sequence_number: int,
        recorded_at_iso: str,
        previous_hash: str,
    ) -> str:
        """SHA-256 over the canonical serialisation of the entry and its predecessor."""
        payload = {
            "previous_hash": previous_hash,
            "sequence_number": sequence_number,
            "recorded_at_iso": recorded_at_iso,
            "event_id": event.event_id.strip(),
            "timestamp_iso": event.timestamp_iso,
            "bypassed_control": event.bypassed_control,
            "original_limit_value": event.original_limit_value,
            "override_value": event.override_value,
            "authorized_by": event.authorized_by,
            "justification": event.justification,
            "strategy_id": event.strategy_id,
            "instrument": event.instrument,
            "requested_by": event.requested_by,
            "risk_function_verifier": event.risk_function_verifier,
            "expires_at_iso": event.expires_at_iso,
            "severity": severity,
            "flag_reasons": list(flag_reasons),
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def verify_integrity(self) -> Tuple[bool, Optional[str]]:
        """Recompute the hash chain and report the first inconsistency, if any.

        Detects edits, deletions and reordering of the in-memory entries. It
        cannot detect an attacker who recomputed the whole chain, which is why the
        chain head must be published to storage the trading system cannot rewrite.

        Returns:
            ``(True, None)`` when the chain is consistent, otherwise
            ``(False, reason)`` naming the first entry that fails.
        """
        with self._lock:
            entries = list(self._entries)
            events = list(self._log)
            head = self._chain_head

        if len(entries) != len(events):
            return False, "entry and event counts diverge"

        previous = _GENESIS_HASH
        for index, (entry, event) in enumerate(zip(entries, events)):
            if entry.sequence_number != index:
                return False, (
                    f"entry {entry.event_id!r} has sequence_number "
                    f"{entry.sequence_number}, expected {index}")
            if entry.previous_hash != previous:
                return False, f"entry {entry.event_id!r} does not link to its predecessor"
            expected = self._compute_hash(
                event=event,
                severity=entry.severity,
                flag_reasons=entry.flag_reasons,
                sequence_number=entry.sequence_number,
                recorded_at_iso=entry.recorded_at_iso,
                previous_hash=entry.previous_hash,
            )
            if entry.record_hash != expected:
                return False, f"entry {entry.event_id!r} hash does not match its content"
            previous = expected

        if previous != head:
            return False, "chain head does not match the last entry"
        return True, None

    @property
    def chain_head_hash(self) -> str:
        """Hash of the most recent entry. Publish this to append-only storage."""
        with self._lock:
            return self._chain_head

    @property
    def entries(self) -> Tuple[BypassAuditEntry, ...]:
        """Defensive copies of the recorded entries, in order."""
        with self._lock:
            return tuple(_copy_entry(e) for e in self._entries)

    # -------------------------------------------------------------------- report

    def generate_audit_report(self) -> RiskBypassAuditReport:
        """Summarise the chain. Classification is read, never re-derived.

        The report reproduces the severity and suspicion verdict fixed at log
        time. Re-deriving them here would let the same event carry two different
        verdicts in one regulatory record.
        """
        with self._lock:
            integrity_ok, integrity_reason = self.verify_integrity()
            entries = [_copy_entry(e) for e in self._entries]
            head = self._chain_head

        severity_counts = {SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 0}
        suspicious = 0
        for entry in entries:
            severity_counts[entry.severity] = severity_counts.get(entry.severity, 0) + 1
            if entry.is_suspicious:
                suspicious += 1

        critical = severity_counts[SEVERITY_CRITICAL]
        total = len(entries)
        if total == 0:
            status = STATUS_NO_BYPASSES
        elif suspicious > 0:
            status = STATUS_SUSPICIOUS_BYPASSES
        else:
            status = STATUS_BYPASSES_LOGGED

        notes = (
            f"RISK BYPASS AUDIT [{status}]: Total = {total}, "
            f"Critical = {critical}, Suspicious = {suspicious}."
        )
        if not integrity_ok:
            notes += f" INTEGRITY FAILURE: {integrity_reason}."
            logger.error(notes)
        elif suspicious > 0:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RiskBypassAuditReport(
            total_bypass_events=total,
            critical_count=critical,
            suspicious_count=suspicious,
            entries=entries,
            status=status,
            audit_notes=notes,
            severity_counts=severity_counts,
            integrity_verified=integrity_ok,
            chain_head_hash=head,
        )
