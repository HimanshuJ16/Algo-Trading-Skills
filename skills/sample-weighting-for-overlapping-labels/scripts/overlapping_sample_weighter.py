import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)

@dataclass
class OverlappingSampleWeighterConfig:
    """Legacy config class for backward compatibility."""
    parameter_1: float = 1.0
    parameter_2: int = 10

class OverlappingSampleWeighter:
    """Legacy class retained for backward compatibility."""
    def __init__(self, config: OverlappingSampleWeighterConfig):
        self.config = config

    def process(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{"result": d.get("value", 0) * self.config.parameter_1} for d in data]

class WeightingMethod(str, Enum):
    UNIQUENESS_ONLY = "UNIQUENESS_ONLY"
    RETURN_ATTRIBUTED = "RETURN_ATTRIBUTED"
    TIME_DECAY = "TIME_DECAY"

@dataclass
class LabelSpan:
    sample_id: str
    start_time_idx: int                 # Time index start (e.g. bar 10)
    end_time_idx: int                   # Time index end (e.g. bar 25)
    realized_return: float = 0.0        # Optional log/simple return over window

@dataclass
class SampleWeightResult:
    sample_id: str
    start_time_idx: int
    end_time_idx: int
    average_uniqueness: float
    raw_weight: float
    normalized_weight: float

@dataclass
class SampleWeightingReport:
    total_samples: int
    average_dataset_uniqueness: float
    weighting_method: WeightingMethod
    sample_results: List[SampleWeightResult]
    audit_notes: str

class SampleWeightingForOverlappingLabelsEngine:
    """
    Production-grade sample weighting engine for overlapping financial labels (Triple Barrier Method)
    based on Marcos López de Prado's concurrent label uniqueness, return-attribution, and time-decay weighting.
    """
    def __init__(self, decay_factor: float = 0.5):
        self.decay_factor = decay_factor

    def compute_concurrency(self, spans: List[LabelSpan]) -> Dict[int, int]:
        """Calculates number of active concurrent labels c_t at each time index t."""
        concurrency: Dict[int, int] = {}
        for s in spans:
            for t in range(s.start_time_idx, s.end_time_idx + 1):
                concurrency[t] = concurrency.get(t, 0) + 1
        return concurrency

    def compute_sample_uniqueness(
        self,
        spans: List[LabelSpan],
        concurrency: Dict[int, int]
    ) -> List[float]:
        """Calculates average uniqueness u_i for each label span: mean(1 / c_t) over span duration."""
        uniqueness_scores: List[float] = []
        for s in spans:
            duration = (s.end_time_idx - s.start_time_idx) + 1
            if duration <= 0:
                uniqueness_scores.append(1.0)
                continue

            inv_concurrency_sum = sum(1.0 / max(concurrency.get(t, 1), 1) for t in range(s.start_time_idx, s.end_time_idx + 1))
            avg_u = inv_concurrency_sum / duration
            uniqueness_scores.append(round(avg_u, 4))
        return uniqueness_scores

    def compute_sample_weights(
        self,
        spans: List[LabelSpan],
        method: WeightingMethod = WeightingMethod.UNIQUENESS_ONLY
    ) -> SampleWeightingReport:
        """
        Computes uniqueness scores and final sample weights normalized so sum(weights) == N.
        """
        n = len(spans)
        if n == 0:
            raise ValueError("At least 1 LabelSpan is required for sample weighting.")

        concurrency = self.compute_concurrency(spans)
        uniqueness = self.compute_sample_uniqueness(spans, concurrency)

        raw_weights: List[float] = []

        for i in range(n):
            u_i = uniqueness[i]
            s = spans[i]

            if method == WeightingMethod.UNIQUENESS_ONLY:
                w_i = u_i
            elif method == WeightingMethod.RETURN_ATTRIBUTED:
                w_i = u_i * abs(s.realized_return)
            elif method == WeightingMethod.TIME_DECAY:
                # Exponential decay toward older samples
                decay = math.exp(-self.decay_factor * (n - 1 - i) / max(n, 1))
                w_i = u_i * decay
            else:
                w_i = u_i

            raw_weights.append(w_i)

        sum_raw = sum(raw_weights)
        if sum_raw <= 0:
            norm_weights = [1.0] * n
        else:
            norm_weights = [(w / sum_raw) * n for w in raw_weights]

        results: List[SampleWeightResult] = []
        for i in range(n):
            s = spans[i]
            results.append(SampleWeightResult(
                sample_id=s.sample_id,
                start_time_idx=s.start_time_idx,
                end_time_idx=s.end_time_idx,
                average_uniqueness=uniqueness[i],
                raw_weight=round(raw_weights[i], 4),
                normalized_weight=round(norm_weights[i], 4)
            ))

        avg_dataset_u = round(sum(uniqueness) / n, 4)
        notes = (
            f"SAMPLE WEIGHTING [{method.value}]: N = {n}, "
            f"Avg Dataset Uniqueness = {avg_dataset_u:.4f} (1.0 = completely non-overlapping)."
        )

        logger.info(notes)

        return SampleWeightingReport(
            total_samples=n,
            average_dataset_uniqueness=avg_dataset_u,
            weighting_method=method,
            sample_results=results,
            audit_notes=notes
        )
