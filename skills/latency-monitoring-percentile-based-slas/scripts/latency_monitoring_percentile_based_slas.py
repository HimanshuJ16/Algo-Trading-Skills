"""Percentile-based latency SLA auditing for tick-to-trade and order-gateway pipelines.

Percentiles are reported using the nearest-rank (inverse-CDF) estimator by default,
matching HdrHistogram's ``getValueAtPercentile`` semantics: "the largest value that
(100% - percentile) of the overall recorded value entries in the histogram are either
larger than or equivalent to".

Nearest rank always returns a latency that was actually observed. Linear interpolation
-- the estimator NumPy and Excel use by default -- returns a weighted blend of two
neighbouring observations, which on a bimodal latency distribution is a number the
system never produced: for a stage that is either 10 us (fast path) or 900 us (slow
path) and nothing in between, interpolation reports a median of 455 us. That is an
awkward figure to put in front of an auditor asking what the median latency was.

Neither estimator is uniformly the more conservative one -- interpolation reads higher
than nearest rank in some tail shapes and lower in others -- so the reason to prefer
nearest rank here is evidentiary, not directional: every number in the report is a
measurement, and it agrees with the HdrHistogram-based collectors these figures are
usually reconciled against. Linear interpolation stays available via
``PERCENTILE_LINEAR`` for parity with tooling that uses it.
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
STATUS_APPROVED = "SLA_COMPLIANCE_APPROVED"
STATUS_P50_WARNING = "SLA_BREACH_P50_WARNING"
STATUS_P99_WARNING = "SLA_BREACH_P99_WARNING"
STATUS_P999_CRITICAL = "SLA_BREACH_P999_CRITICAL"
STATUS_INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES_FOR_SLA"

# A latency in microseconds larger than this is not a measurement, it is a unit error:
# 1e15 us is roughly 31.7 years. The bound also keeps the sum of any realistic series far
# inside the float range, so the mean and variance cannot overflow.
MAX_PLAUSIBLE_LATENCY_US = 1e15

# Ceiling on the series size a coordinated-omission correction may expand to. The
# correction multiplies each sample by roughly value/interval, so an interval supplied in
# the wrong unit (nanoseconds where microseconds were meant) silently turns a thousand
# samples into tens of millions. Refusing is better than exhausting memory.
MAX_CORRECTED_SAMPLES = 5_000_000


class LatencySampleError(ValueError):
    """Raised when a latency sample series cannot support a meaningful audit.

    Subclasses ``ValueError`` so callers written against the previous
    ``raise ValueError`` on an empty series keep working unchanged.
    """


def rank_for_percentile(sample_count: int, percentile: float) -> int:
    """Return the 1-based nearest rank for ``percentile`` over ``sample_count`` samples.

    Uses HdrHistogram's rank rule, ``ceil(percentile / 100 * N)``, clamped to
    ``[1, N]``. This is the index into the ascending-sorted sample array (minus one
    for 0-based access).

    The percentile is first nudged down by one ULP, exactly as HdrHistogram does with
    ``Math.nextAfter(percentile, Double.NEGATIVE_INFINITY)``. Without it, ``99.9`` --
    which is stored as ``0.9990000000000001`` once divided by 100 -- makes
    ``ceil(0.999... * 1000)`` evaluate to 1000 rather than 999, so P99.9 would be
    pinned to the maximum even with the 1,000 samples that are exactly enough to
    resolve it. The nudge is inert for ranks that are already exact.
    """
    if sample_count <= 0:
        raise LatencySampleError("sample_count must be positive to compute a rank.")
    if not 0.0 <= percentile <= 100.0:
        raise LatencySampleError(f"percentile must be within [0, 100], got {percentile}.")
    nudged = math.nextafter(percentile, -math.inf)
    rank = math.ceil((nudged / 100.0) * sample_count)
    return min(max(rank, 1), sample_count)


def is_percentile_resolvable(sample_count: int, percentile: float) -> bool:
    """Return True when ``percentile`` is distinguishable from the observed maximum.

    When ``rank_for_percentile`` lands on the last sample, the reported "percentile"
    is simply the maximum of the series: the sample count is too small to contain a
    single observation beyond it. A "P99.9" computed from 200 samples carries no
    information about a 1-in-1000 event, because no 1-in-1000 event was sampled.
    """
    return rank_for_percentile(sample_count, percentile) < sample_count


def min_samples_for_percentile(percentile: float) -> int:
    """Smallest sample count at which ``percentile`` becomes resolvable.

    Analytically ``1 / (1 - percentile/100)`` -- 1,000 samples for P99.9, 100 for P99.
    The closed form is only used as a starting point and is deliberately seeded from
    the *floor*, because ``1 - 99.9/100`` evaluates slightly small in binary floating
    point and the exact answer would otherwise be skipped over. The true value is then
    settled by :func:`is_percentile_resolvable`, the same predicate the audit uses, so
    the two can never disagree.
    """
    if not 0.0 <= percentile < 100.0:
        raise LatencySampleError(
            f"percentile must be within [0, 100) to be resolvable, got {percentile}."
        )
    candidate = max(2, int(math.floor(1.0 / (1.0 - percentile / 100.0))))
    while not is_percentile_resolvable(candidate, percentile):
        candidate += 1
    return candidate


def correct_for_coordinated_omission(
    samples: Sequence[float], expected_interval_us: float
) -> List[float]:
    """Re-insert the latency observations a stalled sampler failed to record.

    Implements HdrHistogram's documented correction: for each recorded value larger
    than the expected interval between samples, generate "an additional series of
    decreasingly-smaller (down to the expectedIntervalBetweenValueSamples) value
    records". A measuring loop blocked for 50 ms at a 1 ms cadence recorded one
    50 ms sample where an unblocked loop would have recorded fifty degrading ones;
    without this correction the stall appears as a single outlier instead of the
    sustained tail-latency event it actually was.

    Apply this only when the sampler has a known fixed cadence, and only once per
    data set -- correcting an already-corrected series double-counts the stall.
    """
    if not math.isfinite(expected_interval_us) or expected_interval_us <= 0.0:
        raise LatencySampleError(
            f"expected_interval_us must be a positive finite value, got {expected_interval_us}."
        )

    # Size the result before allocating it. Each value contributes
    # 1 + max(0, floor(value / interval) - 1) records, so an interval given in the wrong
    # unit is caught here rather than by the OOM killer.
    projected = 0
    for value in samples:
        projected += 1 + max(0, int(value // expected_interval_us) - 1)
        if projected > MAX_CORRECTED_SAMPLES:
            raise LatencySampleError(
                f"Coordinated-omission correction at a {expected_interval_us} us interval would "
                f"expand this series beyond {MAX_CORRECTED_SAMPLES:,} samples. The expected "
                "sample interval is almost certainly in the wrong unit -- it must be the "
                "sampler's cadence in microseconds, not nanoseconds and not an observed latency."
            )

    corrected: List[float] = []
    for value in samples:
        corrected.append(value)
        missing = value - expected_interval_us
        while missing >= expected_interval_us:
            corrected.append(missing)
            missing -= expected_interval_us
    return corrected


def pool_latency_samples(series_group: Sequence["LatencySampleSeries"]) -> List[float]:
    """Pool raw samples from several nodes into one series for a fleet-wide percentile.

    The only valid way to obtain a fleet P99 is to compute it over the union of the
    underlying observations. The mean of per-node P99 values is not the fleet P99, and
    understates it whenever load is uneven, because a percentile is a quantile of a
    distribution rather than an additive quantity. Prometheus makes the same point for
    histograms: aggregate the bucket counters, then take the quantile -- never the
    reverse.
    """
    if not series_group:
        raise LatencySampleError("Cannot pool an empty group of latency series.")
    pooled: List[float] = []
    for series in series_group:
        pooled.extend(series.samples_microseconds)
    return pooled


@dataclass
class LatencySampleSeries:
    """One measurement window for a single pipeline stage on a single host.

    ``expected_sample_interval_us`` opts into coordinated-omission correction and must
    equal the sampler's intended cadence, not any observed latency.
    ``clock_uncertainty_us`` is the combined timestamp uncertainty of the two clocks
    that bracket the measured interval; it is reported as a verdict noise floor and
    never silently widens an SLA budget.
    """

    pipeline_stage: str                 # e.g. 'TICK_TO_TRADE', 'ORDER_GATEWAY_ACK'
    samples_microseconds: List[float]   # Latency observations in microseconds
    sla_p50_target_us: float = 50.0     # Tunable engineering default, not a published standard
    sla_p99_target_us: float = 200.0    # Tunable engineering default, not a published standard
    sla_p999_target_us: float = 1000.0  # Tunable engineering default, not a published standard
    percentile_method: str = PERCENTILE_NEAREST_RANK
    expected_sample_interval_us: Optional[float] = None  # None = correction disabled
    clock_uncertainty_us: float = 0.0   # 0.0 = noise-floor check disabled


@dataclass
class LatencySlaReport:
    """Result of one SLA audit. Percentiles are microseconds, rounded for display only."""

    pipeline_stage: str
    total_samples_count: int
    mean_latency_us: float
    min_latency_us: float
    max_latency_us: float
    p25_latency_us: float
    p50_latency_us: float
    p75_latency_us: float
    p90_latency_us: float
    p95_latency_us: float
    p99_latency_us: float
    p999_latency_us: float
    jitter_std_dev_us: float            # Population standard deviation over the window
    jitter_iqr_us: float                # P75 - P25; robust to tail outliers
    is_p50_sla_passed: bool
    is_p99_sla_passed: bool
    is_p999_sla_passed: bool
    is_p99_resolvable: bool
    is_p999_resolvable: bool
    percentile_method: str
    coordinated_omission_corrected: bool
    clock_uncertainty_us: float
    status: str
    audit_notes: str
    warnings: List[str] = field(default_factory=list)


class LatencyPercentileSlaEngine:
    """Audits a latency sample series against percentile-based SLA budgets.

    Stateless: one engine instance may audit any number of series, and no audit can
    influence another. Reported percentiles are conservative by construction -- see
    the module docstring for the estimator's semantics.
    """

    def __init__(self) -> None:
        """No configuration is held on the engine; budgets live on each series."""

    def calculate_percentile(
        self,
        sorted_samples: Sequence[float],
        percentile: float,
        method: str = PERCENTILE_NEAREST_RANK,
    ) -> float:
        """Return the ``percentile`` of an ascending-sorted sample sequence.

        ``PERCENTILE_NEAREST_RANK`` (default) returns the observation at
        ``ceil(percentile/100 * N)`` -- an actually measured latency, matching
        HdrHistogram. ``PERCENTILE_LINEAR`` blends the two neighbouring observations and
        can therefore report a latency that never occurred; see the module docstring for
        why that matters for an SLA audit and why it is not simply a bias in one
        direction.

        The caller is responsible for sorting: this runs once per percentile against a
        single sort performed by :meth:`audit_latency_sla`.
        """
        if method not in PERCENTILE_METHODS:
            raise LatencySampleError(
                f"Unknown percentile method {method!r}. Expected one of {PERCENTILE_METHODS}."
            )
        n = len(sorted_samples)
        if n == 0:
            raise LatencySampleError("Cannot compute a percentile of an empty series.")
        if n == 1:
            return float(sorted_samples[0])

        if method == PERCENTILE_NEAREST_RANK:
            return float(sorted_samples[rank_for_percentile(n, percentile) - 1])

        if not 0.0 <= percentile <= 100.0:
            raise LatencySampleError(f"percentile must be within [0, 100], got {percentile}.")
        idx = (percentile / 100.0) * (n - 1)
        lower_idx = int(idx)
        upper_idx = min(lower_idx + 1, n - 1)
        weight = idx - lower_idx
        lower = float(sorted_samples[lower_idx])
        return lower + weight * (float(sorted_samples[upper_idx]) - lower)

    def _validate_samples(self, series: LatencySampleSeries) -> List[float]:
        """Reject any series whose percentiles would be silently meaningless.

        Three rejections, each guarding a documented silent-failure mode:

        * **Empty** -- nothing to audit.
        * **Non-finite** -- a NaN compares False against every bound, so Python's
          ``sorted`` leaves the list unsorted and every subsequent percentile reads an
          arbitrary position. Worse, ``NaN <= budget`` is False for every budget, which
          folds a corrupted series into a passing verdict.
        * **Negative** -- a duration cannot be negative. It means the start and end
          timestamps came from clocks that disagree, so the positive samples in the same
          window are wrong by an unknown amount too.
        """
        samples = series.samples_microseconds
        if samples is None or len(samples) == 0:
            raise LatencySampleError(
                f"Latency sample series for '{series.pipeline_stage}' cannot be empty."
            )

        validated: List[float] = []
        for position, raw in enumerate(samples):
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise LatencySampleError(
                    f"Latency sample at index {position} is {raw!r}; expected a real number."
                )
            value = float(raw)
            if not math.isfinite(value):
                raise LatencySampleError(
                    f"Latency sample at index {position} is non-finite ({value}). "
                    "NaN/Inf samples corrupt the sort order and silently invalidate every "
                    "percentile in this report; drop or repair them upstream."
                )
            if value < 0.0:
                raise LatencySampleError(
                    f"Latency sample at index {position} is negative ({value} us). A negative "
                    "duration indicates the start and end timestamps came from unsynchronised "
                    "clocks, so no sample in this window can be trusted."
                )
            if value > MAX_PLAUSIBLE_LATENCY_US:
                raise LatencySampleError(
                    f"Latency sample at index {position} is {value} us, beyond the plausible "
                    f"bound of {MAX_PLAUSIBLE_LATENCY_US:g} us (~31.7 years). This is a unit "
                    "error or a corrupted timestamp, not a latency."
                )
            validated.append(value)
        return validated

    @staticmethod
    def _validate_budgets(series: LatencySampleSeries) -> None:
        """Reject SLA budgets that cannot produce a coherent verdict.

        A NaN budget fails every comparison, so the audit reports a permanent breach that
        no amount of tuning clears. Budgets that decrease as the percentile rises are
        equally incoherent: percentiles are non-decreasing by construction, so a P99.9
        budget below the P99 budget guarantees a tail breach on a perfectly healthy
        system.
        """
        budgets = (
            ("sla_p50_target_us", series.sla_p50_target_us),
            ("sla_p99_target_us", series.sla_p99_target_us),
            ("sla_p999_target_us", series.sla_p999_target_us),
        )
        for name, value in budgets:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise LatencySampleError(f"{name} must be a real number, got {value!r}.")
            if not math.isfinite(float(value)):
                raise LatencySampleError(
                    f"{name} is non-finite ({value}); every comparison against it would fail "
                    "and the audit would report a breach that cannot be cleared."
                )
            if float(value) < 0.0:
                raise LatencySampleError(f"{name} must be non-negative, got {value}.")

        for (lower_name, lower), (upper_name, upper) in zip(budgets, budgets[1:]):
            if float(upper) < float(lower):
                raise LatencySampleError(
                    f"{upper_name} ({upper}) is below {lower_name} ({lower}). Percentiles are "
                    "non-decreasing, so this budget guarantees a breach at the higher "
                    "percentile regardless of how the system actually performs."
                )

    def audit_latency_sla(self, series: LatencySampleSeries) -> LatencySlaReport:
        """Compute latency percentiles and audit them against the series' SLA budgets.

        Verdict rules, in the order they are applied:

        1. An **observed** breach is always reported, at any sample count -- a latency
           over budget was genuinely measured.
        2. A P99.9 breach outranks a P99 breach, which outranks a P50 breach.
        3. **Approval requires resolution.** If no breach was found but the sample count
           is too small to resolve an audited percentile, the verdict is
           ``INSUFFICIENT_SAMPLES_FOR_SLA``, never approval. The asymmetry is
           deliberate: a small sample can prove a breach but cannot prove compliance.
        """
        samples = self._validate_samples(series)
        self._validate_budgets(series)

        if series.percentile_method not in PERCENTILE_METHODS:
            raise LatencySampleError(
                f"Unknown percentile method {series.percentile_method!r}. "
                f"Expected one of {PERCENTILE_METHODS}."
            )
        if not math.isfinite(series.clock_uncertainty_us) or series.clock_uncertainty_us < 0.0:
            raise LatencySampleError(
                "clock_uncertainty_us must be a non-negative finite value, "
                f"got {series.clock_uncertainty_us}."
            )

        warnings: List[str] = []
        corrected = series.expected_sample_interval_us is not None
        if corrected:
            recorded_count = len(samples)
            samples = correct_for_coordinated_omission(
                samples, float(series.expected_sample_interval_us)
            )
            warnings.append(
                "Coordinated-omission correction applied at a "
                f"{float(series.expected_sample_interval_us):.1f} us expected sample interval: "
                f"{recorded_count:,} recorded samples expanded to {len(samples):,}. "
                "Do not apply a second correction to this series."
            )

        n = len(samples)
        sorted_samples = sorted(samples)
        method = series.percentile_method

        # Unrounded values drive every SLA comparison; rounding is for display only.
        mean_raw = math.fsum(sorted_samples) / float(n)
        p25_raw = self.calculate_percentile(sorted_samples, 25.0, method)
        p50_raw = self.calculate_percentile(sorted_samples, 50.0, method)
        p75_raw = self.calculate_percentile(sorted_samples, 75.0, method)
        p90_raw = self.calculate_percentile(sorted_samples, 90.0, method)
        p95_raw = self.calculate_percentile(sorted_samples, 95.0, method)
        p99_raw = self.calculate_percentile(sorted_samples, 99.0, method)
        p999_raw = self.calculate_percentile(sorted_samples, 99.9, method)

        # Population standard deviation, taken about the unrounded mean. The window is
        # the whole population of interest, so there is no (n-1) correction, and n == 1
        # yields 0.0 rather than a division by zero.
        variance = math.fsum((x - mean_raw) ** 2 for x in sorted_samples) / float(n)
        jitter_std_raw = math.sqrt(max(variance, 0.0))
        iqr_raw = p75_raw - p25_raw

        is_p50_ok = p50_raw <= series.sla_p50_target_us
        is_p99_ok = p99_raw <= series.sla_p99_target_us
        is_p999_ok = p999_raw <= series.sla_p999_target_us

        p99_resolvable = is_percentile_resolvable(n, 99.0)
        p999_resolvable = is_percentile_resolvable(n, 99.9)
        if not p999_resolvable:
            warnings.append(
                f"P99.9 is not resolvable from {n:,} samples: its nearest rank is the observed "
                "maximum, so the reported value describes the worst sample seen, not a 1-in-1000 "
                f"event. {min_samples_for_percentile(99.9):,} samples are required."
            )
        if not p99_resolvable:
            warnings.append(
                f"P99 is not resolvable from {n:,} samples; "
                f"{min_samples_for_percentile(99.0):,} samples are required."
            )

        if series.clock_uncertainty_us > 0.0:
            for label, value, budget in (
                ("P50", p50_raw, series.sla_p50_target_us),
                ("P99", p99_raw, series.sla_p99_target_us),
                ("P99.9", p999_raw, series.sla_p999_target_us),
            ):
                if abs(value - budget) <= series.clock_uncertainty_us:
                    warnings.append(
                        f"{label} ({value:.2f} us) sits within the "
                        f"{series.clock_uncertainty_us:.2f} us timestamp uncertainty of its "
                        f"{budget:.2f} us budget; this pass/fail verdict is inside the "
                        "measurement noise floor and cannot be relied on."
                    )

        stage = series.pipeline_stage
        if not is_p999_ok:
            status = STATUS_P999_CRITICAL
            notes = (
                f"LATENCY SLA CRITICAL BREACH [{stage}]: P99.9 Tail Latency ({p999_raw:.1f} us) "
                f"exceeds critical SLA limit ({series.sla_p999_target_us:.1f} us)! "
                f"Jitter = {jitter_std_raw:.1f} us."
            )
            logger.critical(notes)
        elif not is_p99_ok:
            status = STATUS_P99_WARNING
            notes = (
                f"LATENCY SLA BREACH [{stage}]: P99 Latency ({p99_raw:.1f} us) "
                f"exceeds SLA limit ({series.sla_p99_target_us:.1f} us)."
            )
            logger.warning(notes)
        elif not is_p50_ok:
            status = STATUS_P50_WARNING
            notes = (
                f"LATENCY SLA BREACH [{stage}]: P50 Median Latency ({p50_raw:.1f} us) "
                f"exceeds SLA limit ({series.sla_p50_target_us:.1f} us). The tail budgets hold, "
                "so this is a whole-distribution shift rather than a tail spike."
            )
            logger.warning(notes)
        elif not (p99_resolvable and p999_resolvable):
            status = STATUS_INSUFFICIENT_SAMPLES
            notes = (
                f"LATENCY SLA UNDETERMINED [{stage}]: N = {n:,} samples is too small to resolve "
                "every audited percentile, so compliance cannot be established. No budget was "
                "observed to be breached, which is not the same as passing."
            )
            logger.warning(notes)
        else:
            status = STATUS_APPROVED
            notes = (
                f"LATENCY SLA APPROVED [{stage}]: N = {n:,} samples. "
                f"P50 = {p50_raw:.1f} us, P90 = {p90_raw:.1f} us, P99 = {p99_raw:.1f} us, "
                f"P99.9 = {p999_raw:.1f} us. Jitter (StdDev) = {jitter_std_raw:.1f} us, "
                f"IQR = {iqr_raw:.1f} us. All audited SLAs passed."
            )
            logger.info(notes)

        for warning in warnings:
            logger.warning("LATENCY AUDIT CAVEAT [%s]: %s", stage, warning)

        return LatencySlaReport(
            pipeline_stage=stage,
            total_samples_count=n,
            mean_latency_us=round(mean_raw, 2),
            min_latency_us=round(sorted_samples[0], 2),
            max_latency_us=round(sorted_samples[-1], 2),
            p25_latency_us=round(p25_raw, 2),
            p50_latency_us=round(p50_raw, 2),
            p75_latency_us=round(p75_raw, 2),
            p90_latency_us=round(p90_raw, 2),
            p95_latency_us=round(p95_raw, 2),
            p99_latency_us=round(p99_raw, 2),
            p999_latency_us=round(p999_raw, 2),
            jitter_std_dev_us=round(jitter_std_raw, 2),
            jitter_iqr_us=round(iqr_raw, 2),
            is_p50_sla_passed=is_p50_ok,
            is_p99_sla_passed=is_p99_ok,
            is_p999_sla_passed=is_p999_ok,
            is_p99_resolvable=p99_resolvable,
            is_p999_resolvable=p999_resolvable,
            percentile_method=method,
            coordinated_omission_corrected=corrected,
            clock_uncertainty_us=series.clock_uncertainty_us,
            status=status,
            audit_notes=notes,
            warnings=warnings,
        )
