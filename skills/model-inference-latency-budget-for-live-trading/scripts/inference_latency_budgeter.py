import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class InferenceBudgetConfig:
    model_id: str
    max_inference_budget_ms: float = 1.0     # Hard SLA ceiling for inference (1.0 ms)
    warning_threshold_ms: float = 0.8        # Early warning limit (0.8 ms)
    fallback_action: str = "QUANTIZED_ONNX_FALLBACK" # 'QUANTIZED_ONNX_FALLBACK', 'LINEAR_HEURISTIC_FALLBACK', 'SKIP_SIGNAL'

@dataclass
class LatencyPercentiles:
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    p99_9_ms: float
    jitter_std_dev_ms: float

@dataclass
class InferenceBudgetReport:
    model_id: str
    sample_count: int
    percentiles: LatencyPercentiles
    max_inference_budget_ms: float
    is_sla_compliant: bool
    recommended_fallback_action: Optional[str]
    status: str                              # 'INFERENCE_LATENCY_NORMAL', 'INFERENCE_LATENCY_WARNING_NEAR_LIMIT', 'INFERENCE_LATENCY_SLA_BREACH'
    audit_notes: str

class ModelInferenceLatencyBudgeterEngine:
    """
    Quantitative model inference latency budget and percentile SLA profiling engine,
    managing real-time ONNX/TensorRT execution budgets and automated model fallbacks.
    """
    def __init__(self):
        pass

    def evaluate_inference_latency_budget(
        self,
        config: InferenceBudgetConfig,
        sample_latencies_ms: List[float]
    ) -> InferenceBudgetReport:
        """
        Calculates P50, P90, P95, P99, P99.9 percentiles and jitter, and audits SLA budget compliance.
        """
        if not sample_latencies_ms:
            raise ValueError("Sample latencies array cannot be empty.")
        if config.max_inference_budget_ms <= 0.0:
            raise ValueError("Maximum inference budget must be strictly positive.")

        n = len(sample_latencies_ms)
        sorted_samples = sorted(sample_latencies_ms)

        def get_percentile(p: float) -> float:
            if n == 1:
                return sorted_samples[0]
            k = (n - 1) * (p / 100.0)
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return sorted_samples[int(k)]
            d0 = sorted_samples[int(f)] * (c - k)
            d1 = sorted_samples[int(c)] * (k - f)
            return d0 + d1

        p50 = round(get_percentile(50.0), 3)
        p90 = round(get_percentile(90.0), 3)
        p95 = round(get_percentile(95.0), 3)
        p99 = round(get_percentile(99.0), 3)
        p99_9 = round(get_percentile(99.9), 3)

        mean_val = sum(sorted_samples) / n
        var_val = sum((x - mean_val) ** 2 for x in sorted_samples) / (n - 1) if n > 1 else 0.0
        jitter = round(math.sqrt(var_val), 3)

        percentiles_data = LatencyPercentiles(
            p50_ms=p50,
            p90_ms=p90,
            p95_ms=p95,
            p99_ms=p99,
            p99_9_ms=p99_9,
            jitter_std_dev_ms=jitter
        )

        # SLA Audit against P99
        recommended_fallback = None
        if p99 > config.max_inference_budget_ms:
            is_compliant = False
            status = "INFERENCE_LATENCY_SLA_BREACH"
            recommended_fallback = config.fallback_action
            notes = (
                f"INFERENCE SLA BREACH [{config.model_id}]: P99 Latency ({p99:.3f}ms) exceeds "
                f"max budget ({config.max_inference_budget_ms:.3f}ms)! Activating Fallback: '{config.fallback_action}'."
            )
            logger.critical(notes)
        elif p99 > config.warning_threshold_ms:
            is_compliant = True
            status = "INFERENCE_LATENCY_WARNING_NEAR_LIMIT"
            notes = (
                f"INFERENCE WARNING [{config.model_id}]: P99 Latency ({p99:.3f}ms) nears "
                f"max budget ({config.max_inference_budget_ms:.3f}ms). Jitter = {jitter:.3f}ms."
            )
            logger.warning(notes)
        else:
            is_compliant = True
            status = "INFERENCE_LATENCY_NORMAL"
            notes = (
                f"INFERENCE OK [{config.model_id}]: P50={p50:.3f}ms, P99={p99:.3f}ms. "
                f"Within max budget ({config.max_inference_budget_ms:.3f}ms)."
            )
            logger.info(notes)

        return InferenceBudgetReport(
            model_id=config.model_id,
            sample_count=n,
            percentiles=percentiles_data,
            max_inference_budget_ms=config.max_inference_budget_ms,
            is_sla_compliant=is_compliant,
            recommended_fallback_action=recommended_fallback,
            status=status,
            audit_notes=notes
        )
