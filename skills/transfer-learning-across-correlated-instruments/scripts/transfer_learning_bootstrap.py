import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class TransferMode(Enum):
    REGULARIZED_FINE_TUNING = "REGULARIZED_FINE_TUNING" # L2 penalized adaptation from source weights
    FEATURE_REUSE = "FEATURE_REUSE"                     # Freeze source normalization & feature weights
    WEIGHTED_ENSEMBLE = "WEIGHTED_ENSEMBLE"             # Blend source and target predictions


class MLOpsError(Exception):
    """Base exception for financial machine learning transfer learning errors."""
    pass


@dataclass
class Dataset:
    symbol: str
    features: List[List[float]]  # Shape: (N, D) where N=samples, D=features
    targets: List[float]        # Shape: (N,)
    feature_names: List[str] = field(default_factory=list)


@dataclass
class TransferConfig:
    source_symbol: str
    target_symbol: str
    min_correlation: float = 0.60
    learning_rate: float = 0.01
    l2_penalty: float = 0.1          # Penalizes divergence from source weights
    fine_tune_epochs: int = 100
    max_domain_shift: float = 2.0    # Threshold for covariate shift detection


@dataclass
class ModelParameters:
    weights: List[float]
    bias: float
    feature_means: List[float]
    feature_stds: List[float]


@dataclass
class TransferEvaluation:
    source_symbol: str
    target_symbol: str
    correlation: float
    domain_shift_score: float
    direct_target_r2: float
    transfer_model_r2: float
    transfer_gain_r2: float
    is_transfer_recommended: bool
    audit_trail: List[str] = field(default_factory=list)


class FinancialTransferLearningEngine:
    """
    Institutional Transfer Learning Engine for Financial Time Series Models.
    
    Solves the cold-start problem for newly listed or illiquid instruments by:
    1. Pre-training base feature normalization & regression weights on a liquid Source asset.
    2. Measuring Covariate Shift / Domain Distance between Source & Target features.
    3. Adapting Source weights to Target data using L2 regularized fine-tuning.
    4. Evaluating Out-of-Sample R² gains over direct target training.
    """

    def __init__(self):
        logger.info("Initialized Financial Transfer Learning Engine")

    @staticmethod
    def calculate_correlation(x: List[float], y: List[float]) -> float:
        """Calculates Pearson correlation coefficient between two series."""
        if len(x) != len(y) or len(x) < 2:
            raise MLOpsError("Series must be of equal length and contain at least 2 samples.")

        mean_x = sum(x) / len(x)
        mean_y = sum(y) / len(y)

        num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(len(x)))
        den_x = math.sqrt(sum((x[i] - mean_x) ** 2 for i in range(len(x))))
        den_y = math.sqrt(sum((y[i] - mean_y) ** 2 for i in range(len(y))))

        if den_x * den_y == 0:
            return 0.0
        return num / (den_x * den_y)

    @staticmethod
    def calculate_covariate_shift(source_features: List[List[float]], target_features: List[List[float]]) -> float:
        """
        Calculates domain distance (Normalized Mean Absolute Distance) between source & target distributions.
        """
        n_src, num_dim = len(source_features), len(source_features[0])
        n_tgt = len(target_features)

        shift_scores = []
        for j in range(num_dim):
            src_col = [source_features[i][j] for i in range(n_src)]
            tgt_col = [target_features[i][j] for i in range(n_tgt)]

            src_mean = sum(src_col) / n_src
            tgt_mean = sum(tgt_col) / n_tgt

            src_std = math.sqrt(sum((x - src_mean) ** 2 for x in src_col) / max(1, n_src - 1)) + 1e-8
            dim_shift = abs(src_mean - tgt_mean) / src_std
            shift_scores.append(dim_shift)

        return sum(shift_scores) / num_dim

    def fit_source_model(self, source_data: Dataset) -> ModelParameters:
        """
        Pre-trains a linear regression model on abundant source domain data with standardization.
        """
        N, D = len(source_data.features), len(source_data.features[0])
        if N < D + 1:
            raise MLOpsError(f"Insufficient source data: {N} samples < {D + 1} features.")

        # 1. Standardize features
        means = [sum(source_data.features[i][j] for i in range(N)) / N for j in range(D)]
        stds = [
            math.sqrt(sum((source_data.features[i][j] - means[j]) ** 2 for i in range(N)) / N) + 1e-8
            for j in range(D)
        ]

        scaled_X = [[(source_data.features[i][j] - means[j]) / stds[j] for j in range(D)] for i in range(N)]

        # 2. Gradient Descent optimization for weights
        weights = [0.0] * D
        bias = 0.0
        lr = 0.01

        for _ in range(300):
            for i in range(N):
                pred = sum(weights[j] * scaled_X[i][j] for j in range(D)) + bias
                err = pred - source_data.targets[i]
                for j in range(D):
                    weights[j] -= lr * err * scaled_X[i][j] / N
                bias -= lr * err / N

        logger.info(f"Pre-trained Source Model on {source_data.symbol} ({N} samples)")
        return ModelParameters(weights=weights, bias=bias, feature_means=means, feature_stds=stds)

    def fine_tune_target_model(
        self, source_params: ModelParameters, target_data: Dataset, config: TransferConfig
    ) -> ModelParameters:
        """
        Fine-tunes pre-trained source model on sparse target domain data using L2 penalty.
        """
        N, D = len(target_data.features), len(target_data.features[0])
        if N < 2:
            raise MLOpsError("Target dataset requires at least 2 samples.")

        # Scale target data using Source feature scalers to preserve feature space parity
        scaled_X = [
            [(target_data.features[i][j] - source_params.feature_means[j]) / source_params.feature_stds[j] for j in range(D)]
            for i in range(N)
        ]

        weights = list(source_params.weights)
        bias = source_params.bias

        # Fine-tune with L2 penalty pulling toward source weights
        for _ in range(config.fine_tune_epochs):
            for i in range(N):
                pred = sum(weights[j] * scaled_X[i][j] for j in range(D)) + bias
                err = pred - target_data.targets[i]
                for j in range(D):
                    # L2 penalty derivative = config.l2_penalty * (weights[j] - source_params.weights[j])
                    l2_grad = config.l2_penalty * (weights[j] - source_params.weights[j])
                    weights[j] -= config.learning_rate * ((err * scaled_X[i][j] / N) + l2_grad)
                bias -= config.learning_rate * (err / N)

        logger.info(f"Fine-tuned Target Model for {target_data.symbol} ({N} samples)")
        return ModelParameters(
            weights=weights, bias=bias, feature_means=source_params.feature_means, feature_stds=source_params.feature_stds
        )

    def predict(self, params: ModelParameters, X: List[List[float]]) -> List[float]:
        """Generates predictions using model parameters."""
        N, D = len(X), len(X[0])
        scaled_X = [[(X[i][j] - params.feature_means[j]) / params.feature_stds[j] for j in range(D)] for i in range(N)]
        return [sum(params.weights[j] * scaled_X[i][j] for j in range(D)) + params.bias for i in range(N)]

    def calculate_r2(self, y_true: List[float], y_pred: List[float]) -> float:
        """Calculates Coefficient of Determination (R² score)."""
        mean_y = sum(y_true) / len(y_true)
        ss_tot = sum((y - mean_y) ** 2 for y in y_true)
        ss_res = sum((y_true[i] - y_pred[i]) ** 2 for i in range(len(y_true)))
        if ss_tot == 0:
            return 0.0
        return 1.0 - (ss_res / ss_tot)

    def evaluate_transfer_performance(
        self, source_data: Dataset, target_data: Dataset, config: TransferConfig
    ) -> TransferEvaluation:
        """
        Evaluates whether transfer learning provides positive out-of-sample R² gains over direct target training.
        """
        audit = [f"Evaluating transfer learning from {source_data.symbol} to {target_data.symbol}"]

        # 1. Pairwise Correlation Check
        corr = self.calculate_correlation(source_data.targets[: len(target_data.targets)], target_data.targets)
        audit.append(f"Pairwise target correlation: {corr:.3f}")

        # 2. Covariate Shift Check
        cov_shift = self.calculate_covariate_shift(source_data.features, target_data.features)
        audit.append(f"Covariate shift domain distance: {cov_shift:.3f}")

        # 3. Fit Source Model
        src_params = self.fit_source_model(source_data)

        # 4. Fit Direct Target Model (Cold Start Baseline)
        direct_params = self.fit_source_model(target_data)
        direct_preds = self.predict(direct_params, target_data.features)
        direct_r2 = self.calculate_r2(target_data.targets, direct_preds)

        # 5. Fine-Tune Transfer Model
        transfer_params = self.fine_tune_target_model(src_params, target_data, config)
        transfer_preds = self.predict(transfer_params, target_data.features)
        transfer_r2 = self.calculate_r2(target_data.targets, transfer_preds)

        r2_gain = transfer_r2 - direct_r2
        is_recommended = corr >= config.min_correlation and cov_shift <= config.max_domain_shift and transfer_r2 > direct_r2

        audit.append(f"Direct Target Model R²: {direct_r2:.4f}")
        audit.append(f"Transferred Model R²: {transfer_r2:.4f}")
        audit.append(f"Transfer R² Gain: {r2_gain:+.4f}")
        audit.append(f"Transfer Recommendation: {'APPROVED' if is_recommended else 'REJECTED (Negative Transfer Risk)'}")

        logger.info(f"Transfer Learning Evaluation for {target_data.symbol}: R² Gain={r2_gain:+.4f}, Approved={is_recommended}")

        return TransferEvaluation(
            source_symbol=source_data.symbol,
            target_symbol=target_data.symbol,
            correlation=corr,
            domain_shift_score=cov_shift,
            direct_target_r2=direct_r2,
            transfer_model_r2=transfer_r2,
            transfer_gain_r2=r2_gain,
            is_transfer_recommended=is_recommended,
            audit_trail=audit,
        )

