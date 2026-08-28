"""Segregation of Duties (SoD) and maker-checker gate for custody operations.

This module decides whether a proposed custody transfer has collected enough
*independent* human authorisation to be released, where "independent" means
something stricter than "more than one approval arrived":

* the approver is not the initiator (maker-checker),
* the approver holds a role the firm has declared compatible with approving,
* no single identity holds a combination of roles that would let one person both
  create and bless the same work (the SoD role-conflict matrix), and
* every approval is bound to the exact payload that was reviewed, so an amount
  or destination changed afterwards invalidates the approvals it was changed
  under.

It is a **governance gate, not a vault**. It runs inside your own
infrastructure and can be skipped entirely by anyone who controls this process.
The authoritative enforcer must be the custodian's policy engine, the HSM quorum
policy, or the on-chain multisig threshold. See
``multi-signature-approval-for-large-transfers`` for the quorum/timelock layer
and ``employee-offboarding-procedure-for-custody-access`` for revocation.

Nothing in this module is a cryptographic signature. ``signature_hash`` is an
unkeyed SHA-256 chain link: it makes a later edit *detectable* to a holder of an
earlier chain head. It does not prove that the named approver approved anything.
Authenticity comes from the identity layer that authenticated the caller.
"""

import hashlib
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

DIGEST_DOMAIN = "SOD_CUSTODY_TRANSFER_PROPOSAL_V1"
AUDIT_DOMAIN = "SOD_CUSTODY_AUDIT_CHAIN_V1"
GENESIS_HASH = "0" * 64


@dataclass
class SegregationOfDutiesForCustodyOperationsConfig:
    """Engine configuration.

    ``enabled`` is retained from v1.0.0 for backward compatibility. Every other
    option exists because the corresponding control is a firm policy decision,
    not something any regulator surveyed in ``references/standards.md``
    prescribes.
    """

    enabled: bool = True
    #: Approvals required for a transfer strictly below the large-transfer
    #: threshold. Must be >= 1; 0 would approve a proposal on creation.
    approvals_below_threshold: int = 1
    #: Approvals required at or above the large-transfer threshold.
    approvals_at_or_above_threshold: int = 2
    #: Minimum number of *distinct departments* the approvals must span. 1
    #: disables the check. Two approvals from one desk is one compromised desk.
    min_distinct_approver_departments: int = 1
    #: Reject an approver who sits in the initiator's own department.
    forbid_approver_from_initiator_department: bool = False


class CustodyRole(str, Enum):
    INITIATOR = "INITIATOR"             # Maker: can propose transfers
    APPROVER = "APPROVER"               # Checker: can approve transfers
    SECURITY_ADMIN = "SECURITY_ADMIN"   # Admin: manages policy and access
    AUDITOR = "AUDITOR"                 # Read-only audit access


class SoDViolationType(str, Enum):
    """Machine-readable cause attached to every ``SoDConflictError``."""

    SELF_APPROVAL_ATTEMPT = "SELF_APPROVAL_ATTEMPT"
    UNAUTHORIZED_ROLE = "UNAUTHORIZED_ROLE"
    THRESHOLD_NOT_MET = "THRESHOLD_NOT_MET"
    ROLE_CONFLICT_ADMIN_MAKER = "ROLE_CONFLICT_ADMIN_MAKER"
    ROLE_CONFLICT = "ROLE_CONFLICT"
    DUPLICATE_APPROVAL = "DUPLICATE_APPROVAL"
    DUPLICATE_USER_REGISTRATION = "DUPLICATE_USER_REGISTRATION"
    DUPLICATE_PROPOSAL_ID = "DUPLICATE_PROPOSAL_ID"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    PROPOSAL_NOT_PENDING = "PROPOSAL_NOT_PENDING"
    INSUFFICIENT_DEPARTMENT_SEPARATION = "INSUFFICIENT_DEPARTMENT_SEPARATION"
    ENGINE_DISABLED = "ENGINE_DISABLED"


#: Role pairs no single identity may hold at once, applied at registration.
#:
#: ``SECURITY_ADMIN`` administers access and policy; ``AUDITOR`` reviews what was
#: done. NIST SP 800-53 Rev. 5 AC-5 makes the point directly: "ensuring that
#: security personnel who administer access control functions do not also
#: administer audit functions". An auditor who can initiate or approve a
#: transfer is auditing their own work.
DEFAULT_INCOMPATIBLE_ROLE_PAIRS: FrozenSet[FrozenSet[CustodyRole]] = frozenset({
    frozenset({CustodyRole.SECURITY_ADMIN, CustodyRole.INITIATOR}),
    frozenset({CustodyRole.SECURITY_ADMIN, CustodyRole.APPROVER}),
    frozenset({CustodyRole.SECURITY_ADMIN, CustodyRole.AUDITOR}),
    frozenset({CustodyRole.AUDITOR, CustodyRole.INITIATOR}),
    frozenset({CustodyRole.AUDITOR, CustodyRole.APPROVER}),
})

#: The default matrix plus the maker-checker pair itself. Use this when the firm
#: has decided nobody may be both a maker and a checker *anywhere*, rather than
#: relying only on the per-proposal self-approval block. It is not the default
#: because many firms legitimately staff one person as maker on one workflow and
#: checker on another; the transaction-level block still holds in that case.
STRICT_INCOMPATIBLE_ROLE_PAIRS: FrozenSet[FrozenSet[CustodyRole]] = frozenset(
    DEFAULT_INCOMPATIBLE_ROLE_PAIRS | {frozenset({CustodyRole.INITIATOR, CustodyRole.APPROVER})}
)

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_EXECUTED = "EXECUTED"
TERMINAL_STATUSES = frozenset({STATUS_REJECTED, STATUS_EXECUTED})


class SoDConflictError(Exception):
    """Raised when a Segregation of Duties policy is breached.

    Raised rather than returned: a proposal the engine cannot evaluate must
    never be mistaken for a proposal the engine declined.
    """

    def __init__(self, message: str, violation_type: Optional[SoDViolationType] = None) -> None:
        super().__init__(message)
        self.violation_type = violation_type


def _require_identifier(value: str, label: str) -> str:
    """Rejects blank identifiers before they become an unattributable record."""
    text = str(value).strip()
    if not text:
        raise SoDConflictError(
            f"{label} must be a non-empty identifier.", SoDViolationType.INVALID_PAYLOAD
        )
    return text


def _require_finite(value: float, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise SoDConflictError(
            f"{label} must be numeric, got {value!r}.", SoDViolationType.INVALID_PAYLOAD
        ) from exc
    if not math.isfinite(numeric):
        raise SoDConflictError(
            f"{label} must be a finite number, got {value!r}.", SoDViolationType.INVALID_PAYLOAD
        )
    return numeric


def _require_positive_amount(value: float, label: str) -> float:
    """Rejects NaN/Inf/non-positive notionals.

    ``float('nan') >= threshold`` evaluates to ``False``, so an unvalidated NaN
    would be silently classified as a *small* transfer and routed to the lower
    approval requirement. Fail rather than guess.
    """
    numeric = _require_finite(value, label)
    if numeric <= 0.0:
        raise SoDConflictError(
            f"{label} must be strictly positive, got {numeric!r}.",
            SoDViolationType.INVALID_PAYLOAD,
        )
    return numeric


def _length_prefixed(*parts: str) -> bytes:
    """Length-prefixes each field so no field-boundary shuffle can collide.

    Without this, ``("0xAB", "CD")`` and ``("0xABC", "D")`` hash identically
    under naive concatenation, which would let a destination address absorb a
    character from the neighbouring field without changing the digest.
    """
    encoded = b""
    for part in parts:
        raw = str(part).encode("utf-8")
        encoded += str(len(raw)).encode("ascii") + b":" + raw
    return encoded


@dataclass
class UserIdentity:
    """A custody identity and the roles it holds.

    ``roles`` is snapshotted into a ``frozenset`` at registration. The caller's
    own set stays mutable, but mutating it afterwards no longer changes what the
    engine believes -- which is what made the v1.0.0 role-conflict check
    bypassable from outside the engine.
    """

    user_id: str
    username: str
    department: str
    roles: Set[CustodyRole]


@dataclass
class ApprovalRecord:
    approver_id: str
    approved_at: float
    #: Unkeyed SHA-256 audit-chain link, **not** a signature. It binds this
    #: approval to every event recorded before it; it proves nothing about who
    #: produced it.
    signature_hash: str
    #: Digest of the payload this approver actually reviewed. An approval whose
    #: digest no longer matches the proposal is stale and is not counted.
    approved_digest: str = ""
    #: Department recorded at approval time, so a later reorganisation cannot
    #: retroactively change whether a past quorum was independent.
    department: str = ""


@dataclass
class CustodyTransferProposal:
    proposal_id: str
    initiator_id: str
    destination_address: str
    asset_symbol: str
    amount_usd: float
    required_approvals: int = 2
    approvals: List[ApprovalRecord] = field(default_factory=list)
    status: str = STATUS_PENDING       # PENDING, APPROVED, REJECTED, EXECUTED
    created_at: float = field(default_factory=time.time)
    initiator_department: str = ""
    payload_digest: str = ""
    resolved_by: Optional[str] = None
    resolution_reason: Optional[str] = None
    resolved_at: Optional[float] = None


@dataclass
class AuditEntry:
    """One link in the append-only, tamper-evident approval chain."""

    sequence: int
    event_type: str
    proposal_id: str
    actor_id: str
    recorded_at: float
    detail: str
    previous_hash: str
    entry_hash: str


def compute_proposal_digest(proposal: CustodyTransferProposal) -> str:
    """Digest of every field an approver is consenting to.

    Deliberately covers ``required_approvals``: lowering the threshold after
    approvals are in is as much an escalation as changing the destination.
    ``created_at``, ``status`` and the approvals themselves are excluded -- they
    are not terms of the approval.
    """
    payload = _length_prefixed(
        DIGEST_DOMAIN,
        proposal.proposal_id,
        proposal.initiator_id,
        proposal.destination_address,
        proposal.asset_symbol,
        float(proposal.amount_usd).hex(),
        str(int(proposal.required_approvals)),
    )
    return hashlib.sha256(payload).hexdigest()


class SegregationOfDutiesForCustodyOperationsEngine:
    """Segregation of Duties engine for institutional custody operations.

    Enforces maker-checker dual control, an RBAC role-conflict matrix,
    payload-bound approvals, M-of-N thresholds by notional, optional
    departmental independence, and a tamper-evident audit chain.

    All state lives in process memory and every mutation is guarded by a
    re-entrant lock, so a single engine instance is safe across threads. Two
    *processes* each holding their own engine will each see the same proposal as
    approved and can release the same transfer twice -- persist the proposals
    and serialise the approve-then-execute sequence if that is possible in your
    deployment.
    """

    def __init__(
        self,
        config: Optional[SegregationOfDutiesForCustodyOperationsConfig] = None,
        large_transfer_threshold_usd: float = 50000.0,
        incompatible_role_pairs: Optional[Iterable[Iterable[CustodyRole]]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config or SegregationOfDutiesForCustodyOperationsConfig(enabled=True)

        threshold = _require_finite(large_transfer_threshold_usd, "large_transfer_threshold_usd")
        if threshold < 0.0:
            raise SoDConflictError(
                "large_transfer_threshold_usd must be >= 0 "
                "(use 0.0 to treat every transfer as large).",
                SoDViolationType.INVALID_PAYLOAD,
            )
        self.large_transfer_threshold_usd = threshold

        if (
            self.config.approvals_below_threshold < 1
            or self.config.approvals_at_or_above_threshold < 1
        ):
            raise SoDConflictError(
                "Approval counts must be >= 1; 0 would approve a proposal on creation.",
                SoDViolationType.INVALID_PAYLOAD,
            )
        if self.config.min_distinct_approver_departments < 1:
            raise SoDConflictError(
                "min_distinct_approver_departments must be >= 1.",
                SoDViolationType.INVALID_PAYLOAD,
            )
        if self.config.min_distinct_approver_departments > min(
            self.config.approvals_below_threshold, self.config.approvals_at_or_above_threshold
        ):
            raise SoDConflictError(
                "min_distinct_approver_departments exceeds an approval count it must be "
                "satisfied by; that tier's threshold would be unreachable.",
                SoDViolationType.INVALID_PAYLOAD,
            )

        if incompatible_role_pairs is None:
            self.incompatible_role_pairs: FrozenSet[FrozenSet[CustodyRole]] = (
                DEFAULT_INCOMPATIBLE_ROLE_PAIRS
            )
        else:
            self.incompatible_role_pairs = frozenset(
                frozenset(pair) for pair in incompatible_role_pairs
            )

        self._clock: Callable[[], float] = clock if clock is not None else time.time
        self._lock = threading.RLock()
        self.users: Dict[str, UserIdentity] = {}
        self.proposals: Dict[str, CustodyTransferProposal] = {}
        self._audit_log: List[AuditEntry] = []
        self._chain_head: str = GENESIS_HASH

    # ------------------------------------------------------------------ #
    # Legacy surface
    # ------------------------------------------------------------------ #

    def execute(self) -> bool:
        """Legacy execute method retained for backward compatibility."""
        return True if self.config.enabled else False

    # ------------------------------------------------------------------ #
    # Audit chain
    # ------------------------------------------------------------------ #

    def _append_audit(
        self,
        event_type: str,
        proposal_id: str,
        actor_id: str,
        detail: str,
        recorded_at: float,
    ) -> AuditEntry:
        sequence = len(self._audit_log)
        payload = _length_prefixed(
            AUDIT_DOMAIN,
            self._chain_head,
            str(sequence),
            event_type,
            proposal_id,
            actor_id,
            float(recorded_at).hex(),
            detail,
        )
        entry_hash = hashlib.sha256(payload).hexdigest()
        entry = AuditEntry(
            sequence=sequence,
            event_type=event_type,
            proposal_id=proposal_id,
            actor_id=actor_id,
            recorded_at=recorded_at,
            detail=detail,
            previous_hash=self._chain_head,
            entry_hash=entry_hash,
        )
        self._audit_log.append(entry)
        self._chain_head = entry_hash
        return entry

    @property
    def chain_head_hash(self) -> str:
        """Current head of the audit chain. Publish this to append-only storage.

        A chain nobody anchors externally proves only that its entries are
        consistent with each other -- which is exactly what a tamperer would
        also arrange.
        """
        return self._chain_head

    def audit_trail(self) -> Tuple[AuditEntry, ...]:
        """Immutable view of the audit chain, oldest first."""
        return tuple(self._audit_log)

    def verify_audit_chain(self) -> Tuple[bool, Optional[str]]:
        """Recomputes every link. Returns ``(ok, reason)``.

        Detects an edited, deleted, or reordered entry. It does **not** detect
        an attacker who edits an entry and recomputes every hash after it --
        which is why the head must be published somewhere this process cannot
        rewrite.
        """
        previous = GENESIS_HASH
        for index, entry in enumerate(self._audit_log):
            if entry.sequence != index:
                return False, f"audit entry at position {index} declares sequence {entry.sequence}"
            if entry.previous_hash != previous:
                return False, f"audit entry {entry.sequence} does not chain to its predecessor"
            payload = _length_prefixed(
                AUDIT_DOMAIN,
                entry.previous_hash,
                str(entry.sequence),
                entry.event_type,
                entry.proposal_id,
                entry.actor_id,
                float(entry.recorded_at).hex(),
                entry.detail,
            )
            if hashlib.sha256(payload).hexdigest() != entry.entry_hash:
                return False, f"audit entry {entry.sequence} content does not match its hash"
            previous = entry.entry_hash
        if previous != self._chain_head:
            return False, "chain head does not match the last audit entry"
        return True, None

    # ------------------------------------------------------------------ #
    # Identity and roles
    # ------------------------------------------------------------------ #

    def _screen_role_conflicts(self, user_id: str, roles: FrozenSet[CustodyRole]) -> None:
        for pair in self.incompatible_role_pairs:
            if pair <= roles:
                names = " + ".join(sorted(role.value for role in pair))
                violation = (
                    SoDViolationType.ROLE_CONFLICT_ADMIN_MAKER
                    if pair == frozenset({CustodyRole.SECURITY_ADMIN, CustodyRole.INITIATOR})
                    else SoDViolationType.ROLE_CONFLICT
                )
                raise SoDConflictError(
                    f"User {user_id} cannot combine {names} roles (SoD conflict).",
                    violation,
                )

    def register_user(self, user: UserIdentity, replace: bool = False) -> UserIdentity:
        """Registers an identity with defined RBAC roles.

        Roles are snapshotted into a ``frozenset``, so the engine's view cannot
        be changed by mutating the caller's set afterwards. Re-registering an
        existing ``user_id`` raises unless ``replace=True``: a role grant is an
        explicit, audited act, not a silent overwrite.
        """
        user_id = _require_identifier(user.user_id, "user_id")
        roles = frozenset(user.roles)
        if not roles:
            raise SoDConflictError(
                f"User {user_id} must hold at least one role.",
                SoDViolationType.INVALID_PAYLOAD,
            )
        self._screen_role_conflicts(user_id, roles)
        with self._lock:
            if user_id in self.users and not replace:
                raise SoDConflictError(
                    f"User '{user_id}' is already registered; "
                    "pass replace=True to change their roles.",
                    SoDViolationType.DUPLICATE_USER_REGISTRATION,
                )
            stored = UserIdentity(
                user_id=user_id,
                username=user.username,
                department=str(user.department or "").strip(),
                roles=roles,
            )
            self.users[user_id] = stored
            self._append_audit(
                "USER_REGISTERED",
                proposal_id="",
                actor_id=user_id,
                detail=",".join(sorted(role.value for role in roles)),
                recorded_at=self._clock(),
            )
        return stored

    # ------------------------------------------------------------------ #
    # Proposal lifecycle
    # ------------------------------------------------------------------ #

    def required_approvals_for(self, amount_usd: float) -> int:
        """Approval count for a notional. The threshold boundary is inclusive."""
        amount = _require_positive_amount(amount_usd, "amount_usd")
        if amount >= self.large_transfer_threshold_usd:
            return self.config.approvals_at_or_above_threshold
        return self.config.approvals_below_threshold

    def propose_transfer(
        self,
        proposal_id: str,
        initiator_id: str,
        destination_address: str,
        asset_symbol: str,
        amount_usd: float,
    ) -> CustodyTransferProposal:
        """Creates a custody transfer proposal (the maker step).

        Requires the ``INITIATOR`` role. Re-submitting an identical proposal is
        idempotent and returns the existing proposal; re-using a ``proposal_id``
        with different content raises, rather than silently replacing a proposal
        that may already carry approvals.
        """
        if not self.config.enabled:
            raise SoDConflictError("SoD Engine is disabled.", SoDViolationType.ENGINE_DISABLED)

        proposal_id = _require_identifier(proposal_id, "proposal_id")
        initiator_id = _require_identifier(initiator_id, "initiator_id")
        destination_address = _require_identifier(destination_address, "destination_address")
        asset_symbol = _require_identifier(asset_symbol, "asset_symbol")
        amount = _require_positive_amount(amount_usd, "amount_usd")

        with self._lock:
            initiator = self.users.get(initiator_id)
            if not initiator or CustodyRole.INITIATOR not in initiator.roles:
                raise SoDConflictError(
                    f"User '{initiator_id}' does not have INITIATOR role.",
                    SoDViolationType.UNAUTHORIZED_ROLE,
                )

            proposal = CustodyTransferProposal(
                proposal_id=proposal_id,
                initiator_id=initiator_id,
                destination_address=destination_address,
                asset_symbol=asset_symbol,
                amount_usd=amount,
                required_approvals=self.required_approvals_for(amount),
                created_at=self._clock(),
                initiator_department=initiator.department,
            )
            proposal.payload_digest = compute_proposal_digest(proposal)

            existing = self.proposals.get(proposal_id)
            if existing is not None:
                if compute_proposal_digest(existing) == proposal.payload_digest:
                    logger.info(
                        "PROPOSAL REPLAY [%s]: identical content, returning the existing proposal.",
                        proposal_id,
                    )
                    return existing
                raise SoDConflictError(
                    f"Proposal '{proposal_id}' already exists with different content; "
                    "re-using a proposal id would discard the approvals already collected.",
                    SoDViolationType.DUPLICATE_PROPOSAL_ID,
                )

            self.proposals[proposal_id] = proposal
            self._append_audit(
                "PROPOSAL_CREATED",
                proposal_id=proposal_id,
                actor_id=initiator_id,
                detail=proposal.payload_digest,
                recorded_at=proposal.created_at,
            )

        logger.info(
            "PROPOSAL CREATED [%s]: amount_usd=%.2f initiator=%s required_approvals=%d digest=%s",
            proposal_id,
            amount,
            initiator_id,
            proposal.required_approvals,
            proposal.payload_digest[:16],
        )
        return proposal

    def valid_approvals(self, proposal: CustodyTransferProposal) -> List[ApprovalRecord]:
        """Approvals still bound to the proposal's current payload.

        Any change to a bound field yields a new digest, which strands every
        approval collected under the old one. That is the point: an approval is
        consent to a specific payload, not to a proposal id.
        """
        current = compute_proposal_digest(proposal)
        return [record for record in proposal.approvals if record.approved_digest == current]

    def _quorum_satisfied(self, proposal: CustodyTransferProposal) -> bool:
        valid = self.valid_approvals(proposal)
        if len(valid) < proposal.required_approvals:
            return False
        minimum_departments = self.config.min_distinct_approver_departments
        if minimum_departments > 1:
            departments = {r.department.casefold() for r in valid if r.department}
            if len(departments) < minimum_departments:
                return False
        return True

    def refresh_status(self, proposal_id: str) -> CustodyTransferProposal:
        """Recomputes ``status`` from the approvals still bound to the payload.

        Call this immediately before acting on an approved proposal. If the
        payload was mutated after approval the status falls back to ``PENDING``
        here, rather than releasing a transfer nobody approved. Terminal
        statuses are never revised.
        """
        proposal_id = _require_identifier(proposal_id, "proposal_id")
        with self._lock:
            proposal = self.proposals.get(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal '{proposal_id}' not found.")
            if proposal.status in TERMINAL_STATUSES:
                return proposal
            previous = proposal.status
            proposal.status = STATUS_APPROVED if self._quorum_satisfied(proposal) else STATUS_PENDING
            if previous == STATUS_APPROVED and proposal.status == STATUS_PENDING:
                logger.error(
                    "PROPOSAL DE-APPROVED [%s]: payload changed after approval; "
                    "%d approval(s) no longer bind to the current digest.",
                    proposal_id,
                    len(proposal.approvals),
                )
            return proposal

    def approve_transfer(self, proposal_id: str, approver_id: str) -> CustodyTransferProposal:
        """Records a checker approval.

        Enforcement order matters. The maker-checker self-approval block runs
        first, so an initiator who has since been granted the ``APPROVER`` role
        is still refused on their own proposal, and the refusal names the
        maker-checker violation rather than a role error.
        """
        if not self.config.enabled:
            raise SoDConflictError("SoD Engine is disabled.", SoDViolationType.ENGINE_DISABLED)

        proposal_id = _require_identifier(proposal_id, "proposal_id")
        approver_id = _require_identifier(approver_id, "approver_id")

        with self._lock:
            proposal = self.proposals.get(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal '{proposal_id}' not found.")

            # 1. Maker-checker self-approval check.
            if approver_id == proposal.initiator_id:
                msg = (
                    f"SoD VIOLATION: Initiator '{approver_id}' cannot approve their own "
                    f"transfer proposal '{proposal_id}'."
                )
                logger.error(msg)
                raise SoDConflictError(msg, SoDViolationType.SELF_APPROVAL_ATTEMPT)

            # 2. Terminal state -- an executed or rejected proposal is closed.
            if proposal.status in TERMINAL_STATUSES:
                raise SoDConflictError(
                    f"Proposal '{proposal_id}' is {proposal.status} and "
                    "accepts no further approvals.",
                    SoDViolationType.PROPOSAL_NOT_PENDING,
                )

            # 3. Approver RBAC role check.
            approver = self.users.get(approver_id)
            if not approver or CustodyRole.APPROVER not in approver.roles:
                raise SoDConflictError(
                    f"User '{approver_id}' does not have APPROVER role.",
                    SoDViolationType.UNAUTHORIZED_ROLE,
                )

            # 4. Departmental independence from the requesting desk.
            if (
                self.config.forbid_approver_from_initiator_department
                and approver.department
                and proposal.initiator_department
                and approver.department.casefold() == proposal.initiator_department.casefold()
            ):
                raise SoDConflictError(
                    f"Approver '{approver_id}' sits in the initiator's department "
                    f"'{proposal.initiator_department}'; the approval is not independent.",
                    SoDViolationType.INSUFFICIENT_DEPARTMENT_SEPARATION,
                )

            # 5. Duplicate approval check.
            if any(record.approver_id == approver_id for record in proposal.approvals):
                raise SoDConflictError(
                    f"Approver '{approver_id}' has already approved proposal '{proposal_id}'.",
                    SoDViolationType.DUPLICATE_APPROVAL,
                )

            # 6. Bind the approval to the payload that was actually reviewed.
            approved_at = self._clock()
            digest = compute_proposal_digest(proposal)
            entry = self._append_audit(
                "APPROVAL_RECORDED",
                proposal_id=proposal.proposal_id,
                actor_id=approver_id,
                detail=digest,
                recorded_at=approved_at,
            )
            proposal.approvals.append(
                ApprovalRecord(
                    approver_id=approver_id,
                    approved_at=approved_at,
                    signature_hash=entry.entry_hash,
                    approved_digest=digest,
                    department=approver.department,
                )
            )
            proposal.payload_digest = digest

            # 7. Threshold evaluation over approvals still bound to the payload.
            if self._quorum_satisfied(proposal):
                proposal.status = STATUS_APPROVED

            logger.info(
                "APPROVAL RECORDED [%s]: approver=%s approvals=%d/%d status=%s",
                proposal_id,
                approver_id,
                len(self.valid_approvals(proposal)),
                proposal.required_approvals,
                proposal.status,
            )
            return proposal

    def reject_transfer(
        self, proposal_id: str, rejector_id: str, reason: str
    ) -> CustodyTransferProposal:
        """Terminally rejects a proposal.

        Any registered ``APPROVER`` or ``SECURITY_ADMIN`` may reject, including
        one who already approved -- withdrawing consent must stay available to a
        checker who later spots a problem. The initiator may also withdraw their
        own proposal; that is not self-approval, because a rejection cannot
        release funds.
        """
        proposal_id = _require_identifier(proposal_id, "proposal_id")
        rejector_id = _require_identifier(rejector_id, "rejector_id")
        reason = _require_identifier(reason, "reason")
        with self._lock:
            proposal = self.proposals.get(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal '{proposal_id}' not found.")
            if proposal.status in TERMINAL_STATUSES:
                raise SoDConflictError(
                    f"Proposal '{proposal_id}' is already {proposal.status}.",
                    SoDViolationType.PROPOSAL_NOT_PENDING,
                )
            rejector = self.users.get(rejector_id)
            permitted = rejector is not None and (
                rejector_id == proposal.initiator_id
                or CustodyRole.APPROVER in rejector.roles
                or CustodyRole.SECURITY_ADMIN in rejector.roles
            )
            if not permitted:
                raise SoDConflictError(
                    f"User '{rejector_id}' may not reject proposal '{proposal_id}'.",
                    SoDViolationType.UNAUTHORIZED_ROLE,
                )
            resolved_at = self._clock()
            proposal.status = STATUS_REJECTED
            proposal.resolved_by = rejector_id
            proposal.resolution_reason = reason
            proposal.resolved_at = resolved_at
            self._append_audit(
                "PROPOSAL_REJECTED",
                proposal_id=proposal.proposal_id,
                actor_id=rejector_id,
                detail=reason,
                recorded_at=resolved_at,
            )
            logger.info(
                "PROPOSAL REJECTED [%s]: rejector=%s reason=%s", proposal_id, rejector_id, reason
            )
            return proposal

    def mark_executed(self, proposal_id: str, executor_id: str) -> CustodyTransferProposal:
        """Closes an approved proposal at submission time, exactly once.

        Call this when the transfer is *submitted*, not when it confirms: a
        crash between submission and confirmation must not be resolved by
        releasing the transfer a second time. The status is re-derived first, so
        a proposal whose payload changed after approval cannot be executed.
        """
        proposal_id = _require_identifier(proposal_id, "proposal_id")
        executor_id = _require_identifier(executor_id, "executor_id")
        with self._lock:
            proposal = self.refresh_status(proposal_id)
            if proposal.status == STATUS_EXECUTED:
                raise SoDConflictError(
                    f"Proposal '{proposal_id}' has already been executed.",
                    SoDViolationType.PROPOSAL_NOT_PENDING,
                )
            if proposal.status != STATUS_APPROVED:
                raise SoDConflictError(
                    f"Proposal '{proposal_id}' is {proposal.status}, not {STATUS_APPROVED}; "
                    "the approval threshold is not currently satisfied.",
                    SoDViolationType.THRESHOLD_NOT_MET,
                )
            resolved_at = self._clock()
            proposal.status = STATUS_EXECUTED
            proposal.resolved_by = executor_id
            proposal.resolved_at = resolved_at
            self._append_audit(
                "PROPOSAL_EXECUTED",
                proposal_id=proposal.proposal_id,
                actor_id=executor_id,
                detail=compute_proposal_digest(proposal),
                recorded_at=resolved_at,
            )
            logger.info("PROPOSAL EXECUTED [%s]: executor=%s", proposal_id, executor_id)
            return proposal
