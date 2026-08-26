"""Model inference latency budgeting and percentile SLA auditing for live trading.

Inference is one slice of a tick-to-trade path, and it is the slice most likely to
have a fat tail: a tree ensemble that is 300 us on a warm cache and 9 ms when the
allocator triggers a cyclic garbage collection has a perfectly acceptable *mean*.
This module audits a completed series of inference latency samples against a budget
expressed as a percentile, and reports whether the model may keep serving, must fall
back, or -- the case most often missed -- has not been measured long enough for the
question to be answerable at all.

Percentiles are reported by the nearest-rank (inverse-CDF) estimator by default,
matching HdrHistogram's ``getValueAtPercentile`` and the sibling skill
``latency-monitoring-percentile-based-slas``. Every reported figure is then a latency
the model actually produced. Linear interpolation -- NumPy's and Excel's default, and
what the previous revision of this module used unconditionally -- blends two
neighbouring observations, so on a model that is either 0.3 ms (warm path) or 2.5 ms
(GC pause) and nothing between, it reports latencies the model never produced. Neither
estimator is uniformly the more conservative one; the reason to prefer nearest rank
here is evidentiary. ``PERCENTILE_LINEAR`` remains available for parity with tooling
that uses it.

Scope boundary: this module reads no clock, loads no model, and calls no inference
runtime. It audits a sample series captured elsewhere, and every guarantee it offers
is about arithmetic over those samples. It does not switch models -- it recommends an
action that the caller's model router is responsible for executing.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --- Percentile estimators -------------------------------------------------
PERCENTILE_NEAREST_RANK = "NEAREST_RANK"
PERCENTILE_LINEAR = "LINEAR_INTERPOLATION"
PERCENTILE_METHODS: Tuple[str, ...] = (PERCENTILE_NEAREST_RANK, PERCENTILE_LINEAR)

# --- Audit statuses --------------------------------------------------------
STATUS_NORMAL = "INFERENCE_LATENCY_NORMAL"
STATUS_WARNING = "INFERENCE_LATENCY_WARNING_NEAR_LIMIT"
STATUS_BREACH = "INFERENCE_LATENCY_SLA_BREACH"
STATUS_INSUFFICIENT_SAMPLES = "INFERENCE_LATENCY_INSUFFICIENT_SAMPLES"

# --- Fallback actions ------------------------------------------------------
# The engine recommends one of these on breach; the caller's model router executes it.
# A firm with its own action names may extend this tuple at import time; the config
# validator reads the module attribute, so an appended name is accepted from then on.
FALLBACK_QUANTIZED_ONNX = "QUANTIZED_ONNX_FALLBACK"
FALLBACK_LINEAR_HEURISTIC = "LINEAR_HEURISTIC_FALLBACK"
FALLBACK_SKIP_SIGNAL = "SKIP_SIGNAL"
FALLBACK_ALERT_ONLY = "ALERT_ONLY"
FALLBACK_ACTIONS: Tuple[str, ...] = (
    FALLBACK_QUANTIZED_ONNX,
    FALLBACK_LINEAR_HEURISTIC,
    FALLBACK_SKIP_SIGNAL,
    FALLBACK_ALERT_ONLY,
)

# The percentile the budget is audited against. P99 is the shipped SLA metric because
# a median hides exactly the stalls this module exists to catch; P99.9 is reported but
# not audited, because resolving it needs 1,000 samples and most inference profiling
# windows are shorter than that.
SLA_PERCENTILE = 99.0

# A latency in milliseconds above this is not a measurement, it is a unit error:
# 1e9 ms is roughly 31.7 years. The bound also keeps the sum of any realistic series
# far inside the float range, so the mean and variance cannot overflow.
MAX_PLAUSIBLE_LATENCY_MS = 1e9

# Report fields are rounded to 0.1 us. Three decimal places -- what the previous
# revision used -- rounds sub-microsecond jitter to 0.000 ms, which on the shipped
# sub-millisecond budgets erases the quantity the report exists to show.
_REPORT_DP = 4


class InferenceBudgetError(ValueError):
    """Base class for inference budget audit failures.

    Subclasses ``ValueError`` so callers written against this module's previous
    ``raise ValueError`` behaviour keep working unchanged.
    """


class InferenceSampleError(InferenceBudgetError):
    """Raised when a latency sample series cannot support a meaningful audit."""


class InferenceBudgetConfigError(InferenceBudgetError):
    """Raised when a budget configuration is internally inconsistent."""


def rank_for_percentile(sample_count: int, percentile: float) -> int:
    """Return the 1-based nearest rank for ``percentile`` over ``sample_count`` samples.

    Uses HdrHistogram's rank rule, ``ceil(percentile / 100 * N)``, clamped to
    ``[1, N]``. The percentile is first nudged down by one ULP, exactly as
    HdrHistogram does with ``Math.nextAfter(percentile, Double.NEGATIVE_INFINITY)``.
    Without the nudge, ``99.9 / 100`` -- stored as ``0.9990000000000001`` -- makes
    ``ceil(0.999... * 1000)`` evaluate to 1000 rather than 999, pinning P99.9 to the
    maximum at exactly the sample count that should first resolve it.
    """
    if sample_count <= 0:
        raise InferenceSampleError("sample_count must be positive to compute a rank.")
    if not 0.0 <= percentile <= 100.0:
        raise InferenceSampleError(f"percentile must be within [0, 100], got {percentile}.")
    nudged = math.nextafter(percentile, -math.inf)
    rank = math.ceil((nudged / 100.0) * sample_count)
    return min(max(rank, 1), sample_count)


def is_percentile_resolvable(sample_count: int, percentile: float) -> bool:
    """Return True when ``percentile`` is distinguishable from the observed maximum.

    When the nearest rank lands on the last sample, the reported "percentile" is
    simply the maximum of the series: the window contains no rarer event to measure.
    A "P99.9" over 100 inference calls says nothing about a 1-in-1000 stall, because
    no 1-in-1000 stall was sampled.
    """
    return rank_for_percentile(sample_count, percentile) < sample_count


def min_samples_for_percentile(percentile: float) -> int:
    """Smallest sample count at which ``percentile`` becomes resolvable.

    Analytically ``1 / (1 - percentile/100)`` -- 100 samples for P99, 1,000 for
    P99.9. The closed form is seeded from the floor because ``1 - 99.9/100``
    evaluates slightly small in binary floating point; the exact answer is then
    settled by :func:`is_percentile_resolvable`, the same predicate the audit uses,
    so the two can never disagree.
    """
    if not 0.0 <= percentile < 100.0:
        raise InferenceSampleError(
            f"percentile must be within [0, 100) to be resolvable, got {percentile}."
        )
    candidate = max(2, int(math.floor(1.0 / (1.0 - percentile / 100.0))))
    while not is_percentile_resolvable(candidate, percentile):
        candidate += 1
    return candidate


def validate_inference_samples(sample_latencies_ms: Sequence[float]) -> List[float]:
    """Return the samples as floats, or raise if the series cannot be audited.

    Rejects -- rather than filters -- four input classes, because each one produces a
    confidently wrong report instead of an error:

    * **NaN/Inf** -- a NaN compares ``False`` against every bound, so ``sorted()``
      silently leaves the list unordered *and* ``NaN > budget`` is ``False`` for every
      budget. Unchecked, a corrupted series reads as a passing audit.
    * **Negative** -- an elapsed inference time cannot be negative. It means the two
      timestamps came from a non-monotonic clock that was stepped mid-measurement, so
      the positive samples in the same window are wrong by an unknown amount too.
    * **Booleans and non-numerics** -- ``True`` is arithmetically 1, so a series of
      booleans produces percentiles rather than an error.
    * **Absurd magnitudes** -- above :data:`MAX_PLAUSIBLE_LATENCY_MS` the value is a
      unit error, not a latency.
    """
    if sample_latencies_ms is None:
        raise InferenceSampleError("Sample latencies array cannot be None.")

    validated: List[float] = []
    for index, raw in enumerate(sample_latencies_ms):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise InferenceSampleError(
                f"Sample {index} is {raw!r} ({type(raw).__name__}); "
                "inference latencies must be real numbers in milliseconds."
            )
        value = float(raw)
        if not math.isfinite(value):
            raise InferenceSampleError(
                f"Sample {index} is {value}; NaN and infinity break both sorting and "
                "budget comparison, so the series is rejected rather than filtered."
            )
        if value < 0.0:
            raise InferenceSampleError(
                f"Sample {index} is {value} ms. A negative inference duration means the "
                "measuring clock was stepped mid-measurement; the whole window is "
                "unusable, not just the negative samples. Measure with "
                "time.perf_counter_ns()."
            )
        if value > MAX_PLAUSIBLE_LATENCY_MS:
            raise InferenceSampleError(
                f"Sample {index} is {value} ms, above the {MAX_PLAUSIBLE_LATENCY_MS} ms "
                "plausibility bound. This is a unit error, not a latency."
            )
        validated.append(value)

    if not validated:
        raise InferenceSampleError("Sample latencies array cannot be empty.")
    return validated


def _percentile(sorted_samples: Sequence[float], percentile: float, method: str) -> float:
    """Percentile of an ascending-sorted series, by ``method``. No rounding."""
    n = len(sorted_samples)
    if method == PERCENTILE_LINEAR:
        if n == 1:
            return sorted_samples[0]
        k = (n - 1) * (percentile / 100.0)
        floor_k = math.floor(k)
        ceil_k = math.ceil(k)
        if floor_k == ceil_k:
            return sorted_samples[int(k)]
        return (
            sorted_samples[int(floor_k)] * (ceil_k - k)
            + sorted_samples[int(ceil_k)] * (k - floor_k)
        )
    return sorted_samples[rank_for_percentile(n, percentile) - 1]


@dataclass
class InferenceBudgetConfig:
    """One model's inference latency budget.

    ``max_inference_budget_ms`` and ``warning_threshold_ms`` are the *inference* slice
    of a tick-to-trade budget, not the whole of it. The shipped 1.0 ms / 0.8 ms
    defaults are engineering starting points with no published authority behind them
    -- derive yours from the end-to-end budget the strategy actually has.
    """

    model_id: str
    max_inference_budget_ms: float = 1.0     # Hard SLA ceiling for inference (1.0 ms)
    warning_threshold_ms: float = 0.8        # Early warning limit (0.8 ms)
    fallback_action: str = FALLBACK_QUANTIZED_ONNX
    percentile_method: str = PERCENTILE_NEAREST_RANK
    # Measured P99 of the fallback model on the same hardware, if it has been
    # profiled. Supplying it lets the engine check that the fallback is actually
    # faster and actually fits the budget -- INT8 quantization is not faster on
    # hardware without int8 instruction support (see references/standards.md).
    fallback_profiled_p99_ms: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.model_id or not str(self.model_id).strip():
            raise InferenceBudgetConfigError("model_id must be a non-empty identifier.")
        for name in ("max_inference_budget_ms", "warning_threshold_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InferenceBudgetConfigError(f"{name} must be a real number, got {value!r}.")
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise InferenceBudgetConfigError(
                    f"{name} must be a positive finite number of milliseconds, got {value}."
                )
        if self.warning_threshold_ms > self.max_inference_budget_ms:
            raise InferenceBudgetConfigError(
                f"warning_threshold_ms ({self.warning_threshold_ms}) exceeds "
                f"max_inference_budget_ms ({self.max_inference_budget_ms}); the warning "
                "band would be unreachable and every breach would arrive unannounced."
            )
        if self.fallback_action not in FALLBACK_ACTIONS:
            raise InferenceBudgetConfigError(
                f"fallback_action {self.fallback_action!r} is not one of {FALLBACK_ACTIONS}. "
                "An unrecognised action is silently ignored by the model router, leaving "
                "an over-budget model serving live signals."
            )
        if self.percentile_method not in PERCENTILE_METHODS:
            raise InferenceBudgetConfigError(
                f"percentile_method {self.percentile_method!r} is not one of "
                f"{PERCENTILE_METHODS}."
            )
        if self.fallback_profiled_p99_ms is not None:
            profiled = self.fallback_profiled_p99_ms
            if (
                isinstance(profiled, bool)
                or not isinstance(profiled, (int, float))
                or not math.isfinite(float(profiled))
                or float(profiled) < 0.0
            ):
                raise InferenceBudgetConfigError(
                    "fallback_profiled_p99_ms must be a non-negative finite number of "
                    f"milliseconds when supplied, got {profiled!r}."
                )


@dataclass
class LatencyPercentiles:
    """Inference latency distribution over one window. Milliseconds, display-rounded."""

    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    p99_9_ms: float
    jitter_std_dev_ms: float                 # Bessel-corrected sample sigma (ddof=1)
    p25_ms: float = 0.0
    p75_ms: float = 0.0
    jitter_iqr_ms: float = 0.0               # P75 - P25; robust to a single stall


@dataclass
class InferenceBudgetReport:
    """Result of one inference budget audit.

    ``is_sla_compliant`` is the *affirmative*: True only when no breach was observed
    **and** the sample count could resolve P99. ``is_sla_breached`` is the separate,
    positive finding. Both False means the window proved nothing either way -- read
    ``status``.
    """

    model_id: str
    sample_count: int
    percentiles: LatencyPercentiles
    max_inference_budget_ms: float
    is_sla_compliant: bool
    recommended_fallback_action: Optional[str]
    status: str
    audit_notes: str
    is_sla_breached: bool = False
    is_p99_resolvable: bool = True
    is_p99_9_resolvable: bool = False
    min_samples_for_p99: int = 100
    percentile_method: str = PERCENTILE_NEAREST_RANK
    warnings: List[str] = field(default_factory=list)


class ModelInferenceLatencyBudgeterEngine:
    """Audits a model's inference latency samples against a percentile budget.

    Stateless: one instance may audit any number of models, and no audit can influence
    another. Because it holds no history it cannot debounce a fallback decision --
    hysteresis across consecutive windows is the caller's responsibility, and
    ``warning_threshold_ms`` exists to give that hysteresis a band to work in.
    """

    def __init__(self) -> None:
        """No configuration is held on the engine; budgets live on each config."""

    def evaluate_inference_latency_budget(
        self,
        config: InferenceBudgetConfig,
        sample_latencies_ms: Sequence[float],
    ) -> InferenceBudgetReport:
        """Compute the latency distribution and audit P99 against the budget.

        Budget comparisons run on unrounded percentiles; rounding is applied to the
        report fields only. A P99 of 1.00004 ms against a 1.0 ms budget is a breach,
        not a 1.0000 ms pass.
        """
        samples = validate_inference_samples(sample_latencies_ms)
        n = len(samples)
        sorted_samples = sorted(samples)
        method = config.percentile_method

        p25_raw = _percentile(sorted_samples, 25.0, method)
        p50_raw = _percentile(sorted_samples, 50.0, method)
        p75_raw = _percentile(sorted_samples, 75.0, method)
        p90_raw = _percentile(sorted_samples, 90.0, method)
        p95_raw = _percentile(sorted_samples, 95.0, method)
        p99_raw = _percentile(sorted_samples, SLA_PERCENTILE, method)
        p99_9_raw = _percentile(sorted_samples, 99.9, method)

        mean_val = sum(sorted_samples) / n
        var_val = (
            sum((x - mean_val) ** 2 for x in sorted_samples) / (n - 1) if n > 1 else 0.0
        )
        jitter = math.sqrt(var_val)

        percentiles_data = LatencyPercentiles(
            p50_ms=round(p50_raw, _REPORT_DP),
            p90_ms=round(p90_raw, _REPORT_DP),
            p95_ms=round(p95_raw, _REPORT_DP),
            p99_ms=round(p99_raw, _REPORT_DP),
            p99_9_ms=round(p99_9_raw, _REPORT_DP),
            jitter_std_dev_ms=round(jitter, _REPORT_DP),
            p25_ms=round(p25_raw, _REPORT_DP),
            p75_ms=round(p75_raw, _REPORT_DP),
            jitter_iqr_ms=round(p75_raw - p25_raw, _REPORT_DP),
        )

        p99_resolvable = is_percentile_resolvable(n, SLA_PERCENTILE)
        p99_9_resolvable = is_percentile_resolvable(n, 99.9)
        required_n = min_samples_for_percentile(SLA_PERCENTILE)

        warnings: List[str] = []
        if not p99_9_resolvable:
            warnings.append(
                f"P99.9 ({percentiles_data.p99_9_ms} ms) is the observed maximum of "
                f"{n} samples, not a 1-in-1000 event: resolving P99.9 needs "
                f"{min_samples_for_percentile(99.9)} samples."
            )
        if sorted_samples[0] == 0.0:
            warnings.append(
                "At least one sample measured exactly 0.0 ms. The measuring clock is "
                "probably coarser than the inference time -- use time.perf_counter_ns()."
            )

        # --- SLA audit, on unrounded values -------------------------------
        is_breached = p99_raw > config.max_inference_budget_ms
        recommended_fallback: Optional[str] = None

        if is_breached:
            is_compliant = False
            status = STATUS_BREACH
            recommended_fallback = config.fallback_action
            notes = (
                f"INFERENCE SLA BREACH [{config.model_id}]: P99 latency ({p99_raw:.4f} ms) "
                f"exceeds max budget ({config.max_inference_budget_ms:.4f} ms) over {n} "
                f"samples. Recommended fallback: '{config.fallback_action}'."
            )
            warnings.extend(self._audit_fallback_headroom(config, p99_raw))
            logger.critical(notes)
        elif not p99_resolvable:
            is_compliant = False
            status = STATUS_INSUFFICIENT_SAMPLES
            notes = (
                f"INFERENCE SLA NOT MEASURABLE [{config.model_id}]: {n} samples cannot "
                f"resolve P{SLA_PERCENTILE:g} (needs {required_n}). The reported P99 "
                f"({percentiles_data.p99_ms} ms) is the observed maximum. No breach was "
                "observed, which is not the same as compliance."
            )
            if p99_raw > config.warning_threshold_ms:
                warnings.append(
                    f"Observed maximum ({percentiles_data.p99_ms} ms) is already above the "
                    f"warning threshold ({config.warning_threshold_ms:.4f} ms)."
                )
            logger.warning(notes)
        elif p99_raw > config.warning_threshold_ms:
            is_compliant = True
            status = STATUS_WARNING
            notes = (
                f"INFERENCE WARNING [{config.model_id}]: P99 latency ({p99_raw:.4f} ms) is "
                f"within the max budget ({config.max_inference_budget_ms:.4f} ms) but above "
                f"the warning threshold ({config.warning_threshold_ms:.4f} ms). "
                f"Jitter sigma = {percentiles_data.jitter_std_dev_ms} ms, "
                f"IQR = {percentiles_data.jitter_iqr_ms} ms."
            )
            logger.warning(notes)
        else:
            is_compliant = True
            status = STATUS_NORMAL
            notes = (
                f"INFERENCE OK [{config.model_id}]: P50={percentiles_data.p50_ms} ms, "
                f"P99={percentiles_data.p99_ms} ms over {n} samples. Within max budget "
                f"({config.max_inference_budget_ms:.4f} ms)."
            )
            logger.info(notes)

        for warning in warnings:
            logger.warning("INFERENCE AUDIT [%s]: %s", config.model_id, warning)

        return InferenceBudgetReport(
            model_id=config.model_id,
            sample_count=n,
            percentiles=percentiles_data,
            max_inference_budget_ms=config.max_inference_budget_ms,
            is_sla_compliant=is_compliant,
            recommended_fallback_action=recommended_fallback,
            status=status,
            audit_notes=notes,
            is_sla_breached=is_breached,
            is_p99_resolvable=p99_resolvable,
            is_p99_9_resolvable=p99_9_resolvable,
            min_samples_for_p99=required_n,
            percentile_method=method,
            warnings=warnings,
        )

    @staticmethod
    def _audit_fallback_headroom(
        config: InferenceBudgetConfig, breaching_p99_ms: float
    ) -> List[str]:
        """Check that the recommended fallback would actually relieve the budget.

        A fallback is only a remedy if it is measurably faster than the model it
        replaces *and* fits inside the budget. Neither is automatic: ONNX Runtime
        documents that quantization "has overhead (from quantizing and dequantizing),
        so it is not rare to get worse performance on old devices". Recommending a
        fallback that misses the budget is worth saying out loud rather than
        discovering after the switch.
        """
        if config.fallback_action == FALLBACK_ALERT_ONLY:
            return [
                "fallback_action is ALERT_ONLY: the breaching model keeps serving live "
                "signals until a human intervenes."
            ]
        profiled = config.fallback_profiled_p99_ms
        if profiled is None:
            return [
                f"Fallback '{config.fallback_action}' has no profiled P99 "
                "(fallback_profiled_p99_ms is unset), so there is no evidence it is "
                "faster than the model it replaces."
            ]
        messages: List[str] = []
        if profiled >= breaching_p99_ms:
            messages.append(
                f"Fallback '{config.fallback_action}' profiles at {profiled:.4f} ms P99, "
                f"which is not faster than the breaching model ({breaching_p99_ms:.4f} ms). "
                "Switching would not relieve the budget."
            )
        if profiled > config.max_inference_budget_ms:
            messages.append(
                f"Fallback '{config.fallback_action}' profiles at {profiled:.4f} ms P99, "
                f"above the {config.max_inference_budget_ms:.4f} ms budget it is meant to "
                "restore."
            )
        return messages


def summarize_report(report: InferenceBudgetReport) -> str:
    """One-line, log-friendly summary of an audit. Used by tooling, not by the audit."""
    return (
        f"[{report.model_id}] {report.status} n={report.sample_count} "
        f"p50={report.percentiles.p50_ms}ms p99={report.percentiles.p99_ms}ms "
        f"budget={report.max_inference_budget_ms}ms "
        f"fallback={report.recommended_fallback_action or 'NONE'}"
    )
