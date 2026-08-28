"""Tick-to-trade latency *budget allocation* auditing for a trading strategy.

This module answers a narrower question than "how fast is the pipeline?". It answers
*given a total tick-to-trade budget the strategy actually has, is the way that budget
is split across pipeline stages feasible, which stage is closest to blowing its share,
and which stage is arithmetically capable of closing the gap?*

Three things about that question are easy to get wrong, and this module is built
around them.

**A total budget is not the sum of the stage budgets.** The end-to-end budget is set by
the opportunity -- how long the strategy can take before the edge is gone -- and the
per-stage budgets are an *allocation* of it. The two are independent numbers. When the
allocation sums above the total, the pipeline can breach end-to-end with every stage
inside its own budget; when it sums below, there is headroom that no stage owns. A
prior revision of this module *defined* the total as the sum of the stage budgets, so
neither condition was representable. Both are now reported (``unallocated_budget_us``,
``is_overcommitted``).

**Per-stage percentiles do not add, and the error is not conservative.** Sizing each
stage at its own P99 and summing gives the correct total P99 only when the stages are
*comonotonic* -- when they spike together, which is what a GC pause, a scheduler
preemption or an IRQ storm does to a whole pipeline. When the stages instead stall
*independently*, the sum is an **under**-estimate, sometimes a large one: the total sees
the union of the stages' stall events, whose probability is roughly the sum of the
per-stage stall rates, while each stage's own P99 sees only its own. Five stages that
each stall on 1% of traces put ~5% of *totals* into the tail, so the total's P99 sits in
stall territory while every stage's P99 is still on its clean path. A worked case is in
the tests: five stages each stalling on a different 1% of 100 traces give a measured
total P99 of 19.0 us against a sum-of-stage-P99s of 11.0 us. This is the quantile
functional failing to be subadditive over independent, skewed positions -- the same
structure as the standard defaultable-bond counterexample for Value-at-Risk in McNeil,
Frey and Embrechts, *Quantitative Risk Management* (2015).

The consequence for budgeting is direct: **an end-to-end P99 budget must be measured on
the totals, never assembled from per-stage P99s.**
:meth:`StrategyLatencyBudgetDecompositionEngine.profile_batch` reports both, plus the
signed gap between them, and never substitutes one for the other.

**"Jitter" cannot be computed from one trace.** A prior revision reported a
``p99_jitter_us`` field derived from the standard deviation *across the five stages of a
single trade*, scaled by the normal 99% z-score. That is the dispersion between an
ingress hop and a signal computation -- two unrelated quantities measured once each --
not the variation of anything over time. On a two-stage trace of 10 us and 5 us it
reported 14.0 us of "jitter" for a 15 us trade. The field is gone. Tail behaviour now
comes from :meth:`profile_batch` over a batch of traces, by nearest rank, with a
sample-count gate.

Units are **microseconds (us)** throughout -- see the ``Units`` section of ``SKILL.md``.

Requires Python 3.9+ for ``math.nextafter``.
"""

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from numbers import Real
from types import MappingProxyType
from typing import Dict, List, Mapping, Optional, Sequence, Union

logger = logging.getLogger(__name__)

# --- Batch audit statuses --------------------------------------------------
STATUS_HEALTHY = "LATENCY_BUDGET_HEALTHY"
STATUS_BREACH = "LATENCY_BUDGET_BREACH"
STATUS_INSUFFICIENT_SAMPLES = "LATENCY_BUDGET_INSUFFICIENT_SAMPLES"

# The tail percentile a batch approval must be able to resolve. P99 needs 100 traces:
# below that the nearest rank for P99 lands on the last element and the reported "P99"
# is simply the observed maximum.
AUDIT_TAIL_PERCENTILE = 99.0

# A single pipeline stage lasting longer than this (1e9 us is about 16 minutes) is not a
# tick-to-trade measurement; it is a unit error or an uninitialised timestamp. The bound
# also keeps the sum of any realistic batch far inside the float range.
MAX_PLAUSIBLE_STAGE_LATENCY_US = 1e9

# Display rounding only. Every budget comparison in this module runs on the raw value, so
# a 25.0004 us total against a 25.0 us budget is a breach and not a 25.0 us pass.
_DISPLAY_DP = 3


class LatencyBudgetError(ValueError):
    """Base class for every error this module raises.

    Subclasses ``ValueError`` so callers written against the previous revision's bare
    ``ValueError`` handling keep working unchanged.
    """


class LatencyTraceError(LatencyBudgetError):
    """Raised when a measured trace cannot support a meaningful budget audit."""


class LatencyBudgetConfigError(LatencyBudgetError):
    """Raised when a budget allocation cannot produce a usable verdict."""


# --- Percentile helpers ----------------------------------------------------
# Nearest rank, matching HdrHistogram's getValueAtPercentile and the sibling skills
# `latency-monitoring-percentile-based-slas` and
# `network-jitter-impact-on-strategy-performance`. Every reported percentile is a latency
# that was actually observed.


def rank_for_percentile(sample_count: int, percentile: float) -> int:
    """Return the 1-based nearest rank for ``percentile`` over ``sample_count`` samples.

    Uses HdrHistogram's rank rule, ``ceil(percentile / 100 * N)``, clamped to ``[1, N]``.
    The percentile is first nudged down by one ULP, exactly as HdrHistogram does with
    ``Math.nextAfter(percentile, Double.NEGATIVE_INFINITY)``: without it ``99.0 / 100.0``
    and friends can round up across an integer boundary and pin a percentile to the
    maximum at the very sample count that should first resolve it.
    """
    if sample_count <= 0:
        raise LatencyTraceError("sample_count must be positive to compute a rank.")
    if not 0.0 <= percentile <= 100.0:
        raise LatencyTraceError(f"percentile must be within [0, 100], got {percentile}.")
    nudged = math.nextafter(percentile, -math.inf)
    rank = math.ceil((nudged / 100.0) * sample_count)
    return min(max(rank, 1), sample_count)


def percentile_nearest_rank(sorted_samples: Sequence[float], percentile: float) -> float:
    """Return the nearest-rank ``percentile`` of an **ascending-sorted** series.

    Linear interpolation -- the estimator NumPy and Excel use by default -- would blend
    two neighbouring observations, which on a bimodal pipeline (a warm-cache path and a
    stalled path, nothing in between) reports a latency the pipeline never produced.
    """
    return sorted_samples[rank_for_percentile(len(sorted_samples), percentile) - 1]


def is_percentile_resolvable(sample_count: int, percentile: float) -> bool:
    """Return True when ``percentile`` is distinguishable from the observed maximum.

    When the nearest rank lands on the last sample the reported "percentile" is just the
    batch maximum -- the batch contains no rarer event to measure. A "P99" over 40 traces
    says nothing about a 1-in-100 excursion, because none was sampled.
    """
    return rank_for_percentile(sample_count, percentile) < sample_count


def min_samples_for_percentile(percentile: float) -> int:
    """Smallest sample count at which ``percentile`` becomes resolvable (100 for P99).

    Analytically ``1 / (1 - percentile/100)``. The closed form is seeded from the floor
    because ``1 - 99.0/100`` evaluates slightly small in binary floating point; the true
    answer is then settled by :func:`is_percentile_resolvable`, the same predicate the
    audit uses, so the two can never disagree.
    """
    if not 0.0 <= percentile < 100.0:
        raise LatencyTraceError(
            f"percentile must be within [0, 100) to be resolvable, got {percentile}."
        )
    candidate = max(2, int(math.floor(1.0 / (1.0 - percentile / 100.0))))
    while not is_percentile_resolvable(candidate, percentile):
        candidate += 1
    return candidate


class LatencyPipelineStage(str, Enum):
    """The five stages of a tick-to-trade path, in hot-path order.

    Declaration order is load-bearing: it orders ``stage_breakdown`` and breaks
    bottleneck ties towards the earliest stage.
    """

    INGRESS_NETWORK = "INGRESS_NETWORK"           # NIC arrival to application buffer
    MARKET_DATA_DECODE = "MARKET_DATA_DECODE"     # ITCH / SBE / FIX binary parse
    SIGNAL_COMPUTATION = "SIGNAL_COMPUTATION"     # quantitative model evaluation
    PRE_TRADE_RISK = "PRE_TRADE_RISK"             # risk limits and compliance checks
    EGRESS_ORDER_ENCODE = "EGRESS_ORDER_ENCODE"   # order encode and NIC egress


StageKey = Union[LatencyPipelineStage, str]

# Illustrative starting allocation of a 25 us total budget. **No regulator, exchange,
# vendor or standards body publishes per-stage tick-to-trade budgets** -- see
# `references/standards.md`. These exist so the module runs out of the box, not because
# 2/3/10/5/5 describes any real pipeline. Exposed read-only so one engine cannot mutate
# the defaults out from under the next.
DEFAULT_STAGE_BUDGETS_US: Mapping[LatencyPipelineStage, float] = MappingProxyType({
    LatencyPipelineStage.INGRESS_NETWORK: 2.0,
    LatencyPipelineStage.MARKET_DATA_DECODE: 3.0,
    LatencyPipelineStage.SIGNAL_COMPUTATION: 10.0,
    LatencyPipelineStage.PRE_TRADE_RISK: 5.0,
    LatencyPipelineStage.EGRESS_ORDER_ENCODE: 5.0,
})


def _coerce_stage(key: StageKey, context: str) -> LatencyPipelineStage:
    """Accept a :class:`LatencyPipelineStage` or its string value; reject anything else.

    Strings are accepted because agent-generated callers routinely pass
    ``"PRE_TRADE_RISK"`` rather than the enum member. An unrecognised key raises rather
    than being dropped: a typo that is silently ignored gives the mistyped stage a zero
    latency and a passing audit.
    """
    if isinstance(key, LatencyPipelineStage):
        return key
    if isinstance(key, str):
        try:
            return LatencyPipelineStage(key)
        except ValueError:
            pass
    raise LatencyBudgetConfigError(
        f"{context}: {key!r} is not a LatencyPipelineStage. "
        f"Expected one of {[s.value for s in LatencyPipelineStage]}."
    )


def _validated_latency(stage: LatencyPipelineStage, value: object) -> float:
    """Return ``value`` as a finite, non-negative microsecond duration, or raise.

    Every rejection here is a case that would otherwise produce a confidently wrong
    report rather than an error:

    * **bool** -- ``True`` is a valid ``Real`` worth 1.0 us. It is never a measurement.
    * **NaN** -- compares ``False`` against every bound, so no stage breaches, the total
      is NaN, and ``NaN <= budget`` is ``False``, which some callers read as "no breach".
    * **Inf** -- poisons the sum and every percentile derived from it.
    * **negative** -- physically impossible; a negative stage subtracts from the total
      and can offset a real breach into an apparent pass.
    * **implausibly large** -- a unit error or an uninitialised timestamp difference.
    """
    if isinstance(value, bool) or not isinstance(value, Real):
        raise LatencyTraceError(
            f"{stage.value}: latency must be a real number, got {value!r}."
        )
    latency = float(value)
    if not math.isfinite(latency):
        raise LatencyTraceError(f"{stage.value}: latency must be finite, got {latency!r}.")
    if latency < 0.0:
        raise LatencyTraceError(
            f"{stage.value}: latency must be non-negative, got {latency} us. "
            "A negative stage duration means the two timestamps came from different "
            "clock domains or ran backwards; the whole trace is unusable."
        )
    if latency > MAX_PLAUSIBLE_STAGE_LATENCY_US:
        raise LatencyTraceError(
            f"{stage.value}: latency {latency} us exceeds the plausibility bound of "
            f"{MAX_PLAUSIBLE_STAGE_LATENCY_US} us. Check the units -- this module "
            "expects microseconds."
        )
    return latency


@dataclass(frozen=True)
class StageLatencyMeasurement:
    """One stage's measured latency against its allocated share of the budget."""

    stage: LatencyPipelineStage
    latency_us: float
    sla_budget_us: float

    @property
    def excess_us(self) -> float:
        """Measured latency minus allocated budget. Negative means headroom remains."""
        return self.latency_us - self.sla_budget_us

    @property
    def budget_utilization(self) -> float:
        """Fraction of the stage's allocated budget consumed. ``> 1.0`` is a breach."""
        return self.latency_us / self.sla_budget_us

    @property
    def is_breached(self) -> bool:
        """True when the stage consumed strictly more than its allocation."""
        return self.latency_us > self.sla_budget_us


@dataclass
class LatencyDecompositionReport:
    """Verdict for a single tick-to-trade trace against the configured allocation."""

    trade_id: str
    total_tick_to_trade_latency_us: float
    total_sla_budget_us: float
    is_within_budget: bool
    stage_breakdown: List[StageLatencyMeasurement]
    primary_bottleneck_stage: LatencyPipelineStage
    breached_stages: List[LatencyPipelineStage]
    audit_notes: str
    allocated_budget_us: float
    unallocated_budget_us: float
    budget_deficit_us: float
    stage_share_of_total: Dict[LatencyPipelineStage, float] = field(default_factory=dict)
    stage_reduction_required_fraction: Dict[LatencyPipelineStage, float] = field(
        default_factory=dict
    )

    @property
    def is_overcommitted(self) -> bool:
        """True when the stage allocations sum above the end-to-end budget."""
        return self.unallocated_budget_us < 0.0


@dataclass
class LatencyBudgetProfile:
    """Batch-level tail verdict over a set of single-trace reports."""

    sample_count: int
    status: str
    total_sla_budget_us: float
    p50_total_us: float
    p95_total_us: float
    p99_total_us: float
    max_total_us: float
    stage_p99_us: Dict[LatencyPipelineStage, float]
    stage_p99_excess_us: Dict[LatencyPipelineStage, float]
    sum_of_stage_p99_us: float
    comonotonic_gap_us: float
    primary_bottleneck_stage: LatencyPipelineStage
    breach_count: int
    breach_rate: float
    is_p99_resolvable: bool
    audit_notes: str


class StrategyLatencyBudgetDecompositionEngine:
    """Audits tick-to-trade traces against an explicit latency budget allocation.

    The engine is configured with two independent numbers: ``total_budget_us`` -- the
    end-to-end budget the strategy actually has -- and ``stage_sla_budgets``, the
    allocation of that budget across the five pipeline stages. Supplying an allocation
    that sums above the total is legal and logged: it means a trace can breach
    end-to-end with every stage inside its own share.
    """

    #: Retained for callers of the previous revision. Read-only; prefer
    #: :data:`DEFAULT_STAGE_BUDGETS_US`.
    DEFAULT_SLAS_US: Mapping[LatencyPipelineStage, float] = DEFAULT_STAGE_BUDGETS_US

    def __init__(
        self,
        stage_sla_budgets: Optional[Mapping[StageKey, float]] = None,
        total_budget_us: Optional[float] = None,
    ) -> None:
        """Configure the allocation.

        Args:
            stage_sla_budgets: Budget in microseconds for **every** stage in
                :class:`LatencyPipelineStage`. A partial map raises rather than inventing
                a default for the missing stages -- the previous revision silently
                assigned 10.0 us to any stage the caller omitted, so a one-stage map
                produced a 42 us total budget where 2 us was intended.
            total_budget_us: End-to-end tick-to-trade budget. Defaults to the sum of the
                stage budgets, i.e. a fully allocated budget with no headroom.
        """
        self.sla_budgets: Dict[LatencyPipelineStage, float] = self._validate_budgets(
            stage_sla_budgets
        )
        self.allocated_budget_us: float = math.fsum(self.sla_budgets.values())

        if total_budget_us is None:
            self.total_budget_us: float = self.allocated_budget_us
        else:
            self.total_budget_us = self._validate_total_budget(total_budget_us)

        self.unallocated_budget_us: float = self.total_budget_us - self.allocated_budget_us
        if self.unallocated_budget_us < 0.0:
            logger.warning(
                "Latency budget is overcommitted: stage allocations sum to %.3fus "
                "against a %.3fus end-to-end budget. A trace can breach end-to-end "
                "with every stage inside its own share.",
                self.allocated_budget_us,
                self.total_budget_us,
            )

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _validate_budgets(
        stage_sla_budgets: Optional[Mapping[StageKey, float]],
    ) -> Dict[LatencyPipelineStage, float]:
        if stage_sla_budgets is None:
            return dict(DEFAULT_STAGE_BUDGETS_US)
        if not isinstance(stage_sla_budgets, Mapping):
            raise LatencyBudgetConfigError(
                "stage_sla_budgets must be a mapping of LatencyPipelineStage to "
                f"microsecond budgets, got {type(stage_sla_budgets).__name__}."
            )

        budgets: Dict[LatencyPipelineStage, float] = {}
        for raw_key, raw_value in stage_sla_budgets.items():
            stage = _coerce_stage(raw_key, "stage_sla_budgets")
            if isinstance(raw_value, bool) or not isinstance(raw_value, Real):
                raise LatencyBudgetConfigError(
                    f"stage_sla_budgets[{stage.value}] must be a real number, "
                    f"got {raw_value!r}."
                )
            budget = float(raw_value)
            if not math.isfinite(budget) or budget <= 0.0:
                raise LatencyBudgetConfigError(
                    f"stage_sla_budgets[{stage.value}] must be finite and positive, "
                    f"got {budget}. A zero or negative allocation makes every "
                    "measurement of that stage a breach."
                )
            budgets[stage] = budget

        missing = [s.value for s in LatencyPipelineStage if s not in budgets]
        if missing:
            raise LatencyBudgetConfigError(
                f"stage_sla_budgets is missing a budget for {missing}. Every stage must "
                "be allocated explicitly; an omitted stage would otherwise be given an "
                "invented budget."
            )
        return budgets

    @staticmethod
    def _validate_total_budget(total_budget_us: float) -> float:
        if isinstance(total_budget_us, bool) or not isinstance(total_budget_us, Real):
            raise LatencyBudgetConfigError(
                f"total_budget_us must be a real number, got {total_budget_us!r}."
            )
        total = float(total_budget_us)
        if not math.isfinite(total) or total <= 0.0:
            raise LatencyBudgetConfigError(
                f"total_budget_us must be finite and positive, got {total}."
            )
        return total

    # --- single-trace audit ------------------------------------------------
    def decompose_tick_to_trade(
        self,
        trade_id: str,
        stage_latencies: Mapping[StageKey, float],
    ) -> LatencyDecompositionReport:
        """Decompose one tick-to-trade trace and audit it against the allocation.

        Every stage must be present. A missing stage is an instrumentation defect, not a
        zero-latency stage: the previous revision defaulted absent stages to 0.0 us, so a
        trace carrying one of five measurements reported a 9 us total against a 25 us
        budget and passed.

        Raises:
            LatencyTraceError: on a missing stage or an unusable measurement.
            LatencyBudgetConfigError: on an unrecognised stage key.
        """
        if not isinstance(stage_latencies, Mapping):
            raise LatencyTraceError(
                "stage_latencies must be a mapping of LatencyPipelineStage to "
                f"microsecond latencies, got {type(stage_latencies).__name__}."
            )

        measured: Dict[LatencyPipelineStage, float] = {}
        for raw_key, raw_value in stage_latencies.items():
            stage = _coerce_stage(raw_key, f"stage_latencies for trade {trade_id!r}")
            if stage in measured:
                raise LatencyTraceError(
                    f"trade {trade_id!r}: stage {stage.value} supplied more than once."
                )
            measured[stage] = _validated_latency(stage, raw_value)

        missing = [s.value for s in LatencyPipelineStage if s not in measured]
        if missing:
            raise LatencyTraceError(
                f"trade {trade_id!r}: no measurement for {missing}. An incomplete trace "
                "cannot be audited against an end-to-end budget -- the absent stages "
                "would read as zero latency and the trace would pass."
            )

        stage_measurements: List[StageLatencyMeasurement] = [
            StageLatencyMeasurement(
                stage=stage,
                latency_us=measured[stage],
                sla_budget_us=self.sla_budgets[stage],
            )
            for stage in LatencyPipelineStage
        ]

        # fsum keeps the five-term total exact, so a trace landing on the budget is not
        # pushed over it by accumulated float error.
        total_latency = math.fsum(m.latency_us for m in stage_measurements)
        breached_stages = [m.stage for m in stage_measurements if m.is_breached]

        # One definition of "bottleneck", used whether or not anything breached: the
        # stage furthest over its allocation, or -- when all are inside -- the one with
        # the least headroom. The previous revision switched to *greatest absolute
        # latency* when nothing breached, which simply names whichever stage was given
        # the largest budget. Ties break towards the earliest stage in hot-path order,
        # matching `colocation-latency-budget-accounting`.
        bottleneck_measurement = max(stage_measurements, key=lambda m: m.excess_us)
        bottleneck = bottleneck_measurement.stage

        is_within_budget = total_latency <= self.total_budget_us
        deficit = max(0.0, total_latency - self.total_budget_us)

        stage_share = {
            m.stage: (m.latency_us / total_latency if total_latency > 0.0 else 0.0)
            for m in stage_measurements
        }

        # Amdahl bound on where optimisation effort can pay: the most a single stage can
        # remove from the total is its own duration, so a stage must give up
        # `deficit / latency` of itself to bring the trace back inside budget. A fraction
        # above 1.0 means that stage cannot close the gap even if deleted entirely.
        reduction_required: Dict[LatencyPipelineStage, float] = {}
        if deficit > 0.0:
            reduction_required = {
                m.stage: (deficit / m.latency_us if m.latency_us > 0.0 else math.inf)
                for m in stage_measurements
            }

        status_str = "WITHIN_BUDGET" if is_within_budget else "BUDGET_BREACH"
        notes = (
            f"LATENCY DECOMPOSITION [{status_str}] ({trade_id}): total "
            f"{total_latency:.3f}us against a {self.total_budget_us:.3f}us budget "
            f"({self.allocated_budget_us:.3f}us allocated, "
            f"{self.unallocated_budget_us:+.3f}us unallocated); bottleneck "
            f"{bottleneck.value} at {bottleneck_measurement.excess_us:+.3f}us against "
            f"its share; breached stages {[b.value for b in breached_stages]}."
        )
        if is_within_budget:
            logger.info(notes)
        else:
            logger.warning(notes)

        return LatencyDecompositionReport(
            trade_id=trade_id,
            total_tick_to_trade_latency_us=round(total_latency, _DISPLAY_DP),
            total_sla_budget_us=round(self.total_budget_us, _DISPLAY_DP),
            is_within_budget=is_within_budget,
            stage_breakdown=stage_measurements,
            primary_bottleneck_stage=bottleneck,
            breached_stages=breached_stages,
            audit_notes=notes,
            allocated_budget_us=round(self.allocated_budget_us, _DISPLAY_DP),
            unallocated_budget_us=round(self.unallocated_budget_us, _DISPLAY_DP),
            budget_deficit_us=round(deficit, _DISPLAY_DP),
            stage_share_of_total=stage_share,
            stage_reduction_required_fraction=reduction_required,
        )

    # --- batch audit -------------------------------------------------------
    def profile_batch(
        self,
        reports: Sequence[LatencyDecompositionReport],
    ) -> LatencyBudgetProfile:
        """Summarise a batch of single-trace reports at the tail.

        Reports the measured P99 of the per-trace totals *and* the sum of the per-stage
        P99s. The second is what stage-by-stage budgeting would predict; only the first
        is a measurement of the end-to-end tail. ``comonotonic_gap_us`` is their signed
        difference (``sum_of_stage_p99_us - p99_total_us``) and is a diagnostic, not a
        bound:

        * **near zero** -- the stages spike together. Look for one shared cause (a GC
          pause, a scheduler preemption, an IRQ storm), not five separate ones.
        * **positive** -- the stages stall independently *and* each stall is common
          enough for that stage's own P99 to see it, so summing them double-counts
          excursions that rarely coincide. Stage-by-stage budgeting over-provisions.
        * **negative** -- the dangerous case. Stalls are spread thinly enough across
          stages that no single stage's P99 resolves them, while the totals do.
          Stage-by-stage budgeting at P99 would approve a pipeline whose end-to-end P99
          is already over budget. Always read ``p99_total_us``, never the sum.

        A breach is reported at any sample count -- an over-budget trace was genuinely
        observed. An *approval* requires the P99 to be resolvable (at least 100 traces);
        otherwise the status is ``LATENCY_BUDGET_INSUFFICIENT_SAMPLES``. *No breach
        observed* is not *within budget*.
        """
        if not reports:
            raise LatencyTraceError("profile_batch requires at least one report.")

        totals: List[float] = []
        per_stage: Dict[LatencyPipelineStage, List[float]] = {
            s: [] for s in LatencyPipelineStage
        }
        for report in reports:
            if not isinstance(report, LatencyDecompositionReport):
                raise LatencyTraceError(
                    "profile_batch expects LatencyDecompositionReport objects, got "
                    f"{type(report).__name__}."
                )
            if len(report.stage_breakdown) != len(LatencyPipelineStage):
                raise LatencyTraceError(
                    f"trade {report.trade_id!r}: report carries "
                    f"{len(report.stage_breakdown)} stages, expected "
                    f"{len(LatencyPipelineStage)}."
                )
            for measurement in report.stage_breakdown:
                if measurement.sla_budget_us != self.sla_budgets[measurement.stage]:
                    raise LatencyTraceError(
                        f"trade {report.trade_id!r}: stage {measurement.stage.value} was "
                        f"audited against a {measurement.sla_budget_us}us budget but this "
                        f"engine allocates {self.sla_budgets[measurement.stage]}us. "
                        "Profiling reports produced under different allocations would "
                        "make every excess figure meaningless."
                    )
                per_stage[measurement.stage].append(measurement.latency_us)
            totals.append(math.fsum(m.latency_us for m in report.stage_breakdown))

        sample_count = len(totals)
        sorted_totals = sorted(totals)
        p99_total = percentile_nearest_rank(sorted_totals, AUDIT_TAIL_PERCENTILE)

        # Comparisons run on the raw P99s; only the reported fields are rounded.
        raw_stage_p99 = {
            stage: percentile_nearest_rank(sorted(values), AUDIT_TAIL_PERCENTILE)
            for stage, values in per_stage.items()
        }
        stage_p99 = {s: round(v, _DISPLAY_DP) for s, v in raw_stage_p99.items()}
        stage_p99_excess = {
            s: round(v - self.sla_budgets[s], _DISPLAY_DP) for s, v in raw_stage_p99.items()
        }
        sum_of_stage_p99 = math.fsum(raw_stage_p99.values())
        bottleneck = max(
            LatencyPipelineStage,
            key=lambda s: raw_stage_p99[s] - self.sla_budgets[s],
        )

        breach_count = sum(1 for total in totals if total > self.total_budget_us)
        resolvable = is_percentile_resolvable(sample_count, AUDIT_TAIL_PERCENTILE)

        if breach_count > 0:
            status = STATUS_BREACH
        elif not resolvable:
            status = STATUS_INSUFFICIENT_SAMPLES
        else:
            status = STATUS_HEALTHY

        notes = (
            f"LATENCY BUDGET PROFILE [{status}] over {sample_count} traces: P99 total "
            f"{p99_total:.3f}us against a {self.total_budget_us:.3f}us budget; sum of "
            f"stage P99s {sum_of_stage_p99:.3f}us (comonotonic bound, gap "
            f"{sum_of_stage_p99 - p99_total:+.3f}us); bottleneck {bottleneck.value}; "
            f"{breach_count}/{sample_count} traces over budget."
        )
        if status == STATUS_HEALTHY:
            logger.info(notes)
        else:
            logger.warning(notes)

        return LatencyBudgetProfile(
            sample_count=sample_count,
            status=status,
            total_sla_budget_us=round(self.total_budget_us, _DISPLAY_DP),
            p50_total_us=round(percentile_nearest_rank(sorted_totals, 50.0), _DISPLAY_DP),
            p95_total_us=round(percentile_nearest_rank(sorted_totals, 95.0), _DISPLAY_DP),
            p99_total_us=round(p99_total, _DISPLAY_DP),
            max_total_us=round(sorted_totals[-1], _DISPLAY_DP),
            stage_p99_us=stage_p99,
            stage_p99_excess_us=stage_p99_excess,
            sum_of_stage_p99_us=round(sum_of_stage_p99, _DISPLAY_DP),
            comonotonic_gap_us=round(sum_of_stage_p99 - p99_total, _DISPLAY_DP),
            primary_bottleneck_stage=bottleneck,
            breach_count=breach_count,
            breach_rate=round(breach_count / sample_count, 4),
            is_p99_resolvable=resolvable,
            audit_notes=notes,
        )
