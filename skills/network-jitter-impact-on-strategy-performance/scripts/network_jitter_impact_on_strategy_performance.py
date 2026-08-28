"""Network latency jitter auditing for latency-sensitive trading strategies.

This module answers one question: *given a captured series of one-way packet delays,
is the delay variation small enough that the strategy relying on this link is still
worth running?*

Three things about that question are easy to get wrong, and this module is built
around them.

**"Jitter" is three different quantities.** What this module reports as
``jitter_std_ms`` is the standard deviation of the one-way delay over the window. That
is *not* RFC 5481 PDV, which is defined against the *minimum* delay of the interval
(``PDV(i) = D(i) - D(min)``), and it is *not* RFC 3550 interarrival jitter, which is a
smoothed exponential average of ``|D(i-1,i)|`` over *consecutive* packets. The three
answer different questions and do not agree numerically. Because a trading strategy is
damaged by how far a packet can fall behind the best case the link is capable of, this
module reports the RFC 5481 PDV tail (``pdv_p99_ms``) alongside sigma and the IQR,
rather than asking sigma to stand for all of them.

**Percentiles are computed by nearest rank, not by index truncation.** A prior revision
of this module indexed the sorted series at ``int(n * p)``, which is one rank too high
for every percentile: over 50 samples at 1 ms and 50 at 9 ms it reported a *median* of
9 ms where the median is 1 ms, and over 100 samples its "P99" was arithmetically the
observed maximum. Percentiles here follow HdrHistogram's ``getValueAtPercentile`` rank
rule, ``ceil(p/100 * N)``, matching the sibling skill
``latency-monitoring-percentile-based-slas``.

**The Sharpe degradation model is a fitted heuristic, not a law.** ``SR(sigma) =
SR_base - gamma * sigma`` is a *local linear approximation* whose coefficient gamma
(units: Sharpe per millisecond of delay standard deviation) has no published value from
any regulator, exchange, vendor or paper. The nearest peer-reviewed result -- Moallemi
and Saglam (2013), *The Cost of Latency in High-Frequency Trading*, Operations Research
61(5) -- derives the cost of *mean* latency and finds it asymptotically proportional to
``sigma_price * sqrt(dt) / spread``: concave in the delay, not linear, with the marginal
benefit of latency reduction *increasing* as delay falls. A linear penalty is therefore
defensible only over the narrow range it was fitted on, and only against gamma measured
from your own realized PnL. The shipped defaults are placeholders; see
``references/standards.md``.

Requires Python 3.9+ for ``math.nextafter``.
"""

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

# --- Audit statuses --------------------------------------------------------
STATUS_HEALTHY = "JITTER_HEALTHY"
STATUS_HIGH_RISK = "JITTER_HIGH_RISK_WARNING"
STATUS_INSUFFICIENT_SAMPLES = "JITTER_INSUFFICIENT_SAMPLES"

# --- Sharpe degradation model identifiers ---------------------------------
# Recorded on every report so a consumer can tell which model produced the number,
# and so a future non-linear model does not silently replace this one in place.
SHARPE_MODEL_LINEAR = "LINEAR_LOCAL_FIT"

# The tail percentile an approval is required to be able to resolve. P99 needs 100
# samples: below that, the nearest rank for P99 lands on the last element and the
# reported "P99" is simply the observed maximum.
AUDIT_TAIL_PERCENTILE = 99.0

# A one-way delay larger than this is not a measurement, it is a unit error (1e12 ms is
# roughly 31 years). The bound also keeps the sum of any realistic series far inside the
# float range, so the mean and variance cannot overflow.
MAX_PLAUSIBLE_LATENCY_MS = 1e12

NS_PER_MS = 1_000_000.0


class JitterSampleError(ValueError):
    """Raised when a packet sample series cannot support a meaningful jitter audit.

    Subclasses ``ValueError`` so callers written against the previous
    ``raise ValueError`` on an empty series keep working unchanged.
    """


class JitterConfigError(ValueError):
    """Raised when a :class:`JitterSimulationConfig` cannot produce a usable verdict."""


def rank_for_percentile(sample_count: int, percentile: float) -> int:
    """Return the 1-based nearest rank for ``percentile`` over ``sample_count`` samples.

    Uses HdrHistogram's rank rule, ``ceil(percentile / 100 * N)``, clamped to
    ``[1, N]``. The percentile is first nudged down by one ULP, exactly as
    HdrHistogram does with ``Math.nextAfter(percentile, Double.NEGATIVE_INFINITY)``:
    without it ``99.0 / 100.0`` and friends can round up across an integer boundary and
    pin a percentile to the maximum at the sample count that should first resolve it.
    """
    if sample_count <= 0:
        raise JitterSampleError("sample_count must be positive to compute a rank.")
    if not 0.0 <= percentile <= 100.0:
        raise JitterSampleError(f"percentile must be within [0, 100], got {percentile}.")
    nudged = math.nextafter(percentile, -math.inf)
    rank = math.ceil((nudged / 100.0) * sample_count)
    return min(max(rank, 1), sample_count)


def percentile_nearest_rank(sorted_samples: Sequence[float], percentile: float) -> float:
    """Return the nearest-rank ``percentile`` of an **ascending-sorted** series.

    Every value returned is a delay that was actually observed. Linear interpolation --
    the estimator NumPy and Excel use by default -- would instead blend two neighbouring
    observations, which on a bimodal link (a fast path and a queued path, nothing in
    between) reports a delay the network never produced.
    """
    return sorted_samples[rank_for_percentile(len(sorted_samples), percentile) - 1]


def is_percentile_resolvable(sample_count: int, percentile: float) -> bool:
    """Return True when ``percentile`` is distinguishable from the observed maximum.

    When the nearest rank lands on the last sample, the reported "percentile" is just
    the maximum of the series -- the window contains no rarer event to measure. A "P99"
    computed from 40 packets carries no information about a 1-in-100 delay excursion,
    because no 1-in-100 excursion was sampled.
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
        raise JitterSampleError(
            f"percentile must be within [0, 100) to be resolvable, got {percentile}."
        )
    candidate = max(2, int(math.floor(1.0 / (1.0 - percentile / 100.0))))
    while not is_percentile_resolvable(candidate, percentile):
        candidate += 1
    return candidate


@dataclass
class LatencyPacketSample:
    """One observed one-way packet delay.

    ``send_timestamp_ns`` and ``receive_timestamp_ns`` must be read from clocks that are
    synchronised to each other. A one-way delay spans two hosts, so a monotonic clock
    cannot be used for it and the achievable resolution is bounded by the two clocks'
    combined divergence from UTC -- see ``references/standards.md``.
    """

    packet_id: str
    send_timestamp_ns: int
    receive_timestamp_ns: int


@dataclass
class JitterSimulationConfig:
    """Degradation model parameters and jitter budgets.

    Every field is an operator-calibrated policy input. **None of these defaults is
    published by a regulator, an exchange, or a vendor** -- they exist so the module has
    something to run with, not because 2.5 / 0.5 / 1.0 describes any real strategy.
    """

    base_sharpe: float = 2.5
    """Sharpe ratio the strategy achieves at zero delay variation."""

    jitter_penalty_coeff: float = 0.5
    """Gamma. Units: Sharpe lost per millisecond of delay standard deviation.

    Must be measured by regressing the strategy's own realized Sharpe on measured
    jitter across comparable windows. A gamma carried over from another strategy, venue
    or instrument is a guess wearing a number.
    """

    target_sharpe_min: float = 1.0
    """Lowest Sharpe at which the strategy is still worth running."""

    max_acceptable_jitter_ms: float = 3.0
    """Absolute ceiling on the delay standard deviation, independent of the Sharpe model.

    Enforced since v2.0.0. Earlier revisions declared this field but never read it, so
    setting it had no effect. Under the shipped defaults it coincides with the
    Sharpe-derived tolerance ``(2.5 - 1.0) / 0.5 = 3.0 ms``, so wiring it changes no
    default-configuration verdict.
    """

    max_p99_latency_ms: Optional[float] = None
    """Optional absolute budget on the P99 one-way delay. ``None`` disables the check.

    Delay variation only costs money through the packets that actually arrive late, so a
    tail budget is the check that most directly matches the damage. It is opt-in because
    an uncalibrated tail budget produces confident nonsense.
    """

    def __post_init__(self) -> None:
        for name in ("base_sharpe", "jitter_penalty_coeff", "target_sharpe_min",
                     "max_acceptable_jitter_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise JitterConfigError(f"{name} must be a real number, got {value!r}.")
            if not math.isfinite(float(value)):
                raise JitterConfigError(f"{name} must be finite, got {value!r}.")

        if self.jitter_penalty_coeff <= 0.0:
            # A non-positive gamma makes the tolerance undefined or negative, i.e. it
            # asserts that jitter never hurts (or helps). A previous revision silently
            # substituted a sentinel tolerance of 999.0 ms here, which approved every
            # window. Refusing is the only honest option.
            raise JitterConfigError(
                "jitter_penalty_coeff (gamma) must be > 0; a non-positive coefficient "
                f"asserts that jitter does not degrade the strategy. Got "
                f"{self.jitter_penalty_coeff}."
            )
        if self.target_sharpe_min > self.base_sharpe:
            raise JitterConfigError(
                f"target_sharpe_min ({self.target_sharpe_min}) exceeds base_sharpe "
                f"({self.base_sharpe}); the strategy fails its own floor at zero jitter, "
                "so no jitter budget exists."
            )
        if self.max_acceptable_jitter_ms <= 0.0:
            raise JitterConfigError(
                f"max_acceptable_jitter_ms must be > 0, got {self.max_acceptable_jitter_ms}."
            )
        if self.max_p99_latency_ms is not None:
            if (isinstance(self.max_p99_latency_ms, bool)
                    or not isinstance(self.max_p99_latency_ms, (int, float))
                    or not math.isfinite(float(self.max_p99_latency_ms))
                    or self.max_p99_latency_ms <= 0.0):
                raise JitterConfigError(
                    "max_p99_latency_ms must be a positive finite number or None, got "
                    f"{self.max_p99_latency_ms!r}."
                )

    @property
    def max_jitter_tolerance_ms(self) -> float:
        """Delay standard deviation at which the modelled Sharpe reaches its floor.

        Re-checks gamma rather than trusting ``__post_init__``: this is a mutable
        dataclass, so a caller can assign ``cfg.jitter_penalty_coeff = 0`` after
        construction and skip validation entirely. A named configuration error is more
        useful there than a ``ZeroDivisionError`` from inside the audit.
        """
        if self.jitter_penalty_coeff <= 0.0:
            raise JitterConfigError(
                "jitter_penalty_coeff (gamma) must be > 0; got "
                f"{self.jitter_penalty_coeff}. Was it reassigned after construction?"
            )
        return (self.base_sharpe - self.target_sharpe_min) / self.jitter_penalty_coeff


@dataclass
class JitterImpactReport:
    """Result of one jitter audit over one capture window.

    The first eleven fields keep the order and meaning they had in v1.0.0. Fields added
    in v2.0.0 are appended with defaults, so positional construction of the original
    report still works.
    """

    total_packets_analyzed: int
    mean_latency_ms: float
    jitter_std_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    simulated_degraded_sharpe: float
    max_jitter_tolerance_ms: float
    is_jitter_acceptable: bool
    status: str                          # one of the STATUS_* constants
    audit_notes: str

    # --- added in v2.0.0 ---
    min_latency_ms: float = 0.0
    """Best delay the link achieved in this window; the RFC 5481 PDV reference point."""

    jitter_iqr_ms: float = 0.0
    """P75 - P25. Describes the body of the distribution; unlike sigma, one stall does
    not move it. Report both: they diverge exactly when it matters."""

    pdv_p99_ms: float = 0.0
    """RFC 5481 PDV at the 99th percentile: ``P99 - min``. How far behind the link's own
    best case a tail packet falls."""

    is_p99_resolvable: bool = False
    """False when the window is too short for P99 to mean anything (< 100 packets)."""

    sharpe_model: str = SHARPE_MODEL_LINEAR
    """Which degradation model produced ``simulated_degraded_sharpe``."""

    breaches: List[str] = field(default_factory=list)
    """Machine-readable reasons the window was not approved. Empty on approval."""


def _extract_latencies_ms(samples: Sequence[LatencyPacketSample]) -> List[float]:
    """Convert samples to millisecond delays, rejecting any series that cannot be audited.

    Rejects rather than repairs, because each of these inputs otherwise produces a
    confidently wrong report instead of an error:

    * **NaN / Inf** -- a NaN compares ``False`` against every bound, so ``sorted()``
      silently leaves the list unordered *and* every budget comparison reads as a pass.
    * **Negative delay** -- a receive timestamp before its send timestamp proves the two
      clocks disagree. The positive delays in the same window came from the same pair of
      disagreeing clocks and are wrong by an unknown amount, so the whole window is
      unusable. Do not filter the negatives and audit the rest.
    * **Implausibly large** -- almost always a unit error (nanoseconds supplied where
      milliseconds were meant, or a zero-initialised timestamp).
    """
    if not samples:
        raise JitterSampleError("Latency packet samples cannot be empty.")

    latencies_ms: List[float] = []
    seen_ids = set()
    duplicate_ids = 0

    for index, sample in enumerate(samples):
        for name in ("send_timestamp_ns", "receive_timestamp_ns"):
            value = getattr(sample, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise JitterSampleError(
                    f"Sample {index} ({sample.packet_id!r}): {name} must be a real "
                    f"number of nanoseconds, got {value!r}."
                )
            if not math.isfinite(float(value)):
                raise JitterSampleError(
                    f"Sample {index} ({sample.packet_id!r}): {name} is not finite "
                    f"({value!r}); a non-finite timestamp silently defeats both sorting "
                    "and every budget comparison."
                )

        latency_ms = (sample.receive_timestamp_ns - sample.send_timestamp_ns) / NS_PER_MS
        if latency_ms < 0.0:
            raise JitterSampleError(
                f"Sample {index} ({sample.packet_id!r}): negative one-way delay "
                f"({latency_ms:.6f} ms). The send and receive clocks disagree, so every "
                "delay in this window carries the same unknown error. Reject the window; "
                "do not drop the negative samples and audit the remainder."
            )
        if latency_ms > MAX_PLAUSIBLE_LATENCY_MS:
            raise JitterSampleError(
                f"Sample {index} ({sample.packet_id!r}): one-way delay {latency_ms:.3e} ms "
                f"exceeds the plausibility bound {MAX_PLAUSIBLE_LATENCY_MS:.0e} ms. "
                "Check that both timestamps are in nanoseconds."
            )
        latencies_ms.append(latency_ms)

        if sample.packet_id in seen_ids:
            duplicate_ids += 1
        else:
            seen_ids.add(sample.packet_id)

    if duplicate_ids:
        # A duplicated capture (the same traffic taken from two taps, or a replayed
        # pcap) double-counts observations and biases every percentile. Warn rather than
        # reject: some callers legitimately reuse ids across unrelated flows.
        logger.warning(
            "Jitter audit: %d duplicate packet_id(s) in a %d-packet window. If the "
            "capture was merged from more than one tap, percentiles are biased.",
            duplicate_ids, len(latencies_ms),
        )

    return latencies_ms


class NetworkJitterImpactAnalyzerEngine:
    """Audits a captured one-way delay series against a strategy's jitter budget.

    Reports nearest-rank P50/P95/P99 delays, delay variation as both sigma and IQR, the
    RFC 5481 PDV tail, and a modelled Sharpe ratio under the configured linear
    degradation coefficient.

    **What this engine does not do.** It reads no clock and instruments nothing; every
    guarantee it offers is about arithmetic over a series captured elsewhere. It is a
    windowed, after-the-fact audit, not a live circuit breaker -- halting trading on a
    latency excursion belongs in a dedicated risk control. And the Sharpe figure it
    reports is the output of a linear model with an operator-supplied coefficient: it is
    an estimate of impact under that model's assumptions, never a measurement of
    realized PnL.
    """

    def __init__(self, config: Optional[JitterSimulationConfig] = None) -> None:
        self.config = config or JitterSimulationConfig()

    def analyze_jitter_impact(
        self, samples: Sequence[LatencyPacketSample]
    ) -> JitterImpactReport:
        """Compute delay percentiles, model Sharpe decay, and audit the jitter budgets.

        Breach and approval are deliberately asymmetric, following
        ``latency-monitoring-percentile-based-slas``:

        * A **breach** is reported at any sample count. An over-budget delay was
          genuinely observed, and a handful of packets is enough to observe one.
        * An **approval** requires that P99 be resolvable (>= 100 packets). *No breach
          observed* over a short window is not *within budget*; that verdict is
          ``JITTER_INSUFFICIENT_SAMPLES``, and the fix is a longer capture rather than a
          looser reading of the report.

        All budget comparisons run on unrounded values; rounding is applied to the
        report fields only, so a P99 of 3.0004 ms against a 3 ms budget is a breach and
        not a 3.00 ms pass.

        :raises JitterSampleError: if the series is empty, has fewer than two packets,
            or contains a non-finite, negative, or implausible delay.
        """
        latencies_ms = _extract_latencies_ms(samples)
        n = len(latencies_ms)
        if n < 2:
            raise JitterSampleError(
                "At least 2 packets are required to measure delay variation; a "
                "single-packet window has a standard deviation of zero by construction, "
                "which would read as a perfectly jitter-free link."
            )

        latencies_sorted = sorted(latencies_ms)
        cfg = self.config

        mean_lat = statistics.mean(latencies_ms)
        jitter_std = statistics.stdev(latencies_ms)  # Bessel-corrected (n-1)
        min_lat = latencies_sorted[0]

        p25 = percentile_nearest_rank(latencies_sorted, 25.0)
        p50 = percentile_nearest_rank(latencies_sorted, 50.0)
        p75 = percentile_nearest_rank(latencies_sorted, 75.0)
        p95 = percentile_nearest_rank(latencies_sorted, 95.0)
        p99 = percentile_nearest_rank(latencies_sorted, AUDIT_TAIL_PERCENTILE)
        jitter_iqr = p75 - p25
        pdv_p99 = p99 - min_lat

        # --- Sharpe degradation under the configured linear model ---------------
        gamma = cfg.jitter_penalty_coeff
        base_s = cfg.base_sharpe
        min_s = cfg.target_sharpe_min
        max_jitter_tolerance = cfg.max_jitter_tolerance_ms

        modelled_sharpe = base_s - (gamma * jitter_std)
        # The floor is presentational: a Sharpe below zero is not more informative than
        # zero, but the *breach* test must use the unclamped value, or a configuration
        # with a negative target_sharpe_min would read a clamped 0.0 as passing.
        degraded_sharpe = max(0.0, modelled_sharpe)

        # --- Budget audit (unrounded comparisons) -------------------------------
        # Breach detail is formatted at nanosecond resolution (6 dp on a millisecond)
        # rather than at the report's 3 dp. A P99 of 5.0004 ms over a 5 ms budget is a
        # real breach, and rendering it as "5.000ms > 5.000ms" reads as a tool bug.
        breaches: List[str] = []
        if modelled_sharpe < min_s:
            breaches.append(
                f"SHARPE_BELOW_FLOOR(modelled={modelled_sharpe:.6f} < min={min_s:.6f})"
            )
        if jitter_std > cfg.max_acceptable_jitter_ms:
            breaches.append(
                f"JITTER_STD_OVER_CEILING({jitter_std:.6f}ms > "
                f"{cfg.max_acceptable_jitter_ms:.6f}ms)"
            )
        if cfg.max_p99_latency_ms is not None and p99 > cfg.max_p99_latency_ms:
            breaches.append(
                f"P99_LATENCY_OVER_BUDGET({p99:.6f}ms > {cfg.max_p99_latency_ms:.6f}ms)"
            )

        p99_resolvable = is_percentile_resolvable(n, AUDIT_TAIL_PERCENTILE)

        if breaches:
            status = STATUS_HIGH_RISK
        elif not p99_resolvable:
            status = STATUS_INSUFFICIENT_SAMPLES
            breaches.append(
                f"P99_NOT_RESOLVABLE(n={n} < "
                f"{min_samples_for_percentile(AUDIT_TAIL_PERCENTILE)})"
            )
        else:
            status = STATUS_HEALTHY
        is_acceptable = status == STATUS_HEALTHY

        notes = (
            f"JITTER AUDIT [{status}]: Packets = {n}, Mean Latency = {mean_lat:.3f}ms, "
            f"Min = {min_lat:.3f}ms, Jitter Std = {jitter_std:.3f}ms, IQR = {jitter_iqr:.3f}ms. "
            f"Percentiles (nearest rank): P50={p50:.3f}ms, P95={p95:.3f}ms, P99={p99:.3f}ms "
            f"(PDV P99 = {pdv_p99:.3f}ms). "
            f"Modelled Sharpe [{SHARPE_MODEL_LINEAR}] = {degraded_sharpe:.2f} "
            f"(Base: {base_s:.2f}, Sharpe-derived jitter tolerance: {max_jitter_tolerance:.2f}ms, "
            f"absolute jitter ceiling: {cfg.max_acceptable_jitter_ms:.2f}ms)."
        )
        if breaches:
            notes += " Findings: " + "; ".join(breaches) + "."
        if status == STATUS_INSUFFICIENT_SAMPLES:
            notes += (
                " No breach was observed, but the window is too short to resolve P99 -- "
                "this is 'not measured', not 'within budget'."
            )

        if status == STATUS_HIGH_RISK:
            logger.warning(notes)
        else:
            logger.info(notes)

        return JitterImpactReport(
            total_packets_analyzed=n,
            mean_latency_ms=round(mean_lat, 3),
            jitter_std_ms=round(jitter_std, 3),
            p50_latency_ms=round(p50, 3),
            p95_latency_ms=round(p95, 3),
            p99_latency_ms=round(p99, 3),
            simulated_degraded_sharpe=round(degraded_sharpe, 2),
            max_jitter_tolerance_ms=round(max_jitter_tolerance, 2),
            is_jitter_acceptable=is_acceptable,
            status=status,
            audit_notes=notes,
            min_latency_ms=round(min_lat, 3),
            jitter_iqr_ms=round(jitter_iqr, 3),
            pdv_p99_ms=round(pdv_p99, 3),
            is_p99_resolvable=p99_resolvable,
            sharpe_model=SHARPE_MODEL_LINEAR,
            breaches=breaches,
        )
