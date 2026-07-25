"""
automated-rollback-triggers-on-anomaly-detection:
Monitors post-deployment telemetry and triggers an automated rollback
if technical or trading anomalies exceed safe thresholds.
"""
import dataclasses
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class RollbackThresholdConfig:
    max_latency_ms: float
    max_http_5xx_error_rate: float
    max_order_reject_rate: float


@dataclasses.dataclass
class DeploymentHealthMetrics:
    latency_ms: float
    http_5xx_error_rate: float
    order_reject_rate: float


@dataclasses.dataclass
class RollbackEvaluationResult:
    should_rollback: bool
    anomalies_detected: List[str]


class AutomatedRollbackEngine:
    """
    Evaluates real-time health metrics post-deployment to determine
    if an automated rollback to the previous version is required.
    """

    def __init__(self, config: RollbackThresholdConfig):
        self.config = config

    def evaluate_metrics(self, metrics: DeploymentHealthMetrics) -> RollbackEvaluationResult:
        anomalies = []

        # Technical Metric: Latency
        if metrics.latency_ms > self.config.max_latency_ms:
            anomalies.append(
                f"Latency Spike: {metrics.latency_ms:.1f}ms exceeds threshold of {self.config.max_latency_ms:.1f}ms."
            )

        # Technical Metric: Error Rate
        if metrics.http_5xx_error_rate > self.config.max_http_5xx_error_rate:
            anomalies.append(
                f"API Error Spike: 5xx rate {metrics.http_5xx_error_rate:.2%} exceeds threshold of {self.config.max_http_5xx_error_rate:.2%}."
            )

        # Trading Metric: Order Reject Rate (Crucial for Algo Trading)
        if metrics.order_reject_rate > self.config.max_order_reject_rate:
            anomalies.append(
                f"Trading Anomaly: Order reject rate {metrics.order_reject_rate:.2%} exceeds threshold of {self.config.max_order_reject_rate:.2%}."
            )

        should_rollback = len(anomalies) > 0

        if should_rollback:
            logger.critical("ANOMALY DETECTED IN POST-DEPLOYMENT METRICS. TRIGGERING ROLLBACK.")
            for anomaly in anomalies:
                logger.error(f"- {anomaly}")
        else:
            logger.info("Deployment health metrics are within acceptable parameters.")

        return RollbackEvaluationResult(
            should_rollback=should_rollback,
            anomalies_detected=anomalies
        )
