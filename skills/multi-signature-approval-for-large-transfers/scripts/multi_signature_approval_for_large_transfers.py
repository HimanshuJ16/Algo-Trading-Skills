"""Multi-signature approval gate for large crypto transfers.

This module is an **off-chain policy engine**. It decides whether a transfer
request has collected a valid M-of-N quorum from distinct authorised signers
holding distinct roles, bound to the exact payload those signers reviewed, and
whether the mandatory timelock for the request's risk tier has elapsed against a
clock the requester cannot influence.

It is not a signing device and it is not the authoritative enforcer. The
authoritative enforcer must be the vault itself: the on-chain multisig
threshold, the HSM quorum policy, or the custodian's own policy engine. This
gate runs first, inside your infrastructure, and leaves an auditable record of
why a request was allowed to proceed.
"""

import hashlib
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

DIGEST_DOMAIN = "MULTISIG_TRANSFER_APPROVAL_V1"


class MultiSigApprovalError(Exception):
    """Raised on malformed configuration, or on a request that cannot be scored.

    Raised rather than returned, because a request the engine cannot evaluate
    must never be mistaken for a request the engine declined.
    """


def _require_finite(value: float, label: str) -> float:
    """Rejects NaN/Inf before it can silently short-circuit a comparison."""
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise MultiSigApprovalError(f"{label} must be numeric, got {value!r}.") from exc
    if not math.isfinite(numeric):
        raise MultiSigApprovalError(f"{label} must be a finite number, got {value!r}.")
    return numeric


def _require_identifier(value: str, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise MultiSigApprovalError(f"{label} must be a non-empty identifier.")
    return text


def _normalise_role(role: str) -> str:
    return str(role).strip().upper()


def _length_prefixed(value: object) -> str:
    """Length-prefixes a digest field so no separator injection can forge it.

    Joining fields with a delimiter lets ("A|B", "C") and ("A", "B|C") hash
    identically; prefixing each field with its length removes the ambiguity.
    """
    if value is None:
        text = "None"
    elif isinstance(value, float):
        text = repr(value)
    else:
        text = str(value)
    return f"{len(text)}:{text}"


@dataclass(frozen=True)
class SignerApproval:
    """One signer's approval of one specific transfer payload.

    ``approved_digest`` is the digest of the payload the signer actually
    reviewed. It is what makes this an approval *of something*, rather than a
    vote in favour of a request id whose contents may since have changed.
    """

    signer_id: str
    role: str                            # must match this signer's role on the roster
    timestamp: float
    approved_digest: Optional[str] = None


@dataclass(frozen=True)
class RegisteredSigner:
    signer_id: str
    role: str
    is_suspended: bool = False


@dataclass
class TransferRequestPayload:
    """The transfer being authorised.

    ``amount_usd`` decides the risk tier only. ``asset_symbol`` /
    ``asset_quantity`` / ``chain`` describe what actually moves and are what the
    digest binds, because a USD valuation drifts with price while the on-chain
    quantity does not.
    """

    request_id: str
    amount_usd: float
    source_wallet: str
    destination_address: str
    initiated_by: str
    creation_timestamp: float
    asset_symbol: str = ""
    asset_quantity: Optional[float] = None
    chain: str = ""
    nonce: int = 0


@dataclass
class MultiSigConfig:
    """Firm policy. None of these values is set by any regulator.

    See ``references/standards.md``: the tiers, the dollar thresholds and the
    timelock duration are this skill's engineering defaults, not a compliance
    obligation, and must be set from your own risk appetite.
    """

    auto_approve_threshold_usd: float = 10000.0   # below this: single-signature tier
    high_value_threshold_usd: float = 100000.0    # above this: high tier + timelock
    med_m_required: int = 2
    med_n_total: int = 3
    high_m_required: int = 3
    high_n_total: int = 5
    high_value_timelock_seconds: float = 3600.0   # 1 hour abort window
    med_distinct_roles_required: int = 2
    high_distinct_roles_required: int = 3
    low_tier_allows_self_approval: bool = True
    require_payload_binding: bool = True          # deny by default
    approval_clock_skew_tolerance_seconds: float = 300.0
    approval_validity_seconds: Optional[float] = None   # None: approvals do not expire

    def __post_init__(self) -> None:
        auto = _require_finite(self.auto_approve_threshold_usd, "auto_approve_threshold_usd")
        high = _require_finite(self.high_value_threshold_usd, "high_value_threshold_usd")
        if auto < 0.0 or high < 0.0:
            raise MultiSigApprovalError("Tier thresholds must be non-negative.")
        if high < auto:
            raise MultiSigApprovalError(
                f"high_value_threshold_usd ({high}) must be >= auto_approve_threshold_usd ({auto}); "
                "an inverted pair silently makes the medium tier unreachable."
            )
        for m_field, n_field, roles_field in (
            ("med_m_required", "med_n_total", "med_distinct_roles_required"),
            ("high_m_required", "high_n_total", "high_distinct_roles_required"),
        ):
            m_val = int(getattr(self, m_field))
            n_val = int(getattr(self, n_field))
            roles_val = int(getattr(self, roles_field))
            if m_val < 1:
                raise MultiSigApprovalError(f"{m_field} must be >= 1, got {m_val}.")
            if m_val > n_val:
                raise MultiSigApprovalError(
                    f"{m_field} ({m_val}) exceeds {n_field} ({n_val}); the quorum would be unreachable."
                )
            if roles_val < 1 or roles_val > m_val:
                raise MultiSigApprovalError(
                    f"{roles_field} ({roles_val}) must be between 1 and {m_field} ({m_val})."
                )
        timelock = _require_finite(self.high_value_timelock_seconds, "high_value_timelock_seconds")
        if timelock < 0.0:
            raise MultiSigApprovalError("high_value_timelock_seconds must be non-negative.")
        skew = _require_finite(
            self.approval_clock_skew_tolerance_seconds, "approval_clock_skew_tolerance_seconds"
        )
        if skew < 0.0:
            raise MultiSigApprovalError("approval_clock_skew_tolerance_seconds must be non-negative.")
        if self.approval_validity_seconds is not None:
            validity = _require_finite(self.approval_validity_seconds, "approval_validity_seconds")
            if validity <= 0.0:
                raise MultiSigApprovalError("approval_validity_seconds must be positive when set.")


@dataclass
class MultiSigApprovalReport:
    """Audit record of one approval decision."""

    request_id: str
    amount_usd: float
    risk_tier: str                       # 'LOW_AUTO', 'MEDIUM_MULTISIG', 'HIGH_MULTISIG_TIMELOCK'
    m_required: int
    n_total: int
    submitted_approvals_count: int       # distinct signers whose approval was accepted
    timelock_satisfied: bool
    is_approved: bool
    status: str                          # see MultiSigApprovalEngine.STATUSES
    audit_notes: str
    transfer_digest: str = ""
    total_submitted_approvals: int = 0
    eligible_signer_count: int = 0
    approving_signers: Tuple[str, ...] = ()
    distinct_roles_present: Tuple[str, ...] = ()
    distinct_roles_required: int = 0
    rejected_approvals: Tuple[Tuple[str, str], ...] = ()
    timelock_anchor_timestamp: float = 0.0
    timelock_required_seconds: float = 0.0
    remaining_timelock_seconds: float = 0.0
    warnings: Tuple[str, ...] = ()


def compute_transfer_digest(request: TransferRequestPayload) -> str:
    """SHA-256 over the canonical payload a signer is agreeing to.

    Any change to the destination, chain, asset, quantity, USD valuation or
    nonce produces a different digest, which invalidates every approval already
    collected and re-anchors the timelock.
    """
    canonical = "".join(
        _length_prefixed(part)
        for part in (
            DIGEST_DOMAIN,
            request.request_id,
            request.source_wallet,
            request.destination_address,
            request.chain,
            request.asset_symbol,
            request.asset_quantity,
            float(request.amount_usd),
            int(request.nonce),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MultiSigApprovalEngine:
    """Tiered M-of-N approval gate with role separation and a trusted-clock timelock.

    State held here -- signer roster, timelock anchors, revocations, executed
    digests -- is in-process only. The internal lock serialises concurrent
    callers within one process; a multi-process or multi-host deployment must
    back this state with a shared store, or two workers will each see an
    unexecuted digest and release the same transfer twice.
    """

    STATUSES = (
        "TRANSFER_APPROVED",
        "INSUFFICIENT_SIGNATURES",
        "INSUFFICIENT_DISTINCT_ROLES",
        "TIMELOCK_PENDING",
        "REQUEST_REVOKED",
        "ALREADY_EXECUTED",
    )

    def __init__(self, config: Optional[MultiSigConfig] = None):
        self.config = config or MultiSigConfig()
        self._lock = threading.RLock()
        self._signers: Dict[str, RegisteredSigner] = {}
        self._timelock_anchors: Dict[str, float] = {}
        self._revoked_requests: Dict[str, str] = {}
        self._executed_digests: Dict[str, float] = {}

    # ------------------------------------------------------------------ roster

    def register_signer(self, signer_id: str, role: str) -> RegisteredSigner:
        """Adds a signer to the quorum roster.

        An approval from an id that is not on this roster is not counted.
        Without a roster, "3-of-5" degrades to "any three strings", which is not
        a threshold at all.
        """
        clean_id = _require_identifier(signer_id, "signer_id")
        clean_role = _normalise_role(_require_identifier(role, "role"))
        signer = RegisteredSigner(signer_id=clean_id, role=clean_role)
        with self._lock:
            self._signers[clean_id] = signer
        logger.info(f"MULTISIG ROSTER: registered signer '{clean_id}' with role '{clean_role}'.")
        return signer

    def suspend_signer(self, signer_id: str, reason: str = "") -> None:
        """Marks a signer ineligible, e.g. on suspected key compromise.

        Suspension takes effect on the next evaluation, so an approval already
        collected from a signer suspended during the timelock window stops
        counting before the transfer can be released.
        """
        clean_id = _require_identifier(signer_id, "signer_id")
        with self._lock:
            existing = self._signers.get(clean_id)
            if existing is None:
                raise MultiSigApprovalError(f"Cannot suspend unknown signer '{clean_id}'.")
            self._signers[clean_id] = RegisteredSigner(
                existing.signer_id, existing.role, is_suspended=True
            )
        logger.warning(
            f"MULTISIG ROSTER: suspended signer '{clean_id}'. Reason: {reason or 'unspecified'}."
        )

    def reinstate_signer(self, signer_id: str) -> None:
        """Returns a suspended signer to the eligible roster."""
        clean_id = _require_identifier(signer_id, "signer_id")
        with self._lock:
            existing = self._signers.get(clean_id)
            if existing is None:
                raise MultiSigApprovalError(f"Cannot reinstate unknown signer '{clean_id}'.")
            self._signers[clean_id] = RegisteredSigner(
                existing.signer_id, existing.role, is_suspended=False
            )
        logger.info(f"MULTISIG ROSTER: reinstated signer '{clean_id}'.")

    # -------------------------------------------------------- request lifecycle

    def register_request(
        self,
        request: TransferRequestPayload,
        current_time: Optional[float] = None,
    ) -> float:
        """Anchors the timelock for this payload to the engine's own clock.

        The anchor is deliberately *not* ``request.creation_timestamp``: that
        field travels with the request, and a requester who can back-date it
        would open the timelock instantly. Re-registering the same payload keeps
        the original anchor; changing the payload yields a new digest and a
        fresh full timelock.
        """
        digest = self._validated_digest(request)
        now = self._resolve_now(current_time)
        with self._lock:
            return self._timelock_anchors.setdefault(digest, now)

    def restore_timelock_anchor(self, transfer_digest: str, anchor_timestamp: float) -> None:
        """Trusted restore of an anchor from durable storage after a restart.

        Only ever call this with a value the engine itself previously emitted.
        It is the one path that can set an anchor into the past, which is why it
        is a named administrative operation and not a field on the request.
        """
        digest = _require_identifier(transfer_digest, "transfer_digest")
        anchor = _require_finite(anchor_timestamp, "anchor_timestamp")
        with self._lock:
            self._timelock_anchors[digest] = anchor
        logger.warning(f"MULTISIG ANCHOR RESTORED [{digest[:12]}]: anchor set to {anchor}.")

    def revoke_request(self, request_id: str, revoked_by: str, reason: str = "") -> None:
        """Aborts a request. This is what the timelock window exists for.

        Revocation is keyed on ``request_id``, not on the digest, so a revoked
        request cannot be resurrected by bumping the nonce or nudging the
        amount.
        """
        clean_id = _require_identifier(request_id, "request_id")
        clean_actor = _require_identifier(revoked_by, "revoked_by")
        with self._lock:
            self._revoked_requests[clean_id] = (
                f"revoked by {clean_actor}: {reason or 'unspecified'}"
            )
            note = self._revoked_requests[clean_id]
        logger.error(f"MULTISIG REVOKED [{clean_id}]: {note}.")

    def mark_executed(self, transfer_digest: str, current_time: Optional[float] = None) -> None:
        """Records that this exact payload has been released.

        Call it at submission, not at confirmation, so a crash between the two
        cannot be resolved by releasing the transfer a second time.
        """
        digest = _require_identifier(transfer_digest, "transfer_digest")
        now = self._resolve_now(current_time)
        with self._lock:
            if digest in self._executed_digests:
                raise MultiSigApprovalError(
                    f"Transfer digest '{digest[:12]}' was already marked executed at "
                    f"{self._executed_digests[digest]}."
                )
            self._executed_digests[digest] = now
        logger.info(f"MULTISIG EXECUTED [{digest[:12]}]: recorded at {now}.")

    # ------------------------------------------------------------- evaluation

    def evaluate_transfer_approval(
        self,
        request: TransferRequestPayload,
        approvals: Sequence[SignerApproval],
        current_time: Optional[float] = None,
    ) -> MultiSigApprovalReport:
        """Scores one transfer request against the tiered M-of-N policy.

        ``current_time`` is the trusted evaluation clock. Pass it explicitly for
        reproducible audits; ``0.0`` is honoured as a real timestamp rather than
        falling back to the wall clock.
        """
        digest = self._validated_digest(request)
        now = self._resolve_now(current_time)
        warnings: List[str] = []

        tier, m_req, n_tot, roles_req, timelock_req = self._classify(request.amount_usd)

        if request.asset_quantity is None and tier != "LOW_AUTO":
            warnings.append(
                "No asset_quantity bound to the payload: the quorum is approving a USD "
                "valuation, and the on-chain amount is unconstrained by this digest."
            )

        creation = float(request.creation_timestamp)
        skew = self.config.approval_clock_skew_tolerance_seconds
        if creation > now + skew:
            warnings.append(
                f"creation_timestamp is {creation - now:.0f}s ahead of the evaluation clock "
                "(recorded for audit only; the timelock does not use it)."
            )

        with self._lock:
            anchor = self._timelock_anchors.setdefault(digest, now)
            revocation = self._revoked_requests.get(request.request_id)
            executed_at = self._executed_digests.get(digest)
            roster = dict(self._signers)

        eligible = {sid: s for sid, s in roster.items() if not s.is_suspended}
        if eligible and len(eligible) < n_tot:
            warnings.append(
                f"Policy declares {m_req}-of-{n_tot} but only {len(eligible)} eligible signers "
                "are on the roster; N is not actually available."
            )

        accepted, rejected = self._screen_approvals(
            request, approvals, digest, roster, eligible, tier, now, warnings
        )
        accepted_roles = tuple(sorted({eligible[sid].role for sid in accepted}))
        valid_count = len(accepted)

        elapsed = now - anchor
        remaining = max(0.0, timelock_req - elapsed)
        timelock_ok = elapsed >= timelock_req

        status, is_approved, notes = self._decide(
            request_id=request.request_id,
            tier=tier,
            revocation=revocation,
            executed_at=executed_at,
            valid_count=valid_count,
            m_req=m_req,
            roles_present=accepted_roles,
            roles_req=roles_req,
            timelock_ok=timelock_ok,
            remaining=remaining,
        )

        if is_approved:
            logger.info(notes)
        else:
            logger.warning(notes)
        for warning in warnings:
            logger.warning(f"MULTISIG WARN [{request.request_id}]: {warning}")
        for signer_id, reason in rejected:
            logger.warning(
                f"MULTISIG APPROVAL REJECTED [{request.request_id}]: '{signer_id}' -> {reason}."
            )

        return MultiSigApprovalReport(
            request_id=request.request_id,
            amount_usd=float(request.amount_usd),
            risk_tier=tier,
            m_required=m_req,
            n_total=n_tot,
            submitted_approvals_count=valid_count,
            timelock_satisfied=timelock_ok,
            is_approved=is_approved,
            status=status,
            audit_notes=notes,
            transfer_digest=digest,
            total_submitted_approvals=len(approvals),
            eligible_signer_count=len(eligible),
            approving_signers=tuple(sorted(accepted)),
            distinct_roles_present=accepted_roles,
            distinct_roles_required=roles_req,
            rejected_approvals=tuple(rejected),
            timelock_anchor_timestamp=anchor,
            timelock_required_seconds=timelock_req,
            remaining_timelock_seconds=remaining,
            warnings=tuple(warnings),
        )

    # ---------------------------------------------------------------- internals

    def _resolve_now(self, current_time: Optional[float]) -> float:
        # `current_time or time.time()` would silently discard a legitimate 0.0.
        if current_time is None:
            return time.time()
        return _require_finite(current_time, "current_time")

    def _validated_digest(self, request: TransferRequestPayload) -> str:
        """Validates the payload, then derives the digest signers must bind to."""
        _require_identifier(request.request_id, "request_id")
        _require_identifier(request.source_wallet, "source_wallet")
        _require_identifier(request.destination_address, "destination_address")
        _require_identifier(request.initiated_by, "initiated_by")
        amount = _require_finite(request.amount_usd, "amount_usd")
        if amount <= 0.0:
            raise MultiSigApprovalError(f"amount_usd must be positive, got {amount!r}.")
        if request.asset_quantity is not None:
            quantity = _require_finite(request.asset_quantity, "asset_quantity")
            if quantity <= 0.0:
                raise MultiSigApprovalError(
                    f"asset_quantity must be positive when set, got {quantity!r}."
                )
        _require_finite(request.creation_timestamp, "creation_timestamp")
        return compute_transfer_digest(request)

    def _classify(self, amount_usd: float) -> Tuple[str, int, int, int, float]:
        """Maps a USD notional to (tier, M, N, distinct roles, timelock seconds)."""
        amount = float(amount_usd)
        if amount < self.config.auto_approve_threshold_usd:
            return "LOW_AUTO", 1, 1, 1, 0.0
        if amount <= self.config.high_value_threshold_usd:
            return (
                "MEDIUM_MULTISIG",
                self.config.med_m_required,
                self.config.med_n_total,
                self.config.med_distinct_roles_required,
                0.0,
            )
        return (
            "HIGH_MULTISIG_TIMELOCK",
            self.config.high_m_required,
            self.config.high_n_total,
            self.config.high_distinct_roles_required,
            self.config.high_value_timelock_seconds,
        )

    def _screen_approvals(
        self,
        request: TransferRequestPayload,
        approvals: Sequence[SignerApproval],
        digest: str,
        roster: Dict[str, RegisteredSigner],
        eligible: Dict[str, RegisteredSigner],
        tier: str,
        now: float,
        warnings: List[str],
    ) -> Tuple[Set[str], List[Tuple[str, str]]]:
        """Filters submitted approvals down to the distinct signers that count."""
        accepted: Set[str] = set()
        rejected: List[Tuple[str, str]] = []
        skew = self.config.approval_clock_skew_tolerance_seconds
        validity = self.config.approval_validity_seconds
        initiator = str(request.initiated_by).strip()

        if not eligible:
            warnings.append(
                "No eligible signers registered: every approval is rejected. Call "
                "register_signer() first, or the M-of-N threshold is unenforceable."
            )

        for approval in approvals:
            signer_id = str(approval.signer_id).strip()
            if not signer_id:
                rejected.append(("<blank>", "BLANK_SIGNER_ID"))
                continue

            allow_self = tier == "LOW_AUTO" and self.config.low_tier_allows_self_approval
            if signer_id == initiator and not allow_self:
                rejected.append((signer_id, "SELF_APPROVAL_BY_INITIATOR"))
                continue

            signer = eligible.get(signer_id)
            if signer is None:
                reason = "SIGNER_SUSPENDED" if signer_id in roster else "SIGNER_NOT_ON_ROSTER"
                rejected.append((signer_id, reason))
                continue

            if _normalise_role(approval.role) != signer.role:
                # A declared role that disagrees with the roster is a tamper
                # signal, not a typo to be tolerated.
                rejected.append((signer_id, "ROLE_MISMATCH_WITH_ROSTER"))
                continue

            if self.config.require_payload_binding:
                if not approval.approved_digest:
                    rejected.append((signer_id, "APPROVAL_NOT_BOUND_TO_PAYLOAD"))
                    continue
                if approval.approved_digest != digest:
                    rejected.append((signer_id, "APPROVAL_BOUND_TO_DIFFERENT_PAYLOAD"))
                    continue

            try:
                approved_at = _require_finite(approval.timestamp, "approval timestamp")
            except MultiSigApprovalError:
                rejected.append((signer_id, "NON_FINITE_APPROVAL_TIMESTAMP"))
                continue

            if approved_at > now + skew:
                rejected.append((signer_id, "APPROVAL_TIMESTAMP_IN_FUTURE"))
                continue
            if validity is not None and (now - approved_at) > validity:
                rejected.append((signer_id, "APPROVAL_EXPIRED"))
                continue

            if signer_id in accepted:
                rejected.append((signer_id, "DUPLICATE_APPROVAL_FROM_SAME_SIGNER"))
                continue

            accepted.add(signer_id)

        return accepted, rejected

    def _decide(
        self,
        request_id: str,
        tier: str,
        revocation: Optional[str],
        executed_at: Optional[float],
        valid_count: int,
        m_req: int,
        roles_present: Tuple[str, ...],
        roles_req: int,
        timelock_ok: bool,
        remaining: float,
    ) -> Tuple[str, bool, str]:
        """Fail-closed decision ladder: the most decisive blocker wins."""
        if revocation is not None:
            return (
                "REQUEST_REVOKED",
                False,
                f"REQUEST REVOKED [{request_id}]: {revocation}.",
            )
        if executed_at is not None:
            return (
                "ALREADY_EXECUTED",
                False,
                f"ALREADY EXECUTED [{request_id}]: this payload was released at {executed_at}; "
                "re-approving it would double-spend.",
            )
        if valid_count < m_req:
            return (
                "INSUFFICIENT_SIGNATURES",
                False,
                f"INSUFFICIENT SIGNATURES [{request_id}]: {valid_count} valid approvals of "
                f"{m_req} required for {tier}.",
            )
        if len(roles_present) < roles_req:
            return (
                "INSUFFICIENT_DISTINCT_ROLES",
                False,
                f"INSUFFICIENT DISTINCT ROLES [{request_id}]: quorum met ({valid_count}/{m_req}) "
                f"but approvals span {len(roles_present)} role(s) {list(roles_present)}, "
                f"{roles_req} required for {tier}.",
            )
        if not timelock_ok:
            return (
                "TIMELOCK_PENDING",
                False,
                f"TIMELOCK PENDING [{request_id}]: quorum met ({valid_count}/{m_req}) but the "
                f"abort window needs {remaining:.0f}s more.",
            )
        return (
            "TRANSFER_APPROVED",
            True,
            f"TRANSFER APPROVED [{request_id}]: {tier} satisfied "
            f"({valid_count}/{m_req} signers across {len(roles_present)} roles, timelock elapsed).",
        )
