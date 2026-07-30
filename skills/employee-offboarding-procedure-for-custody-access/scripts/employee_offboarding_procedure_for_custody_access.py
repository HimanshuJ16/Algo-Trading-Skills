import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

OFFBOARDING_STEPS = [
    "IDP_SSO_REVOKED",
    "EXCHANGE_API_KEYS_REVOKED",
    "CUSTODY_PORTAL_REVOKED",
    "MULTISIG_MPC_KEY_ROTATED",
    "HARDWARE_TOKEN_WIPED"
]

@dataclass
class EmployeeOffboardingRecord:
    employee_id: str
    employee_name: str
    role: str                           # e.g. 'KEY_CUSTODIAN', 'QUANT_DEV', 'DEVOPS'
    termination_time_epoch: float
    held_custody_keys: bool             # True if employee held private key / MPC shard
    completed_steps: List[str] = field(default_factory=list)

@dataclass
class CustodyOffboardingAuditReport:
    employee_id: str
    employee_name: str
    role: str
    completion_percentage: float        # 0.0% to 100.0%
    is_fully_compliant: bool
    key_exposure_risk: str              # 'CRITICAL_KEY_EXPOSURE_RISK', 'LOW_RISK'
    pending_steps: List[str]
    audit_notes: str

class CustodyOffboardingEngine:
    """
    Quantitative crypto custody operational risk engine for executing mandatory 5-step employee offboarding procedures
    (SSO revocation, exchange API key destruction, MPC key shard rotation, hardware token wiping).
    """
    def __init__(self, key_rotation_sla_hours: float = 24.0):
        self.key_rotation_sla_hours = key_rotation_sla_hours

    def evaluate_offboarding_status(
        self,
        record: EmployeeOffboardingRecord,
        current_time_epoch: Optional[float] = None
    ) -> CustodyOffboardingAuditReport:
        """
        Audits offboarding checklist progress and key exposure risk.
        """
        now = current_time_epoch if current_time_epoch is not None else time.time()
        elapsed_hours = (now - record.termination_time_epoch) / 3600.0

        done_set = set(record.completed_steps)
        pending = [s for s in OFFBOARDING_STEPS if s not in done_set]

        completion_pct = round((len(done_set) / float(len(OFFBOARDING_STEPS))) * 100.0, 1)
        is_compliant = (len(pending) == 0)

        # Risk Audit: If employee held custody keys and MPC rotation is incomplete > 24 hours -> CRITICAL RISK!
        risk_level = "LOW_RISK"
        if record.held_custody_keys and "MULTISIG_MPC_KEY_ROTATED" in pending and elapsed_hours > self.key_rotation_sla_hours:
            risk_level = "CRITICAL_KEY_EXPOSURE_RISK"
            notes = f"CRITICAL KEY EXPOSURE RISK [{record.employee_id}]: Employee held private key / MPC shard, but MULTISIG_MPC_KEY_ROTATED is incomplete after {elapsed_hours:.1f} hours (> {self.key_rotation_sla_hours}h SLA)!"
            logger.critical(notes)
        elif not is_compliant:
            notes = f"OFFBOARDING IN PROGRESS [{record.employee_id}]: {completion_pct}% complete. Pending steps: {pending}."
            logger.warning(notes)
        else:
            notes = f"OFFBOARDING COMPLIANT [{record.employee_id}]: 100% completed across all 5 custody security steps."
            logger.info(notes)

        return CustodyOffboardingAuditReport(
            employee_id=record.employee_id,
            employee_name=record.employee_name,
            role=record.role,
            completion_percentage=completion_pct,
            is_fully_compliant=is_compliant,
            key_exposure_risk=risk_level,
            pending_steps=pending,
            audit_notes=notes
        )
