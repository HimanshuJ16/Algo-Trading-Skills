import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class FreshnessSlaConfig:
    model_id: str
    dataset_name: str
    target_sla_hours: float = 24.0      # Target data freshness (24 hours)
    warning_sla_hours: float = 36.0     # Warning threshold (36 hours)
    breach_sla_hours: float = 48.0      # Hard SLA breach limit (48 hours)
    action_on_breach: str = "HALT_MODEL_RETRAINING" # 'HALT_MODEL_RETRAINING', 'REDUCE_CONFIDENCE', 'ALERT_ONLY'

@dataclass
class DatasetMetadataPayload:
    dataset_name: str
    latest_record_timestamp_epoch: float
    current_system_timestamp_epoch: float
    total_record_count: int
    missing_days_count: int = 0

@dataclass
class FreshnessSlaReport:
    model_id: str
    dataset_name: str
    data_lag_hours: float
    target_sla_hours: float
    breach_sla_hours: float
    is_sla_compliant: bool
    recommended_governance_action: str  # 'PROCEED_NORMAL', 'TRIGGER_BACKFILL_ALERT', 'HALT_MODEL_RETRAINING'
    status: str                         # 'SLA_COMPLIANT', 'SLA_WARNING_NEAR_LIMIT', 'SLA_BREACH_CRITICAL'
    audit_notes: str

class TrainingFreshnessSlaEngine:
    """
    Training data freshness SLA monitoring engine, tracking data pipeline ingestion lag,
    detecting ETL delays, and enforcing automated retraining halt governance.
    """
    def __init__(self):
        pass

    def evaluate_training_freshness_sla(
        self,
        config: FreshnessSlaConfig,
        metadata: DatasetMetadataPayload
    ) -> FreshnessSlaReport:
        """
        Calculates data lag in hours, evaluates SLA compliance, and returns recommended governance actions.
        """
        if metadata.current_system_timestamp_epoch < metadata.latest_record_timestamp_epoch:
            raise ValueError("Current system timestamp cannot be prior to latest record timestamp.")

        lag_seconds = metadata.current_system_timestamp_epoch - metadata.latest_record_timestamp_epoch
        lag_hours = round(lag_seconds / 3600.0, 2)

        # Audit SLA Thresholds
        if lag_hours > config.breach_sla_hours or metadata.missing_days_count > 2:
            is_compliant = False
            status = "SLA_BREACH_CRITICAL"
            action = config.action_on_breach
            notes = (
                f"FRESHNESS SLA CRITICAL BREACH [{config.dataset_name}]: Data lag ({lag_hours:.2f}h) "
                f"exceeds hard limit ({config.breach_sla_hours:.2f}h) or missing days ({metadata.missing_days_count}). "
                f"Enforcing Action: '{action}'."
            )
            logger.critical(notes)
        elif lag_hours > config.target_sla_hours:
            is_compliant = True
            status = "SLA_WARNING_NEAR_LIMIT"
            action = "TRIGGER_BACKFILL_ALERT"
            notes = (
                f"FRESHNESS SLA WARNING [{config.dataset_name}]: Data lag ({lag_hours:.2f}h) "
                f"exceeds target SLA ({config.target_sla_hours:.2f}h). Warning limit ({config.warning_sla_hours:.2f}h)."
            )
            logger.warning(notes)
        else:
            is_compliant = True
            status = "SLA_COMPLIANT"
            action = "PROCEED_NORMAL"
            notes = (
                f"FRESHNESS SLA OK [{config.dataset_name}]: Data lag ({lag_hours:.2f}h) "
                f"is within target SLA ({config.target_sla_hours:.2f}h)."
            )
            logger.info(notes)

        return FreshnessSlaReport(
            model_id=config.model_id,
            dataset_name=config.dataset_name,
            data_lag_hours=lag_hours,
            target_sla_hours=config.target_sla_hours,
            breach_sla_hours=config.breach_sla_hours,
            is_sla_compliant=is_compliant,
            recommended_governance_action=action,
            status=status,
            audit_notes=notes
        )
