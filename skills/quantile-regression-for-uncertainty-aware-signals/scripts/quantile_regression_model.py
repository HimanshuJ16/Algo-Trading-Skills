"""
quantile-regression-for-uncertainty-aware-signals: Multi-quantile Pinball Loss trainer,
quantile-crossing filter, and uncertainty-scaled position sizer.
"""
from dataclasses import dataclass
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class QuantilePrediction:
    q10: float
    q50: float
    q90: float
    uncertainty_width: float
    confidence_scaled_size: float


class QuantileRegressionSignalModel:
    """
    Fits multi-quantile linear models (tau=0.10, 0.50, 0.90) using Pinball Loss
    to produce uncertainty-aware trading signals.
    """

    def __init__(
        self,
        num_features: int,
        learning_rate: float = 0.02,
        quantiles: Tuple[float, float, float] = (0.10, 0.50, 0.90),
    ):
        self.num_features = num_features
        self.eta = learning_rate
        self.quantiles = quantiles
        # Separate weight vector for each quantile
        self.weights: Dict[float, List[float]] = {q: [0.0] * num_features for q in quantiles}

    @staticmethod
    def pinball_loss_gradient(y_true: float, y_pred: float, tau: float) -> float:
        """
        Subgradient of Pinball Loss L_tau(y, y_hat):
        dL/dy_hat = -tau if y >= y_hat else (1 - tau)
        """
        err = y_true - y_pred
        return -tau if err >= 0 else (1.0 - tau)

    def train_sample(self, features: List[float], target: float) -> None:
        for q in self.quantiles:
            y_pred = sum(w * x for w, x in zip(self.weights[q], features))
            grad = self.pinball_loss_gradient(target, y_pred, q)

            for i in range(self.num_features):
                self.weights[q][i] -= self.eta * grad * features[i]

    def predict(self, features: List[float], max_position_size: float = 1.0) -> QuantilePrediction:
        preds = {q: sum(w * x for w, x in zip(self.weights[q], features)) for q in self.quantiles}

        raw_q10 = preds[0.10]
        raw_q50 = preds[0.50]
        raw_q90 = preds[0.90]

        # Monotonicity / Quantile crossing fix: sort quantiles
        q10, q50, q90 = sorted([raw_q10, raw_q50, raw_q90])

        uncertainty_width = max(0.0001, q90 - q10)

        # Signal direction from median forecast q50, scaled inversely by uncertainty width
        signal_dir = 1.0 if q50 > 0 else (-1.0 if q50 < 0 else 0.0)
        confidence_ratio = abs(q50) / uncertainty_width
        scaled_size = round(signal_dir * min(max_position_size, confidence_ratio), 4)

        return QuantilePrediction(
            q10=round(q10, 6),
            q50=round(q50, 6),
            q90=round(q90, 6),
            uncertainty_width=round(uncertainty_width, 6),
            confidence_scaled_size=scaled_size,
        )
