"""
audit-logging-for-configuration-changes:
Compliance engine for tracking modifications to trading configurations to satisfy SEC/FINRA rules.
"""
import dataclasses
import logging
import json
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ConfigChangeRequest:
    parameter_name: str
    old_value: Any
    new_value: Any
    user_id: str
    justification: str


@dataclasses.dataclass
class ConfigChangeRecord:
    timestamp_utc: str
    parameter_name: str
    old_value: Any
    new_value: Any
    user_id: str
    justification: str
    is_approved: bool

    def to_json(self) -> str:
        """Serializes to a JSON string for append-only SIEM ingestion."""
        return json.dumps(dataclasses.asdict(self))


class ConfigurationAuditLogger:
    """
    Enforces regulatory audit logging for trading system configuration changes.
    """

    def process_change_request(self, request: ConfigChangeRequest) -> ConfigChangeRecord:
        # SEC/FINRA compliance requires a justification for critical parameter changes.
        if not request.justification or len(request.justification.strip()) < 5:
            logger.error(f"Change rejected for {request.parameter_name}: Missing or insufficient justification.")
            return self._create_record(request, is_approved=False)

        # Optimization / Sanity check: ensure the value is actually changing
        if request.old_value == request.new_value:
            logger.warning(f"Change rejected for {request.parameter_name}: New value identical to old value.")
            return self._create_record(request, is_approved=False)

        # In a real system, further RBAC (Role Based Access Control) checks would happen here.
        # For this audit logger, if it has a justification and is a real change, it is approved and logged.
        record = self._create_record(request, is_approved=True)
        
        # Emit the immutable log line
        logger.info(f"AUDIT_LOG_ENTRY: {record.to_json()}")
        
        return record

    def _create_record(self, request: ConfigChangeRequest, is_approved: bool) -> ConfigChangeRecord:
        # Use high-precision UTC time as required by CAT / Reg SCI
        now_utc = datetime.now(timezone.utc).isoformat()
        return ConfigChangeRecord(
            timestamp_utc=now_utc,
            parameter_name=request.parameter_name,
            old_value=request.old_value,
            new_value=request.new_value,
            user_id=request.user_id,
            justification=request.justification,
            is_approved=is_approved
        )
