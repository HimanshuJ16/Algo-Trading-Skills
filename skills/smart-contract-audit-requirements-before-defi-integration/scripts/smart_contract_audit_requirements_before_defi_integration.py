"""
smart-contract-audit-requirements-before-defi-integration: pre-integration due
diligence gate for third-party DeFi protocols.

What this module is and is not
------------------------------
It is a **structured, auditable gate** that turns a documented protocol profile
into a pass/fail integration decision with named blocking violations, so a
capital allocation decision leaves an evidence trail rather than a gut call.

It is **not** a security assessment. It does not read bytecode, simulate the
protocol, or detect vulnerabilities. It scores assertions a human reviewer has
already verified against primary artefacts (the audit reports, the deployed
contract's verified source, the governance contracts on-chain).

Why "audited" is not a safety property
--------------------------------------
Audit firms say so themselves. OpenZeppelin's Terms of Service (s. 8.8) states
that reports "do not constitute statements, representations or warranties by
OpenZeppelin in any respect, including regarding the security of such Protocol"
and that "[y]ou may not rely on the Reports in any way, including for the
purpose of making any decisions to use a Protocol". An audit is a time-boxed
review of a *specific commit*. This module therefore treats an audit as
qualifying only when the reviewer has attested that its scope covers the code
actually deployed at ``contract_address``.

Thresholds
----------
No regulator mandates any threshold used here. The defaults are firm policy
inputs with their provenance recorded in ``references/standards.md`` and in the
constant comments below. Calibrate them and record the calibration.

Determinism
-----------
``evaluate_protocol`` accepts an ``assessment_date``. It defaults to today only
as a convenience; pass it explicitly for reproducible, auditable output.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Engineering defaults -------------------------------------------------
# None of these are regulatory requirements. Provenance is recorded per
# constant so a reviewer can argue with the number rather than inherit it.

#: Compound's ``Timelock.sol`` hard-codes ``MINIMUM_DELAY = 2 days`` (with
#: ``MAXIMUM_DELAY = 30 days`` and ``GRACE_PERIOD = 14 days``). Governor
#: Bravo / Timelock forks inherit it, which is where the 48h figure comes from:
#: a de facto ecosystem floor, not a rule. Note that major protocols sit below
#: it -- Aave governance imposes either a 1-day or a 7-day delay depending on
#: proposal level, so a blanket 48h floor rejects its 1-day tier.
DEFAULT_MIN_TIMELOCK_HOURS = 48.0

#: Diversification-of-reviewers heuristic. No external basis.
DEFAULT_MIN_TIER1_AUDITS = 2

#: Battle-testing proxy. No external basis. Must be measured from the
#: deployment of the *current implementation*, not the protocol's launch.
DEFAULT_MIN_MAINNET_DAYS = 90

#: Absolute floor on the maximum critical payout, applied as the greater of
#: this and the TVL-proportional requirement below.
DEFAULT_MIN_BUG_BOUNTY_USD = 100_000.0

#: Immunefi's "Scaling Bug Bounty" proposal (2024-09-02) prices the maximum
#: critical payout at ~10% of funds at risk. Immunefi describes it as an
#: experiment to be adjusted, not a standard anyone is bound by, and in
#: practice no large protocol funds it -- 10% of a $5B pool is $500M. It is
#: therefore an *advisory* reference ratio here, not a blocking threshold:
#: a relative risk signal, not an attainable target.
DEFAULT_BUG_BOUNTY_TVL_RATIO = 0.10

# Multisig floors follow the Security Alliance (SEAL) "Secure Multisig Best
# Practices": a minimum of 3 signers, a signing threshold of at least 50%,
# never an N-of-N scheme (losing one key would permanently lock the funds),
# and 7+ signers for multisigs controlling $1M or more.
DEFAULT_MIN_MULTISIG_THRESHOLD = 3
DEFAULT_MIN_MULTISIG_SIGNERS = 5
DEFAULT_MIN_MULTISIG_THRESHOLD_RATIO = 0.5
SEAL_LARGE_TREASURY_USD = 1_000_000.0
SEAL_LARGE_TREASURY_MIN_SIGNERS = 7

#: Age past which a qualifying audit raises an advisory. Not blocking: if the
#: audited commit still matches the deployed bytecode, age alone does not
#: invalidate the review -- but vulnerability classes known today may postdate
#: it.
DEFAULT_MAX_AUDIT_AGE_DAYS = 365

# --- Violation codes ------------------------------------------------------
# Stable string prefixes. Callers match on these; do not rename them.
VIOLATION_INSUFFICIENT_AUDITS = "INSUFFICIENT_AUDITS"
VIOLATION_UNRESOLVED_VULNERABILITIES = "UNRESOLVED_VULNERABILITIES"
VIOLATION_UNTESTED_CODEBASE = "UNTESTED_CODEBASE"
VIOLATION_DANGEROUS_TIMELOCK = "DANGEROUS_TIMELOCK"
VIOLATION_WEAK_MULTISIG = "WEAK_MULTISIG"
VIOLATION_INADEQUATE_BUG_BOUNTY = "INADEQUATE_BUG_BOUNTY"


class DeFiDueDiligenceError(ValueError):
    """Raised when a protocol spec or engine configuration is invalid.

    A due diligence gate must fail loudly. A spec carrying an impossible value
    (a 4-of-2 multisig, a negative TVL, an audit dated after the assessment) is
    a reviewer data-entry error, and gating capital on it would produce an
    authoritative-looking approval built on garbage.
    """


@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "smart-contract-audit-requirements-before-defi-integration"


class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return True


class AuditFirmTier(str, Enum):
    """Reviewer-assigned reputation tier.

    There is no industry-standard ranking of smart contract audit firms and no
    body that certifies one. The firm names below are the common composition of
    an institutional Tier-1 roster, not an authoritative list -- maintain your
    own roster and record why each firm is on it.
    """

    TIER_1_TOP_REPUTATION = "TIER_1_TOP_REPUTATION"  # e.g. Trail of Bits, OpenZeppelin, Spearbit
    TIER_2_REPUTABLE = "TIER_2_REPUTABLE"            # e.g. CertiK, Hacken, PeckShield
    UNVERIFIED_INDIVIDUAL = "UNVERIFIED_INDIVIDUAL"


@dataclass
class AuditReport:
    """One audit report, as read by the reviewer -- not as advertised.

    ``scope_covers_deployed_code`` is the field that does the real work. An
    audit reviews a specific commit; the protocol you are about to fund runs
    whatever bytecode is at ``contract_address`` today. Set it to ``True`` only
    after comparing the commit named in the report's scope section against the
    verified source of the deployed contract. ``None`` means nobody has
    checked. Neither ``None`` nor ``False`` qualifies, but they are different
    remediation items, so the engine reports them differently.
    """

    firm_name: str
    firm_tier: AuditFirmTier
    audit_date_iso: str                              # ISO-8601 date, e.g. "2026-01-15"
    critical_findings_count: int
    high_findings_count: int
    all_critical_high_remediated: bool
    fix_verification_confirmed: bool
    scope_covers_deployed_code: Optional[bool] = None


@dataclass
class DeFiProtocolSpec:
    """Documented, evidence-backed attributes of a candidate DeFi protocol."""

    protocol_name: str
    contract_address: str
    #: Total value locked. Sizes the required bug bounty, and is the figure a
    #: critical bug puts at risk, so keep it current.
    tvl_usd: float
    #: Days the *currently deployed implementation* has been live on mainnet.
    mainnet_days_active: int
    audits: List[AuditReport]
    has_active_bug_bounty: bool
    bug_bounty_max_payout_usd: float
    #: Delay enforced by the timelock that actually owns the proxy admin. A
    #: deployed timelock that does not hold the admin role protects nothing.
    admin_timelock_delay_hours: float
    admin_multisig_signers_count: int                # Total keys (N)
    admin_multisig_threshold_required: int           # Signatures required (M)
    #: A pause/guardian role. Protective against an in-progress exploit, but it
    #: is itself an un-timelocked power -- see the advisory this raises.
    has_emergency_pause_circuit_breaker: bool


@dataclass
class DeFiIntegrationGateReport:
    protocol_name: str
    is_approved: bool
    safety_score_pct: float                          # 0.0 to 100.0
    blocking_violations: List[str]
    audit_notes: str
    #: Non-blocking findings the reviewer must still disposition.
    advisories: List[str] = field(default_factory=list)
    #: Max critical bounty payout / TVL. ``None`` when there is no active
    #: programme or TVL is zero. A relative risk signal, never a gate -- see
    #: the ``BOUNTY_SMALL_VS_TVL`` advisory.
    bug_bounty_tvl_coverage_ratio: Optional[float] = None


def _require_non_negative(value: float, label: str) -> None:
    """Reject NaN, infinities, and negative magnitudes."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DeFiDueDiligenceError(f"{label} must be numeric (got {value!r}).")
    if not math.isfinite(value):
        raise DeFiDueDiligenceError(f"{label} must be finite (got {value!r}).")
    if value < 0:
        raise DeFiDueDiligenceError(f"{label} must be >= 0 (got {value!r}).")


def _coerce_tier(report: AuditReport) -> AuditFirmTier:
    """Accept either the enum member or its equivalent string.

    ``AuditFirmTier`` subclasses ``str``, so ``"TIER_1_TOP_REPUTATION"``
    compares equal to the member and callers reasonably pass it. Coercing here
    keeps identity comparisons and ``.value`` access safe for both forms, and
    turns an unrecognised tier into a data-entry error rather than a silent
    demotion to "not Tier-1".
    """
    try:
        return AuditFirmTier(report.firm_tier)
    except ValueError as exc:
        raise DeFiDueDiligenceError(
            f"Audit by {report.firm_name!r} has an unrecognised firm_tier "
            f"({report.firm_tier!r}); expected one of "
            f"{[t.value for t in AuditFirmTier]}."
        ) from exc


def _parse_audit_date(report: AuditReport) -> date:
    try:
        return date.fromisoformat(str(report.audit_date_iso).strip())
    except (AttributeError, TypeError, ValueError) as exc:
        raise DeFiDueDiligenceError(
            f"Audit by {report.firm_name!r} has an unparseable audit_date_iso "
            f"({report.audit_date_iso!r}); expected an ISO-8601 date such as "
            "'2026-01-15'."
        ) from exc


class SmartContractAuditRequirementsBeforeDeFiIntegrationEngine:
    """Pre-integration due diligence gate for third-party DeFi protocols.

    All six gates are mandatory: ``is_approved`` is true only when every one
    passes. ``safety_score_pct`` is therefore a *remediation progress*
    indicator (how much of the gate a protocol already clears), not a risk
    appetite dial -- an 83% protocol is blocked exactly as firmly as a 17% one.
    """

    def __init__(
        self,
        min_tier1_audits_required: int = DEFAULT_MIN_TIER1_AUDITS,
        min_mainnet_days: int = DEFAULT_MIN_MAINNET_DAYS,
        min_timelock_hours: float = DEFAULT_MIN_TIMELOCK_HOURS,
        min_bug_bounty_usd: float = DEFAULT_MIN_BUG_BOUNTY_USD,
        min_multisig_threshold: int = DEFAULT_MIN_MULTISIG_THRESHOLD,
        min_multisig_signers: int = DEFAULT_MIN_MULTISIG_SIGNERS,
        min_multisig_threshold_ratio: float = DEFAULT_MIN_MULTISIG_THRESHOLD_RATIO,
        min_bug_bounty_tvl_ratio: float = DEFAULT_BUG_BOUNTY_TVL_RATIO,
        max_audit_age_days: int = DEFAULT_MAX_AUDIT_AGE_DAYS,
    ) -> None:
        if min_tier1_audits_required < 1:
            raise DeFiDueDiligenceError(
                "min_tier1_audits_required must be >= 1; a gate that accepts zero "
                "audits is not a gate."
            )
        if min_multisig_threshold < 1:
            raise DeFiDueDiligenceError("min_multisig_threshold must be >= 1.")
        if min_multisig_signers < min_multisig_threshold:
            raise DeFiDueDiligenceError(
                f"min_multisig_signers ({min_multisig_signers}) must be >= "
                f"min_multisig_threshold ({min_multisig_threshold})."
            )
        if not 0.0 < min_multisig_threshold_ratio <= 1.0:
            raise DeFiDueDiligenceError(
                "min_multisig_threshold_ratio must be in (0.0, 1.0]."
            )
        _require_non_negative(min_bug_bounty_tvl_ratio, "min_bug_bounty_tvl_ratio")
        if max_audit_age_days < 1:
            raise DeFiDueDiligenceError("max_audit_age_days must be >= 1.")
        _require_non_negative(min_mainnet_days, "min_mainnet_days")
        _require_non_negative(min_timelock_hours, "min_timelock_hours")
        _require_non_negative(min_bug_bounty_usd, "min_bug_bounty_usd")

        self.min_tier1_audits_required = min_tier1_audits_required
        self.min_mainnet_days = min_mainnet_days
        self.min_timelock_hours = min_timelock_hours
        self.min_bug_bounty_usd = min_bug_bounty_usd
        self.min_multisig_threshold = min_multisig_threshold
        self.min_multisig_signers = min_multisig_signers
        self.min_multisig_threshold_ratio = min_multisig_threshold_ratio
        self.min_bug_bounty_tvl_ratio = min_bug_bounty_tvl_ratio
        self.max_audit_age_days = max_audit_age_days

    # -- validation --------------------------------------------------------

    def _validate(self, protocol: DeFiProtocolSpec, assessment_date: date) -> None:
        """Reject specs that cannot describe a real protocol.

        Raises rather than scoring, because every value below is a reviewer
        data-entry error rather than a substantive deficiency of the protocol,
        and the two must never be conflated in an audit trail.
        """
        if not str(protocol.protocol_name).strip():
            raise DeFiDueDiligenceError("protocol_name must be a non-empty string.")
        if not str(protocol.contract_address).strip():
            raise DeFiDueDiligenceError(
                f"{protocol.protocol_name}: contract_address must be a non-empty "
                "string; audit scope cannot be checked against an unnamed contract."
            )

        _require_non_negative(protocol.tvl_usd, "tvl_usd")
        _require_non_negative(protocol.mainnet_days_active, "mainnet_days_active")
        _require_non_negative(
            protocol.bug_bounty_max_payout_usd, "bug_bounty_max_payout_usd"
        )
        _require_non_negative(
            protocol.admin_timelock_delay_hours, "admin_timelock_delay_hours"
        )

        signers = protocol.admin_multisig_signers_count
        threshold = protocol.admin_multisig_threshold_required
        if signers < 1:
            raise DeFiDueDiligenceError(
                f"admin_multisig_signers_count must be >= 1 (got {signers})."
            )
        if threshold < 1:
            raise DeFiDueDiligenceError(
                f"admin_multisig_threshold_required must be >= 1 (got {threshold})."
            )
        if threshold > signers:
            # Previously accepted silently: a 4-of-2 multisig scored as strong.
            raise DeFiDueDiligenceError(
                f"{protocol.protocol_name}: admin_multisig_threshold_required "
                f"({threshold}) exceeds admin_multisig_signers_count ({signers}); "
                "this configuration cannot exist on-chain."
            )

        if not protocol.audits:
            raise DeFiDueDiligenceError(
                f"{protocol.protocol_name}: audits must contain at least one "
                "AuditReport. An empty list is indistinguishable from 'nobody "
                "looked', which must not be recorded as a substantive finding."
            )

        for report in protocol.audits:
            if report.critical_findings_count < 0 or report.high_findings_count < 0:
                raise DeFiDueDiligenceError(
                    f"Audit by {report.firm_name!r} has negative finding counts."
                )
            _coerce_tier(report)
            audit_date = _parse_audit_date(report)
            if audit_date > assessment_date:
                raise DeFiDueDiligenceError(
                    f"Audit by {report.firm_name!r} is dated "
                    f"{audit_date.isoformat()}, after the assessment date "
                    f"{assessment_date.isoformat()}."
                )

    # -- individual gates --------------------------------------------------

    def _gate_audits(
        self, protocol: DeFiProtocolSpec, assessment_date: date
    ) -> Tuple[bool, Optional[str], List[str]]:
        """Count Tier-1 audits whose scope covers the deployed code."""
        advisories: List[str] = []
        qualifying: List[AuditReport] = []
        exclusions: List[str] = []

        for report in protocol.audits:
            tier = _coerce_tier(report)
            if tier is not AuditFirmTier.TIER_1_TOP_REPUTATION:
                exclusions.append(f"{report.firm_name} (tier {tier.value})")
            elif report.scope_covers_deployed_code is None:
                exclusions.append(
                    f"{report.firm_name} (scope vs deployed bytecode never attested)"
                )
            elif not report.scope_covers_deployed_code:
                exclusions.append(
                    f"{report.firm_name} (audited commit does not match code deployed "
                    f"at {protocol.contract_address})"
                )
            else:
                qualifying.append(report)

        for report in qualifying:
            age_days = (assessment_date - _parse_audit_date(report)).days
            if age_days > self.max_audit_age_days:
                advisories.append(
                    f"STALE_AUDIT: {report.firm_name} audit is {age_days} days old "
                    f"(> {self.max_audit_age_days}). Scope still matches the deployed "
                    "code, so this is not blocking, but vulnerability classes known "
                    "today may postdate the review."
                )

        if len(qualifying) < self.min_tier1_audits_required:
            detail = f" Excluded: {'; '.join(exclusions)}." if exclusions else ""
            return False, (
                f"{VIOLATION_INSUFFICIENT_AUDITS}: Found {len(qualifying)} Tier-1 "
                f"audit(s) covering the deployed code (Required >= "
                f"{self.min_tier1_audits_required}).{detail}"
            ), advisories
        return True, None, advisories

    def _gate_unresolved_findings(
        self, protocol: DeFiProtocolSpec
    ) -> Tuple[bool, Optional[str], List[str]]:
        """Flag audits that reported Critical/High findings still open.

        Only audits that actually reported Critical or High findings are
        considered. A clean report has nothing to remediate, and it was
        previously flagged as "unresolved" purely because its remediation
        booleans were left false.
        """
        unresolved = [
            a for a in protocol.audits
            if (a.critical_findings_count + a.high_findings_count) > 0
            and not (a.all_critical_high_remediated and a.fix_verification_confirmed)
        ]
        if unresolved:
            detail = ", ".join(
                f"{a.firm_name} ({a.critical_findings_count}C/{a.high_findings_count}H)"
                for a in unresolved
            )
            return False, (
                f"{VIOLATION_UNRESOLVED_VULNERABILITIES}: Audits by [{detail}] have "
                "un-remediated or unverified Critical/High findings."
            ), []
        return True, None, []

    def _gate_mainnet_longevity(
        self, protocol: DeFiProtocolSpec
    ) -> Tuple[bool, Optional[str], List[str]]:
        if protocol.mainnet_days_active < self.min_mainnet_days:
            return False, (
                f"{VIOLATION_UNTESTED_CODEBASE}: Deployed implementation active for "
                f"{protocol.mainnet_days_active} days on mainnet (Required >= "
                f"{self.min_mainnet_days} days)."
            ), []
        return True, None, []

    def _gate_timelock(
        self, protocol: DeFiProtocolSpec
    ) -> Tuple[bool, Optional[str], List[str]]:
        advisories: List[str] = []
        if protocol.has_emergency_pause_circuit_breaker:
            advisories.append(
                "UNTIMELOCKED_GUARDIAN: Protocol has an emergency pause/guardian "
                "role. That is protective against an in-progress exploit, but the "
                "role itself acts without the timelock delay. Confirm it is held by "
                "a multisig and scoped to pause-only -- a guardian that can also "
                "upgrade or set parameters makes the timelock decorative."
            )
        else:
            advisories.append(
                "NO_CIRCUIT_BREAKER: Protocol has no emergency pause. Nothing can "
                "stop an exploit mid-drain, and the timelock delays a defensive "
                "response as much as it delays a malicious upgrade."
            )

        if protocol.admin_timelock_delay_hours < self.min_timelock_hours:
            return False, (
                f"{VIOLATION_DANGEROUS_TIMELOCK}: Admin upgrade timelock delay is "
                f"{protocol.admin_timelock_delay_hours}h (Required >= "
                f"{self.min_timelock_hours}h)."
            ), advisories
        return True, None, advisories

    def _gate_multisig(
        self, protocol: DeFiProtocolSpec
    ) -> Tuple[bool, Optional[str], List[str]]:
        signers = protocol.admin_multisig_signers_count
        threshold = protocol.admin_multisig_threshold_required
        advisories: List[str] = []
        reasons: List[str] = []

        if threshold < self.min_multisig_threshold:
            reasons.append(
                f"threshold {threshold} < required {self.min_multisig_threshold}"
            )
        if signers < self.min_multisig_signers:
            reasons.append(
                f"only {signers} total signer key(s) < required "
                f"{self.min_multisig_signers}"
            )
        if threshold == signers:
            reasons.append(
                f"{threshold}-of-{signers} is an N-of-N scheme; losing any single "
                "key permanently locks admin control"
            )
        elif threshold / signers < self.min_multisig_threshold_ratio:
            reasons.append(
                f"{threshold}-of-{signers} is a {threshold / signers:.0%} signing "
                f"threshold < required {self.min_multisig_threshold_ratio:.0%}"
            )

        if (
            protocol.tvl_usd >= SEAL_LARGE_TREASURY_USD
            and signers < SEAL_LARGE_TREASURY_MIN_SIGNERS
        ):
            advisories.append(
                f"SMALL_SIGNER_SET: {signers} signers control "
                f"${protocol.tvl_usd:,.0f} TVL. SEAL multisig guidance suggests >= "
                f"{SEAL_LARGE_TREASURY_MIN_SIGNERS} signers above "
                f"${SEAL_LARGE_TREASURY_USD:,.0f}."
            )
        advisories.append(
            "SIGNER_INDEPENDENCE_UNVERIFIED: Signer count is not signer diversity. "
            "An M-of-N whose keys sit with one team, on one hardware model, in one "
            "location is effectively 1-of-1. This engine cannot see key custody."
        )

        if reasons:
            return False, (
                f"{VIOLATION_WEAK_MULTISIG}: Admin multisig is "
                f"{threshold}-of-{signers} -- {'; '.join(reasons)}."
            ), advisories
        return True, None, advisories

    def _gate_bug_bounty(
        self, protocol: DeFiProtocolSpec
    ) -> Tuple[bool, Optional[str], List[str]]:
        """Gate on the absolute payout floor; report TVL coverage as advisory.

        The blocking check is the absolute floor. The TVL ratio is reported but
        deliberately does **not** block: Immunefi's 10%-of-funds-at-risk
        proposal is an incentive-design argument, and 10% of a large pool is a
        number no protocol funds. Blocking on it would reject every protocol
        worth integrating with and teach reviewers to disable the gate.
        """
        advisories: List[str] = []

        if not protocol.has_active_bug_bounty:
            # Previously this branch reported the payout figure even when no
            # programme existed, e.g. "$500,000.00 < $100,000.00" on a reject.
            return False, (
                f"{VIOLATION_INADEQUATE_BUG_BOUNTY}: No active bug bounty program "
                f"(required max critical payout >= ${self.min_bug_bounty_usd:,.2f})."
            ), advisories

        if protocol.tvl_usd > 0:
            ratio = protocol.bug_bounty_max_payout_usd / protocol.tvl_usd
            if ratio < self.min_bug_bounty_tvl_ratio:
                advisories.append(
                    f"BOUNTY_SMALL_VS_TVL: Max critical payout "
                    f"${protocol.bug_bounty_max_payout_usd:,.2f} is {ratio:.4%} of "
                    f"${protocol.tvl_usd:,.2f} TVL, below the "
                    f"{self.min_bug_bounty_tvl_ratio:.0%} reference ratio. A "
                    "researcher who finds a critical bug is paid more by exploiting "
                    "it than by reporting it. Advisory, not blocking -- few "
                    "protocols of any size clear this ratio."
                )

        if protocol.bug_bounty_max_payout_usd < self.min_bug_bounty_usd:
            return False, (
                f"{VIOLATION_INADEQUATE_BUG_BOUNTY}: Max critical payout "
                f"${protocol.bug_bounty_max_payout_usd:,.2f} < required "
                f"${self.min_bug_bounty_usd:,.2f} absolute floor."
            ), advisories
        return True, None, advisories

    # -- entry point -------------------------------------------------------

    def evaluate_protocol(
        self,
        protocol: DeFiProtocolSpec,
        assessment_date: Optional[date] = None,
    ) -> DeFiIntegrationGateReport:
        """Evaluate a DeFi protocol against the six mandatory integration gates.

        Args:
            protocol: The documented protocol profile. Every field must be
                supported by an artefact the reviewer has actually read.
            assessment_date: Date the assessment is made against, used for
                audit staleness. Defaults to today; pass it explicitly for
                reproducible output.

        Returns:
            A ``DeFiIntegrationGateReport``. ``is_approved`` is true only when
            all six gates pass.

        Raises:
            DeFiDueDiligenceError: If the spec or configuration contains values
                that cannot describe a real protocol.
        """
        effective_date = assessment_date or date.today()
        self._validate(protocol, effective_date)

        violations: List[str] = []
        advisories: List[str] = []
        passed = 0

        gates = (
            self._gate_audits(protocol, effective_date),
            self._gate_unresolved_findings(protocol),
            self._gate_mainnet_longevity(protocol),
            self._gate_timelock(protocol),
            self._gate_multisig(protocol),
            self._gate_bug_bounty(protocol),
        )
        for ok, violation, gate_advisories in gates:
            if ok:
                passed += 1
            elif violation is not None:
                violations.append(violation)
            advisories.extend(gate_advisories)

        safety_score = round((passed / len(gates)) * 100.0, 2)
        is_approved = not violations
        coverage_ratio: Optional[float] = None
        if protocol.has_active_bug_bounty and protocol.tvl_usd > 0:
            coverage_ratio = protocol.bug_bounty_max_payout_usd / protocol.tvl_usd

        status_str = "APPROVED_FOR_INTEGRATION" if is_approved else "BLOCKED_HIGH_RISK"
        notes = (
            f"DEFI DUE DILIGENCE [{status_str}] ({protocol.protocol_name} @ "
            f"{protocol.contract_address}, assessed {effective_date.isoformat()}): "
            f"Safety Score = {safety_score}%, Violations = {violations}."
        )

        if is_approved:
            logger.info(notes)
        else:
            logger.warning(notes)

        return DeFiIntegrationGateReport(
            protocol_name=protocol.protocol_name,
            is_approved=is_approved,
            safety_score_pct=safety_score,
            blocking_violations=violations,
            audit_notes=notes,
            advisories=advisories,
            bug_bounty_tvl_coverage_ratio=coverage_ratio,
        )
