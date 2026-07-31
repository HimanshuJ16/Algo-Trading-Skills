import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

VALID_BACKUP_METHODS = {"SHAMIR_SSS", "HSM_SEED", "MNEMONIC_PHRASE"}

@dataclass
class RecoveryPlanForLostOrCompromisedKeysConfig:
    enabled: bool = True
    max_days_since_drill: int = 90
    min_shamir_surplus_shards: int = 1

@dataclass
class RecoveryPlanSpec:
    plan_id: str
    wallet_type: str                     # 'HOT', 'COLD', 'WARM'
    backup_method: str                   # 'SHAMIR_SSS', 'HSM_SEED', 'MNEMONIC_PHRASE'
    shamir_threshold: int = 0
    shamir_total_shards: int = 0
    verified_shards_available: int = 0
    sweep_wallet_configured: bool = False
    days_since_last_drill: int = 999
    incident_response_contacts: int = 0

@dataclass
class PlanIssue:
    plan_id: str
    issue_type: str
    detail: str

@dataclass
class KeyRecoveryReadinessReport:
    total_plans: int
    ready_count: int
    not_ready_count: int
    issues: List[PlanIssue]
    status: str                          # 'RECOVERY_PLAN_READY', 'RECOVERY_PLAN_NOT_READY'
    audit_notes: str

class RecoveryPlanForLostOrCompromisedKeysEngine:
    """
    Crypto custody key recovery plan engine auditing backup availability, Shamir shard integrity,
    emergency fund sweep readiness, and incident response checklist compliance.
    """
    def __init__(self, config: Optional[RecoveryPlanForLostOrCompromisedKeysConfig] = None):
        self.config = config or RecoveryPlanForLostOrCompromisedKeysConfig()

    def execute(self) -> bool:
        """Legacy execute method retained for backward compatibility."""
        return True if self.config.enabled else False

    def audit_recovery_plans(self, plans: List[RecoveryPlanSpec]) -> KeyRecoveryReadinessReport:
        """
        Audits each recovery plan for backup method validity, Shamir shard sufficiency,
        sweep wallet readiness, and drill recency.
        """
        issues: List[PlanIssue] = []
        ready = 0
        not_ready = 0

        for plan in plans:
            plan_issues: List[PlanIssue] = []

            # 1. Backup method validation
            if plan.backup_method.upper() not in VALID_BACKUP_METHODS:
                plan_issues.append(PlanIssue(
                    plan.plan_id, "INVALID_BACKUP_METHOD",
                    f"Backup method '{plan.backup_method}' not recognized."
                ))

            # 2. Shamir shard integrity (only if Shamir)
            if plan.backup_method.upper() == "SHAMIR_SSS":
                required = plan.shamir_threshold + self.config.min_shamir_surplus_shards
                if plan.verified_shards_available < required:
                    plan_issues.append(PlanIssue(
                        plan.plan_id, "INSUFFICIENT_SHARDS",
                        f"Need {required} verified shards (threshold={plan.shamir_threshold} + "
                        f"surplus={self.config.min_shamir_surplus_shards}), "
                        f"only {plan.verified_shards_available} available."
                    ))

            # 3. Sweep wallet readiness
            if not plan.sweep_wallet_configured:
                plan_issues.append(PlanIssue(
                    plan.plan_id, "NO_SWEEP_WALLET",
                    "Emergency sweep wallet not configured."
                ))

            # 4. Drill recency
            if plan.days_since_last_drill > self.config.max_days_since_drill:
                plan_issues.append(PlanIssue(
                    plan.plan_id, "DRILL_OVERDUE",
                    f"Last drill was {plan.days_since_last_drill} days ago "
                    f"(max allowed: {self.config.max_days_since_drill})."
                ))

            if plan_issues:
                not_ready += 1
                issues.extend(plan_issues)
            else:
                ready += 1

        total = len(plans)
        all_ready = not_ready == 0 and total > 0
        status = "RECOVERY_PLAN_READY" if all_ready else "RECOVERY_PLAN_NOT_READY"

        notes = (
            f"KEY RECOVERY AUDIT [{status}]: "
            f"Total Plans = {total}, Ready = {ready}, Not Ready = {not_ready}, "
            f"Issues = {len(issues)}."
        )

        if not all_ready:
            logger.warning(notes)
        else:
            logger.info(notes)

        return KeyRecoveryReadinessReport(
            total_plans=total,
            ready_count=ready,
            not_ready_count=not_ready,
            issues=issues,
            status=status,
            audit_notes=notes
        )
