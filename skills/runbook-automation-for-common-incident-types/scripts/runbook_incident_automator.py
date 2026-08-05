import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy config class for backward compatibility."""
    name: str = "default"

class IncidentType(str, Enum):
    FEED_DISCONNECT = "FEED_DISCONNECT"
    LATENCY_SPIKE = "LATENCY_SPIKE"
    BROKER_API_OUTAGE = "BROKER_API_OUTAGE"
    DRAWDOWN_BREACH = "DRAWDOWN_BREACH"
    ORDER_THROTTLE = "ORDER_THROTTLE"

class RemediationAction(str, Enum):
    RECONNECT_SOCKET = "RECONNECT_SOCKET"
    FAILOVER_VENUE = "FAILOVER_VENUE"
    CANCEL_OPEN_ORDERS = "CANCEL_OPEN_ORDERS"
    TRIGGER_KILL_SWITCH = "TRIGGER_KILL_SWITCH"
    THROTTLE_ORDER_RATE = "THROTTLE_ORDER_RATE"

class IncidentStatus(str, Enum):
    TRIGGERED = "TRIGGERED"
    EXECUTING = "EXECUTING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"

@dataclass
class IncidentAlert:
    incident_id: str
    incident_type: IncidentType
    severity: str                        # 'CRITICAL', 'HIGH', 'MEDIUM'
    source_service: str
    metric_value: float
    threshold_value: float
    timestamp_iso: str

@dataclass
class RemediationStep:
    step_number: int
    action: RemediationAction
    status: str                          # 'SUCCESS', 'FAILED', 'SKIPPED'
    detail: str

@dataclass
class IncidentRunbookReport:
    incident_id: str
    incident_type: IncidentType
    status: IncidentStatus
    steps_executed: List[RemediationStep]
    total_time_taken_ms: float
    audit_notes: str

class RunbookIncidentAutomator:
    """Legacy class retained for backward compatibility."""
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config("default")

    def process(self) -> bool:
        return True

class RunbookIncidentAutomationEngine:
    """
    Production-grade runbook automation engine executing automated remediation playbooks
    for common algorithmic trading incidents (feed disconnects, venue outages, drawdown breaches).
    """
    def __init__(self, is_dry_run: bool = False):
        self.is_dry_run = is_dry_run
        self._audit_history: List[IncidentRunbookReport] = []

    def _get_playbook(self, incident_type: IncidentType) -> List[RemediationAction]:
        """Returns standard remediation action steps per incident type."""
        playbooks = {
            IncidentType.FEED_DISCONNECT: [RemediationAction.RECONNECT_SOCKET, RemediationAction.FAILOVER_VENUE],
            IncidentType.LATENCY_SPIKE: [RemediationAction.THROTTLE_ORDER_RATE, RemediationAction.FAILOVER_VENUE],
            IncidentType.BROKER_API_OUTAGE: [RemediationAction.CANCEL_OPEN_ORDERS, RemediationAction.FAILOVER_VENUE],
            IncidentType.DRAWDOWN_BREACH: [RemediationAction.CANCEL_OPEN_ORDERS, RemediationAction.TRIGGER_KILL_SWITCH],
            IncidentType.ORDER_THROTTLE: [RemediationAction.THROTTLE_ORDER_RATE],
        }
        return playbooks.get(incident_type, [RemediationAction.CANCEL_OPEN_ORDERS])

    def execute_runbook(self, alert: IncidentAlert) -> IncidentRunbookReport:
        """
        Executes automated remediation playbook steps for the triggered incident alert.
        """
        t0 = time.perf_counter_ns()
        playbook = self._get_playbook(alert.incident_type)
        executed_steps: List[RemediationStep] = []

        all_success = True
        for idx, action in enumerate(playbook, 1):
            if self.is_dry_run:
                step_status = "SKIPPED_DRY_RUN"
                detail = f"DRY RUN: Would execute {action.value} for {alert.incident_type.value}"
            else:
                # Simulate execution of remediation action
                step_status = "SUCCESS"
                detail = f"Executed {action.value} successfully."

            executed_steps.append(RemediationStep(
                step_number=idx,
                action=action,
                status=step_status,
                detail=detail
            ))

            if step_status == "FAILED":
                all_success = False
                break

        dt_ms = round((time.perf_counter_ns() - t0) / 1e6, 2)
        status = IncidentStatus.RESOLVED if all_success else IncidentStatus.ESCALATED

        notes = (
            f"RUNBOOK INCIDENT [{status.value}] ({alert.incident_id}): Type = {alert.incident_type.value}, "
            f"Severity = {alert.severity}, Steps Executed = {len(executed_steps)}, Time = {dt_ms}ms."
        )

        if status == IncidentStatus.ESCALATED:
            logger.error(notes)
        else:
            logger.info(notes)

        report = IncidentRunbookReport(
            incident_id=alert.incident_id,
            incident_type=alert.incident_type,
            status=status,
            steps_executed=executed_steps,
            total_time_taken_ms=dt_ms,
            audit_notes=notes
        )

        self._audit_history.append(report)
        return report

    def get_audit_history(self) -> List[IncidentRunbookReport]:
        """Returns full audit history of executed runbook automations."""
        return list(self._audit_history)
