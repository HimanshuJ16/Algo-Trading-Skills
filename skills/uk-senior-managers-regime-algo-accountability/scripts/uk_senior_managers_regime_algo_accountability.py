"""UK FCA Senior Managers & Certification Regime (SM&CR) governance engine for
algorithmic trading accountability.

Primary sources relied on by this module (verify currency before relying on them
for a live compliance decision):

* FCA Handbook SUP 10C - FCA senior managers regime for approved persons in
  SMCR firms (specification of SMFs; SMF24 at SUP 10C.6B.2R, SMF4 at
  SUP 10C.6A.4R).
* FCA Handbook SYSC 25 - Management responsibilities maps. Applies only to
  SMCR banking firms, SMCR insurance firms that are Solvency II firms, and
  enhanced scope SMCR firms (SYSC 25.1.1R). Core and limited scope firms are
  NOT required to produce one.
* FCA Handbook SYSC 27 - Certification regime. SYSC 27.8.23R defines the
  "algorithmic trading" FCA certification function.
* FSMA s.63F / SYSC 27.2 - a certificate is valid for a maximum of 12 months
  from the day it is issued.
* FCA Handbook SYSC 22 - regulatory references (six-year look-back).
* FCA Handbook MAR 7A - algorithmic trading systems and controls requirements.
* UK-assimilated RTS 6 (Commission Delegated Regulation (EU) 2017/589):
  Article 9 annual self-assessment and validation, Article 10 stress testing,
  Article 12 kill functionality, Article 15 pre-trade controls on order entry.
* FCA PS17/9 and DEPP 6.2.9-A to 6.2.9-E - Duty of Responsibility and the
  factors the FCA weighs when assessing whether a Senior Manager took
  reasonable steps.

This module records and checks governance evidence. It does not itself
establish that a Senior Manager discharged the statutory Duty of
Responsibility, and it is not a substitute for legal or compliance advice.
"""

import datetime
import logging
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# FSMA s.63F / SYSC 27.2: a certificate may not be drafted to last more than
# 12 months. A firm may issue one for a shorter period.
MAX_CERTIFICATE_VALIDITY_MONTHS = 12

# House completeness guard for sign-off notes. This is a firm-configurable
# policy value, NOT a regulatory threshold - the FCA prescribes no minimum
# length for reasonable-steps documentation.
DEFAULT_MIN_REASONABLE_STEPS_CHARS = 10

# Content-free sign-off tokens rejected by the completeness guard. Not a
# regulatory list; extend it to match the firm's own drafting standards.
_BOILERPLATE_SIGN_OFF_NOTES = frozenset(
    {"ok", "okay", "approved", "approved.", "signed off", "sign off", "lgtm", "n/a", "none", "fine"}
)


class SMCRFirmTier(Enum):
    """SM&CR firm classification, which determines available SMFs and whether
    a management responsibilities map is required (SYSC 25.1.1R)."""

    LIMITED_SCOPE = "LIMITED_SCOPE"
    CORE = "CORE"
    ENHANCED = "ENHANCED"
    BANKING = "BANKING"
    SOLVENCY_II_INSURANCE = "SOLVENCY_II_INSURANCE"


class SMFRole(Enum):
    SMF24_CHIEF_OPERATIONS = "SMF24_CHIEF_OPERATIONS"  # SUP 10C.6B.2R - enhanced/banking/insurance firms only
    SMF16_COMPLIANCE_OVERSIGHT = "SMF16_COMPLIANCE_OVERSIGHT"  # Available to core firms
    SMF4_CHIEF_RISK = "SMF4_CHIEF_RISK"  # SUP 10C.6A.4R - enhanced/banking/insurance firms only
    SMF1_CHIEF_EXECUTIVE = "SMF1_CHIEF_EXECUTIVE"  # Available to core firms
    SMF18_OTHER_OVERALL_RESPONSIBILITY = "SMF18_OTHER_OVERALL_RESPONSIBILITY"  # Enhanced firms only


# SMFs that only exist for enhanced scope SMCR firms and dual-regulated
# (banking / Solvency II insurance) firms. A core or limited scope firm cannot
# appoint these, so mapping an algorithm to one is a governance error.
_ENHANCED_ONLY_SMF_ROLES = frozenset(
    {
        SMFRole.SMF24_CHIEF_OPERATIONS,
        SMFRole.SMF4_CHIEF_RISK,
        SMFRole.SMF18_OTHER_OVERALL_RESPONSIBILITY,
    }
)

_TIERS_WITH_ENHANCED_SMFS = frozenset(
    {SMCRFirmTier.ENHANCED, SMCRFirmTier.BANKING, SMCRFirmTier.SOLVENCY_II_INSURANCE}
)

# SYSC 25.1.1R: management responsibilities map required for these tiers only.
_TIERS_REQUIRING_MRM = _TIERS_WITH_ENHANCED_SMFS


class CertificationStatus(Enum):
    FIT_AND_PROPER = "FIT_AND_PROPER"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    PENDING_REVIEW = "PENDING_REVIEW"


class SignOffStatus(Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PENDING_SIGN_OFF = "PENDING_SIGN_OFF"


class SMCRError(Exception):
    """Base exception for UK SM&CR governance errors."""

    pass


def _utc_now() -> datetime.datetime:
    """Timezone-aware UTC timestamp.

    ``datetime.utcnow()`` returns a naive datetime and is deprecated from
    Python 3.12; regulatory audit records must carry an explicit offset.
    """
    return datetime.datetime.now(datetime.timezone.utc)


def _add_months(start: datetime.date, months: int) -> datetime.date:
    """Return ``start`` advanced by ``months`` calendar months.

    Clamps to the last valid day of the target month, so a 29 February
    assessment expires on 28 February in a non-leap year rather than raising.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    if month == 12:
        next_month_start = datetime.date(year + 1, 1, 1)
    else:
        next_month_start = datetime.date(year, month + 1, 1)
    last_day_of_month = (next_month_start - datetime.timedelta(days=1)).day
    return datetime.date(year, month, min(start.day, last_day_of_month))


@dataclass
class SeniorManager:
    smf_id: str
    name: str
    role: SMFRole
    fca_irn: str  # FCA Individual Reference Number as shown on the FCA Register.
    email: str


@dataclass
class CertifiedDeveloper:
    """An employee performing the FCA algorithmic trading certification
    function (SYSC 27.8.23R): approving deployment of a trading algorithm or an
    amendment to one, or having significant responsibility for monitoring or
    deciding whether an algorithm remains compliant.

    Writing or testing algorithm code does not by itself bring an employee into
    the certification regime; the approval, monitoring, or compliance-decision
    responsibility does.
    """

    dev_id: str
    name: str
    role_title: str
    status: CertificationStatus
    last_assessment_date: datetime.date
    accredited_by_smf_id: str
    # Firms may issue a certificate for less than 12 months (SYSC 27.2). Leave
    # as None to derive the statutory maximum from last_assessment_date.
    certificate_validity_months: int = MAX_CERTIFICATE_VALIDITY_MONTHS

    def __post_init__(self) -> None:
        if not 1 <= self.certificate_validity_months <= MAX_CERTIFICATE_VALIDITY_MONTHS:
            raise SMCRError(
                f"Certificate validity for {self.dev_id} must be between 1 and "
                f"{MAX_CERTIFICATE_VALIDITY_MONTHS} months (FSMA s.63F / SYSC 27.2); "
                f"got {self.certificate_validity_months}."
            )

    @property
    def certificate_expiry_date(self) -> datetime.date:
        """First date on which the certificate is no longer valid."""
        return _add_months(self.last_assessment_date, self.certificate_validity_months)

    def is_certificate_current(self, as_of: datetime.date) -> bool:
        """Whether the F&P certificate is still within its validity window."""
        return as_of < self.certificate_expiry_date


@dataclass
class AlgoStrategyRegistration:
    algo_id: str
    name: str
    version: str
    responsible_smf_id: str
    certified_dev_ids: List[str]
    pre_trade_risk_approved: bool = False  # RTS 6 Article 15
    kill_switch_tested: bool = False  # RTS 6 Article 12
    stress_tested: bool = False  # RTS 6 Article 10


@dataclass
class DeploymentSignOff:
    sign_off_id: str
    algo_id: str
    smf_id: str
    status: SignOffStatus
    reasonable_steps_notes: str
    # Binds the sign-off to the exact algorithm version reviewed. Left as None,
    # execute_deployment_sign_off() resolves it to the currently registered
    # version. A later amendment therefore invalidates this sign-off, matching
    # SYSC 27.8.23R(a)(ii) which treats amendments as separately approvable.
    algo_version: Optional[str] = None
    sign_off_timestamp: datetime.datetime = field(default_factory=_utc_now)


@dataclass
class SMCRComplianceReport:
    total_registered_algos: int
    compliant_algos_count: int
    unassigned_algos: List[str]
    uncertified_dev_algos: List[str]
    firm_tier: SMCRFirmTier = SMCRFirmTier.ENHANCED
    mrm_required: bool = True
    generated_at: datetime.datetime = field(default_factory=_utc_now)
    audit_trail: List[str] = field(default_factory=list)


class SMCRAlgoAccountabilityEngine:
    """UK Senior Managers & Certification Regime (SM&CR) governance engine for
    algorithmic trading.

    Maps algorithmic trading strategies to Senior Management Functions, tracks
    the validity of Certification Function Fitness & Propriety certificates,
    and records version-bound reasonable-steps sign-offs so that deployment
    evidence can be reconstructed for FCA inspection.

    The engine records evidence; it does not determine legal compliance.
    """

    def __init__(
        self,
        firm_tier: SMCRFirmTier = SMCRFirmTier.ENHANCED,
        min_reasonable_steps_chars: int = DEFAULT_MIN_REASONABLE_STEPS_CHARS,
    ) -> None:
        self.firm_tier = firm_tier
        self.min_reasonable_steps_chars = min_reasonable_steps_chars
        self.senior_managers: Dict[str, SeniorManager] = {}
        self.certified_developers: Dict[str, CertifiedDeveloper] = {}
        self.algo_registrations: Dict[str, AlgoStrategyRegistration] = {}
        # Keyed by (algo_id, algo_version) so a sign-off never silently carries
        # over to an amended algorithm.
        self.deployment_sign_offs: Dict[Tuple[str, str], DeploymentSignOff] = {}
        logger.info(
            f"Initialized UK SM&CR Algorithmic Trading Accountability Engine "
            f"[firm_tier={firm_tier.value}]"
        )

    @property
    def mrm_required(self) -> bool:
        """Whether SYSC 25.1.1R requires this firm to maintain a management
        responsibilities map."""
        return self.firm_tier in _TIERS_REQUIRING_MRM

    def register_senior_manager(self, smf: SeniorManager) -> None:
        """Registers an FCA-approved Senior Management Function (SMF) holder.

        The FCA does not publish a fixed Individual Reference Number format, so
        the IRN is checked for presence only and must be verified against the
        FCA Register out of band.
        """
        if not smf.smf_id or not smf.smf_id.strip():
            raise SMCRError("Senior Manager requires a non-empty smf_id.")
        if not smf.fca_irn or not smf.fca_irn.strip():
            raise SMCRError(
                f"Senior Manager {smf.smf_id} requires an FCA Individual Reference Number "
                f"verified against the FCA Register."
            )
        if smf.role in _ENHANCED_ONLY_SMF_ROLES and self.firm_tier not in _TIERS_WITH_ENHANCED_SMFS:
            raise SMCRError(
                f"{smf.role.value} is only available to enhanced scope SMCR firms and "
                f"dual-regulated firms (SUP 10C); this firm is classified "
                f"{self.firm_tier.value}. Allocate the responsibility to an SMF the firm "
                f"can appoint (e.g. SMF1 or SMF16)."
            )

        if smf.smf_id in self.senior_managers:
            logger.warning(
                f"Overwriting existing SMF registration {smf.smf_id} "
                f"(was {self.senior_managers[smf.smf_id].name}, now {smf.name}). "
                f"Confirm a handover certificate exists (SYSC 25.9)."
            )
        self.senior_managers[smf.smf_id] = smf
        logger.info(f"Registered SMF Holder: {smf.name} [{smf.role.value}] IRN={smf.fca_irn}")

    def certify_developer(self, dev: CertifiedDeveloper) -> None:
        """Records an employee performing the FCA algorithmic trading
        certification function (SYSC 27.8.23R) following F&P assessment."""
        if dev.accredited_by_smf_id not in self.senior_managers:
            raise SMCRError(f"Accrediting Senior Manager {dev.accredited_by_smf_id} not registered.")

        if dev.dev_id in self.certified_developers:
            logger.warning(f"Overwriting existing certification record for {dev.dev_id}.")
        self.certified_developers[dev.dev_id] = dev
        logger.info(
            f"Certified Algo Developer: {dev.name} [{dev.status.value}] "
            f"Accredited by {dev.accredited_by_smf_id}, expires {dev.certificate_expiry_date.isoformat()}"
        )

    def register_algo_strategy(self, algo: AlgoStrategyRegistration) -> None:
        """Registers an algorithmic trading strategy under a responsible SMF holder."""
        if algo.responsible_smf_id not in self.senior_managers:
            raise SMCRError(f"Responsible Senior Manager {algo.responsible_smf_id} not registered.")

        for dev_id in algo.certified_dev_ids:
            if dev_id not in self.certified_developers:
                raise SMCRError(f"Developer {dev_id} not found in Certification Register.")

        existing = self.algo_registrations.get(algo.algo_id)
        if existing is not None and existing.version != algo.version:
            logger.warning(
                f"Algo {algo.algo_id} amended from v{existing.version} to v{algo.version}. "
                f"The v{existing.version} sign-off does not carry over; a fresh SMF sign-off "
                f"is required before deployment."
            )

        self.algo_registrations[algo.algo_id] = algo
        logger.info(
            f"Registered Algo Strategy: {algo.name} v{algo.version} under SMF {algo.responsible_smf_id}"
        )

    def get_sign_off(self, algo_id: str, version: str) -> Optional[DeploymentSignOff]:
        """Returns the sign-off recorded for a specific algorithm version, if any."""
        return self.deployment_sign_offs.get((algo_id, version))

    def verify_algo_deployment_readiness(
        self, algo_id: str, as_of: Optional[datetime.date] = None
    ) -> Tuple[bool, List[str]]:
        """Verifies SM&CR and RTS 6 governance prerequisites before live deployment.

        Args:
            algo_id: Registered algorithm identifier.
            as_of: Date used to test certificate validity. Defaults to today in
                UTC. Pass an explicit date to keep checks deterministic in
                backtests, replays, and tests.

        Returns:
            ``(is_ready, issues)``. ``is_ready`` is True only when ``issues`` is
            empty. Readiness means the recorded governance evidence is complete,
            not that the firm has discharged its regulatory obligations.
        """
        if algo_id not in self.algo_registrations:
            return False, [f"Algo {algo_id} is not registered in the SM&CR governance register."]

        effective_date = as_of or _utc_now().date()
        algo = self.algo_registrations[algo_id]
        issues: List[str] = []

        # 1. Verify SMF assignment.
        smf = self.senior_managers.get(algo.responsible_smf_id)
        if smf is None:
            issues.append(f"SMF Holder {algo.responsible_smf_id} is unassigned or invalid.")
        elif smf.role in _ENHANCED_ONLY_SMF_ROLES and self.firm_tier not in _TIERS_WITH_ENHANCED_SMFS:
            issues.append(
                f"SMF Holder {algo.responsible_smf_id} holds {smf.role.value}, which a "
                f"{self.firm_tier.value} firm cannot appoint under SUP 10C."
            )

        # 2. Verify certification function F&P status and certificate currency
        #    (FSMA s.63F / SYSC 27.2: certificates last at most 12 months).
        for dev_id in algo.certified_dev_ids:
            dev = self.certified_developers.get(dev_id)
            if dev is None:
                issues.append(f"Developer {dev_id} is absent from the Certification Register.")
                continue
            if dev.status != CertificationStatus.FIT_AND_PROPER:
                issues.append(
                    f"Developer {dev_id} lacks active Fit & Proper Certification "
                    f"(status={dev.status.value})."
                )
                continue
            if not dev.is_certificate_current(effective_date):
                issues.append(
                    f"Developer {dev_id} F&P certificate expired on "
                    f"{dev.certificate_expiry_date.isoformat()}; re-assessment required "
                    f"before further algorithm approvals (SYSC 27.2)."
                )

        # 3. Verify pre-trade risk, kill functionality, and stress testing evidence.
        if not algo.pre_trade_risk_approved:
            issues.append("Pre-trade controls on order entry have not been approved (RTS 6 Article 15).")
        if not algo.kill_switch_tested:
            issues.append("Kill functionality has not been tested (RTS 6 Article 12).")
        if not algo.stress_tested:
            issues.append("Stress testing has not been completed (RTS 6 Article 10).")

        # 4. Verify a formal SMF sign-off exists for this exact algorithm version.
        sign_off = self.get_sign_off(algo_id, algo.version)
        if sign_off is None:
            issues.append(
                f"No SMF deployment sign-off recorded for {algo_id} v{algo.version}."
            )
        elif sign_off.status != SignOffStatus.APPROVED:
            issues.append(
                f"SMF deployment sign-off for {algo_id} v{algo.version} is "
                f"{sign_off.status.value}, not APPROVED."
            )

        return len(issues) == 0, issues

    def execute_deployment_sign_off(self, sign_off: DeploymentSignOff) -> DeploymentSignOff:
        """Records a Senior Manager's deployment decision and reasonable-steps notes.

        The notes check is a completeness guard against content-free records. It
        cannot establish that the steps taken were reasonable; that assessment
        follows DEPP 6.2.9-A to 6.2.9-E and FCA PS17/9.

        The sign-off is bound to a specific algorithm version. Signing off a
        version other than the one currently registered is rejected, so an
        amended algorithm cannot inherit approval from its predecessor.
        """
        if sign_off.smf_id not in self.senior_managers:
            raise SMCRError(f"Sign-off SMF Holder {sign_off.smf_id} not registered.")
        algo = self.algo_registrations.get(sign_off.algo_id)
        if algo is None:
            raise SMCRError(f"Algo Strategy {sign_off.algo_id} not registered.")

        version = sign_off.algo_version or algo.version
        if version != algo.version:
            raise SMCRError(
                f"Sign-off targets {sign_off.algo_id} v{version} but the registered version "
                f"is v{algo.version}. Register the version being deployed before signing off."
            )

        notes = (sign_off.reasonable_steps_notes or "").strip()
        if len(notes) < self.min_reasonable_steps_chars:
            raise SMCRError(
                f"Sign-off requires reasonable-steps documentation of at least "
                f"{self.min_reasonable_steps_chars} characters; got {len(notes)}."
            )
        if notes.lower() in _BOILERPLATE_SIGN_OFF_NOTES:
            raise SMCRError(
                f"Sign-off notes '{notes}' are content-free. Record the specific checks "
                f"performed (pre-trade control review, stress test results, kill "
                f"functionality drill outcome)."
            )

        # Store a copy so the recorded decision cannot be altered afterwards
        # through the caller's reference to the submitted object.
        recorded = replace(sign_off, algo_version=version)
        self.deployment_sign_offs[(sign_off.algo_id, version)] = recorded
        smf = self.senior_managers[sign_off.smf_id]

        log = logger.info if recorded.status == SignOffStatus.APPROVED else logger.warning
        log(
            f"SM&CR Deployment Sign-Off {recorded.status.value} "
            f"[{recorded.algo_id} v{version}]: Signed by {smf.name} ({smf.role.value})"
        )
        return recorded

    def generate_mrm_report(self, as_of: Optional[datetime.date] = None) -> SMCRComplianceReport:
        """Generates a governance report over the registered algorithm estate.

        For firms in scope of SYSC 25.1.1R this supports the management
        responsibilities map. Core and limited scope firms are not required to
        maintain an MRM; the report is still useful as internal governance
        evidence and flags that distinction via ``mrm_required``.
        """
        effective_date = as_of or _utc_now().date()
        compliant_count = 0
        unassigned: List[str] = []
        uncertified: List[str] = []
        audit = [
            f"SM&CR governance audit - generated {_utc_now().isoformat()} "
            f"(as_of={effective_date.isoformat()}, firm_tier={self.firm_tier.value}, "
            f"SYSC 25 MRM required={self.mrm_required})"
        ]

        for algo_id, algo in self.algo_registrations.items():
            is_ready, issues = self.verify_algo_deployment_readiness(algo_id, as_of=effective_date)
            if is_ready:
                compliant_count += 1
                responsible = self.senior_managers[algo.responsible_smf_id].name
                audit.append(
                    f"[COMPLIANT] {algo.name} v{algo.version} - Responsible: {responsible}"
                )
                continue

            audit.append(
                f"[NON-COMPLIANT] {algo.name} v{algo.version} - Issues: {'; '.join(issues)}"
            )
            if algo.responsible_smf_id not in self.senior_managers:
                unassigned.append(algo_id)
            if self._has_certification_defect(algo, effective_date):
                uncertified.append(algo_id)

        return SMCRComplianceReport(
            total_registered_algos=len(self.algo_registrations),
            compliant_algos_count=compliant_count,
            unassigned_algos=unassigned,
            uncertified_dev_algos=uncertified,
            firm_tier=self.firm_tier,
            mrm_required=self.mrm_required,
            audit_trail=audit,
        )

    def _has_certification_defect(
        self, algo: AlgoStrategyRegistration, as_of: datetime.date
    ) -> bool:
        """True if any developer mapped to the algorithm is missing, not fit and
        proper, or holds an expired certificate as at ``as_of``."""
        for dev_id in algo.certified_dev_ids:
            dev = self.certified_developers.get(dev_id)
            if dev is None:
                return True
            if dev.status != CertificationStatus.FIT_AND_PROPER:
                return True
            if not dev.is_certificate_current(as_of):
                return True
        return False
