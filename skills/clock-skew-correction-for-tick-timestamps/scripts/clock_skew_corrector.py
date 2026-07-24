"""
clock-skew-correction-for-tick-timestamps: Production-grade dynamic clock skew estimator,
EWMA smoothing filter, network jitter outlier rejector, and timestamp calibrator.
"""
from dataclasses import dataclass, field
import logging
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ClockSkewResult:
    raw_delta_ms: float
    estimated_skew_ms: float
    calibrated_timestamp: float
    is_outlier: bool


class ClockSkewCorrector:
    """
    Dynamically estimates local system clock drift relative to exchange timestamps,
    filters network transport jitter, and calibrates local tick timestamps.
    """

    def __init__(
        self,
        alpha: float = 0.05,
        max_drift_threshold_ms: float = 100.0,
        sample_window_size: int = 50,
        outlier_std_multiplier: float = 3.0,
    ):
        self.alpha = alpha
        self.max_drift_threshold_ms = max_drift_threshold_ms
        self.sample_window_size = sample_window_size
        self.outlier_std_multiplier = outlier_std_multiplier

        self.estimated_skew_ms: float = 0.0
        self.recent_deltas_ms: List[float] = []
        self.is_initialized: bool = False

    def is_outlier_jitter(self, delta_ms: float) -> bool:
        """Determines if delta sample is a network latency outlier spike."""
        if len(self.recent_deltas_ms) < 10:
            return False
        median = statistics.median(self.recent_deltas_ms)
        mad = statistics.median([abs(x - median) for x in self.recent_deltas_ms])
        threshold = max(mad * self.outlier_std_multiplier, 15.0)  # Min 15ms threshold
        return abs(delta_ms - median) > threshold

    def add_sample(self, t_exchange: float, t_local_receipt: float) -> ClockSkewResult:
        """
        Ingests exchange and local receipt timestamps, updates EWMA skew estimate,
        and returns calibrated result. Timestamps passed in seconds or milliseconds.
        """
        # Convert to milliseconds for numerical stability
        t_ex_ms = t_exchange * 1000.0 if t_exchange < 1e11 else t_exchange
        t_loc_ms = t_local_receipt * 1000.0 if t_local_receipt < 1e11 else t_local_receipt

        raw_delta_ms = t_ex_ms - t_loc_ms
        is_outlier = self.is_outlier_jitter(raw_delta_ms)

        if not is_outlier:
            self.recent_deltas_ms.append(raw_delta_ms)
            if len(self.recent_deltas_ms) > self.sample_window_size:
                self.recent_deltas_ms.pop(0)

            if not self.is_initialized:
                self.estimated_skew_ms = raw_delta_ms
                self.is_initialized = True
            else:
                # EWMA update
                self.estimated_skew_ms = (
                    self.alpha * raw_delta_ms + (1.0 - self.alpha) * self.estimated_skew_ms
                )
        else:
            logger.debug(f"Network jitter outlier rejected: {raw_delta_ms:.2f}ms")

        # Check drift threshold
        if abs(self.estimated_skew_ms) > self.max_drift_threshold_ms:
            logger.warning(
                f"CLOCK DRIFT THRESHOLD BREACHED: Estimated skew is {self.estimated_skew_ms:.2f}ms > {self.max_drift_threshold_ms}ms! PTP/NTP resync recommended."
            )

        calibrated_ts_sec = (t_loc_ms + self.estimated_skew_ms) / 1000.0
        return ClockSkewResult(
            raw_delta_ms=raw_delta_ms,
            estimated_skew_ms=self.estimated_skew_ms,
            calibrated_timestamp=calibrated_ts_sec,
            is_outlier=is_outlier,
        )

    def calibrate_timestamp(self, t_local_receipt: float) -> float:
        """Applies estimated clock skew to local receipt timestamp."""
        t_loc_sec = t_local_receipt / 1000.0 if t_local_receipt > 1e11 else t_local_receipt
        return t_loc_sec + (self.estimated_skew_ms / 1000.0)
