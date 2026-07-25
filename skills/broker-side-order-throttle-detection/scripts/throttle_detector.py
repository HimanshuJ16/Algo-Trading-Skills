"""
broker-side-order-throttle-detection: Advanced quant standard implementation for detecting
silent broker-side order throttling using EWMA/EWMVar anomaly detection and AIMD
(Additive Increase, Multiplicative Decrease) adaptive order dispatch backoff.
"""
import logging
import math
from enum import Enum
from dataclasses import dataclass

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
    ewma_rtt_ms: float
    ewmsd_rtt_ms: float
    recommended_backoff_ms: float
    is_throttled: bool
    summary: str


class OrderThrottleDetector:
    """
    Monitors order ACK round-trip times (RTT) using Exponentially Weighted 
    Moving Average (EWMA) and Variance (EWMVar) for robust statistical anomaly detection.
    Applies AIMD-style backoff logic.
    """

    def __init__(
        self,
        alpha: float = 0.1,
        z_score_threshold: float = 3.0,
        max_absolute_rtt_ms: float = 500.0,
        min_variance_clamp: float = 1.0,
        min_backoff_ms: float = 10.0,
        max_backoff_ms: float = 2000.0,
    ):
        """
        :param alpha: Smoothing factor for EWMA (0 < alpha <= 1).
        :param z_score_threshold: Standard deviations threshold for SILENT_THROTTLE.
        :param max_absolute_rtt_ms: Hard threshold above which throttling is assumed.
        :param min_variance_clamp: Prevents division by zero in highly deterministic environments.
        """
        self.alpha = alpha
        self.z_score_threshold = z_score_threshold
        self.max_absolute_rtt_ms = max_absolute_rtt_ms
        self.min_variance_clamp = min_variance_clamp
        self.min_backoff_ms = min_backoff_ms
        self.max_backoff_ms = max_backoff_ms
        
        # State variables
        self.ewma_rtt = 0.0
        self.ewmvar_rtt = 0.0
        self.initialized = False
        self.current_backoff_ms = 0.0

    def record_order_ack(self, order_id: str, submission_time: float, ack_time: float) -> ThrottleStatusReport:
        sample = OrderACKSample(order_id, submission_time, ack_time)
        rtt = sample.rtt_ms

        if not self.initialized:
            self.ewma_rtt = rtt
            self.ewmvar_rtt = 0.0
            self.initialized = True
            z_score = 0.0
            ewmsd = 0.0
        else:
            ewmsd = math.sqrt(self.ewmvar_rtt)
            clamped_std = max(ewmsd, self.min_variance_clamp)
            z_score = (rtt - self.ewma_rtt) / clamped_std

            # Update EWMA and EWMVar using Welford's method for exponential weights
            diff = rtt - self.ewma_rtt
            self.ewma_rtt += self.alpha * diff
            self.ewmvar_rtt = (1 - self.alpha) * (self.ewmvar_rtt + self.alpha * diff ** 2)

        state = ThrottleState.NORMAL
        is_throttled = False

        if rtt >= self.max_absolute_rtt_ms or z_score >= self.z_score_threshold:
            state = ThrottleState.SILENT_THROTTLE
            is_throttled = True
            
            # Multiplicative Decrease (in throughput, so Multiplicative Increase in backoff)
            if self.current_backoff_ms == 0:
                self.current_backoff_ms = max(self.min_backoff_ms, rtt * 0.5)
            else:
                self.current_backoff_ms = min(self.max_backoff_ms, self.current_backoff_ms * 2.0)
                
            logger.warning(
                f"SILENT BROKER THROTTLE DETECTED on order {order_id}: RTT {rtt:.1f}ms "
                f"(EWMA: {self.ewma_rtt:.1f}ms, Z-Score: {z_score:.2f}). "
                f"Applying backoff delay {self.current_backoff_ms:.0f}ms."
            )
        elif rtt > self.ewma_rtt + max(ewmsd, self.min_variance_clamp):
            state = ThrottleState.ELEVATED_LATENCY
            # Minor penalty
            self.current_backoff_ms = min(self.max_backoff_ms, max(self.min_backoff_ms, self.current_backoff_ms + 10.0))
        else:
            # Additive Increase (in throughput, so Additive Decrease in backoff)
            self.current_backoff_ms = max(0.0, self.current_backoff_ms - 20.0)

        summary = (
            f"ACK RTT: {rtt:.1f}ms | State: {state.value} | "
            f"EWMA: {self.ewma_rtt:.1f}ms +/- {ewmsd:.1f}ms | Backoff: {self.current_backoff_ms:.0f}ms"
        )

        return ThrottleStatusReport(
            state=state,
            latest_rtt_ms=rtt,
            ewma_rtt_ms=self.ewma_rtt,
            ewmsd_rtt_ms=ewmsd,
            recommended_backoff_ms=self.current_backoff_ms,
            is_throttled=is_throttled,
            summary=summary,
        )
