import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class DeveloperCredentials:
    personnel_id: str
    name: str
    role_title: str                     # e.g. 'QUANT_DEVELOPER', 'TRADING_SYSTEMS_ENGINEER'
    is_series_57_active: bool
    is_sie_active: bool
    crd_number: Optional[str] = None

@dataclass
class AlgoCodeCommitRequest:
    commit_id: str
    algorithm_id: str
    algorithm_name: str
    author_id: str
    approving_supervisor_id: str
    is_significant_modification: bool  # True if logic modifies order generation/routing
    modifies_order_routing_logic: bool

@dataclass
class FinraRegistrationAuditReport:
    commit_id: str
    algorithm_id: str
    author_id: str
    supervisor_id: str
    is_rule_1220b4_applicable: bool
    author_series_57_valid: bool
    supervisor_series_57_valid: bool
    cicd_gate_status: str               # 'COMPLIANCE_APPROVED', 'REGISTRATION_VIOLATION_BLOCKED'
    audit_notes: str

class FinraAlgoRegistrationEngine:
    """
    Broker-dealer compliance engine for auditing FINRA Rule 1220(b)(4) registration requirements,
    verifying Series 57 licensing for algo developers/supervisors, and enforcing deployment controls.
    """
    def __init__(self):
        self.personnel_registry: Dict[str, DeveloperCredentials] = {}

    def register_personnel(self, creds: DeveloperCredentials):
        self.personnel_registry[creds.personnel_id] = creds

    def audit_code_commit(self, req: AlgoCodeCommitRequest) -> FinraRegistrationAuditReport:
        """
        Audits code commit against FINRA Rule 1220(b)(4) Series 57 registration requirements.
        """
        # Rule 1220(b)(4) applies if significant modification to algorithmic trading/order routing logic
        is_applicable = req.is_significant_modification and req.modifies_order_routing_logic

        author = self.personnel_registry.get(req.author_id)
        supervisor = self.personnel_registry.get(req.approving_supervisor_id)

        author_valid = False
        if author and author.is_series_57_active and author.is_sie_active:
            author_valid = True

        supervisor_valid = False
        if supervisor and supervisor.is_series_57_active and supervisor.is_sie_active:
            supervisor_valid = True

        if not is_applicable:
            notes = f"FINRA RULE 1220(b)(4) N/A [{req.commit_id}]: Change is minor or non-routing. Build APPROVED."
            logger.info(notes)
            return FinraRegistrationAuditReport(
                commit_id=req.commit_id,
                algorithm_id=req.algorithm_id,
                author_id=req.author_id,
                supervisor_id=req.approving_supervisor_id,
                is_rule_1220b4_applicable=False,
                author_series_57_valid=author_valid,
                supervisor_series_57_valid=supervisor_valid,
                cicd_gate_status="COMPLIANCE_APPROVED",
                audit_notes=notes
            )

        # In-scope audit
        violations: List[str] = []
        if not author_valid:
            violations.append(f"Author '{req.author_id}' lacks active Series 57 / SIE registration")
        if not supervisor_valid:
            violations.append(f"Supervisor '{req.approving_supervisor_id}' lacks active Series 57 / SIE registration")

        if violations:
            gate_status = "REGISTRATION_VIOLATION_BLOCKED"
            notes = (
                f"FINRA RULE 1220(b)(4) VIOLATION [{req.commit_id} - {req.algorithm_name}]: "
                f"Significant algo modification blocked! Details: {'; '.join(violations)}. "
                f"Deployment REJECTED by compliance gate."
            )
            logger.critical(notes)
        else:
            gate_status = "COMPLIANCE_APPROVED"
            notes = (
                f"FINRA RULE 1220(b)(4) PASSED [{req.commit_id} - {req.algorithm_name}]: "
                f"Author '{req.author_id}' and Supervisor '{req.approving_supervisor_id}' hold active Series 57 registration. "
                f"Deployment APPROVED."
            )
            logger.info(notes)

        return FinraRegistrationAuditReport(
            commit_id=req.commit_id,
            algorithm_id=req.algorithm_id,
            author_id=req.author_id,
            supervisor_id=req.approving_supervisor_id,
            is_rule_1220b4_applicable=True,
            author_series_57_valid=author_valid,
            supervisor_series_57_valid=supervisor_valid,
            cicd_gate_status=gate_status,
            audit_notes=notes
        )
