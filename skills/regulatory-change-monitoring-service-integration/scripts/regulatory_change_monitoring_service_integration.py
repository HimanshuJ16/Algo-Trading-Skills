import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set

logger = logging.getLogger(__name__)

@dataclass
class ComplianceResult:
    """Legacy ComplianceResult for backward compatibility."""
    is_compliant: bool
    reason: str

@dataclass
class RegulatoryUpdate:
    update_id: str
    regulator: str                      # e.g. 'SEC', 'FCA', 'SEBI', 'ESMA', 'MAS'
    title: str
    effective_date: str                 # ISO format e.g. '2024-06-01'
    impacted_subdomains: List[str]      # e.g. ['SHORT_SELLING', 'TICK_SIZE', 'LEVERAGE']
    severity: str                       # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    action_required: bool = True
    summary: str = ""

@dataclass
class ChangeImpactAssessment:
    update_id: str
    regulator: str
    severity: str
    days_until_effective: int
    requires_immediate_action: bool
    status: str                         # 'ACTION_REQUIRED', 'MONITORING', 'COMPLIANT'

@dataclass
class RegulatoryChangeReport:
    total_updates: int
    critical_count: int
    action_required_count: int
    assessments: List[ChangeImpactAssessment]
    overall_status: str                 # 'ACTION_REQUIRED', 'MONITORING_ONLY', 'NO_UPDATES'
    audit_notes: str

class RegulatoryChangeMonitoringServiceIntegrationEngine:
    """
    Regulatory change monitoring service integration engine tracking regulatory updates,
    classifying impact severity, calculating implementation deadlines, and alerting compliance teams.
    """
    def __init__(self, monitored_regulators: Optional[List[str]] = None):
        self.monitored_regulators = monitored_regulators or ["SEC", "FCA", "SEBI", "ESMA", "MAS"]

    def check(self, data: dict) -> ComplianceResult:
        """Legacy check method retained for backward compatibility."""
        if data.get("valid"):
            return ComplianceResult(True, "Valid")
        return ComplianceResult(False, "Invalid")

    def process_updates(
        self,
        updates: List[RegulatoryUpdate],
        current_date_iso: str = "2024-01-01"
    ) -> RegulatoryChangeReport:
        """
        Processes incoming regulatory updates, assesses impact and deadline urgency.
        """
        assessments: List[ChangeImpactAssessment] = []
        critical = 0
        action_req = 0

        for up in updates:
            if up.regulator.upper() not in [r.upper() for r in self.monitored_regulators]:
                continue

            # Calculate days until effective date (simplified string comparison/day calc)
            # Assuming YYYY-MM-DD format
            try:
                from datetime import datetime
                curr_dt = datetime.strptime(current_date_iso, "%Y-%m-%d")
                eff_dt = datetime.strptime(up.effective_date, "%Y-%m-%d")
                days_until = (eff_dt - curr_dt).days
            except Exception:
                days_until = 30

            urgent = up.severity in ("CRITICAL", "HIGH") and days_until <= 30 and up.action_required

            if up.severity == "CRITICAL":
                critical += 1

            if up.action_required:
                action_req += 1
                status = "ACTION_REQUIRED"
            else:
                status = "MONITORING"

            assessments.append(ChangeImpactAssessment(
                update_id=up.update_id,
                regulator=up.regulator,
                severity=up.severity,
                days_until_effective=days_until,
                requires_immediate_action=urgent,
                status=status
            ))

        total = len(assessments)
        if action_req > 0:
            overall = "ACTION_REQUIRED"
        elif total > 0:
            overall = "MONITORING_ONLY"
        else:
            overall = "NO_UPDATES"

        notes = (
            f"REGULATORY CHANGE MONITOR [{overall}]: Total = {total}, "
            f"Critical = {critical}, Action Required = {action_req}."
        )

        if overall == "ACTION_REQUIRED":
            logger.warning(notes)
        else:
            logger.info(notes)

        return RegulatoryChangeReport(
            total_updates=total,
            critical_count=critical,
            action_required_count=action_req,
            assessments=assessments,
            overall_status=overall,
            audit_notes=notes
        )
