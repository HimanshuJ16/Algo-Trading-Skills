"""Per-vendor market data latency decomposition and SLA auditing.

A tick's journey from the matching engine to a strategy's decision loop is stamped
at four points -- exchange, vendor gateway, local NIC, application thread -- and the
three intervals between them are the only place a "the feed got slow" complaint can
actually be pinned to a party. That is what this module computes.

The measurement is harder than the subtraction suggests, because **the four
timestamps do not come from one clock.** They come from four, owned by three
different organisations:

* ``t_exchange_us`` is written by the venue. Its epoch is a venue convention, not a
  universal one: Nasdaq TotalView-ITCH 5.0 carries "nanoseconds since midnight" with
  no date and no timezone in the field, while CME MDP 3.0 carries a sending time in
  nanoseconds since the Unix epoch. Subtracting one from a Unix-epoch timestamp
  without converting it produces a delta of several decades.
* ``t_vendor_us`` is written by the vendor's gateway, on the vendor's clock, in the
  vendor's datacentre.
* ``t_local_nic_us`` is written by the NIC. A raw hardware receive timestamp is taken
  from the NIC's own PTP hardware clock, which is a *different clock* from the host's
  system clock -- Linux deprecated the kernel-side translation between them and
  exposes the PHC to userspace instead, expecting the caller to convert or discipline.
* ``t_app_us`` is written by the application, on the system clock.

The practical consequence is that a segment delta can come out **negative** without
anything on the wire being wrong. That is not a small latency; it is proof that the
two clocks bracketing the segment disagree, which means the *positive* deltas in the
same window are wrong by an unknown amount too. Earlier revisions of this module
clamped negative deltas to zero. A vendor gateway clock running 2 ms ahead then
reported a wire segment of a flawless 0 us, a healthy 80 us end-to-end verdict, and a
decomposition summing to 2,020 us -- three mutually contradictory numbers, no warning.
This module rejects that window instead (``VENDOR_CLOCK_DOMAIN_ERROR``).

Percentile semantics follow ``latency-monitoring-percentile-based-slas``: nearest rank
with HdrHistogram's one-ULP rank guard, a resolution gate that refuses to approve a
tail the sample count cannot measure, and comparisons on unrounded values.

Percentiles are not additive, so the tail of the total is not the sum of the segment
tails. Attribution is therefore computed over the *tail subset* -- the samples whose
end-to-end latency lands at or above the audited percentile -- rather than by ranking
the segments independently. See :func:`MarketDataLatencyMonitorEngine.audit_vendor_latencies`.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --- Percentile estimators (names shared with latency-monitoring-percentile-based-slas)
PERCENTILE_NEAREST_RANK = "NEAREST_RANK"
PERCENTILE_LINEAR = "LINEAR_INTERPOLATION"
PERCENTILE_METHODS: Tuple[str, ...] = (PERCENTILE_NEAREST_RANK, PERCENTILE_LINEAR)

# --- Per-vendor statuses ---------------------------------------------------
STATUS_HEALTHY = "VENDOR_LATENCY_HEALTHY"
STATUS_SLA_BREACH = "VENDOR_LATENCY_SLA_BREACH_ALERT"
STATUS_INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES_FOR_SLA"
STATUS_CLOCK_DOMAIN_ERROR = "VENDOR_CLOCK_DOMAIN_ERROR"

# --- Report-level statuses -------------------------------------------------
REPORT_ALL_HEALTHY = "ALL_VENDORS_HEALTHY"
REPORT_SLA_BREACH = "VENDOR_SLA_BREACH_ALERT"
REPORT_UNMEASURABLE = "VENDOR_LATENCY_UNMEASURABLE"

# --- Pipeline segments -----------------------------------------------------
SEGMENT_VENDOR_TRANSPORT = "VENDOR_TRANSPORT"   # exchange stamp -> vendor gateway stamp
SEGMENT_NETWORK_WIRE = "NETWORK_WIRE"           # vendor gateway stamp -> local NIC stamp
SEGMENT_APP_QUEUE = "APP_QUEUE"                 # local NIC stamp -> application thread
SEGMENTS: Tuple[str, ...] = (
    SEGMENT_VENDOR_TRANSPORT,
    SEGMENT_NETWORK_WIRE,
    SEGMENT_APP_QUEUE,
)

# Absolute bound on a timestamp expressed in microseconds. 1e17 us is about 3,170
# years, which comfortably admits a microseconds-since-Unix-epoch stamp (~1.8e15 in
# 2026) while rejecting the most common epoch error in this domain: a nanosecond
# timestamp dropped into a microsecond field, which lands around 1.8e18.
MAX_PLAUSIBLE_TIMESTAMP_US = 1e17

# Largest float64 quantum, in microseconds, that still supports a microsecond report.
# A float64 carries a 53-bit significand, so the spacing between representable values
# grows with magnitude: 0.25 us at 1.8e15 (microseconds since the Unix epoch), 2 us at
# 1e16, 16 us at 1e17. Once that spacing reaches 1 us the timestamps cannot separate
# adjacent microseconds and a microsecond-granularity latency report is fiction. The
# fix is to rebase the timestamps against a session epoch before measuring, not to
# widen this bound.
MAX_USABLE_TIMESTAMP_QUANTUM_US = 1.0


class LatencySampleError(ValueError):
    """Raised when a latency sample set cannot support a meaningful audit.

    Subclasses ``ValueError`` so callers written against the previous
    ``raise ValueError("Latency sample list cannot be empty.")`` keep working.
    """


def _is_real_number(value: object) -> bool:
    """True for a finite int/float that is not a bool.

    ``bool`` is excluded deliberately: ``True`` is an ``int`` in Python and would
    otherwise be accepted as a 1 microsecond timestamp.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def rank_for_percentile(sample_count: int, percentile: float) -> int:
    """1-based nearest rank for ``percentile`` over ``sample_count`` samples.

    HdrHistogram's rule, ``ceil(percentile / 100 * N)``, clamped to ``[1, N]``.

    The percentile is nudged down by one ULP first, exactly as HdrHistogram does with
    ``Math.nextAfter(percentile, Double.NEGATIVE_INFINITY)``. Without the nudge,
    ``99.9 / 100`` evaluates to ``0.9990000000000001``, so ``ceil(0.999... * 1000)``
    is 1000 rather than 999 and P99.9 would be pinned to the observed maximum at
    exactly the sample count that should first resolve it. The nudge is inert for
    ranks that are already exact.
    """
    if sample_count <= 0:
        raise LatencySampleError("sample_count must be positive to compute a rank.")
    if not 0.0 <= percentile <= 100.0:
        raise LatencySampleError(f"percentile must be within [0, 100], got {percentile}.")
    nudged = math.nextafter(percentile, -math.inf)
    rank = math.ceil((nudged / 100.0) * sample_count)
    return min(max(rank, 1), sample_count)


def is_percentile_resolvable(sample_count: int, percentile: float) -> bool:
    """True when ``percentile`` is distinguishable from the observed maximum.

    When the nearest rank lands on the last sample, the reported "percentile" is just
    the maximum: the window holds no rarer event to measure. A P99.9 over 100 samples
    is the worst of 100 samples wearing a label it did not earn.
    """
    return rank_for_percentile(sample_count, percentile) < sample_count


def min_samples_for_percentile(percentile: float) -> int:
    """Smallest sample count at which ``percentile`` becomes resolvable.

    Analytically ``1 / (1 - percentile/100)`` -- 100 for P99, 1,000 for P99.9. The
    closed form only seeds the search, from the floor, because ``1 - 99.9/100``
    evaluates slightly small in binary floating point and the exact answer would
    otherwise be stepped over. :func:`is_percentile_resolvable` settles it, so the
    helper and the audit can never disagree.
    """
    if not 0.0 <= percentile < 100.0:
        raise LatencySampleError(
            f"percentile must be within [0, 100) to be resolvable, got {percentile}."
        )
    candidate = max(2, int(math.floor(1.0 / (1.0 - percentile / 100.0))))
    while not is_percentile_resolvable(candidate, percentile):
        candidate += 1
    return candidate


@dataclass
class LatencySample:
    """One tick, timestamped at four points along the exchange-to-application path.

    All four timestamps are microseconds on their *own* clock's epoch. They must be
    normalised to a single comparable epoch **before** construction -- see the module
    docstring on venue timestamp conventions.
    """

    vendor_id: str                      # e.g. 'BLOOMBERG', 'REFINITIV', 'DIRECT_CME'
    symbol: str
    t_exchange_us: float                # Exchange / matching-engine stamp
    t_vendor_us: float                  # Vendor ingestion gateway stamp
    t_local_nic_us: float               # Local NIC hardware stamp (PTP hardware clock)
    t_app_us: float                     # Application logic thread stamp (system clock)


@dataclass
class SegmentLatencyStats:
    """Distribution of one pipeline segment's latency, for one vendor."""

    segment: str
    mean_us: float
    p50_us: float
    p99_us: float
    max_us: float
    # Mean contribution of this segment across only those samples whose end-to-end
    # latency landed at or above the audited percentile. This is the attribution
    # figure: percentiles are not additive, so ranking segment P99s independently does
    # not identify which segment was slow *during the slow ticks*.
    tail_mean_us: float
    tail_share_pct: float


@dataclass
class VendorLatencyMetrics:
    """Audit outcome for a single vendor feed."""

    vendor_id: str
    sample_count: int
    mean_latency_us: float
    std_dev_jitter_us: float
    iqr_jitter_us: float
    p50_us: float
    p90_us: float
    p95_us: float
    p99_us: float
    p99_9_us: float
    max_us: float
    # Retained from v1 for callers already reading these three fields.
    avg_vendor_transport_us: float
    avg_network_wire_us: float
    avg_app_processing_us: float
    segment_stats: Dict[str, SegmentLatencyStats]
    dominant_tail_segment: Optional[str]
    audited_percentile: float
    audited_percentile_us: float
    is_sla_compliant: bool
    is_audited_percentile_resolvable: bool
    min_samples_required: int
    clock_inconsistent_sample_count: int
    timestamp_quantum_us: float
    status: str
    warnings: List[str] = field(default_factory=list)


@dataclass
class VendorLatencyReport:
    """Aggregate audit across every vendor present in the sample set."""

    total_samples_processed: int
    vendor_metrics: Dict[str, VendorLatencyMetrics]
    sla_breaching_vendors: List[str]
    unmeasurable_vendors: List[str]
    audited_percentile: float
    percentile_method: str
    status: str
    audit_notes: str


class MarketDataLatencyMonitorEngine:
    """Decomposes per-vendor market data latency and audits it against an SLA budget.

    Args:
        max_allowed_p99_latency_us: Budget the audited percentile is compared against.
            **Not a published figure.** No regulator, exchange or market data vendor
            publishes a microsecond latency SLA for a commercial feed; see
            ``references/standards.md``. Calibrate it, or the verdict is decorative.
        audited_percentile: Which percentile the budget applies to. Defaults to 99.0.
            Configurable because the percentile a latency obligation attaches to is not
            universal -- the EU consolidated tape timeliness rule, for instance, is
            expressed as a 95% confidence interval measured daily, not a hard maximum.
        percentile_method: ``PERCENTILE_NEAREST_RANK`` (default) or
            ``PERCENTILE_LINEAR``. Nearest rank always returns a latency that was
            actually observed; interpolation blends neighbours and on a bimodal feed
            reports a value the system never produced.
        clock_uncertainty_us: Combined timestamp uncertainty of the two clocks
            bracketing the measurement. When set, a verdict landing within this
            distance of the budget is annotated as undecidable. This never changes the
            status -- the budget is policy, the noise floor is a measurement fact.
        reject_clock_inconsistent_windows: When True (default) a vendor with any
            negative segment delta is reported as ``VENDOR_CLOCK_DOMAIN_ERROR`` and no
            percentiles are published for it. Set False only to inspect a known-broken
            feed, never to make a dashboard green.
    """

    def __init__(
        self,
        max_allowed_p99_latency_us: float = 500.0,
        audited_percentile: float = 99.0,
        percentile_method: str = PERCENTILE_NEAREST_RANK,
        clock_uncertainty_us: float = 0.0,
        reject_clock_inconsistent_windows: bool = True,
    ) -> None:
        if not _is_real_number(max_allowed_p99_latency_us) or max_allowed_p99_latency_us <= 0:
            raise LatencySampleError(
                f"max_allowed_p99_latency_us must be a positive finite number, "
                f"got {max_allowed_p99_latency_us!r}."
            )
        if not _is_real_number(audited_percentile) or not 0.0 < audited_percentile < 100.0:
            raise LatencySampleError(
                f"audited_percentile must be within (0, 100), got {audited_percentile!r}."
            )
        if percentile_method not in PERCENTILE_METHODS:
            raise LatencySampleError(
                f"percentile_method must be one of {PERCENTILE_METHODS}, "
                f"got {percentile_method!r}."
            )
        if not _is_real_number(clock_uncertainty_us) or clock_uncertainty_us < 0:
            raise LatencySampleError(
                f"clock_uncertainty_us must be a non-negative finite number, "
                f"got {clock_uncertainty_us!r}."
            )
        self.max_allowed_p99_latency_us = float(max_allowed_p99_latency_us)
        self.audited_percentile = float(audited_percentile)
        self.percentile_method = percentile_method
        self.clock_uncertainty_us = float(clock_uncertainty_us)
        self.reject_clock_inconsistent_windows = bool(reject_clock_inconsistent_windows)

    # --- percentiles -------------------------------------------------------
    def compute_percentile(
        self,
        sorted_values: Sequence[float],
        percentile: float,
        method: Optional[str] = None,
    ) -> float:
        """Percentile of an **ascending-sorted** series, in the series' own units.

        Defaults to nearest rank. Callers relying on the v1 linear-interpolation
        behaviour must now pass ``method=PERCENTILE_LINEAR`` explicitly; the estimator
        was changed because both this skill's documentation and its standards
        reference claimed percentiles were taken "directly from sample distributions
        (never averaged)", which interpolation is not.

        Raises:
            LatencySampleError: on an empty series. v1 returned ``0.0`` here, which
                reads as a perfect latency for a feed that produced no data at all.
        """
        chosen = method or self.percentile_method
        if chosen not in PERCENTILE_METHODS:
            raise LatencySampleError(
                f"method must be one of {PERCENTILE_METHODS}, got {chosen!r}."
            )
        if not sorted_values:
            raise LatencySampleError("Cannot compute a percentile of an empty series.")
        if not 0.0 <= percentile <= 100.0:
            raise LatencySampleError(f"percentile must be within [0, 100], got {percentile}.")

        n = len(sorted_values)
        if n == 1:
            return float(sorted_values[0])

        if chosen == PERCENTILE_NEAREST_RANK:
            return float(sorted_values[rank_for_percentile(n, percentile) - 1])

        k = (n - 1) * (percentile / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return float(sorted_values[int(k)])
        d0 = float(sorted_values[int(f)]) * (c - k)
        d1 = float(sorted_values[int(c)]) * (k - f)
        return d0 + d1

    # --- validation --------------------------------------------------------
    @staticmethod
    def _validate_sample(sample: LatencySample, index: int) -> None:
        """Reject a sample that cannot produce a meaningful latency, rather than repair it.

        NaN is the reason this is a hard rejection and not a filter: it does not raise,
        does not sort, and does not compare, so a single NaN yields an unordered
        series, an arbitrary median and a mean of ``nan`` -- while ``nan <= budget`` is
        ``False`` for every budget, which a naive audit reads either way it likes.
        """
        if not isinstance(sample, LatencySample):
            raise LatencySampleError(
                f"samples[{index}] must be a LatencySample, got {type(sample).__name__}."
            )
        if not isinstance(sample.vendor_id, str) or not sample.vendor_id.strip():
            raise LatencySampleError(
                f"samples[{index}] has an empty or non-string vendor_id "
                f"({sample.vendor_id!r}); latency cannot be attributed to a vendor."
            )
        for name in ("t_exchange_us", "t_vendor_us", "t_local_nic_us", "t_app_us"):
            value = getattr(sample, name)
            if not _is_real_number(value):
                raise LatencySampleError(
                    f"samples[{index}].{name} must be a finite real number, got {value!r}."
                )
            if abs(float(value)) > MAX_PLAUSIBLE_TIMESTAMP_US:
                raise LatencySampleError(
                    f"samples[{index}].{name} = {value} exceeds the plausible "
                    f"timestamp bound ({MAX_PLAUSIBLE_TIMESTAMP_US:g} us); this is a "
                    f"unit or epoch error -- most often a nanosecond timestamp in a "
                    f"microsecond field."
                )

    # --- helpers -----------------------------------------------------------
    @staticmethod
    def _segment_deltas(sample: LatencySample) -> Dict[str, float]:
        """The three segment latencies. Not clamped: a negative value is a finding."""
        return {
            SEGMENT_VENDOR_TRANSPORT: sample.t_vendor_us - sample.t_exchange_us,
            SEGMENT_NETWORK_WIRE: sample.t_local_nic_us - sample.t_vendor_us,
            SEGMENT_APP_QUEUE: sample.t_app_us - sample.t_local_nic_us,
        }

    @staticmethod
    def _timestamp_quantum(vendor_samples: Sequence[LatencySample]) -> float:
        """Spacing between representable float64 values at this window's magnitude.

        A latency is a difference of two timestamps, and no difference can be finer
        than the representation of its operands. Microseconds since the Unix epoch sit
        near 1.8e15, where consecutive float64 values are 0.25 us apart -- so a feed
        stamped that way cannot evidence a sub-microsecond figure however precisely the
        NIC measured it.
        """
        largest = 0.0
        for sample in vendor_samples:
            for name in ("t_exchange_us", "t_vendor_us", "t_local_nic_us", "t_app_us"):
                largest = max(largest, abs(float(getattr(sample, name))))
        return math.ulp(largest) if largest > 0.0 else 0.0

    def _jitter(self, totals_sorted: List[float], mean: float) -> Tuple[float, float]:
        """Population sigma and IQR.

        Both are reported because they answer different questions: one 100 ms stall
        moves sigma by orders of magnitude and leaves the IQR untouched. Sigma is
        tail-sensitive, the IQR describes the body, and the gap between them is itself
        the signal.
        """
        n = len(totals_sorted)
        if n < 2:
            return 0.0, 0.0
        variance = sum((x - mean) ** 2 for x in totals_sorted) / n
        sigma = math.sqrt(variance)
        iqr = (
            self.compute_percentile(totals_sorted, 75.0)
            - self.compute_percentile(totals_sorted, 25.0)
        )
        return sigma, iqr

    # --- audit -------------------------------------------------------------
    def audit_vendor_latencies(
        self,
        samples: Sequence[LatencySample],
    ) -> VendorLatencyReport:
        """Group by vendor, decompose, compute percentiles, and audit the budget.

        Samples are pooled per vendor across every symbol and host present, which is
        the only correct way to obtain a vendor-level percentile: a percentile is a
        quantile of a distribution, not an additive quantity, so per-symbol P99s
        cannot be averaged into a feed P99.

        Verdict rules, in precedence order:

        1. ``VENDOR_CLOCK_DOMAIN_ERROR`` -- any segment delta was negative, so the
           clocks bracketing that segment disagree and no latency from that window is
           trustworthy. Reported ahead of a breach because an untrustworthy breach is
           not a breach.
        2. ``VENDOR_LATENCY_SLA_BREACH_ALERT`` -- the audited percentile exceeded the
           budget. Reported at **any** sample count: an over-budget latency was
           genuinely observed, and observing one is enough to prove it happened.
        3. ``INSUFFICIENT_SAMPLES_FOR_SLA`` -- nothing breached, but the sample count
           cannot resolve the audited percentile. *No breach observed* is not
           *compliant*; a short window can prove a breach but never its absence.
        4. ``VENDOR_LATENCY_HEALTHY``.

        Raises:
            LatencySampleError: if the sample set is empty or any sample is malformed.
        """
        if not samples:
            raise LatencySampleError("Latency sample list cannot be empty.")

        vendor_groups: Dict[str, List[LatencySample]] = {}
        for index, sample in enumerate(samples):
            self._validate_sample(sample, index)
            key = sample.vendor_id.strip().upper()
            vendor_groups.setdefault(key, []).append(sample)

        vendor_metrics: Dict[str, VendorLatencyMetrics] = {}
        breaching: List[str] = []
        unmeasurable: List[str] = []

        for vendor_id, vendor_samples in vendor_groups.items():
            metrics = self._audit_one_vendor(vendor_id, vendor_samples)
            vendor_metrics[vendor_id] = metrics
            if metrics.status == STATUS_SLA_BREACH:
                breaching.append(vendor_id)
            elif metrics.status in (STATUS_CLOCK_DOMAIN_ERROR, STATUS_INSUFFICIENT_SAMPLES):
                unmeasurable.append(vendor_id)

        if breaching:
            status = REPORT_SLA_BREACH
            notes = (
                f"VENDOR SLA ALERT: breaching vendors = {sorted(breaching)} "
                f"(P{self.audited_percentile:g} > {self.max_allowed_p99_latency_us:g} us)."
            )
            if unmeasurable:
                notes += f" Not measurable: {sorted(unmeasurable)}."
        elif unmeasurable:
            status = REPORT_UNMEASURABLE
            notes = (
                f"VENDOR LATENCY NOT MEASURABLE: {sorted(unmeasurable)} could not be "
                f"audited (clock domain disagreement or insufficient samples). No "
                f"breach was observed, which is not the same as compliance."
            )
        else:
            status = REPORT_ALL_HEALTHY
            notes = (
                f"ALL VENDORS HEALTHY: all {len(vendor_groups)} vendor feeds meet "
                f"P{self.audited_percentile:g} <= {self.max_allowed_p99_latency_us:g} us."
            )

        return VendorLatencyReport(
            total_samples_processed=len(samples),
            vendor_metrics=vendor_metrics,
            sla_breaching_vendors=breaching,
            unmeasurable_vendors=unmeasurable,
            audited_percentile=self.audited_percentile,
            percentile_method=self.percentile_method,
            status=status,
            audit_notes=notes,
        )

    def _audit_one_vendor(
        self,
        vendor_id: str,
        vendor_samples: List[LatencySample],
    ) -> VendorLatencyMetrics:
        """Decompose and audit one vendor's pooled samples."""
        warnings: List[str] = []
        n = len(vendor_samples)

        totals: List[float] = []
        per_segment: Dict[str, List[float]] = {seg: [] for seg in SEGMENTS}
        clock_inconsistent = 0

        for sample in vendor_samples:
            deltas = self._segment_deltas(sample)
            if any(value < 0.0 for value in deltas.values()):
                clock_inconsistent += 1
            totals.append(sample.t_app_us - sample.t_exchange_us)
            for segment, value in deltas.items():
                per_segment[segment].append(value)

        if clock_inconsistent and self.reject_clock_inconsistent_windows:
            return self._clock_error_metrics(
                vendor_id, n, clock_inconsistent, self._timestamp_quantum(vendor_samples))
        if clock_inconsistent:
            warnings.append(
                f"{clock_inconsistent}/{n} samples have a negative segment latency. "
                f"The clocks bracketing that segment disagree, so every latency in "
                f"this window -- including the positive ones -- is wrong by an unknown "
                f"amount. Reported only because reject_clock_inconsistent_windows=False."
            )

        quantum = self._timestamp_quantum(vendor_samples)
        if quantum >= MAX_USABLE_TIMESTAMP_QUANTUM_US:
            warnings.append(
                f"Timestamps of this magnitude are only representable to {quantum:g} us "
                f"in float64, so latencies below that are quantisation noise and a "
                f"microsecond report is not supportable. Rebase the stamps against a "
                f"session epoch before measuring."
            )

        totals_sorted = sorted(totals)
        mean_latency = sum(totals_sorted) / n
        sigma, iqr = self._jitter(totals_sorted, mean_latency)

        p50 = self.compute_percentile(totals_sorted, 50.0)
        p90 = self.compute_percentile(totals_sorted, 90.0)
        p95 = self.compute_percentile(totals_sorted, 95.0)
        p99 = self.compute_percentile(totals_sorted, 99.0)
        p99_9 = self.compute_percentile(totals_sorted, 99.9)
        audited_value = self.compute_percentile(totals_sorted, self.audited_percentile)

        segment_stats, dominant = self._segment_statistics(per_segment, totals, audited_value)

        # Compared unrounded. Rounding first turns a 500.004 us P99 into a 500.00 pass.
        is_compliant = audited_value <= self.max_allowed_p99_latency_us
        resolvable = is_percentile_resolvable(n, self.audited_percentile)
        min_required = min_samples_for_percentile(self.audited_percentile)

        if not resolvable:
            warnings.append(
                f"P{self.audited_percentile:g} is not resolvable at {n} samples -- its "
                f"nearest rank is the observed maximum. {min_required} samples are "
                f"needed before this figure describes a "
                f"1-in-{min_required} event."
            )
        if not is_percentile_resolvable(n, 99.9):
            warnings.append(
                f"Reported p99_9_us is the observed maximum at {n} samples; "
                f"{min_samples_for_percentile(99.9)} samples are required to resolve it."
            )
        if self.clock_uncertainty_us > 0.0 and abs(
            audited_value - self.max_allowed_p99_latency_us
        ) <= self.clock_uncertainty_us:
            warnings.append(
                f"P{self.audited_percentile:g} = {audited_value:.2f} us is within the "
                f"{self.clock_uncertainty_us:g} us timestamp uncertainty of the "
                f"{self.max_allowed_p99_latency_us:g} us budget; the verdict is inside "
                f"the measurement noise floor and is not decidable from this data."
            )

        if not is_compliant:
            status = STATUS_SLA_BREACH
            logger.error(
                "VENDOR LATENCY SLA BREACH [%s]: P%g = %.2f us exceeds the %.0f us "
                "budget over %d samples.",
                vendor_id, self.audited_percentile, audited_value,
                self.max_allowed_p99_latency_us, n,
            )
        elif not resolvable:
            status = STATUS_INSUFFICIENT_SAMPLES
            logger.warning(
                "VENDOR LATENCY NOT MEASURABLE [%s]: no breach observed, but P%g needs "
                "%d samples and only %d were supplied.",
                vendor_id, self.audited_percentile, min_required, n,
            )
        else:
            status = STATUS_HEALTHY
            logger.info(
                "VENDOR LATENCY HEALTHY [%s]: P50 = %.2f us, P%g = %.2f us "
                "(<= %.0f us budget) over %d samples.",
                vendor_id, p50, self.audited_percentile, audited_value,
                self.max_allowed_p99_latency_us, n,
            )

        return VendorLatencyMetrics(
            vendor_id=vendor_id,
            sample_count=n,
            mean_latency_us=round(mean_latency, 2),
            std_dev_jitter_us=round(sigma, 2),
            iqr_jitter_us=round(iqr, 2),
            p50_us=round(p50, 2),
            p90_us=round(p90, 2),
            p95_us=round(p95, 2),
            p99_us=round(p99, 2),
            p99_9_us=round(p99_9, 2),
            max_us=round(totals_sorted[-1], 2),
            avg_vendor_transport_us=segment_stats[SEGMENT_VENDOR_TRANSPORT].mean_us,
            avg_network_wire_us=segment_stats[SEGMENT_NETWORK_WIRE].mean_us,
            avg_app_processing_us=segment_stats[SEGMENT_APP_QUEUE].mean_us,
            segment_stats=segment_stats,
            dominant_tail_segment=dominant,
            audited_percentile=self.audited_percentile,
            audited_percentile_us=round(audited_value, 2),
            is_sla_compliant=is_compliant,
            is_audited_percentile_resolvable=resolvable,
            min_samples_required=min_required,
            clock_inconsistent_sample_count=clock_inconsistent,
            timestamp_quantum_us=quantum,
            status=status,
            warnings=warnings,
        )

    def _segment_statistics(
        self,
        per_segment: Dict[str, List[float]],
        totals: List[float],
        tail_threshold_us: float,
    ) -> Tuple[Dict[str, SegmentLatencyStats], Optional[str]]:
        """Per-segment distribution plus tail attribution.

        Attribution runs over the *tail subset* -- the samples whose end-to-end latency
        landed at or above ``tail_threshold_us`` -- not over independently ranked
        segment percentiles. Percentiles are not additive: the segment with the highest
        P99 in isolation need not be the segment that was slow during the ticks that
        actually blew the budget, because those P99s can come from disjoint samples.
        """
        tail_indices = [i for i, total in enumerate(totals) if total >= tail_threshold_us]
        tail_means: Dict[str, float] = {}
        stats: Dict[str, SegmentLatencyStats] = {}

        for segment in SEGMENTS:
            values = per_segment[segment]
            values_sorted = sorted(values)
            tail_values = [values[i] for i in tail_indices] or values
            tail_means[segment] = sum(tail_values) / len(tail_values)
            stats[segment] = SegmentLatencyStats(
                segment=segment,
                mean_us=round(sum(values) / len(values), 2),
                p50_us=round(self.compute_percentile(values_sorted, 50.0), 2),
                p99_us=round(self.compute_percentile(values_sorted, 99.0), 2),
                max_us=round(values_sorted[-1], 2),
                tail_mean_us=round(tail_means[segment], 2),
                tail_share_pct=0.0,
            )

        tail_total = sum(tail_means.values())
        for segment in SEGMENTS:
            # A zero or negative tail total means the segments cancel out; a share is
            # not meaningful there, so it stays at 0.0 rather than dividing by zero.
            share = (tail_means[segment] / tail_total * 100.0) if tail_total > 0 else 0.0
            stats[segment].tail_share_pct = round(share, 2)

        dominant = max(SEGMENTS, key=lambda s: tail_means[s]) if tail_total > 0 else None
        return stats, dominant

    def _clock_error_metrics(
        self,
        vendor_id: str,
        sample_count: int,
        clock_inconsistent: int,
        timestamp_quantum_us: float,
    ) -> VendorLatencyMetrics:
        """Verdict for a vendor whose timestamps prove its clocks disagree.

        No percentiles are published. Publishing them alongside the error would invite
        exactly the reading the error exists to prevent -- that the numbers are usable
        if the warning is acknowledged. They are not: the offset is unknown and
        unsigned, so it corrupts the positive deltas as well.
        """
        logger.error(
            "VENDOR CLOCK DOMAIN ERROR [%s]: %d/%d samples have a negative segment "
            "latency. The bracketing clocks disagree; no latency from this window is "
            "usable. Check epoch/timezone normalisation and PHC-to-system-clock "
            "discipline before re-auditing.",
            vendor_id, clock_inconsistent, sample_count,
        )
        empty_stats = {
            segment: SegmentLatencyStats(
                segment=segment,
                mean_us=0.0, p50_us=0.0, p99_us=0.0, max_us=0.0,
                tail_mean_us=0.0, tail_share_pct=0.0,
            )
            for segment in SEGMENTS
        }
        return VendorLatencyMetrics(
            vendor_id=vendor_id,
            sample_count=sample_count,
            mean_latency_us=0.0,
            std_dev_jitter_us=0.0,
            iqr_jitter_us=0.0,
            p50_us=0.0, p90_us=0.0, p95_us=0.0, p99_us=0.0, p99_9_us=0.0, max_us=0.0,
            avg_vendor_transport_us=0.0,
            avg_network_wire_us=0.0,
            avg_app_processing_us=0.0,
            segment_stats=empty_stats,
            dominant_tail_segment=None,
            audited_percentile=self.audited_percentile,
            audited_percentile_us=0.0,
            is_sla_compliant=False,
            is_audited_percentile_resolvable=False,
            min_samples_required=min_samples_for_percentile(self.audited_percentile),
            clock_inconsistent_sample_count=clock_inconsistent,
            timestamp_quantum_us=timestamp_quantum_us,
            status=STATUS_CLOCK_DOMAIN_ERROR,
            warnings=[
                f"{clock_inconsistent}/{sample_count} samples have a negative segment "
                f"latency. Percentiles are withheld: a negative delta proves the two "
                f"clocks bracketing that segment disagree, and the positive deltas from "
                f"the same window carry the same unknown error.",
            ],
        )
