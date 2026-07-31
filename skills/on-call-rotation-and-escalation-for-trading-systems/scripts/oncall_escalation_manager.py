import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class OnCallEngineer:
    engineer_id: str
    name: str
    tier: str                            # 'PRIMARY', 'SECONDARY', 'EXECUTIVE'
    phone: str
    email: str

@dataclass
class SystemIncident:
    incident_id: str
    severity: str                        # 'SEV_1', 'SEV_2', 'SEV_3'
    title: str
    description: str
    created_at_utc: float
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at_utc: Optional[float] = None
    resolved: bool = False

@dataclass
class EscalationPolicyConfig:
    sev1_sec_escalate_mins: float = 3.0   # Primary -> Secondary after 3 mins
    sev1_exec_escalate_mins: float = 5.0  # Secondary -> Executive after 5 mins
    sev2_sec_escalate_mins: float = 10.0
    sev3_sec_escalate_mins: float = 30.0

@dataclass
class OnCallEscalationReport:
    incident_id: str
    severity: str
    elapsed_minutes: float
    current_assigned_tier: str
    current_responder_name: str
    notification_channel: str            # 'PHONE_CALL', 'SMS', 'SLACK'
    is_sla_breached: bool
    status: str                          # 'ACKNOWLEDGED', 'ESCALATED_WARNING', 'SLA_BREACH'
    audit_notes: str

class OnCallEscalationManagerEngine:
    """
    Site Reliability Engineering (SRE) on-call rotation manager evaluating incident severity SLAs (SEV-1, SEV-2, SEV-3),
    multi-tier escalation timelines (Primary, Secondary, Executive), and notification routing.
    """
    def __init__(
        self,
        roster: List[OnCallEngineer],
        config: Optional[EscalationPolicyConfig] = None
    ):
        self.roster: Dict[str, OnCallEngineer] = {e.tier.upper(): e for e in roster}
        self.config = config or EscalationPolicyConfig()
        self.active_incidents: Dict[str, SystemIncident] = {}

    def create_incident(self, incident: SystemIncident):
        self.active_incidents[incident.incident_id] = incident
        logger.info(f"Created incident [{incident.incident_id}] Severity: {incident.severity} - '{incident.title}'")

    def acknowledge_incident(self, incident_id: str, engineer_id: str, ack_time_utc: float) -> bool:
        if incident_id in self.active_incidents:
            inc = self.active_incidents[incident_id]
            inc.acknowledged = True
            inc.acknowledged_by = engineer_id
            inc.acknowledged_at_utc = ack_time_utc
            logger.info(f"Incident [{incident_id}] ACKNOWLEDGED by {engineer_id}.")
            return True
        return False

    def evaluate_escalation(
        self, incident_id: str, current_time_utc: float
    ) -> OnCallEscalationReport:
        """
        Evaluates incident status based on elapsed time and severity SLA policy.
        """
        if incident_id not in self.active_incidents:
            raise ValueError(f"Unknown incident_id '{incident_id}'.")

        inc = self.active_incidents[incident_id]
        elapsed_mins = max(0.0, (current_time_utc - inc.created_at_utc) / 60.0)

        # If already acknowledged
        if inc.acknowledged:
            ack_eng_name = inc.acknowledged_by or "Unknown"
            notes = f"Incident [{incident_id}] is ACKNOWLEDGED by {ack_eng_name}."
            return OnCallEscalationReport(
                incident_id=incident_id,
                severity=inc.severity,
                elapsed_minutes=round(elapsed_mins, 2),
                current_assigned_tier="ACKNOWLEDGED",
                current_responder_name=ack_eng_name,
                notification_channel="NONE",
                is_sla_breached=False,
                status="ACKNOWLEDGED",
                audit_notes=notes
            )

        # Evaluate unacknowledged escalation logic
        sev = inc.severity.upper()

        if sev == "SEV_1":
            if elapsed_mins >= self.config.sev1_exec_escalate_mins:
                assigned_tier = "EXECUTIVE"
                channel = "PHONE_CALL"
                is_breached = True
            elif elapsed_mins >= self.config.sev1_sec_escalate_mins:
                assigned_tier = "SECONDARY"
                channel = "PHONE_CALL"
                is_breached = False
            else:
                assigned_tier = "PRIMARY"
                channel = "PHONE_CALL"
                is_breached = False

        elif sev == "SEV_2":
            if elapsed_mins >= self.config.sev2_sec_escalate_mins:
                assigned_tier = "SECONDARY"
                channel = "SMS"
                is_breached = True
            else:
                assigned_tier = "PRIMARY"
                channel = "SMS"
                is_breached = False

        else: # SEV_3
            if elapsed_mins >= self.config.sev3_sec_escalate_mins:
                assigned_tier = "SECONDARY"
                channel = "SLACK"
                is_breached = True
            else:
                assigned_tier = "PRIMARY"
                channel = "SLACK"
                is_breached = False

        responder = self.roster.get(assigned_tier, OnCallEngineer("ENG_UNASSIGNED", "Duty Engineer", assigned_tier, "", ""))
        status = "SLA_BREACH" if is_breached else ("ESCALATED_WARNING" if assigned_tier != "PRIMARY" else "ACTIVE_PRIMARY")

        notes = (
            f"INCIDENT ESCALATION [{incident_id} - {status}]: Severity = {sev}, Elapsed = {elapsed_mins:.1f} mins. "
            f"Assigned Tier = {assigned_tier} ({responder.name}), Channel = {channel}."
        )
        if is_breached:
            logger.error(f"SLA BREACH ALERT: {notes}")
        elif assigned_tier != "PRIMARY":
            logger.warning(f"ESCALATION ALERT: {notes}")
        else:
            logger.info(notes)

        return OnCallEscalationReport(
            incident_id=incident_id,
            severity=inc.severity,
            elapsed_minutes=round(elapsed_mins, 2),
            current_assigned_tier=assigned_tier,
            current_responder_name=responder.name,
            notification_channel=channel,
            is_sla_breached=is_breached,
            status=status,
            audit_notes=notes
        )
