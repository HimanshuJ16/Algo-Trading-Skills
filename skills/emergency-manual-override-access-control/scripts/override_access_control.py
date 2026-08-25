"""Break-glass emergency manual override access control for trading systems.

Governs *who* may fire an emergency manual override (kill switch, strategy halt,
order pause), under *what* authorisation quorum, for *how long*, and with what
audit evidence.

Regulatory anchors (full citations in ``references/standards.md``):

* Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6) Art. 12
  requires the firm to be able to cancel any or all unexecuted orders
  immediately as an emergency measure; Art. 15(6) requires exceptional handling
  of blocked orders to be verified by the risk management function and
  authorised by a designated individual; Art. 18(5) requires the firm to
  identify and restrict persons with critical access rights and to monitor that
  access with complete traceability.
* SEC Rule 17 CFR 240.15c3-5(b) requires documented risk management controls;
  SEC staff FAQ No. 18 states the reasons for threshold modifications should be
  documented and retained as part of the broker-dealer's books and records.
* NIST SP 800-53 Rev. 5 AC-2(2) (automatic removal/disabling of emergency
  accounts after an organisation-defined period), AC-6(9) (log the use of
  privileged functions), AU-9(3) (cryptographic protection of audit information).

Design notes:

* Every decision -- approvals **and** denials -- is hashed into an append-only
  chain (Schneier & Kelsey, ACM TISSEC 2(2), 1999). A denied break-glass attempt
  is precisely the event an investigator needs.
* The chain is *tamper-evident*, not tamper-proof. With the default unkeyed
  SHA-256 (FIPS 180-4) anyone able to rewrite the records can recompute the
  chain; supply ``audit_hmac_key`` for keyed HMAC-SHA-256 (FIPS 198-1) and ship
  records to an append-only sink so that rewriting is detectable.
* This module is a decision and evidence engine. It does not itself cancel
  orders -- wire an approved report into the kill-switch executor.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SEVERITY_CRITICAL = "SEVERITY_CRITICAL"
SEVERITY_HIGH = "SEVERITY_HIGH"

APPROVAL_DUAL_SIGN_OFF = "DUAL_SIGN_OFF"
APPROVAL_BREAK_GLASS = "BREAK_GLASS"
APPROVAL_SINGLE_OPERATOR = "SINGLE_OPERATOR"

# Default RBAC roles. Library defaults reflecting a common desk structure, not a
# regulatory list: RTS 6 Art. 15(6) requires a *designated* individual, it does
# not name titles. Override via OverridePolicy.
DEFAULT_AUTHORIZED_ROLES: FrozenSet[str] = frozenset(
    {"RISK_OFFICER", "HEAD_TRADER", "CTO", "MANAGING_DIRECTOR"}
)

# Actions classified as firm-wide critical. Anything not listed is SEVERITY_HIGH,
# so an unrecognised action can never be silently promoted to a firm-wide kill
# switch without appearing here.
DEFAULT_CRITICAL_ACTIONS: FrozenSet[str] = frozenset({"KILL_SWITCH_ALL_ALGOS"})

DEFAULT_MAX_TTL_MINUTES = 60
DEFAULT_MIN_JUSTIFICATION_CHARS = 10

# Machine-readable rejection codes (stable contract for alerting/surveillance).
REJECT_INVALID_FIELD = "INVALID_FIELD"
REJECT_MISSING_JUSTIFICATION = "MISSING_JUSTIFICATION"
REJECT_UNAUTHORIZED_ROLE = "UNAUTHORIZED_ROLE"
REJECT_DUAL_SIGN_OFF_REQUIRED = "DUAL_SIGN_OFF_REQUIRED"
REJECT_SELF_APPROVAL = "SELF_APPROVAL"
REJECT_SECONDARY_ROLE_UNAUTHORIZED = "SECONDARY_ROLE_UNAUTHORIZED"
REJECT_BREAK_GLASS_INVALID = "BREAK_GLASS_INVALID"
REJECT_BREAK_GLASS_NOT_CONFIGURED = "BREAK_GLASS_NOT_CONFIGURED"
REJECT_INVALID_TTL = "INVALID_TTL"
REJECT_DUPLICATE_REQUEST_ID = "DUPLICATE_REQUEST_ID"


class OverrideAccessError(Exception):
    """Raised for engine misconfiguration or misuse, never for a denied override."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    """UTC ISO-8601 with millisecond precision."""
    if not isinstance(ts, datetime):
        raise OverrideAccessError(
            "timestamp must be a timezone-aware datetime "
            "(2.0.0 replaced the epoch-float parameter)"
        )
    if ts.tzinfo is None:
        raise OverrideAccessError("timestamps must be timezone-aware UTC")
    return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _norm_id(value: Optional[str]) -> str:
    """Case-fold and strip an identity, for comparison purposes only."""
    return (value or "").strip().casefold()


def _norm_role(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _canonical(fields: Sequence[Tuple[str, str]]) -> str:
    """Length-prefixed canonical encoding: ``key:<len>:<value>`` per line.

    Length prefixing defeats a field-boundary attack: without it,
    ``operator='a|b'`` and ``operator='a'`` followed by a field starting ``b``
    hash identically under a ``|``-joined payload.
    """
    return "\n".join(f"{key}:{len(value)}:{value}" for key, value in fields)


@dataclass(frozen=True)
class OverridePolicy:
    """Firm-configurable RBAC and quorum policy.

    Every value here is firm policy, not a regulatory constant.
    ``max_ttl_minutes`` in particular is a house default: no regulator publishes
    a maximum break-glass duration.
    """

    authorized_roles: FrozenSet[str] = DEFAULT_AUTHORIZED_ROLES
    critical_actions: FrozenSet[str] = DEFAULT_CRITICAL_ACTIONS
    critical_approver_roles: Optional[FrozenSet[str]] = None
    max_ttl_minutes: int = DEFAULT_MAX_TTL_MINUTES
    min_justification_chars: int = DEFAULT_MIN_JUSTIFICATION_CHARS

    def __post_init__(self) -> None:
        if not self.authorized_roles:
            raise OverrideAccessError("authorized_roles must not be empty")
        if self.max_ttl_minutes < 1:
            raise OverrideAccessError("max_ttl_minutes must be >= 1")
        if self.min_justification_chars < 1:
            raise OverrideAccessError("min_justification_chars must be >= 1")
        object.__setattr__(
            self, "authorized_roles",
            frozenset(_norm_role(r) for r in self.authorized_roles),
        )
        object.__setattr__(
            self, "critical_actions",
            frozenset(_norm_role(a) for a in self.critical_actions),
        )
        if self.critical_approver_roles is not None:
            approvers = frozenset(_norm_role(r) for r in self.critical_approver_roles)
            if not approvers:
                raise OverrideAccessError("critical_approver_roles must not be empty")
            if not approvers <= self.authorized_roles:
                raise OverrideAccessError(
                    "critical_approver_roles must be a subset of authorized_roles"
                )
            object.__setattr__(self, "critical_approver_roles", approvers)

    def severity_for(self, action_type: str) -> str:
        return (
            SEVERITY_CRITICAL
            if _norm_role(action_type) in self.critical_actions
            else SEVERITY_HIGH
        )

    def approvers_for(self, severity: str) -> FrozenSet[str]:
        if severity == SEVERITY_CRITICAL and self.critical_approver_roles is not None:
            return self.critical_approver_roles
        return self.authorized_roles


@dataclass
class OverrideRequest:
    """One emergency override request.

    ``primary_operator_role`` must be authenticated, server-derived role data.
    Never populate it from an unverified client request body -- an actor who can
    choose their own role defeats every check in this module.
    """

    request_id: str
    target_system_id: str               # e.g. 'STRATEGY_STAT_ARB_01' or 'ALL_ALGOS'
    action_type: str                    # 'HALT_STRATEGY', 'KILL_SWITCH_ALL_ALGOS', 'PAUSE_ORDERS'
    primary_operator_id: str
    primary_operator_role: str          # e.g. 'RISK_OFFICER'
    justification_reason: str           # Mandatory justification text
    secondary_operator_id: Optional[str] = None
    secondary_operator_role: Optional[str] = None
    break_glass_token: Optional[str] = None
    ttl_minutes: int = DEFAULT_MAX_TTL_MINUTES


@dataclass
class OverrideControlReport:
    """Decision record. Persist verbatim to an append-only audit sink."""

    request_id: str
    target_system_id: str
    action_type: str
    severity_level: str                 # 'SEVERITY_HIGH' or 'SEVERITY_CRITICAL'
    is_approved: bool
    audit_hash_sha256: str
    ttl_minutes: int
    rejection_reason: Optional[str]
    audit_summary: str
    # Fields below were added in 2.0.0; all defaulted, so existing positional
    # construction of the original nine fields is unaffected.
    rejection_code: Optional[str] = None
    decision_timestamp_utc: str = ""
    expires_at_utc: Optional[str] = None
    approval_mode: Optional[str] = None
    break_glass_used: bool = False
    post_incident_review_required: bool = False
    audit_chain_index: int = -1
    previous_audit_hash: str = ""
    hash_algorithm: str = "sha256"
    primary_operator_id: str = ""
    secondary_operator_id: Optional[str] = None


@dataclass
class ActiveOverride:
    """An approved override currently in force."""

    request: OverrideRequest
    report: OverrideControlReport
    approved_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None


@dataclass(frozen=True)
class BreakGlassToken:
    """A pre-issued, single-use break-glass credential.

    Only the SHA-256 digest of the secret is retained, so capture of the
    registry (memory dump, log, backup) does not yield a usable token.
    """

    token_id: str
    secret_sha256: str
    expires_at: datetime
    issued_to_operator_id: Optional[str] = None
    consumed: bool = False

    @staticmethod
    def from_secret(
        token_id: str,
        secret: str,
        expires_at: datetime,
        issued_to_operator_id: Optional[str] = None,
    ) -> "BreakGlassToken":
        if not token_id or not token_id.strip():
            raise OverrideAccessError("token_id must be a non-empty string")
        if not secret or len(secret) < 16:
            raise OverrideAccessError("break-glass secret must be at least 16 characters")
        if expires_at.tzinfo is None:
            raise OverrideAccessError("expires_at must be timezone-aware UTC")
        return BreakGlassToken(
            token_id=token_id,
            secret_sha256=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
            expires_at=expires_at,
            issued_to_operator_id=issued_to_operator_id,
        )


class BreakGlassTokenRegistry:
    """In-memory single-use token store. Replace with a durable store in production.

    Tokens are consumed only on a *successful* authorisation, so a token is not
    burned by a request that fails validation for an unrelated reason.
    """

    def __init__(self, tokens: Optional[Sequence[BreakGlassToken]] = None) -> None:
        self._tokens: Dict[str, BreakGlassToken] = {t.token_id: t for t in (tokens or ())}
        self._lock = threading.Lock()

    def issue(self, token: BreakGlassToken) -> None:
        with self._lock:
            if token.token_id in self._tokens:
                raise OverrideAccessError(f"token_id '{token.token_id}' already issued")
            self._tokens[token.token_id] = token

    def verify(self, presented: str, operator_id: str, now: datetime) -> Optional[str]:
        """Return the matching ``token_id`` when the secret is valid, else ``None``."""
        if not presented:
            return None
        digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()
        with self._lock:
            for token in self._tokens.values():
                if not hmac.compare_digest(token.secret_sha256, digest):
                    continue
                if token.consumed or now >= token.expires_at:
                    return None
                if token.issued_to_operator_id is not None and _norm_id(
                    token.issued_to_operator_id
                ) != _norm_id(operator_id):
                    return None
                return token.token_id
        return None

    def consume(self, token_id: str) -> None:
        with self._lock:
            token = self._tokens.get(token_id)
            if token is not None:
                self._tokens[token_id] = replace(token, consumed=True)

    def is_consumed(self, token_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(token_id)
            return bool(token and token.consumed)


def build_audit_payload(
    req: OverrideRequest,
    timestamp_iso: str,
    severity: str,
    approved: bool,
    rejection_code: Optional[str],
    approval_mode: Optional[str],
    previous_hash: str,
) -> str:
    """Canonical hash pre-image binding every field that authorises the action.

    Omitting the secondary approver, the severity, the TTL or the outcome would
    let a stored record be edited in exactly the way an insider would want to
    edit it while its hash still verified.
    """
    return _canonical(
        [
            ("prev", previous_hash),
            ("request_id", req.request_id or ""),
            ("target_system_id", req.target_system_id or ""),
            ("action_type", req.action_type or ""),
            ("severity", severity),
            ("primary_operator_id", req.primary_operator_id or ""),
            ("primary_operator_role", _norm_role(req.primary_operator_role)),
            ("secondary_operator_id", req.secondary_operator_id or ""),
            ("secondary_operator_role", _norm_role(req.secondary_operator_role)),
            ("approval_mode", approval_mode or ""),
            ("justification", req.justification_reason or ""),
            ("ttl_minutes", str(req.ttl_minutes)),
            ("approved", "1" if approved else "0"),
            ("rejection_code", rejection_code or ""),
            ("timestamp_utc", timestamp_iso),
        ]
    )


def compute_record_hash(
    req: OverrideRequest,
    report: OverrideControlReport,
    hmac_key: Optional[bytes] = None,
) -> str:
    """Recompute one chained record's hash from the request that produced it.

    Use this to verify externally persisted evidence: the report alone does not
    carry the justification text or the operator roles, so verification requires
    the archived request as well.
    """
    message = build_audit_payload(
        req,
        report.decision_timestamp_utc,
        report.severity_level,
        report.is_approved,
        report.rejection_code,
        report.approval_mode,
        report.previous_audit_hash,
    ).encode("utf-8")
    if hmac_key:
        return hmac.new(hmac_key, message, hashlib.sha256).hexdigest()
    return hashlib.sha256(message).hexdigest()


def verify_audit_chain(
    records: Sequence[Tuple[OverrideRequest, OverrideControlReport]],
    hmac_key: Optional[bytes] = None,
) -> Tuple[bool, Optional[int]]:
    """Recompute a chain of ``(request, report)`` pairs.

    Returns ``(is_intact, first_broken_index)``. Detects edited fields, deleted
    records and reordering. It cannot detect a wholesale rewrite of the entire
    chain unless ``hmac_key`` is used and the key is held outside the log store.
    """
    previous_hash = ""
    for index, (req, report) in enumerate(records):
        if report.previous_audit_hash != previous_hash:
            return False, index
        if compute_record_hash(req, report, hmac_key) != report.audit_hash_sha256:
            return False, index
        previous_hash = report.audit_hash_sha256
    return True, None


class EmergencyOverrideAccessEngine:
    """Break-glass access control engine for emergency manual overrides.

    Enforces RBAC, dual sign-off (four-eyes) on critical firm-wide actions,
    verified single-use break-glass tokens, mandatory justification, a bounded
    TTL with real expiry, replay protection, and a chained audit trail covering
    approvals *and* denials.

    Thread-safe for concurrent ``process_override_request`` calls; the audit
    chain, active-override map and decision map are guarded by one re-entrant
    lock, so two operators racing on the same ``request_id`` cannot both win.
    """

    def __init__(
        self,
        policy: Optional[OverridePolicy] = None,
        break_glass_registry: Optional[BreakGlassTokenRegistry] = None,
        audit_hmac_key: Optional[bytes] = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.policy = policy or OverridePolicy()
        self.break_glass_registry = break_glass_registry
        self._hmac_key = audit_hmac_key
        self.hash_algorithm = "hmac-sha256" if audit_hmac_key else "sha256"
        self._clock = clock
        self._lock = threading.RLock()
        self.active_overrides: Dict[str, ActiveOverride] = {}
        self.audit_chain: List[Tuple[OverrideRequest, OverrideControlReport]] = []
        self._decisions: Dict[str, Tuple[str, OverrideControlReport]] = {}

    # ---------------------------------------------------------------- audit

    def _digest(self, message: str) -> str:
        encoded = message.encode("utf-8")
        if self._hmac_key:
            return hmac.new(self._hmac_key, encoded, hashlib.sha256).hexdigest()
        return hashlib.sha256(encoded).hexdigest()

    def _decide(
        self,
        req: OverrideRequest,
        severity: str,
        approved: bool,
        rejection_code: Optional[str],
        rejection_reason: Optional[str],
        approval_mode: Optional[str],
        now: datetime,
        break_glass_used: bool = False,
        expires_at: Optional[datetime] = None,
        summary: Optional[str] = None,
    ) -> OverrideControlReport:
        """Hash, chain and log one decision. Denials are chained too (AC-6(9))."""
        timestamp_iso = _iso(now)
        with self._lock:
            previous_hash = self.audit_chain[-1][1].audit_hash_sha256 if self.audit_chain else ""
            audit_hash = self._digest(
                build_audit_payload(
                    req, timestamp_iso, severity, approved, rejection_code,
                    approval_mode, previous_hash,
                )
            )
            report = OverrideControlReport(
                request_id=req.request_id,
                target_system_id=req.target_system_id,
                action_type=req.action_type,
                severity_level=severity,
                is_approved=approved,
                audit_hash_sha256=audit_hash,
                ttl_minutes=req.ttl_minutes if approved else 0,
                rejection_reason=rejection_reason,
                audit_summary=summary or rejection_reason or "",
                rejection_code=rejection_code,
                decision_timestamp_utc=timestamp_iso,
                expires_at_utc=_iso(expires_at) if expires_at else None,
                approval_mode=approval_mode,
                break_glass_used=break_glass_used,
                post_incident_review_required=break_glass_used,
                audit_chain_index=len(self.audit_chain),
                previous_audit_hash=previous_hash,
                hash_algorithm=self.hash_algorithm,
                primary_operator_id=req.primary_operator_id,
                secondary_operator_id=req.secondary_operator_id,
            )
            self.audit_chain.append((req, report))

        if approved:
            logger.critical(report.audit_summary, extra={"audit_hash": audit_hash})
        else:
            # A denied break-glass attempt is a security event, not noise.
            logger.error(report.audit_summary, extra={"audit_hash": audit_hash})
        return report

    def _reject(
        self, req: OverrideRequest, severity: str, code: str, reason: str, now: datetime
    ) -> OverrideControlReport:
        return self._decide(
            req, severity, False, code, f"REJECTED [{code}]: {reason}", None, now,
            summary=f"OVERRIDE DENIED [{req.request_id}] {code}: {reason}",
        )

    def compute_audit_hash(self, req: OverrideRequest, timestamp: datetime) -> str:
        """Hash a request as a standalone, *unchained* record.

        Retained for callers hashing a payload outside the decision flow. The
        authoritative value is ``OverrideControlReport.audit_hash_sha256``, which
        is chained to its predecessor; this one is not.

        Note the 2.0.0 signature change: ``timestamp`` is now a timezone-aware
        ``datetime``, not an epoch float, so the hashed timestamp matches the
        ISO-8601 value published in the report and the hash is reproducible.
        """
        severity = self.policy.severity_for(req.action_type)
        return self._digest(
            build_audit_payload(req, _iso(timestamp), severity, False, None, None, "")
        )

    # -------------------------------------------------------- request entry

    def process_override_request(self, req: OverrideRequest) -> OverrideControlReport:
        """Authorise or deny one override request. Never raises on a denial.

        The whole decision runs under the engine lock: check-then-act on the
        duplicate map, the break-glass token and the active-override map must be
        atomic, or two operators racing on one ``request_id`` both win and the
        kill switch fires twice.
        """
        with self._lock:
            return self._process_locked(req)

    def _process_locked(self, req: OverrideRequest) -> OverrideControlReport:
        now = self._clock()
        severity = self.policy.severity_for(req.action_type)

        # 1. Structural validation. Blank identities make the audit trail
        #    useless; a blank action cannot be classified for severity.
        for field_name, value in (
            ("request_id", req.request_id),
            ("target_system_id", req.target_system_id),
            ("action_type", req.action_type),
            ("primary_operator_id", req.primary_operator_id),
            ("primary_operator_role", req.primary_operator_role),
        ):
            if not isinstance(value, str) or not value.strip():
                return self._reject(
                    req, severity, REJECT_INVALID_FIELD,
                    f"Field '{field_name}' must be a non-empty string.", now,
                )

        # 2. Replay handling. An identical resubmission returns the original
        #    decision (a retried HTTP call must not fire a second kill switch);
        #    a different payload reusing a decided id is a tampering signal.
        #    Denied requests are not recorded here, so an operator may correct
        #    the justification and resubmit under the same id.
        payload_key = _canonical(
            [
                ("target_system_id", req.target_system_id),
                ("action_type", _norm_role(req.action_type)),
                ("primary_operator_id", _norm_id(req.primary_operator_id)),
                ("secondary_operator_id", _norm_id(req.secondary_operator_id)),
                ("justification", req.justification_reason or ""),
                ("ttl_minutes", str(req.ttl_minutes)),
            ]
        )
        prior = self._decisions.get(req.request_id)
        if prior is not None:
            prior_key, prior_report = prior
            if hmac.compare_digest(prior_key, payload_key):
                logger.warning(
                    "Idempotent replay of override request %s; returning original decision.",
                    req.request_id,
                )
                return prior_report
            return self._reject(
                req, severity, REJECT_DUPLICATE_REQUEST_ID,
                f"request_id '{req.request_id}' was already decided with a different payload.",
                now,
            )

        # 3. Mandatory justification (SEC staff FAQ No. 18: reasons documented
        #    and retained as books and records).
        justification = (req.justification_reason or "").strip()
        if len(justification) < self.policy.min_justification_chars:
            return self._reject(
                req, severity, REJECT_MISSING_JUSTIFICATION,
                "Override requires a justification of at least "
                f"{self.policy.min_justification_chars} characters.",
                now,
            )

        # 4. TTL bounds. An override with no expiry is a permanently disabled
        #    control (cf. NIST SP 800-53 Rev. 5 AC-2(2)).
        if not isinstance(req.ttl_minutes, int) or isinstance(req.ttl_minutes, bool):
            return self._reject(
                req, severity, REJECT_INVALID_TTL, "ttl_minutes must be an integer.", now
            )
        if req.ttl_minutes < 1 or req.ttl_minutes > self.policy.max_ttl_minutes:
            return self._reject(
                req, severity, REJECT_INVALID_TTL,
                f"ttl_minutes must be between 1 and {self.policy.max_ttl_minutes}.", now,
            )

        # 5. Primary operator RBAC.
        primary_role = _norm_role(req.primary_operator_role)
        if primary_role not in self.policy.authorized_roles:
            return self._reject(
                req, severity, REJECT_UNAUTHORIZED_ROLE,
                f"Operator '{req.primary_operator_id}' role "
                f"'{req.primary_operator_role}' is not authorised for overrides.", now,
            )

        approval_mode = APPROVAL_SINGLE_OPERATOR
        break_glass_used = False
        consume_token_id: Optional[str] = None

        # 6. Critical actions require four-eyes or a verified break-glass token.
        if severity == SEVERITY_CRITICAL:
            approver_roles = self.policy.approvers_for(severity)
            if primary_role not in approver_roles:
                return self._reject(
                    req, severity, REJECT_UNAUTHORIZED_ROLE,
                    f"Role '{primary_role}' may not initiate a {severity} action.", now,
                )

            if (req.secondary_operator_id or "").strip():
                if _norm_id(req.secondary_operator_id) == _norm_id(req.primary_operator_id):
                    return self._reject(
                        req, severity, REJECT_SELF_APPROVAL,
                        "Secondary approver must be a different person from the initiator.",
                        now,
                    )
                secondary_role = _norm_role(req.secondary_operator_role)
                if secondary_role not in approver_roles:
                    return self._reject(
                        req, severity, REJECT_SECONDARY_ROLE_UNAUTHORIZED,
                        f"Secondary approver role '{req.secondary_operator_role}' is not "
                        f"authorised to approve a {severity} action.", now,
                    )
                approval_mode = APPROVAL_DUAL_SIGN_OFF
            elif (req.break_glass_token or "").strip():
                if self.break_glass_registry is None:
                    return self._reject(
                        req, severity, REJECT_BREAK_GLASS_NOT_CONFIGURED,
                        "Break-glass path is not configured; dual sign-off is required.", now,
                    )
                consume_token_id = self.break_glass_registry.verify(
                    req.break_glass_token, req.primary_operator_id, now
                )
                if consume_token_id is None:
                    return self._reject(
                        req, severity, REJECT_BREAK_GLASS_INVALID,
                        "Break-glass token is unknown, expired, already consumed, or issued "
                        "to a different operator.", now,
                    )
                approval_mode = APPROVAL_BREAK_GLASS
                break_glass_used = True
            else:
                return self._reject(
                    req, severity, REJECT_DUAL_SIGN_OFF_REQUIRED,
                    f"Critical action '{req.action_type}' requires a secondary authorised "
                    "sign-off or a valid break-glass token.", now,
                )

        expires_at = now + timedelta(minutes=req.ttl_minutes)
        summary = (
            f"EMERGENCY OVERRIDE APPROVED [{req.request_id}]: '{req.action_type}' on "
            f"'{req.target_system_id}' by {req.primary_operator_id} ({primary_role}) "
            f"via {approval_mode}. Expires {_iso(expires_at)}."
        )
        report = self._decide(
            req, severity, True, None, None, approval_mode, now,
            break_glass_used=break_glass_used, expires_at=expires_at, summary=summary,
        )

        if consume_token_id is not None and self.break_glass_registry is not None:
            self.break_glass_registry.consume(consume_token_id)

        self.active_overrides[req.request_id] = ActiveOverride(
            request=req, report=report, approved_at=now, expires_at=expires_at
        )
        self._decisions[req.request_id] = (payload_key, report)
        return report

    # ------------------------------------------------------------ lifecycle

    def expire_due_overrides(self, now: Optional[datetime] = None) -> List[ActiveOverride]:
        """Remove overrides whose TTL has elapsed and return them.

        Expiry is *at or after* ``expires_at``. Nothing expires on its own --
        call this from the supervisory loop, and treat each returned entry as a
        control that is live again unless separately re-authorised.
        """
        moment = now or self._clock()
        expired: List[ActiveOverride] = []
        with self._lock:
            for request_id, active in list(self.active_overrides.items()):
                if moment >= active.expires_at:
                    expired.append(self.active_overrides.pop(request_id))
        for active in expired:
            logger.warning(
                "OVERRIDE EXPIRED [%s]: '%s' on '%s' expired at %s; the suppressed control "
                "is live again unless separately re-authorised.",
                active.report.request_id, active.request.action_type,
                active.request.target_system_id, _iso(active.expires_at),
            )
        return expired

    def revoke_override(
        self, request_id: str, revoked_by: str, now: Optional[datetime] = None
    ) -> Optional[ActiveOverride]:
        """Stand down an active override before its TTL elapses."""
        moment = now or self._clock()
        with self._lock:
            active = self.active_overrides.pop(request_id, None)
        if active is None:
            return None
        active.revoked_at = moment
        active.revoked_by = revoked_by
        logger.warning("OVERRIDE REVOKED [%s] by %s at %s.", request_id, revoked_by, _iso(moment))
        return active

    def list_active_overrides(self, now: Optional[datetime] = None) -> List[ActiveOverride]:
        """Active, unexpired overrides. An expired entry is never reported active."""
        moment = now or self._clock()
        with self._lock:
            return [a for a in self.active_overrides.values() if moment < a.expires_at]

    def is_override_active(self, request_id: str, now: Optional[datetime] = None) -> bool:
        moment = now or self._clock()
        with self._lock:
            active = self.active_overrides.get(request_id)
        return active is not None and moment < active.expires_at

    # --------------------------------------------------------- verification

    def verify_audit_chain(self) -> Tuple[bool, Optional[int]]:
        """Recompute this engine's in-memory chain; ``(is_intact, first_broken_index)``."""
        with self._lock:
            snapshot = list(self.audit_chain)
        return verify_audit_chain(snapshot, self._hmac_key)
