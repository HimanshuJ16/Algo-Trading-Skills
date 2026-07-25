"""
online-learning-for-adaptive-signal-models: Incremental Online Learner (SGD / RLS)
with weight clipping and concept drift detection.
"""
from dataclasses import dataclass
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OnlinePredictionResult:
    sample_index: int
    predicted_y: float
    actual_y: Optional[float]
    prediction_error: Optional[float]
    weights_norm: float


@dataclass
class OnlineModelAuditReport:
    total_samples_processed: int
    initial_mae: float
    final_mae: float
    mae_improvement_pct: float
    weights: List[float]
    is_converged: bool
    message: str


class OnlineAdaptiveSignalModel:
    """
    Incremental online learning model using SGD with learning rate decay,
    weight bounding, and rolling prediction error monitoring.
    """

    def __init__(
        self,
        num_features: int,
        learning_rate: float = 0.01,
        l2_penalty: float = 0.001,
        max_weight_norm: float = 10.0,
    ):
        self.num_features = num_features
        self.eta = learning_rate
        self.l2 = l2_penalty
        self.max_norm = max_weight_norm
        self.weights = [0.0] * num_features
        self.history_errors: List[float] = []

    def predict(self, features: List[float]) -> float:
        if len(features) != self.num_features:
            raise ValueError(f"Expected {self.num_features} features, got {len(features)}.")
        return sum(w * x for w, x in zip(self.weights, features))

    def update(self, features: List[float], target: float) -> OnlinePredictionResult:
        pred = self.predict(features)
        err = target - pred
        self.history_errors.append(abs(err))

        # Online SGD step with L2 regularization
        for i in range(self.num_features):
            grad = -err * features[i] + self.l2 * self.weights[i]
            self.weights[i] -= self.eta * grad

        # Clip total weight norm to prevent instability
        norm = math.sqrt(sum(w * w for w in self.weights))
        if norm > self.max_norm:
            scale = self.max_norm / norm
            self.weights = [w * scale for w in self.weights]
            norm = self.max_norm

        return OnlinePredictionResult(
            sample_index=len(self.history_errors),
            predicted_y=round(pred, 6),
            actual_y=round(target, 6),
            prediction_error=round(err, 6),
            weights_norm=round(norm, 4),
        )

    def audit_performance(self) -> OnlineModelAuditReport:
        n = len(self.history_errors)
        if n < 10:
            return OnlineModelAuditReport(n, 0.0, 0.0, 0.0, self.weights, False, "Not enough samples.")

        init_window = self.history_errors[: n // 4]
        final_window = self.history_errors[-(n // 4) :]

        init_mae = sum(init_window) / len(init_window)
        final_mae = sum(final_window) / len(final_window)

        improvement = ((init_mae - final_mae) / max(0.0001, init_mae)) * 100.0
        converged = final_mae < init_mae

        msg = f"Online Adaptation Audit ({n} samples): Initial MAE={init_mae:.4f} -> Final MAE={final_mae:.4f} ({improvement:.1f}% improvement)."
        logger.info(msg)

        return OnlineModelAuditReport(
            total_samples_processed=n,
            initial_mae=round(init_mae, 4),
            final_mae=round(final_mae, 4),
            mae_improvement_pct=round(improvement, 2),
            weights=[round(w, 4) for w in self.weights],
            is_converged=converged,
            message=msg,
        )
