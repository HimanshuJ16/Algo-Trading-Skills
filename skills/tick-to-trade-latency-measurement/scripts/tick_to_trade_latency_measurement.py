"""
tick-to-trade-latency-measurement: wire-to-wire (T0 -> T5) tick-to-trade latency
aggregation, per-stage share attribution, and tail attribution.

This module is the **aggregation half of the measurement harness**. It consumes
six-timestamp samples captured by hot-path instrumentation and reports:

- the aggregate T2T distribution (mean, P50/P90/P99/P99.9, max, jitter),
- each stage's share of the *mean* T2T budget,
- which stage actually produced the *tail*, and
- an SLA verdict that distinguishes "breached", "compliant" and
  "not measurable from this many samples".

Scope boundary
--------------
This module reads no clock and instruments nothing. It must never be called from
the hot path: computing percentiles allocates, and allocation on the critical path
is the jitter this skill exists to find. Capture into a pre-allocated ring buffer,
drain off-path, then call this.

Neighbouring skills, so that nothing here duplicates them:

- ``colocation-latency-budget-accounting`` -- per-phase SLA *budget* audit with a
  named bottleneck phase, in integer nanoseconds. Use it when every phase has its
  own budget. This module needs no per-stage budget: it attributes shares of the
  *measured* total.
- ``strategy-latency-budget-decomposition`` -- how to derive and allocate the
  budget in the first place.
- ``latency-monitoring-percentile-based-slas`` -- coordinated-omission correction
  and cross-node fleet pooling for a single flat sample series.
- ``hardware-timestamping-vs-software-timestamping-accuracy`` -- obtaining T0/T5
  and converting the NIC clock domain to the host timebase.

Units
-----
**Input timestamps are integer nanoseconds. Every reported latency is a float in
microseconds (us).** ``colocation-latency-budget-accounting`` reports nanoseconds;
a value moved between the two without conversion is wrong by 1,000x and neither
module can detect it.

Timestamps must be ``int``. IEEE-754 binary64 has a 53-bit significand, so at an
epoch magnitude of ~1.7e18 ns the representable spacing is 256 ns -- coarser than
most of the stages being measured. A ``float`` timestamp is rejected rather than
silently rounded.

The clock-domain hazard
-----------------------
T0 and T5 are NIC hardware timestamps taken in the adapter's PTP hardware clock
(PHC) domain. T1..T4 come from an in-host counter (``CLOCK_MONOTONIC_RAW`` or a
calibrated invariant TSC). The Linux kernel does **not** convert hardware
timestamps to system time -- it exposes the NIC clock as a PTP clock source "to
allow time conversion in userspace". Subtracting across the two domains without
that conversion yields an offset, not a duration.

Monotonicity validation catches only the case where the offset is large enough to
run the timestamps backwards. A *constant, small* inter-domain offset produces
positive, plausible, and entirely wrong NIC_INGRESS and NIC_EGRESS deltas, and no
check in this module can detect it. Convert to one timebase before building a
sample, and set ``SLAConfig.timestamp_uncertainty_us`` so stages measured below
the noise floor are flagged rather than believed.

Limitations (documented, deliberate)
------------------------------------
- **Per-stage percentiles are not additive.** ``StageBreakdown.p99_us`` is that
  stage ranked independently; the sum of per-stage P99s is not the T2T P99 and the
  error is not signed conservatively. Use ``TailAttribution`` -- which conditions
  on the samples that were actually slow -- to attribute a tail, never the sum.
- **``percentage_of_total`` decomposes the mean only.** Means are additive, so the
  shares sum to 100%. That says nothing about the tail.
- **No coordinated-omission correction.** These samples are event-driven (one per
  tick that produced an order), not paced by a fixed-cadence sampler, so there is
  no expected interval to correct against. A pipeline that *drops* ticks under load
  omits its own worst samples and this module cannot see that; count drops in the
  feed handler.
- **No published SLA is encoded.** The ``SLAConfig`` defaults are engineering
  starting points. See ``references/standards.md``.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "LatencyStage",
    "LatencyError",
    "LatencySample",
    "StageBreakdown",
    "TailStageAttribution",
    "TailAttribution",
    "SLAConfig",
    "LatencySummary",
    "TickToTradeLatencyEngine",
    "STAGE_ORDER",
    "PERCENTILE_NEAREST_RANK",
    "PERCENTILE_LINEAR",
    "STATUS_APPROVED",
    "STATUS_BREACHED",
    "STATUS_INSUFFICIENT_SAMPLES",
    "STATUS_NOT_AUDITED",
    "rank_for_percentile",
    "is_percentile_resolvable",
    "min_samples_for_percentile",
]

#: HdrHistogram-compatible nearest rank. Every reported figure is a latency that
#: was actually observed.
PERCENTILE_NEAREST_RANK = "NEAREST_RANK"

#: Linear interpolation, matching NumPy's default and Excel's PERCENTILE. Blends
#: neighbouring observations and can report a latency the system never produced.
PERCENTILE_LINEAR = "LINEAR_INTERPOLATION"

PERCENTILE_METHODS: Tuple[str, ...] = (PERCENTILE_NEAREST_RANK, PERCENTILE_LINEAR)

#: An over-budget latency was observed. Reportable at any sample count.
STATUS_BREACHED = "T2T_SLA_BREACH"

#: Nothing breached and every audited percentile was resolvable.
STATUS_APPROVED = "T2T_SLA_COMPLIANCE_APPROVED"

#: Nothing breached, but the sample count cannot resolve an audited percentile.
#: "No breach observed" is not "compliant".
STATUS_INSUFFICIENT_SAMPLES = "T2T_INSUFFICIENT_SAMPLES_FOR_SLA"

#: No ``SLAConfig`` was supplied, so no audit ran. Distinct from an approval: a
#: report that was never audited must not read as one that passed.
STATUS_NOT_AUDITED = "T2T_NOT_AUDITED"

#: Percentile used to define the tail set for :class:`TailAttribution`.
DEFAULT_TAIL_PERCENTILE = 99.0

#: Rejects timestamps that are not plausible nanosecond counts (~317 years of ns).
MAX_PLAUSIBLE_TIMESTAMP_NS = 10 ** 19


class LatencyStage(Enum):
    """The five stages of a wire-to-wire tick-to-trade pipeline.

    Boundaries are the six capture points T0..T5; each stage is the interval
    between two adjacent ones. The stage names below are the *authoritative*
    mapping -- revision 1.1.0 of this module shifted every label by one position
    and never computed :attr:`NIC_EGRESS` at all.
    """

    NIC_INGRESS = "NIC_INGRESS"                  # T0 -> T1: NIC hardware RX timestamp to user-space read
    DECODER_PARSING = "DECODER_PARSING"          # T1 -> T2: protocol decode and book update
    STRATEGY_EVALUATION = "STRATEGY_EVALUATION"  # T2 -> T3: signal calculation and pre-trade risk check
    ORDER_SERIALIZATION = "ORDER_SERIALIZATION"  # T3 -> T4: FIX/OUCH/BOE encoding to socket write
    NIC_EGRESS = "NIC_EGRESS"                    # T4 -> T5: socket write to NIC hardware TX timestamp


#: Hot-path order. Iterate this so report ordering is stable and matches the wire.
STAGE_ORDER: Tuple[LatencyStage, ...] = (
    LatencyStage.NIC_INGRESS,
    LatencyStage.DECODER_PARSING,
    LatencyStage.STRATEGY_EVALUATION,
    LatencyStage.ORDER_SERIALIZATION,
    LatencyStage.NIC_EGRESS,
)


class LatencyError(ValueError):
    """Raised when a sample or sample set cannot support a meaningful measurement.

    Subclasses ``ValueError`` so callers written against a bare ``ValueError``
    keep working.
    """


def _require_timestamp_ns(value: object, name: str, sample_id: str) -> int:
    """Validate one nanosecond timestamp.

    ``bool`` is rejected explicitly: it is a subclass of ``int``, so ``True``
    would otherwise be accepted as the timestamp ``1``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise LatencyError(
            f"Sample {sample_id!r}: {name} must be an integer nanosecond timestamp, "
            f"got {type(value).__name__} ({value!r}). Floats are rejected because "
            f"binary64 spacing at epoch-scale nanoseconds is 256 ns."
        )
    if value < 0:
        raise LatencyError(f"Sample {sample_id!r}: {name} must be non-negative, got {value}.")
    if value > MAX_PLAUSIBLE_TIMESTAMP_NS:
        raise LatencyError(
            f"Sample {sample_id!r}: {name} = {value} is not a plausible nanosecond "
            f"timestamp (limit {MAX_PLAUSIBLE_TIMESTAMP_NS}). Check for a value in the "
            f"wrong unit pasted into a nanosecond field."
        )
    return value


def rank_for_percentile(sample_count: int, percentile: float) -> int:
    """1-based nearest rank for ``percentile`` over ``sample_count`` samples.

    HdrHistogram's rule, ``ceil(percentile / 100 * N)``, clamped to ``[1, N]``.
    The percentile is first nudged down by one ULP exactly as HdrHistogram does
    with ``Math.nextAfter(percentile, Double.NEGATIVE_INFINITY)``: without it,
    ``99.9 / 100.0`` is ``0.9990000000000001`` in binary64, so
    ``ceil(0.999... * 1000)`` evaluates to 1000 rather than 999 and P99.9 would be
    pinned to the maximum at exactly the sample count that should first resolve
    it. The nudge is inert for ranks that are already exact.
    """
    if sample_count <= 0:
        raise LatencyError("sample_count must be positive to compute a rank.")
    if not 0.0 <= percentile <= 100.0:
        raise LatencyError(f"percentile must be within [0, 100], got {percentile}.")
    nudged = math.nextafter(percentile, -math.inf)
    rank = math.ceil((nudged / 100.0) * sample_count)
    return min(max(rank, 1), sample_count)


def is_percentile_resolvable(sample_count: int, percentile: float) -> bool:
    """True when ``percentile`` is distinguishable from the observed maximum.

    When the nearest rank lands on the last sample, the reported "percentile" is
    simply the maximum: the window contains no rarer event to measure. A "P99.9"
    over 200 samples carries no information about a 1-in-1000 event.
    """
    return rank_for_percentile(sample_count, percentile) < sample_count


def min_samples_for_percentile(percentile: float) -> int:
    """Smallest sample count at which ``percentile`` becomes resolvable.

    Analytically ``1 / (1 - percentile/100)`` -- 100 for P99, 1,000 for P99.9. The
    closed form only seeds the search, from the *floor*, because ``1 - 99.9/100``
    evaluates slightly small in binary floating point. The answer is then settled
    by :func:`is_percentile_resolvable`, the same predicate the audit uses, so the
    two can never disagree.
    """
    if not 0.0 <= percentile < 100.0:
        raise LatencyError(
            f"percentile must be within [0, 100) to be resolvable, got {percentile}."
        )
    candidate = max(2, int(math.floor(1.0 / (1.0 - percentile / 100.0))))
    while not is_percentile_resolvable(candidate, percentile):
        candidate += 1
    return candidate


@dataclass
class LatencySample:
    """One wire-to-wire tick-to-trade observation, as six integer-ns capture points.

    All six timestamps must be on **one** timebase; see the module docstring on
    the NIC PHC / host counter clock-domain hazard.

    .. warning::
       Revision 1.1.0 carried only five timestamps and had no T1 (socket read).
       ``socket_read_ns`` is new and required, and the four stage deltas that
       revision computed were each labelled with the *following* stage's name.
       Callers must supply T1 and re-read any stored stage labels.
    """

    sample_id: str
    symbol: str
    ingress_ns: int      # T0: NIC hardware RX timestamp (PHC domain, converted)
    socket_read_ns: int  # T1: packet visible to user space
    decoded_ns: int      # T2: protocol decoded, book updated
    strategy_ns: int     # T3: signal computed and pre-trade risk passed
    serialized_ns: int   # T4: order encoded, handed to the socket
    egress_ns: int       # T5: NIC hardware TX timestamp (PHC domain, converted)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate identity, timestamp types, and non-decreasing ordering.

        Idempotent, and called automatically on construction so an invalid sample
        cannot exist. A stage of exactly 0 ns is accepted -- it means the timer
        could not resolve the stage, not that the stage was free -- but a negative
        stage is rejected outright: it proves the two capture points came from
        clocks that disagree, which makes the *positive* stages in the same sample
        wrong by an unknown amount too.
        """
        if not isinstance(self.sample_id, str) or not self.sample_id.strip():
            raise LatencyError("sample_id must be a non-empty string.")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise LatencyError(f"Sample {self.sample_id!r}: symbol must be a non-empty string.")

        ordered = (
            ("ingress_ns", self.ingress_ns),
            ("socket_read_ns", self.socket_read_ns),
            ("decoded_ns", self.decoded_ns),
            ("strategy_ns", self.strategy_ns),
            ("serialized_ns", self.serialized_ns),
            ("egress_ns", self.egress_ns),
        )
        for name, value in ordered:
            _require_timestamp_ns(value, name, self.sample_id)

        for (prev_name, prev_value), (next_name, next_value) in zip(ordered, ordered[1:]):
            if next_value < prev_value:
                raise LatencyError(
                    f"Non-monotonic timestamps in sample {self.sample_id!r}: "
                    f"{next_name}={next_value} < {prev_name}={prev_value}. This is a "
                    f"clock-domain or instrumentation defect, not a fast execution; "
                    f"quarantine and count the sample rather than clamping it."
                )

    def stage_deltas_ns(self) -> Dict[LatencyStage, int]:
        """Per-stage durations in nanoseconds, keyed by the stage that produced them."""
        return {
            LatencyStage.NIC_INGRESS: self.socket_read_ns - self.ingress_ns,
            LatencyStage.DECODER_PARSING: self.decoded_ns - self.socket_read_ns,
            LatencyStage.STRATEGY_EVALUATION: self.strategy_ns - self.decoded_ns,
            LatencyStage.ORDER_SERIALIZATION: self.serialized_ns - self.strategy_ns,
            LatencyStage.NIC_EGRESS: self.egress_ns - self.serialized_ns,
        }

    @property
    def total_t2t_ns(self) -> int:
        """Total wire-to-wire tick-to-trade latency in nanoseconds (T5 - T0)."""
        return self.egress_ns - self.ingress_ns

    @property
    def total_t2t_us(self) -> float:
        """Total wire-to-wire tick-to-trade latency in microseconds."""
        return self.total_t2t_ns / 1_000.0


@dataclass
class StageBreakdown:
    """Aggregate statistics for one stage.

    ``p50_us`` .. ``p999_us`` rank *this stage in isolation*. They do not sum to
    the corresponding T2T percentile -- see :class:`TailAttribution` for the
    additive tail decomposition.
    """

    stage: LatencyStage
    avg_us: float
    p50_us: float
    p90_us: float
    p99_us: float
    p999_us: float
    max_us: float
    std_dev_us: float
    percentage_of_total: float  # Share of the *mean* T2T. Means are additive; percentiles are not.
    below_noise_floor: bool = False  # p50 is under the configured timestamp uncertainty.


@dataclass
class TailStageAttribution:
    """One stage's contribution to the difference between the tail and the body."""

    stage: LatencyStage
    tail_mean_us: float
    body_mean_us: float
    excess_us: float             # tail_mean_us - body_mean_us; signed.
    share_of_excess_pct: float   # Share of the summed *positive* excess.


@dataclass
class TailAttribution:
    """Which stage produced the tail.

    Built by splitting the samples at the nearest-rank T2T ``percentile`` into a
    tail set and a body set, then differencing each stage's mean across the two.
    Because the total is the sum of the stages and the mean is linear, the stage
    excesses sum *exactly* to ``total_excess_us`` -- which is what makes this the
    correct decomposition of a tail, and independently ranked per-stage P99s the
    incorrect one.
    """

    percentile: float
    threshold_us: float
    tail_sample_count: int
    body_sample_count: int
    tail_mean_total_us: float
    body_mean_total_us: float
    total_excess_us: float
    stages: List[TailStageAttribution]
    dominant_stage: Optional[LatencyStage]


@dataclass
class SLAConfig:
    """Latency budgets, in microseconds.

    **These defaults are engineering starting points, not requirements.** No
    regulator, exchange or standards body publishes a tick-to-trade latency SLA;
    see ``references/standards.md``. Calibrate against your own venue, colocation
    and strategy or the verdict means nothing.
    """

    max_p50_us: float = 5.0
    max_p99_us: float = 15.0
    max_p999_us: float = 50.0
    max_tail_us: float = 100.0
    #: Combined uncertainty of the two clocks bracketing a stage, in microseconds.
    #: 0.0 disables the noise-floor annotation. A stage whose P50 falls below this
    #: is not measurable by these clocks, whatever the report says.
    timestamp_uncertainty_us: float = 0.0

    def validate(self) -> None:
        """Reject budgets that cannot express a coherent verdict."""
        bounds = (
            ("max_p50_us", self.max_p50_us),
            ("max_p99_us", self.max_p99_us),
            ("max_p999_us", self.max_p999_us),
            ("max_tail_us", self.max_tail_us),
            ("timestamp_uncertainty_us", self.timestamp_uncertainty_us),
        )
        for name, value in bounds:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise LatencyError(f"SLAConfig.{name} must be numeric, got {type(value).__name__}.")
            if not math.isfinite(value):
                raise LatencyError(f"SLAConfig.{name} must be finite, got {value}.")
            if value < 0:
                raise LatencyError(f"SLAConfig.{name} must be non-negative, got {value}.")
        if not (self.max_p50_us <= self.max_p99_us <= self.max_p999_us <= self.max_tail_us):
            raise LatencyError(
                "SLAConfig budgets must be non-decreasing "
                "(max_p50_us <= max_p99_us <= max_p999_us <= max_tail_us), got "
                f"{self.max_p50_us}, {self.max_p99_us}, {self.max_p999_us}, {self.max_tail_us}. "
                "A tighter budget on a higher percentile can never be satisfied."
            )


@dataclass
class LatencySummary:
    """Aggregate T2T report.

    ``sla_breaches`` being empty is **not** an approval -- read ``sla_status``.
    A short window can prove a breach but cannot prove its absence, and a report
    produced without an ``SLAConfig`` was never audited at all
    (``STATUS_NOT_AUDITED``).
    """

    total_samples: int
    t2t_avg_us: float
    t2t_p50_us: float
    t2t_p90_us: float
    t2t_p99_us: float
    t2t_p999_us: float
    t2t_max_us: float
    t2t_std_dev_us: float
    stage_breakdowns: Dict[LatencyStage, StageBreakdown]
    sla_breaches: List[str] = field(default_factory=list)
    sla_status: str = STATUS_NOT_AUDITED
    resolution_warnings: List[str] = field(default_factory=list)
    tail_attribution: Optional[TailAttribution] = None
    percentile_method: str = PERCENTILE_NEAREST_RANK


class TickToTradeLatencyEngine:
    """Aggregates wire-to-wire tick-to-trade samples into a distribution and a verdict.

    Off the hot path only. See the module docstring for the scope boundary against
    ``colocation-latency-budget-accounting`` and
    ``latency-monitoring-percentile-based-slas``.
    """

    def __init__(
        self,
        percentile_method: str = PERCENTILE_NEAREST_RANK,
        max_samples: Optional[int] = None,
    ) -> None:
        """
        Args:
            percentile_method: ``PERCENTILE_NEAREST_RANK`` (default, HdrHistogram
                semantics, every figure an observed latency) or
                ``PERCENTILE_LINEAR`` for parity with NumPy/Excel tooling.
            max_samples: optional hard cap on retained samples. Exceeding it
                **raises**; the engine never evicts. Evicting to stay under a cap
                -- ring-buffer or otherwise -- discards observations from the
                distribution being measured, and the ones most likely to matter
                are exactly the ones a cap would drop.
        """
        if percentile_method not in PERCENTILE_METHODS:
            raise LatencyError(
                f"percentile_method must be one of {PERCENTILE_METHODS}, "
                f"got {percentile_method!r}."
            )
        if max_samples is not None and (
            isinstance(max_samples, bool) or not isinstance(max_samples, int) or max_samples < 1
        ):
            raise LatencyError(
                f"max_samples must be a positive integer or None, got {max_samples!r}."
            )
        self.percentile_method = percentile_method
        self.max_samples = max_samples
        self.samples: List[LatencySample] = []
        logger.info(
            "Initialized Tick-to-Trade Latency Engine (method=%s, max_samples=%s)",
            percentile_method,
            max_samples,
        )

    def record_sample(self, sample: LatencySample) -> None:
        """Record one validated sample.

        Re-validates rather than trusting construction, so a sample mutated after
        construction cannot enter the set.
        """
        sample.validate()
        if self.max_samples is not None and len(self.samples) >= self.max_samples:
            raise LatencyError(
                f"max_samples ({self.max_samples}) reached. Drain and evaluate the "
                f"current window rather than discarding samples: evicting biases the "
                f"tail this engine exists to measure."
            )
        self.samples.append(sample)

    def reset(self) -> None:
        """Drop all recorded samples, e.g. to start a new measurement window."""
        self.samples.clear()

    @staticmethod
    def calculate_percentile(
        values: Sequence[float],
        p: float,
        method: str = PERCENTILE_NEAREST_RANK,
    ) -> float:
        """Percentile ``p`` (0-100) of ``values``.

        ``PERCENTILE_NEAREST_RANK`` (default) returns an observed value.
        ``PERCENTILE_LINEAR`` interpolates between neighbours and can return a
        latency the system never produced: on a stage that is either 10 us or
        900 us and nothing between, linear reports a median of 455 us.

        Raises on an empty sequence. Revision 1.1.0 returned ``0.0``, which is
        indistinguishable from a genuinely zero latency and reads as a pass.
        """
        if method not in PERCENTILE_METHODS:
            raise LatencyError(f"method must be one of {PERCENTILE_METHODS}, got {method!r}.")
        if not values:
            raise LatencyError("Cannot compute a percentile of an empty sequence.")
        if not 0.0 <= p <= 100.0:
            raise LatencyError(f"percentile must be within [0, 100], got {p}.")

        sorted_vals = sorted(values)
        if method == PERCENTILE_NEAREST_RANK:
            return sorted_vals[rank_for_percentile(len(sorted_vals), p) - 1]

        k = (len(sorted_vals) - 1) * (p / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_vals[int(k)]
        return sorted_vals[int(f)] * (c - k) + sorted_vals[int(c)] * (k - f)

    @staticmethod
    def calculate_std_dev(values: Sequence[float], mean: float) -> float:
        """Sample standard deviation (Bessel-corrected, ``n-1``) -- the jitter figure.

        Returns 0.0 for fewer than two values, where the sample variance is
        undefined. Note that sigma is dominated by single outliers: one stall moves
        it by orders of magnitude while the body of the distribution is unchanged.
        """
        if len(values) < 2:
            return 0.0
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)

    def _percentile(self, values: Sequence[float], p: float) -> float:
        return self.calculate_percentile(values, p, self.percentile_method)

    def _build_tail_attribution(
        self,
        totals_ns: Sequence[int],
        stage_deltas_ns: Dict[LatencyStage, List[int]],
        percentile: float,
    ) -> Optional[TailAttribution]:
        """Difference each stage's mean between the tail set and the body set.

        The split is by **rank**, not by value: the samples ranked at or above
        ``rank_for_percentile`` form the tail and the rest form the body. Splitting
        on ``total >= threshold`` instead would put every sample in the tail
        whenever the threshold value repeats -- which it routinely does on a
        pipeline whose body is flat -- and silently produce no attribution at all.
        Ties on the boundary value are broken by input order.

        Means are taken over the **integer nanosecond** deltas and converted once,
        at the end. Averaging the microsecond floats instead accumulates rounding
        across the window, and on a genuinely flat pipeline that residue is enough
        to hand ``dominant_stage`` to whichever stage rounded upwards -- naming a
        bottleneck that does not exist.

        Returns ``None`` when the body would be empty (nearest rank of 1), because
        there is then nothing to compare the tail against.
        """
        n = len(totals_ns)
        rank = rank_for_percentile(n, percentile)
        if rank < 2:
            return None
        by_latency = sorted(range(n), key=lambda i: totals_ns[i])
        body_idx = by_latency[: rank - 1]
        tail_idx = by_latency[rank - 1 :]

        def _mean_us(vals: Sequence[int], idx: Sequence[int]) -> float:
            return sum(vals[i] for i in idx) / len(idx) / 1_000.0

        attributions: List[TailStageAttribution] = []
        for stage in STAGE_ORDER:
            deltas = stage_deltas_ns[stage]
            tail_mean = _mean_us(deltas, tail_idx)
            body_mean = _mean_us(deltas, body_idx)
            attributions.append(
                TailStageAttribution(
                    stage=stage,
                    tail_mean_us=tail_mean,
                    body_mean_us=body_mean,
                    excess_us=tail_mean - body_mean,
                    share_of_excess_pct=0.0,
                )
            )

        tail_mean_total = _mean_us(totals_ns, tail_idx)
        body_mean_total = _mean_us(totals_ns, body_idx)
        total_excess = tail_mean_total - body_mean_total

        positive_excess = sum(a.excess_us for a in attributions if a.excess_us > 0)
        if positive_excess > 0:
            for attribution in attributions:
                attribution.share_of_excess_pct = (
                    max(attribution.excess_us, 0.0) / positive_excess
                ) * 100.0

        # No excess means the tail is no slower than the body: there is nothing to
        # attribute, and naming the largest of five equal shares would be noise.
        dominant = max(attributions, key=lambda a: a.excess_us)
        dominant_stage = dominant.stage if total_excess > 0 and dominant.excess_us > 0 else None

        return TailAttribution(
            percentile=percentile,
            threshold_us=totals_ns[by_latency[rank - 1]] / 1_000.0,
            tail_sample_count=len(tail_idx),
            body_sample_count=len(body_idx),
            tail_mean_total_us=tail_mean_total,
            body_mean_total_us=body_mean_total,
            total_excess_us=total_excess,
            stages=attributions,
            dominant_stage=dominant_stage,
        )

    def evaluate_latency_distribution(
        self,
        sla_config: Optional[SLAConfig] = None,
        tail_percentile: float = DEFAULT_TAIL_PERCENTILE,
    ) -> LatencySummary:
        """Compute the T2T distribution, per-stage breakdown, tail attribution and verdict.

        Args:
            sla_config: budgets to audit against. When ``None`` no verdict is
                produced: ``sla_status`` is ``STATUS_NOT_AUDITED``, which is
                deliberately distinct from an approval. The distribution and the
                resolution warnings are still reported.
            tail_percentile: the percentile that defines the tail set for
                :class:`TailAttribution`.

        Raises:
            LatencyError: on an empty sample set. There is nothing to summarise,
                and returning zeros would read as a perfectly fast pipeline.
        """
        if not self.samples:
            raise LatencyError("Cannot evaluate latency distribution on empty sample set.")
        if sla_config is not None:
            sla_config.validate()

        n = len(self.samples)
        totals_ns = [s.total_t2t_ns for s in self.samples]
        totals_us = [ns / 1_000.0 for ns in totals_ns]
        per_sample_ns = [s.stage_deltas_ns() for s in self.samples]
        stage_deltas_ns: Dict[LatencyStage, List[int]] = {
            stage: [d[stage] for d in per_sample_ns] for stage in STAGE_ORDER
        }
        stage_deltas: Dict[LatencyStage, List[float]] = {
            stage: [ns / 1_000.0 for ns in stage_deltas_ns[stage]] for stage in STAGE_ORDER
        }

        t2t_avg = sum(totals_ns) / n / 1_000.0
        t2t_p50 = self._percentile(totals_us, 50.0)
        t2t_p90 = self._percentile(totals_us, 90.0)
        t2t_p99 = self._percentile(totals_us, 99.0)
        t2t_p999 = self._percentile(totals_us, 99.9)
        t2t_max = max(totals_us)
        t2t_std = self.calculate_std_dev(totals_us, t2t_avg)

        uncertainty = sla_config.timestamp_uncertainty_us if sla_config else 0.0
        stage_breakdowns: Dict[LatencyStage, StageBreakdown] = {}
        for stage in STAGE_ORDER:
            deltas = stage_deltas[stage]
            # Mean from the exact integer-ns sum, so the shares stay additive.
            stg_avg = sum(stage_deltas_ns[stage]) / n / 1_000.0
            stg_p50 = self._percentile(deltas, 50.0)
            stage_breakdowns[stage] = StageBreakdown(
                stage=stage,
                avg_us=stg_avg,
                p50_us=stg_p50,
                p90_us=self._percentile(deltas, 90.0),
                p99_us=self._percentile(deltas, 99.0),
                p999_us=self._percentile(deltas, 99.9),
                max_us=max(deltas),
                std_dev_us=self.calculate_std_dev(deltas, stg_avg),
                percentage_of_total=(stg_avg / t2t_avg * 100.0) if t2t_avg > 0 else 0.0,
                below_noise_floor=uncertainty > 0.0 and stg_p50 < uncertainty,
            )

        tail_attribution = self._build_tail_attribution(
            totals_ns, stage_deltas_ns, tail_percentile
        )

        # Resolution: a percentile whose nearest rank lands on the last sample is
        # the observed maximum wearing a percentile's name.
        resolution_warnings: List[str] = []
        for label, pct in (("P99", 99.0), ("P99.9", 99.9)):
            if not is_percentile_resolvable(n, pct):
                resolution_warnings.append(
                    f"{label} is not resolvable from {n} sample(s): the nearest rank is the "
                    f"observed maximum. {min_samples_for_percentile(pct):,} samples are required."
                )
        if tail_attribution is None:
            resolution_warnings.append(
                f"Tail attribution unavailable: the P{tail_percentile:g} split leaves no body "
                f"set to compare the tail against ({n} sample(s))."
            )

        # SLA verdict. A breach is reportable at any sample count -- an over-budget
        # latency was genuinely observed. An approval is not.
        sla_breaches: List[str] = []
        sla_status = STATUS_NOT_AUDITED
        if sla_config:
            sla_status = STATUS_APPROVED
            for label, observed, limit in (
                ("P50", t2t_p50, sla_config.max_p50_us),
                ("P99", t2t_p99, sla_config.max_p99_us),
                ("P99.9", t2t_p999, sla_config.max_p999_us),
                ("Max Tail", t2t_max, sla_config.max_tail_us),
            ):
                if observed > limit:
                    sla_breaches.append(
                        f"{label} SLA Breach: {observed:.2f} us > limit {limit:.2f} us"
                    )
            if sla_breaches:
                sla_status = STATUS_BREACHED
            elif resolution_warnings:
                sla_status = STATUS_INSUFFICIENT_SAMPLES

            if sla_status == STATUS_BREACHED:
                logger.warning("T2T SLA breach over %d samples: %s", n, "; ".join(sla_breaches))
            elif sla_status == STATUS_INSUFFICIENT_SAMPLES:
                logger.warning(
                    "T2T SLA not approvable over %d samples: %s",
                    n,
                    "; ".join(resolution_warnings),
                )

        logger.info(
            "T2T evaluation complete (%d samples, %s): P50=%.2fus, P99=%.2fus, Max=%.2fus, status=%s",
            n,
            self.percentile_method,
            t2t_p50,
            t2t_p99,
            t2t_max,
            sla_status,
        )

        return LatencySummary(
            total_samples=n,
            t2t_avg_us=t2t_avg,
            t2t_p50_us=t2t_p50,
            t2t_p90_us=t2t_p90,
            t2t_p99_us=t2t_p99,
            t2t_p999_us=t2t_p999,
            t2t_max_us=t2t_max,
            t2t_std_dev_us=t2t_std,
            stage_breakdowns=stage_breakdowns,
            sla_breaches=sla_breaches,
            sla_status=sla_status,
            resolution_warnings=resolution_warnings,
            tail_attribution=tail_attribution,
            percentile_method=self.percentile_method,
        )
