"""
model-monitoring-dashboard-for-non-technical-stakeholders: Traffic light model health aggregator
and plain-language risk action recommendation generator.
"""
from dataclasses import dataclass
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"


@dataclass
class ModelHealthComponent:
    name: str
    value: float
    unit: str
    status: HealthStatus
    summary: str


@dataclass
class DashboardReport:
    model_name: str
    overall_status: HealthStatus
    components: List[ModelHealthComponent]
    recommended_action: str
    summary_message: str


class NonTechnicalMonitoringDashboard:
    """
    Translates complex ML model telemetry (PSI drift, loss, staleness, latency)
    into simple Traffic Light statuses and clear risk management action plans.
    """

    def __init__(self):
        pass

    def evaluate_health(
        self,
        model_name: str,
        accuracy_pct: float,
        staleness_days: int,
        feature_drift_psi: float,
        latency_ms: float,
    ) -> DashboardReport:
        components: List[ModelHealthComponent] = []

        # 1. Accuracy Component
        if accuracy_pct >= 55.0:
            st_acc = HealthStatus.GREEN
            sum_acc = f"Accuracy ({accuracy_pct:.1f}%) is strong."
        elif accuracy_pct >= 50.0:
            st_acc = HealthStatus.AMBER
            sum_acc = f"Accuracy ({accuracy_pct:.1f}%) is decaying."
        else:
            st_acc = HealthStatus.RED
            sum_acc = f"Accuracy ({accuracy_pct:.1f}%) is below break-even threshold."
        components.append(ModelHealthComponent("Prediction Accuracy", accuracy_pct, "%", st_acc, sum_acc))

        # 2. Staleness Component
        if staleness_days <= 14:
            st_stale = HealthStatus.GREEN
            sum_stale = f"Model is fresh ({staleness_days} days old)."
        elif staleness_days <= 30:
            st_stale = HealthStatus.AMBER
            sum_stale = f"Model is aging ({staleness_days} days old)."
        else:
            st_stale = HealthStatus.RED
            sum_stale = f"Model is stale ({staleness_days} days old)."
        components.append(ModelHealthComponent("Model Age", float(staleness_days), "days", st_stale, sum_stale))

        # 3. Feature Drift Component
        if feature_drift_psi <= 0.10:
            st_psi = HealthStatus.GREEN
            sum_psi = f"Feature drift (PSI={feature_drift_psi:.2f}) is low."
        elif feature_drift_psi <= 0.25:
            st_psi = HealthStatus.AMBER
            sum_psi = f"Moderate feature drift (PSI={feature_drift_psi:.2f}) detected."
        else:
            st_psi = HealthStatus.RED
            sum_psi = f"Severe feature drift (PSI={feature_drift_psi:.2f}) detected."
        components.append(ModelHealthComponent("Feature Drift PSI", feature_drift_psi, "PSI", st_psi, sum_psi))

        # Overall status = worst status
        order = {HealthStatus.GREEN: 0, HealthStatus.AMBER: 1, HealthStatus.RED: 2}
        overall = max(components, key=lambda c: order[c.status]).status

        if overall == HealthStatus.RED:
            action = "HALT_TRADING_IMMEDIATELY"
            msg = f"CRITICAL DASHBOARD ALERT: Model '{model_name}' STATUS IS RED. Immediate intervention required."
            logger.error(msg)
        elif overall == HealthStatus.AMBER:
            action = "SCHEDULE_RETRAIN_AND_REVIEW"
            msg = f"DASHBOARD WARNING: Model '{model_name}' STATUS IS AMBER. Schedule retrain and review risk."
            logger.warning(msg)
        else:
            action = "NO_ACTION_REQUIRED"
            msg = f"DASHBOARD OK: Model '{model_name}' STATUS IS GREEN."
            logger.info(msg)

        return DashboardReport(
            model_name=model_name,
            overall_status=overall,
            components=components,
            recommended_action=action,
            summary_message=msg,
        )
