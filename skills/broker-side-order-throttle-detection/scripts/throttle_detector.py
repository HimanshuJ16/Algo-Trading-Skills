"""
broker-side-order-throttle-detection: ACK RTT latency collector, statistical anomaly
classifier, and adaptive order dispatch backoff engine.
"""
from dataclasses import dataclass, field
import logging
import math
import statistics
import time
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


class ThrottleState(Enum):
    NORMAL = "NORMAL"
    ELEVATED_LATENCY = "ELEVATED_LATENCY"
    SILENT_THROTTLE = "SILENT_THROTTLE"


@dataclass
class OrderACKSample:
    order_id: str
    submission_time: float
    ack_time: float

    @property
    def rtt_ms(self) -> float:
        return max(0.0, (self.ack_time - self.submission_time) * 1000.0)


@dataclass
class ThrottleStatusReport:
    state: ThrottleState
    latest_rtt_ms: float
    baseline_mean_ms: float
    baseline_std_ms: float
    recommended_backoff_ms: float
    is_throttled: bool
    summary: str


class OrderThrottleDetector:
    """
    Monitors order ACK round-trip times (RTT), identifies statistical latency spikes,
    and calculates adaptive order dispatch backoff delays.
    """

    def __init__(
        self,
        window_size: int = 50,
        z_score_threshold: float = 3.0,
        max_absolute_rtt_ms: float = 500.0,
    ):
        self.window_size = window_size
        self.z_score_threshold = z_score_threshold
        self.max_absolute_rtt_ms = max_absolute_rtt_ms
        self.rtt_history: List[float] = []

    def record_order_ack(self, order_id: str, submission_time: float, ack_time: float) -> ThrottleStatusReport:
        """
        Records an ACK RTT measurement and evaluates throttle status.
        """
        sample = OrderACKSample(order_id=order_id, submission_time=submission_time, ack_time=ack_time)
        rtt = sample.rtt_ms

        # Keep sliding window
        self.rtt_history.append(rtt)
        if len(self.rtt_history) > self.window_size:
            self.rtt_history.pop(0)

        # Compute baseline statistics if we have enough samples
        if len(self.rtt_history) >= 5:
            mean_rtt = statistics.mean(self.rtt_history)
            std_rtt = statistics.stdev(self.rtt_history) if len(self.rtt_history) > 1 else 0.0
        else:
            mean_rtt = rtt
            std_rtt = 0.0

        # Evaluate Throttle State
        state = ThrottleState.NORMAL
        backoff_ms = 0.0
        is_throttled = False

        z_score = (rtt - mean_rtt) / std_rtt if std_rtt > 1e-3 else 0.0

        if rtt >= self.max_absolute_rtt_ms or z_score >= self.z_score_threshold:
            state = ThrottleState.SILENT_THROTTLE
            is_throttled = True
            # Adaptive backoff proportional to latency spike
            backoff_ms = min(2000.0, max(100.0, rtt * 1.5))
            logger.warning(
                f"SILENT BROKER THROTTLE DETECTED on order {order_id}: RTT {rtt:.1f}ms "
                f"(Baseline: {mean_rtt:.1f}ms +/- {std_rtt:.1f}ms, Z-Score: {z_score:.2f}). "
                f"Applying backoff delay {backoff_ms:.0f}ms."
            )
        elif rtt > mean_rtt + std_rtt:
            state = ThrottleState.ELEVATED_LATENCY
            backoff_ms = 25.0

        summary = (
            f"ACK RTT: {rtt:.1f}ms | State: {state.value} | "
            f"Baseline: {mean_rtt:.1f}ms +/- {std_rtt:.1f}ms | Backoff: {backoff_ms:.0f}ms"
        )

        return ThrottleStatusReport(
            state=state,
            latest_rtt_ms=rtt,
            baseline_mean_ms=mean_rtt,
            baseline_std_ms=std_rtt,
            recommended_backoff_ms=backoff_ms,
            is_throttled=is_throttled,
            summary=summary,
        )
