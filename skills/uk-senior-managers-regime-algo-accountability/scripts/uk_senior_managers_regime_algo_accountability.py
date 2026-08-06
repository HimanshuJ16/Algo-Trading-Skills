import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class SMFRole(Enum):
    SMF24_CHIEF_OPERATIONS = "SMF24_CHIEF_OPERATIONS" # Primary FCA function for Algo Trading Infrastructure
    SMF16_COMPLIANCE_OVERSIGHT = "SMF16_COMPLIANCE_OVERSIGHT" # Compliance oversight & regulatory reporting
    SMF4_CHIEF_RISK = "SMF4_CHIEF_RISK"             # Risk control frameworks & limit approvals
    SMF1_CHIEF_EXECUTIVE = "SMF1_CHIEF_EXECUTIVE"    # Executive responsibility for business units


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


@dataclass
class SeniorManager:
    smf_id: str
    name: str
    role: SMFRole
    fca_irn: str  # FCA Individual Reference Number e.g. "ABC12345"
    email: str


@dataclass
class CertifiedDeveloper:
    dev_id: str
    name: str
    role_title: str
    status: CertificationStatus
    last_assessment_date: datetime.date
    accredited_by_smf_id: str


@dataclass
class AlgoStrategyRegistration:
    algo_id: str
    name: str
    version: str
    responsible_smf_id: str
    certified_dev_ids: List[str]
    pre_trade_risk_approved: bool = False
    kill_switch_tested: bool = False
    stress_tested: bool = False


@dataclass
class DeploymentSignOff:
    sign_off_id: str
    algo_id: str
    smf_id: str
    status: SignOffStatus
    reasonable_steps_notes: str
    sign_off_timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


@dataclass
class SMCRComplianceReport:
    total_registered_algos: int
    compliant_algos_count: int
    unassigned_algos: List[str]
    uncertified_dev_algos: List[str]
    audit_trail: List[str] = field(default_factory=list)


class SMCRAlgoAccountabilityEngine:
    """
    Institutional UK Senior Managers & Certification Regime (SM&CR) Governance Engine.
    
    Enforces FCA SUP 10C & FG18/9 algorithmic trading accountability by mapping
    Senior Management Functions (SMF24, SMF16, SMF4) to algorithmic strategies, tracking
    Certification Function Fitness & Propriety (F&P), and recording Reasonable Steps sign-offs.
    """

    def __init__(self):
        self.senior_managers: Dict[str, SeniorManager] = {}
        self.certified_developers: Dict[str, CertifiedDeveloper] = {}
        self.algo_registrations: Dict[str, AlgoStrategyRegistration] = {}
        self.deployment_sign_offs: Dict[str, DeploymentSignOff] = {}
        logger.info("Initialized UK SM&CR Algorithmic Trading Accountability Engine")

    def register_senior_manager(self, smf: SeniorManager) -> None:
        """Registers an FCA-approved Senior Management Function (SMF) holder."""
        self.senior_managers[smf.smf_id] = smf
        logger.info(f"Registered SMF Holder: {smf.name} [{smf.role.value}] IRN={smf.fca_irn}")

    def certify_developer(self, dev: CertifiedDeveloper) -> None:
        """Registers a Certified Function developer following Fitness & Propriety (F&P) assessment."""
        if dev.accredited_by_smf_id not in self.senior_managers:
            raise SMCRError(f"Accrediting Senior Manager {dev.accredited_by_smf_id} not registered.")
        self.certified_developers[dev.dev_id] = dev
        logger.info(f"Certified Algo Developer: {dev.name} [{dev.status.value}] Accredited by {dev.accredited_by_smf_id}")

    def register_algo_strategy(self, algo: AlgoStrategyRegistration) -> None:
        """Registers an algorithmic trading strategy under a responsible SMF manager."""
        if algo.responsible_smf_id not in self.senior_managers:
            raise SMCRError(f"Responsible Senior Manager {algo.responsible_smf_id} not registered.")

        for dev_id in algo.certified_dev_ids:
            if dev_id not in self.certified_developers:
                raise SMCRError(f"Developer {dev_id} not found in Certification Register.")

        self.algo_registrations[algo.algo_id] = algo
        logger.info(f"Registered Algo Strategy: {algo.name} v{algo.version} under SMF {algo.responsible_smf_id}")

    def verify_algo_deployment_readiness(self, algo_id: str) -> Tuple[bool, List[str]]:
        """
        Verifies all mandatory SM&CR and FG18/9 governance prerequisites prior to live production deployment.
        """
        if algo_id not in self.algo_registrations:
            return False, [f"Algo {algo_id} is not registered in SM&CR Management Responsibilities Map."]

        algo = self.algo_registrations[algo_id]
        issues = []

        # 1. Verify SMF Assignment
        if algo.responsible_smf_id not in self.senior_managers:
            issues.append(f"SMF Holder {algo.responsible_smf_id} is unassigned or invalid.")

        # 2. Verify Certified Developers (Fitness & Propriety)
        for dev_id in algo.certified_dev_ids:
            dev = self.certified_developers.get(dev_id)
            if not dev or dev.status != CertificationStatus.FIT_AND_PROPER:
                issues.append(f"Developer {dev_id} lacks active Fit & Proper Certification.")

        # 3. Verify Pre-Trade Risk & Stress Testing (FG18/9)
        if not algo.pre_trade_risk_approved:
            issues.append("Pre-trade risk controls have not been approved by SMF4 Chief Risk Officer.")
        if not algo.kill_switch_tested:
            issues.append("Emergency Kill Switch testing has not been completed.")
        if not algo.stress_tested:
            issues.append("RTS 6 Article 14 stress testing has not been completed.")

        # 4. Verify Formal SMF Deployment Sign-Off
        sign_off = self.deployment_sign_offs.get(algo_id)
        if not sign_off or sign_off.status != SignOffStatus.APPROVED:
            issues.append("Formal SMF Deployment Sign-Off is missing or rejected.")

        is_ready = len(issues) == 0
        return is_ready, issues

    def execute_deployment_sign_off(self, sign_off: DeploymentSignOff) -> DeploymentSignOff:
        """
        Executes formal production deployment sign-off by designated SMF holder, recording Reasonable Steps.
        """
        if sign_off.smf_id not in self.senior_managers:
            raise SMCRError(f"Sign-off SMF Holder {sign_off.smf_id} not registered.")
        if sign_off.algo_id not in self.algo_registrations:
            raise SMCRError(f"Algo Strategy {sign_off.algo_id} not registered.")

        if not sign_off.reasonable_steps_notes or len(sign_off.reasonable_steps_notes) < 10:
            raise SMCRError("Sign-off requires detailed 'Reasonable Steps' audit documentation (min 10 chars).")

        self.deployment_sign_offs[sign_off.algo_id] = sign_off
        smf = self.senior_managers[sign_off.smf_id]

        logger.info(
            f"SM&CR Deployment Sign-Off APPROVED [{sign_off.algo_id}]: Signed by {smf.name} ({smf.role.value})"
        )
        return sign_off

    def generate_mrm_report(self) -> SMCRComplianceReport:
        """Generates Management Responsibilities Map (MRM) compliance report for FCA submission."""
        total = len(self.algo_registrations)
        compliant_count = 0
        unassigned = []
        uncertified = []
        audit = [f"SM&CR Management Responsibilities Audit - {datetime.datetime.utcnow().isoformat()}"]

        for algo_id, algo in self.algo_registrations.items():
            is_ready, issues = self.verify_algo_deployment_readiness(algo_id)
            if is_ready:
                compliant_count += 1
                audit.append(f"[COMPLIANT] {algo.name} v{algo.version} - Responsible: {self.senior_managers[algo.responsible_smf_id].name}")
            else:
                audit.append(f"[NON-COMPLIANT] {algo.name} v{algo.version} - Issues: {'; '.join(issues)}")
                if algo.responsible_smf_id not in self.senior_managers:
                    unassigned.append(algo_id)

        return SMCRComplianceReport(
            total_registered_algos=total,
            compliant_algos_count=compliant_count,
            unassigned_algos=unassigned,
            uncertified_dev_algos=uncertified,
            audit_trail=audit,
        )

