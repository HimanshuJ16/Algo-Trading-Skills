import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class RiskBypassEvent:
    event_id: str
    timestamp_iso: str
    bypassed_control: str                # e.g. 'MAX_POSITION_SIZE', 'DAILY_LOSS_LIMIT', 'SPREAD_VETO'
    original_limit_value: str
    override_value: str
    authorized_by: str                   # user/service principal who authorized
    justification: str
    strategy_id: str = ""
    instrument: str = ""

@dataclass
class BypassAuditEntry:
    event_id: str
    timestamp_iso: str
    bypassed_control: str
    authorized_by: str
    severity: str                        # 'CRITICAL', 'HIGH', 'MEDIUM'
    is_suspicious: bool
    flag_reason: Optional[str]

@dataclass
class RiskBypassAuditReport:
    total_bypass_events: int
    critical_count: int
    suspicious_count: int
    entries: List[BypassAuditEntry]
    status: str                          # 'NO_BYPASSES', 'BYPASSES_LOGGED', 'SUSPICIOUS_BYPASSES_DETECTED'
    audit_notes: str

# Controls classified as critical (any bypass is high-severity)
CRITICAL_CONTROLS = {
    "MAX_POSITION_SIZE", "DAILY_LOSS_LIMIT", "PORTFOLIO_VAR_LIMIT",
    "KILL_SWITCH", "MARGIN_CALL_HALT"
}

# Known authorized principals (example allowlist)
DEFAULT_AUTHORIZED_PRINCIPALS = {"risk_officer", "cro", "head_of_trading", "system_admin"}

class RiskControlBypassAuditEngine:
    """
    Risk control bypass audit logging engine recording every instance where a pre-trade
    or intra-trade risk control was manually overridden, classifying bypass severity,
    flagging suspicious patterns, and generating immutable audit trail reports.
    """
    def __init__(
        self,
        authorized_principals: Optional[set] = None,
        critical_controls: Optional[set] = None
    ):
        self.authorized_principals = authorized_principals or DEFAULT_AUTHORIZED_PRINCIPALS
        self.critical_controls = critical_controls or CRITICAL_CONTROLS
        self._log: List[RiskBypassEvent] = []

    def log_bypass(self, event: RiskBypassEvent) -> BypassAuditEntry:
        """
        Logs a risk control bypass event, classifies severity, and flags suspicious patterns.
        """
        self._log.append(event)

        # Classify severity
        control_upper = event.bypassed_control.upper()
        if control_upper in self.critical_controls:
            severity = "CRITICAL"
        elif "LIMIT" in control_upper or "CAP" in control_upper:
            severity = "HIGH"
        else:
            severity = "MEDIUM"

        # Flag suspicious: unauthorized principal or missing justification
        is_suspicious = False
        flag_reason = None

        principal_lower = event.authorized_by.lower()
        if principal_lower not in self.authorized_principals:
            is_suspicious = True
            flag_reason = f"Unauthorized principal '{event.authorized_by}' bypassed control."

        if not event.justification or len(event.justification.strip()) < 5:
            is_suspicious = True
            flag_reason = (flag_reason or "") + " Missing or insufficient justification."

        entry = BypassAuditEntry(
            event_id=event.event_id,
            timestamp_iso=event.timestamp_iso,
            bypassed_control=event.bypassed_control,
            authorized_by=event.authorized_by,
            severity=severity,
            is_suspicious=is_suspicious,
            flag_reason=flag_reason.strip() if flag_reason else None
        )

        if is_suspicious:
            logger.warning(
                f"SUSPICIOUS BYPASS [{severity}]: {event.bypassed_control} by '{event.authorized_by}' "
                f"({flag_reason})"
            )
        else:
            logger.info(
                f"BYPASS LOGGED [{severity}]: {event.bypassed_control} by '{event.authorized_by}'."
            )

        return entry

    def generate_audit_report(self) -> RiskBypassAuditReport:
        """
        Generates a comprehensive audit report of all logged bypass events.
        """
        entries: List[BypassAuditEntry] = []
        critical = 0
        suspicious = 0

        for event in self._log:
            entry = BypassAuditEntry(
                event_id=event.event_id,
                timestamp_iso=event.timestamp_iso,
                bypassed_control=event.bypassed_control,
                authorized_by=event.authorized_by,
                severity="CRITICAL" if event.bypassed_control.upper() in self.critical_controls else "HIGH",
                is_suspicious=event.authorized_by.lower() not in self.authorized_principals or len(event.justification.strip()) < 5,
                flag_reason=None
            )
            entries.append(entry)
            if entry.severity == "CRITICAL":
                critical += 1
            if entry.is_suspicious:
                suspicious += 1

        total = len(entries)
        if total == 0:
            status = "NO_BYPASSES"
        elif suspicious > 0:
            status = "SUSPICIOUS_BYPASSES_DETECTED"
        else:
            status = "BYPASSES_LOGGED"

        notes = (
            f"RISK BYPASS AUDIT [{status}]: Total = {total}, "
            f"Critical = {critical}, Suspicious = {suspicious}."
        )

        if suspicious > 0:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RiskBypassAuditReport(
            total_bypass_events=total,
            critical_count=critical,
            suspicious_count=suspicious,
            entries=entries,
            status=status,
            audit_notes=notes
        )
