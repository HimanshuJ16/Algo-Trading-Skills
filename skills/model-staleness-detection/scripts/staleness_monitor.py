"""
model-staleness-detection: Production-grade ML model staleness monitoring,
rolling accuracy/precision tracking, Population Stability Index (PSI) feature drift detector,
and automated model confidence scaling matrix.
"""
from collections import deque
from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ModelHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED_WARNING = "DEGRADED_WARNING"
    HALTED_STALE = "HALTED_STALE"


@dataclass
class FeatureDriftResult:
    feature_name: str
    z_score_distance: float
    psi_score: float
    is_drifting: bool


@dataclass
class ModelStalenessReport:
    status: ModelHealthStatus
    rolling_accuracy: float
    min_accuracy_threshold: float
    drifted_features_count: int
    sizing_multiplier: float
    action_required: str


class ModelStalenessMonitor:
    """
    Monitors live ML model inference health via rolling accuracy metrics
    and Population Stability Index (PSI) feature distribution drift.
    """

    def __init__(
        self,
        window: int = 60,
        min_accuracy_threshold: float = 0.52,
        psi_warning_threshold: float = 0.10,
        psi_halt_threshold: float = 0.25,
        alert_fn: Optional[Any] = None,
    ):
        self.window = window
        self.min_accuracy_threshold = min_accuracy_threshold
        self.psi_warning_threshold = psi_warning_threshold
        self.psi_halt_threshold = psi_halt_threshold
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))

        self.predictions: deque = deque(maxlen=window)
        self.train_feature_stats: Dict[str, Dict[str, float]] = {}  # {name: {"mean": float, "std": float}}

    def set_training_baseline(self, train_stats: Dict[str, Dict[str, float]]):
        """Sets training-time feature mean and std for drift comparison."""
        self.train_feature_stats = train_stats

    def record_prediction(self, predicted: int, actual: int):
        """Records a single live prediction outcome (1 for correct, 0 for incorrect)."""
        match = 1 if int(predicted) == int(actual) else 0
        self.predictions.append(match)

    def get_rolling_accuracy(self) -> float:
        if not self.predictions:
            return 1.0
        return sum(self.predictions) / len(self.predictions)

    def compute_feature_drift(
        self, feature_name: str, live_values: List[float]
    ) -> FeatureDriftResult:
        """Computes standardized Z-score distance and PSI approximation for a live feature stream."""
        stats = self.train_feature_stats.get(feature_name, {"mean": 0.0, "std": 1.0})
        train_mean = stats.get("mean", 0.0)
        train_std = stats.get("std", 1.0)

        if not live_values or train_std == 0:
            return FeatureDriftResult(feature_name, 0.0, 0.0, False)

        live_mean = sum(live_values) / len(live_values)
        z_distance = abs(live_mean - train_mean) / train_std

        # Simplified PSI estimate based on standardized mean deviation
        psi_estimate = 0.5 * (z_distance ** 2)
        is_drifting = psi_estimate >= self.psi_warning_threshold

        return FeatureDriftResult(
            feature_name=feature_name,
            z_score_distance=round(z_distance, 4),
            psi_score=round(psi_estimate, 4),
            is_drifting=is_drifting,
        )

    def evaluate_health(
        self, live_features_batch: Optional[Dict[str, List[float]]] = None
    ) -> ModelStalenessReport:
        accuracy = self.get_rolling_accuracy()
        drifted_count = 0

        if live_features_batch:
            for feat, vals in live_features_batch.items():
                res = self.compute_feature_drift(feat, vals)
                if res.is_drifting:
                    drifted_count += 1

        # State determination
        if accuracy < self.min_accuracy_threshold or drifted_count >= 3:
            status = ModelHealthStatus.HALTED_STALE
            sizing = 0.0
            action = "HALT MODEL: Accuracy breached min threshold or severe feature drift detected."
            self.alert_fn(f"MODEL STALENESS ALERT [{status.value}]: {action}")
        elif accuracy < self.min_accuracy_threshold + 0.05 or drifted_count >= 1:
            status = ModelHealthStatus.DEGRADED_WARNING
            sizing = 0.5  # Reduce position sizing to 50%
            action = "DEGRADED WARNING: Moderate feature drift or accuracy decay. Sizing reduced to 50%."
        else:
            status = ModelHealthStatus.HEALTHY
            sizing = 1.0
            action = "Model performing within expected parameters."

        return ModelStalenessReport(
            status=status,
            rolling_accuracy=round(accuracy, 4),
            min_accuracy_threshold=self.min_accuracy_threshold,
            drifted_features_count=drifted_count,
            sizing_multiplier=sizing,
            action_required=action,
        )


# Backward compatibility classes & functions
class RollingAccuracy:

    def __init__(self, window=60):
        self.results = deque(maxlen=window)

    def record(self, predicted, actual):
        self.results.append(1 if predicted == actual else 0)

    def accuracy(self):
        return sum(self.results) / len(self.results) if self.results else None


def feature_drift_score(live_mean, live_std, train_mean, train_std):
    if train_std == 0:
        return float("inf") if live_mean != train_mean else 0.0
    return abs(live_mean - train_mean) / train_std
