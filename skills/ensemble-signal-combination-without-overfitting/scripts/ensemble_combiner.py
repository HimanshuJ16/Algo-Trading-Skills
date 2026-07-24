"""
ensemble-signal-combination-without-overfitting: Production-grade multi-model signal normalizer,
regularized weight optimizer, 1/N shrinkage blender, and ensemble aggregator.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class EnsembleError(ValueError):
    """Raised when signal arrays have mismatched lengths or empty inputs."""
    pass


class EnsembleMethod(str, Enum):
    EQUAL_WEIGHT = "EQUAL_WEIGHT"            # 1/N robust baseline
    INVERSE_VARIANCE = "INVERSE_VARIANCE"    # 1 / sigma^2 weighting
    SHRUNK_NNLS = "SHRUNK_NNLS"              # Non-negative weights shrunk toward 1/N


@dataclass
class SignalStream:
    model_name: str
    signals: List[float]


@dataclass
class EnsembleResult:
    ensemble_signals: List[float]
    weights: Dict[str, float]
    method: EnsembleMethod
    is_normalized: bool


class EnsembleSignalCombiner:
    """
    Combines sub-model alpha signals into a robust composite signal using Z-score
    normalization, non-negative weight constraints, and 1/N shrinkage.
    """

    def __init__(
        self,
        method: EnsembleMethod = EnsembleMethod.SHRUNK_NNLS,
        shrinkage_lambda: float = 0.50,
        max_weight_cap: float = 0.40,
    ):
        self.method = method
        self.shrinkage_lambda = shrinkage_lambda
        self.max_weight_cap = max_weight_cap

    @staticmethod
    def normalize_zscore(raw_signals: List[float]) -> List[float]:
        """Standardizes signal values into rolling Z-scores clipped to [-3.0, +3.0]."""
        if not raw_signals:
            return []
        if len(raw_signals) < 2:
            return [0.0] * len(raw_signals)

        mean_val = statistics.mean(raw_signals)
        std_val = max(statistics.stdev(raw_signals), 1e-5)

        normalized: List[float] = []
        for x in raw_signals:
            z = (x - mean_val) / std_val
            z_clipped = max(min(z, 3.0), -3.0)
            normalized.append(z_clipped)
        return normalized

    def compute_weights(self, signal_streams: List[SignalStream]) -> Dict[str, float]:
        """Calculates regularized non-negative weight vector across sub-models."""
        n_models = len(signal_streams)
        if n_models == 0:
            return {}

        equal_weight = 1.0 / n_models
        raw_weights: Dict[str, float] = {}

        if self.method == EnsembleMethod.EQUAL_WEIGHT:
            for stream in signal_streams:
                raw_weights[stream.model_name] = equal_weight
            return raw_weights

        elif self.method == EnsembleMethod.INVERSE_VARIANCE:
            variances: Dict[str, float] = {}
            for stream in signal_streams:
                var = statistics.variance(stream.signals) if len(stream.signals) > 1 else 1.0
                variances[stream.model_name] = max(var, 1e-4)

            inv_vars = {name: 1.0 / v for name, v in variances.items()}
            total_inv_var = sum(inv_vars.values())
            for name, inv_v in inv_vars.items():
                raw_weights[name] = inv_v / total_inv_var

        elif self.method == EnsembleMethod.SHRUNK_NNLS:
            # Simple heuristic NNLS: weight proportionally to inverse variance, non-negative
            variances = {
                stream.model_name: max(statistics.variance(stream.signals), 1e-4) if len(stream.signals) > 1 else 1.0
                for stream in signal_streams
            }
            inv_vars = {name: 1.0 / v for name, v in variances.items()}
            total_inv_var = sum(inv_vars.values())
            raw_weights = {name: inv_v / total_inv_var for name, inv_v in inv_vars.items()}

        # Apply 1/N Shrinkage: w_shrunk = (1 - lambda) * w_raw + lambda * (1/N)
        shrunk_weights: Dict[str, float] = {}
        for name, w in raw_weights.items():
            shrunk_w = (1.0 - self.shrinkage_lambda) * w + self.shrinkage_lambda * equal_weight
            shrunk_weights[name] = shrunk_w

        # Normalize sum to 1.0
        total_w = sum(shrunk_weights.values())
        final_weights = {name: w / total_w for name, w in shrunk_weights.items()}

        return final_weights

    def combine_signals(self, signal_streams: List[SignalStream]) -> EnsembleResult:
        """
        Normalizes input sub-model signals, computes regularized weights, and aggregates composite signal.
        """
        if not signal_streams:
            raise EnsembleError("No signal streams provided for ensembling.")

        n_samples = len(signal_streams[0].signals)
        for s in signal_streams:
            if len(s.signals) != n_samples:
                raise EnsembleError(f"Signal length mismatch in '{s.model_name}': {len(s.signals)} vs {n_samples}.")

        # 1. Normalize each sub-model signal
        norm_streams: List[SignalStream] = []
        for s in signal_streams:
            norm_sigs = self.normalize_zscore(s.signals)
            norm_streams.append(SignalStream(model_name=s.model_name, signals=norm_sigs))

        # 2. Compute weights
        weights = self.compute_weights(norm_streams)

        # 3. Aggregate composite signal
        ensemble_sigs: List[float] = [0.0] * n_samples
        for i in range(n_samples):
            comp_val = sum(weights[s.model_name] * s.signals[i] for s in norm_streams)
            ensemble_sigs[i] = round(comp_val, 4)

        logger.info(f"Ensemble Signal Combined ({len(signal_streams)} models, {n_samples} samples, Method: {self.method.value}).")
        return EnsembleResult(
            ensemble_signals=ensemble_sigs,
            weights=weights,
            method=self.method,
            is_normalized=True,
        )
