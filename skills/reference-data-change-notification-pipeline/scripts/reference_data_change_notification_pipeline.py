import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set

logger = logging.getLogger(__name__)

DEFAULT_CRITICAL_FIELDS: Set[str] = {"symbol", "exchange", "status", "currency"}

@dataclass
class ReferenceDataChangeNotificationPipelineConfig:
    enabled: bool = True
    critical_fields: Set[str] = field(default_factory=lambda: set(DEFAULT_CRITICAL_FIELDS))

@dataclass
class ChangeNotification:
    instrument_id: str
    field_name: str
    old_value: Optional[str]
    new_value: Optional[str]
    change_type: str                     # 'MODIFIED', 'ADDED', 'REMOVED'
    severity: str                        # 'CRITICAL', 'INFO'

@dataclass
class ReferenceDataChangeReport:
    instrument_id: str
    total_changes: int
    critical_changes: int
    info_changes: int
    notifications: List[ChangeNotification]
    status: str                          # 'CHANGES_DETECTED', 'NO_CHANGES'
    audit_notes: str

class ReferenceDataChangeNotificationPipelineEngine:
    """
    Reference data change detection and notification pipeline comparing instrument snapshots
    to detect field-level mutations and routing alerts to downstream consumers.
    """
    def __init__(self, config: Optional[ReferenceDataChangeNotificationPipelineConfig] = None):
        self.config = config or ReferenceDataChangeNotificationPipelineConfig()

    def process(self, data: str) -> str:
        """Legacy process method retained for backward compatibility."""
        if not self.config.enabled:
            return ""
        return data.upper()

    def detect_changes(
        self,
        instrument_id: str,
        before: Dict[str, str],
        after: Dict[str, str]
    ) -> ReferenceDataChangeReport:
        """
        Compares before/after instrument snapshots, detects field-level mutations,
        classifies severity, and generates change notifications.
        """
        if not self.config.enabled:
            return ReferenceDataChangeReport(
                instrument_id=instrument_id,
                total_changes=0, critical_changes=0, info_changes=0,
                notifications=[], status="ENGINE_DISABLED",
                audit_notes="Change detection engine is disabled."
            )

        notifications: List[ChangeNotification] = []
        all_fields = set(before.keys()) | set(after.keys())

        for f in sorted(all_fields):
            old_val = before.get(f)
            new_val = after.get(f)

            if old_val == new_val:
                continue

            if old_val is None:
                change_type = "ADDED"
            elif new_val is None:
                change_type = "REMOVED"
            else:
                change_type = "MODIFIED"

            severity = "CRITICAL" if f in self.config.critical_fields else "INFO"

            notifications.append(ChangeNotification(
                instrument_id=instrument_id,
                field_name=f,
                old_value=old_val,
                new_value=new_val,
                change_type=change_type,
                severity=severity
            ))

        critical = sum(1 for n in notifications if n.severity == "CRITICAL")
        info = sum(1 for n in notifications if n.severity == "INFO")
        total = len(notifications)
        has_changes = total > 0
        status = "CHANGES_DETECTED" if has_changes else "NO_CHANGES"

        notes = (
            f"REF DATA CHANGE [{status}] {instrument_id}: "
            f"Total = {total}, Critical = {critical}, Info = {info}."
        )

        if critical > 0:
            logger.warning(notes)
        elif has_changes:
            logger.info(notes)

        return ReferenceDataChangeReport(
            instrument_id=instrument_id,
            total_changes=total,
            critical_changes=critical,
            info_changes=info,
            notifications=notifications,
            status=status,
            audit_notes=notes
        )
