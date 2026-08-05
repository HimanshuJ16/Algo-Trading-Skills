import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

class EscalationMatrixError(Exception):
    """Base exception for risk escalation matrix errors."""

class InvalidBreachError(EscalationMatrixError):
    """Raised when breach inputs are invalid."""

class ResponseAction(str, Enum):
    NONE = "NONE"
    WARN = "WARN"
    THROTTLE = "THROTTLE"
    REDUCE = "REDUCE"
    HALT = "HALT"
    FLATTEN = "FLATTEN"
    GLOBAL_KILL_SWITCH = "GLOBAL_KILL_SWITCH"

class SeverityLevel(str, Enum):
    INFO = "INFO"
    AMBER = "AMBER"
    RED = "RED"
    CRITICAL = "CRITICAL"

class NotificationChannel(str, Enum):
    DASHBOARD = "DASHBOARD"
    SLACK = "SLACK"
    EMAIL = "EMAIL"
    PAGERDUTY = "PAGERDUTY"
    COMPLIANCE_TICKET = "COMPLIANCE_TICKET"

@dataclass
class EscalationResult:
    """Legacy result container for backward compatibility."""
    action: ResponseAction
    level: float

@dataclass
class BreachEvent:
    event_id: str
    metric_name: str                     # e.g. 'DAILY_DRAWDOWN', 'MAX_LEVERAGE', 'POSITION_CAP'
    strategy_id: str
    current_value: float
    limit_value: float
    timestamp_iso: str
    duration_seconds: float = 0.0

@dataclass
class EscalationPolicy:
    ratio_threshold: float               # e.g., 1.0 (100%), 1.2 (120%), 1.5 (150%), 2.0 (200%)
    severity: SeverityLevel
    action: ResponseAction
    channels: List[NotificationChannel]
    ack_timeout_seconds: int             # SLA for risk manager acknowledgment

DEFAULT_POLICIES = [
    EscalationPolicy(1.0, SeverityLevel.INFO, ResponseAction.WARN, [NotificationChannel.DASHBOARD, NotificationChannel.SLACK], 900),
    EscalationPolicy(1.2, SeverityLevel.AMBER, ResponseAction.REDUCE, [NotificationChannel.SLACK, NotificationChannel.EMAIL], 300),
    EscalationPolicy(1.5, SeverityLevel.RED, ResponseAction.HALT, [NotificationChannel.SLACK, NotificationChannel.PAGERDUTY], 120),
    EscalationPolicy(2.0, SeverityLevel.CRITICAL, ResponseAction.FLATTEN, [NotificationChannel.PAGERDUTY, NotificationChannel.COMPLIANCE_TICKET], 60),
]

@dataclass
class EscalationDecision:
    event_id: str
    metric_name: str
    strategy_id: str
    ratio: float
    severity: SeverityLevel
    action: ResponseAction
    notification_channels: List[NotificationChannel]
    ack_deadline_seconds: int
    is_sustained_breach: bool
    audit_notes: str

class RiskEscalationMatrix:
    """
    Production-grade risk limit breach escalation matrix evaluating metric breaches against multi-tier
    severity thresholds, duration auto-escalations, notification channel routing, and legacy interfaces.
    """
    def __init__(
        self,
        warn_lvl: float = 1.0,
        reduce_lvl: float = 1.2,
        halt_lvl: float = 1.5,
        flatten_lvl: float = 2.0,
        policies: Optional[List[EscalationPolicy]] = None
    ):
        # Legacy level mapping
        self.levels = {
            flatten_lvl: ResponseAction.FLATTEN,
            halt_lvl: ResponseAction.HALT,
            reduce_lvl: ResponseAction.REDUCE,
            warn_lvl: ResponseAction.WARN
        }
        self.policies = sorted(policies or DEFAULT_POLICIES, key=lambda p: p.ratio_threshold, reverse=True)
        self._audit_log: List[EscalationDecision] = []

    def evaluate(self, risk_metric: float, limit: float) -> EscalationResult:
        """Legacy evaluate method retained for 100% backward compatibility."""
        if limit <= 0:
            return EscalationResult(ResponseAction.NONE, 0.0)
        ratio = risk_metric / limit

        for lvl in sorted(self.levels.keys(), reverse=True):
            if ratio >= lvl:
                return EscalationResult(self.levels[lvl], lvl)
        return EscalationResult(ResponseAction.NONE, 0.0)

    def process_breach_event(self, event: BreachEvent) -> EscalationDecision:
        """
        Evaluates a structured breach event, applies duration auto-escalation, routes notification
        channels, and records audit trail.
        """
        if event.limit_value <= 0:
            raise InvalidBreachError(f"Limit value must be > 0, got {event.limit_value}")

        ratio = round(event.current_value / event.limit_value, 4)

        # Match policy threshold
        matched_policy: Optional[EscalationPolicy] = None
        for pol in self.policies:
            if ratio >= pol.ratio_threshold:
                matched_policy = pol
                break

        if not matched_policy:
            decision = EscalationDecision(
                event_id=event.event_id,
                metric_name=event.metric_name,
                strategy_id=event.strategy_id,
                ratio=ratio,
                severity=SeverityLevel.INFO,
                action=ResponseAction.NONE,
                notification_channels=[],
                ack_deadline_seconds=0,
                is_sustained_breach=False,
                audit_notes=f"No breach. Ratio {ratio:.2f} < threshold 1.0."
            )
            return decision

        severity = matched_policy.severity
        action = matched_policy.action
        channels = list(matched_policy.channels)
        ack_timeout = matched_policy.ack_timeout_seconds

        # Duration auto-escalation: sustained breach > 300s escalates action tier
        is_sustained = event.duration_seconds >= 300.0
        if is_sustained and action == ResponseAction.WARN:
            action = ResponseAction.REDUCE
            severity = SeverityLevel.AMBER
        elif is_sustained and action == ResponseAction.REDUCE:
            action = ResponseAction.HALT
            severity = SeverityLevel.RED

        notes = (
            f"ESCALATION DECISION [{severity.value}]: {event.metric_name} for strategy '{event.strategy_id}' "
            f"ratio={ratio:.2f} ({event.current_value}/{event.limit_value}) -> Action: {action.value}, "
            f"Channels: {[c.value for c in channels]}."
        )

        decision = EscalationDecision(
            event_id=event.event_id,
            metric_name=event.metric_name,
            strategy_id=event.strategy_id,
            ratio=ratio,
            severity=severity,
            action=action,
            notification_channels=channels,
            ack_deadline_seconds=ack_timeout,
            is_sustained_breach=is_sustained,
            audit_notes=notes
        )

        self._audit_log.append(decision)
        if severity in (SeverityLevel.RED, SeverityLevel.CRITICAL):
            logger.warning(notes)
        else:
            logger.info(notes)

        return decision

    def get_audit_trail(self) -> List[EscalationDecision]:
        """Returns the immutable audit log of escalation decisions."""
        return list(self._audit_log)
