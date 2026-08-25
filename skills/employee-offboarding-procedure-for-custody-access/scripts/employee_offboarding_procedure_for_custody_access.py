"""employee-offboarding-procedure-for-custody-access: offboarding attestation and
key-exposure risk engine for staff holding digital asset custody access.

What this module is and is not
------------------------------
It is an **attestation and escalation engine**. It records which offboarding steps
an operator has attested to completing, computes completion against the steps that
actually apply to the departing individual, and escalates when access that is still
live has exceeded its revocation SLA.

It does **not** execute anything. It does not call an identity provider, an
exchange, or a custody platform, and it cannot verify that an attested step really
happened. A tick in ``completed_steps`` is an operator's assertion; the evidence for
it (IdP deactivation log, exchange key-deletion confirmation, custody platform
signer-set change, device sanitisation record) lives outside this module and is what
an auditor will ask for.

SLAs are engineering defaults, not regulatory clocks
----------------------------------------------------
No regulator publishes a numeric deadline for revoking custody access. NIST SP
800-53 Rev. 5 PS-4 requires disabling access within an *organisation-defined* time
period; 23 NYCRR 500.7(a)(6) requires covered entities to "promptly terminate access
following departures"; the AICPA Trust Services Criteria (CC6.2/CC6.3) require
*timely* removal. The defaults here (0 hours for credential revocation — i.e.
immediate — and 24 hours for signing-key rotation) are the firm's policy choice and
must be configured to whatever the firm has actually documented. See
``references/standards.md``.

Determinism
-----------
``evaluate_offboarding_status`` accepts ``current_time_epoch``. It falls back to
``time.time()`` only as a convenience; pass it explicitly for reproducible,
auditable output.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STEP_IDP_SSO_REVOKED = "IDP_SSO_REVOKED"
STEP_EXCHANGE_API_KEYS_REVOKED = "EXCHANGE_API_KEYS_REVOKED"
STEP_CUSTODY_PORTAL_REVOKED = "CUSTODY_PORTAL_REVOKED"
STEP_MULTISIG_MPC_KEY_ROTATED = "MULTISIG_MPC_KEY_ROTATED"
STEP_HARDWARE_TOKEN_WIPED = "HARDWARE_TOKEN_WIPED"

#: Canonical step ordering. Immutable: callers must not extend the procedure by
#: mutating shared module state.
OFFBOARDING_STEPS: Tuple[str, ...] = (
    STEP_IDP_SSO_REVOKED,
    STEP_EXCHANGE_API_KEYS_REVOKED,
    STEP_CUSTODY_PORTAL_REVOKED,
    STEP_MULTISIG_MPC_KEY_ROTATED,
    STEP_HARDWARE_TOKEN_WIPED,
)
_VALID_STEPS = frozenset(OFFBOARDING_STEPS)

#: Steps that revoke a live credential the departing individual can use directly.
#: These carry the credential revocation SLA.
CREDENTIAL_REVOCATION_STEPS = frozenset({
    STEP_IDP_SSO_REVOKED,
    STEP_EXCHANGE_API_KEYS_REVOKED,
    STEP_CUSTODY_PORTAL_REVOKED,
})

#: Steps that can never be waived as not-applicable. Everyone has an identity
#: provider account, so waiving SSO revocation is always a data-entry error; and a
#: custody-key holder can never waive key rotation (enforced separately, since it
#: depends on ``held_custody_keys``).
_NEVER_WAIVABLE_STEPS = frozenset({STEP_IDP_SSO_REVOKED})

RISK_CRITICAL_KEY_EXPOSURE = "CRITICAL_KEY_EXPOSURE_RISK"
RISK_HIGH_CREDENTIAL_EXPOSURE = "HIGH_CREDENTIAL_EXPOSURE_RISK"
RISK_ELEVATED_ROTATION_PENDING = "ELEVATED_ROTATION_PENDING"
RISK_PENDING_LOW = "PENDING_LOW_RISK"
RISK_LOW = "LOW_RISK"

_SECONDS_PER_HOUR = 3600.0


class CustodyOffboardingError(ValueError):
    """Raised when an offboarding record or engine configuration is invalid.

    An offboarding audit must fail loudly. A misspelled step name silently counted
    as progress, or a waiver with no justification, produces an authoritative-looking
    completion figure that an auditor will later disprove.
    """


@dataclass
class EmployeeOffboardingRecord:
    """Attested offboarding state for one departing individual.

    Args:
        employee_id: Stable identifier used in audit logs.
        employee_name: Human-readable name for the audit report.
        role: Role at departure, e.g. ``'KEY_CUSTODIAN'``, ``'QUANT_DEV'``,
            ``'DEVOPS'``. Informational only; it does not drive scoring, because
            role titles are not a reliable proxy for what access was actually held.
        termination_time_epoch: Effective termination time, Unix epoch seconds.
            This is the moment access *should* have stopped working — normally the
            end of the individual's last authorised session, not the HR record's
            creation time. May be in the future for a pre-planned departure.
        held_custody_keys: True if the individual held a private key, an MPC key
            share, or a multi-sig signer slot. When True, key rotation cannot be
            waived and drives the critical escalation.
        completed_steps: Steps attested complete. Must be drawn from
            ``OFFBOARDING_STEPS``; duplicates are ignored.
        not_applicable_steps: Optional ``{step: justification}`` waivers for steps
            that genuinely do not apply (e.g. an individual who never held an
            exchange API key). Waived steps leave the denominator, so a 100% score
            still means "everything that applied was done". Every waiver needs a
            non-empty justification because a waiver is the one way to reach 100%
            without doing work.
    """

    employee_id: str
    employee_name: str
    role: str
    termination_time_epoch: float
    held_custody_keys: bool
    completed_steps: List[str] = field(default_factory=list)
    not_applicable_steps: Dict[str, str] = field(default_factory=dict)


@dataclass
class CustodyOffboardingAuditReport:
    """Outcome of one offboarding evaluation, suitable for retention as evidence."""

    employee_id: str
    employee_name: str
    role: str
    completion_percentage: float        # 0.0 to 100.0, over applicable steps only
    is_fully_compliant: bool
    key_exposure_risk: str              # see the RISK_* constants
    pending_steps: List[str]
    audit_notes: str
    hours_since_termination: float = 0.0
    overdue_steps: List[str] = field(default_factory=list)
    waived_steps: List[str] = field(default_factory=list)


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CustodyOffboardingError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _require_finite(value: object, name: str, *, minimum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CustodyOffboardingError(f"{name} must be a finite number, got {value!r}")
    numeric = float(value)
    if minimum is not None and numeric < minimum:
        raise CustodyOffboardingError(f"{name} must be >= {minimum}, got {numeric!r}")
    return numeric


class CustodyOffboardingEngine:
    """Scores offboarding completeness and escalates overdue custody access.

    Args:
        key_rotation_sla_hours: Hours after termination by which a departing key
            holder's signing material must be rotated out. Default 24.0 is a firm
            policy default with no regulatory basis.
        credential_revocation_sla_hours: Hours after termination by which SSO,
            exchange API keys and custody portal access must be revoked. Default 0.0
            encodes "immediately" — any credential step still pending after the
            effective termination time is overdue. Raise it only to a grace period
            the firm has actually documented.
    """

    def __init__(
        self,
        key_rotation_sla_hours: float = 24.0,
        credential_revocation_sla_hours: float = 0.0,
    ) -> None:
        self.key_rotation_sla_hours = _require_finite(
            key_rotation_sla_hours, "key_rotation_sla_hours", minimum=0.0)
        self.credential_revocation_sla_hours = _require_finite(
            credential_revocation_sla_hours, "credential_revocation_sla_hours", minimum=0.0)

    def evaluate_offboarding_status(
        self,
        record: EmployeeOffboardingRecord,
        current_time_epoch: Optional[float] = None,
    ) -> CustodyOffboardingAuditReport:
        """Audit one offboarding record and classify residual key-exposure risk.

        Raises:
            CustodyOffboardingError: If the record is malformed — an unrecognised
                step name, a waiver without justification, a step both completed and
                waived, a non-finite timestamp, or a waiver of a step that can never
                be waived.
        """
        self._validate_record(record)
        now = (time.time() if current_time_epoch is None
               else _require_finite(current_time_epoch, "current_time_epoch"))
        elapsed_hours = (now - record.termination_time_epoch) / _SECONDS_PER_HOUR

        waived = [s for s in OFFBOARDING_STEPS if s in record.not_applicable_steps]
        applicable = [s for s in OFFBOARDING_STEPS if s not in record.not_applicable_steps]
        done = {s for s in record.completed_steps}
        pending = [s for s in applicable if s not in done]

        completion_pct = round(
            (len(applicable) - len(pending)) / float(len(applicable)) * 100.0, 1)
        is_compliant = not pending

        overdue = self._overdue_steps(record, pending, elapsed_hours)
        risk_level = self._classify_risk(record, pending, overdue)
        notes = self._compose_notes(
            record, completion_pct, pending, overdue, waived, elapsed_hours, risk_level)

        if risk_level == RISK_CRITICAL_KEY_EXPOSURE:
            logger.critical(notes)
        elif risk_level == RISK_HIGH_CREDENTIAL_EXPOSURE:
            logger.error(notes)
        elif not is_compliant:
            logger.warning(notes)
        else:
            logger.info(notes)

        return CustodyOffboardingAuditReport(
            employee_id=record.employee_id,
            employee_name=record.employee_name,
            role=record.role,
            completion_percentage=completion_pct,
            is_fully_compliant=is_compliant,
            key_exposure_risk=risk_level,
            pending_steps=pending,
            audit_notes=notes,
            hours_since_termination=round(elapsed_hours, 2),
            overdue_steps=overdue,
            waived_steps=waived,
        )

    # -- internals ---------------------------------------------------------

    def _validate_record(self, record: EmployeeOffboardingRecord) -> None:
        _require_text(record.employee_id, "employee_id")
        _require_text(record.employee_name, "employee_name")
        _require_text(record.role, "role")
        _require_finite(record.termination_time_epoch, "termination_time_epoch")
        if not isinstance(record.held_custody_keys, bool):
            raise CustodyOffboardingError(
                f"held_custody_keys must be a bool, got {record.held_custody_keys!r}")

        # A bare string is iterable, so without this guard "IDP_SSO_REVOKED" would be
        # read character by character -- a plausible agent-written mistake.
        if isinstance(record.completed_steps, str) or not isinstance(
                record.completed_steps, (list, tuple)):
            raise CustodyOffboardingError(
                f"completed_steps must be a list of step names, got "
                f"{record.completed_steps!r}")
        if not isinstance(record.not_applicable_steps, dict):
            raise CustodyOffboardingError(
                f"not_applicable_steps must be a {{step: justification}} mapping, got "
                f"{record.not_applicable_steps!r}")

        unknown = [s for s in record.completed_steps if s not in _VALID_STEPS]
        if unknown:
            raise CustodyOffboardingError(
                f"unrecognised completed_steps {unknown!r}; valid steps are "
                f"{list(OFFBOARDING_STEPS)!r}")

        for step, justification in record.not_applicable_steps.items():
            if step not in _VALID_STEPS:
                raise CustodyOffboardingError(
                    f"unrecognised not_applicable_steps entry {step!r}; valid steps "
                    f"are {list(OFFBOARDING_STEPS)!r}")
            _require_text(justification, f"not_applicable_steps[{step!r}] justification")
            if step in _NEVER_WAIVABLE_STEPS:
                raise CustodyOffboardingError(
                    f"{step} can never be waived as not-applicable")
            if step in record.completed_steps:
                raise CustodyOffboardingError(
                    f"{step} is marked both completed and not-applicable")

        if (record.held_custody_keys
                and STEP_MULTISIG_MPC_KEY_ROTATED in record.not_applicable_steps):
            raise CustodyOffboardingError(
                f"{STEP_MULTISIG_MPC_KEY_ROTATED} cannot be waived when "
                "held_custody_keys is True")

    def _overdue_steps(
        self,
        record: EmployeeOffboardingRecord,
        pending: List[str],
        elapsed_hours: float,
    ) -> List[str]:
        """Pending steps whose SLA has elapsed.

        A termination timestamp in the future (a pre-planned departure, or clock
        skew between the HR system and this host) yields negative elapsed hours and
        nothing overdue — access is not late before it is due.
        """
        overdue: List[str] = []
        for step in pending:
            if step in CREDENTIAL_REVOCATION_STEPS:
                if elapsed_hours > self.credential_revocation_sla_hours:
                    overdue.append(step)
            elif step == STEP_MULTISIG_MPC_KEY_ROTATED and record.held_custody_keys:
                if elapsed_hours > self.key_rotation_sla_hours:
                    overdue.append(step)
        return overdue

    @staticmethod
    def _classify_risk(
        record: EmployeeOffboardingRecord,
        pending: List[str],
        overdue: List[str],
    ) -> str:
        if STEP_MULTISIG_MPC_KEY_ROTATED in overdue:
            return RISK_CRITICAL_KEY_EXPOSURE
        if any(step in CREDENTIAL_REVOCATION_STEPS for step in overdue):
            return RISK_HIGH_CREDENTIAL_EXPOSURE
        if record.held_custody_keys and STEP_MULTISIG_MPC_KEY_ROTATED in pending:
            return RISK_ELEVATED_ROTATION_PENDING
        if pending:
            return RISK_PENDING_LOW
        return RISK_LOW

    @staticmethod
    def _compose_notes(
        record: EmployeeOffboardingRecord,
        completion_pct: float,
        pending: List[str],
        overdue: List[str],
        waived: List[str],
        elapsed_hours: float,
        risk_level: str,
    ) -> str:
        waived_note = f" Waived as not-applicable: {waived}." if waived else ""
        if risk_level == RISK_CRITICAL_KEY_EXPOSURE:
            return (
                f"CRITICAL KEY EXPOSURE RISK [{record.employee_id}]: departing "
                f"individual held signing material, and {STEP_MULTISIG_MPC_KEY_ROTATED} "
                f"is still incomplete {elapsed_hours:.1f}h after termination. "
                f"Overdue: {overdue}. Pending: {pending}.{waived_note}")
        if risk_level == RISK_HIGH_CREDENTIAL_EXPOSURE:
            return (
                f"HIGH CREDENTIAL EXPOSURE RISK [{record.employee_id}]: live "
                f"credentials remain past their revocation SLA {elapsed_hours:.1f}h "
                f"after termination. Overdue: {overdue}. Pending: {pending}."
                f"{waived_note}")
        if risk_level == RISK_ELEVATED_ROTATION_PENDING:
            return (
                f"ROTATION PENDING WITHIN SLA [{record.employee_id}]: "
                f"{completion_pct}% complete, signing-key rotation still open "
                f"{elapsed_hours:.1f}h after termination. Pending: {pending}."
                f"{waived_note}")
        if risk_level == RISK_PENDING_LOW:
            return (
                f"OFFBOARDING IN PROGRESS [{record.employee_id}]: {completion_pct}% "
                f"complete. Pending: {pending}.{waived_note}")
        return (
            f"OFFBOARDING COMPLIANT [{record.employee_id}]: 100% of applicable "
            f"custody offboarding steps attested complete.{waived_note}")
