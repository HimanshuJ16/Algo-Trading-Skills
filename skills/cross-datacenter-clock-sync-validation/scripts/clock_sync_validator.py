"""
cross-datacenter-clock-sync-validation: pairwise inter-datacenter clock drift
evaluation and a fail-closed arbitration veto.

What this measures, and what it does not
----------------------------------------
This module evaluates the *relative* clock agreement between datacenter nodes:
how far apart two sites' business clocks read from one another. That is the
quantity that governs whether a cross-region tick merge can be ordered
correctly.

It is **not** a measure of divergence from UTC, and passing it does not
establish MiFID II RTS 25 compliance. The two quantities are related but
neither implies the other:

  - Two clocks each within 100 us of UTC can be 200 us apart from each other.
    Pairwise drift is bounded by the *sum* of the two UTC divergences, so an
    RTS 25 HFT-compliant pair implies a pairwise budget of 2 x 100 us.
  - Two clocks that agree perfectly with each other can both be 10 ms away
    from UTC. Pairwise agreement is evidence of nothing at all about UTC.

Per-host divergence from UTC, and the latched halt that must follow a breach,
belong to ``clock-drift-monitoring-alerting-thresholds``. Configuring the sync
stack itself belongs to ``clock-synchronization-ptp-for-trading-hosts``. Do not
build a second, divergent set of UTC thresholds here.

Probe field semantics
---------------------
These were previously undefined, which made the arithmetic unverifiable.

``timestamp_sec``
    The node's own clock reading, in seconds, captured at the sampling
    instant. **All probes in one call must be sampled as a coordinated
    simultaneous snapshot.** This module cannot distinguish genuine clock
    drift from probe sampling skew - both appear as a difference in
    ``timestamp_sec``. Probes gathered 500 ms apart read as 500 ms of drift.
    ``max_sampling_skew_ms`` exists to catch that operator error; it cannot
    correct it.

``reported_offset_ms``
    The node's own estimate of its offset from its time reference, signed, as
    reported by its daemon (``chronyc tracking`` "System time", ``ptp4l``
    master offset converted to ms). Positive means the local clock is ahead.

``rtt_ms``
    The **root delay of the node's synchronization path to its reference
    clock** (``chronyc tracking`` "Root delay") - not the round-trip time of
    the monitoring query that collected this probe. Half of it is an
    irreducible uncertainty on the offset, because the offset calculation
    assumes a symmetric path (RFC 5905 section 8) and cannot observe
    asymmetry.

``root_dispersion_ms``
    ``chronyc tracking`` "Root dispersion". Optional, defaults to 0.0.

The uncertainty arithmetic is chrony's own published absolute error bound:

    clock_error <= |system_time_offset| + root_dispersion + (0.5 * root_delay)

and is consistent with RFC 5905 section 4, where the synchronization distance
LAMBDA = EPSILON + DELTA / 2 "represents the maximum error due to all causes".

Note the sign: measurement uncertainty is **added** to a drift estimate, never
subtracted from it. Subtracting RTT/2 from an observed drift - a
transcription error that was present in this skill's documentation - inflates
apparent accuracy without bound and is the opposite of conservative.

Resolution floor
----------------
``timestamp_sec`` is a Python float holding a Unix-epoch value. At epoch
magnitude (~1.8e9) the ULP of a float64 is about **0.24 microseconds**, so
every drift figure derived from those readings carries roughly that much
quantization regardless of how precise the underlying clocks are. That is
~0.24% of the 100 us RTS 25 HFT row and is immaterial there, but it makes this
module unfit for validating agreement at the sub-microsecond level - pass
nanosecond-scale ``ptp4l`` telemetry through ``reported_offset_ms`` (which is a
small number and keeps its precision) rather than encoding it into
``timestamp_sec``.

Fail-closed behaviour
---------------------
This is a safety veto, so every path that cannot produce a trustworthy verdict
denies arbitration rather than permitting it:

  - Fewer than two usable probes -> ``UNKNOWN`` / denied. A failed remote probe
    must not read as "one healthy region, proceed".
  - Any non-finite input -> :class:`ClockProbeError`. NaN is the specific
    hazard: ``nan > max_drift`` is ``False``, so an unchecked NaN leaves the
    running maximum at 0.0 and the verdict reads EXCELLENT.
  - Measurement uncertainty too large to certify the threshold -> denied, via
    ``is_measurement_conclusive``. A pair whose offsets are known only to
    +/- 40 ms cannot evidence 1 ms agreement, whatever the point estimate says.

This validator holds no state between calls and is safe to call concurrently.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Sequence

logger = logging.getLogger(__name__)

# MiFID II RTS 25 (Commission Delegated Regulation (EU) 2017/574), Annex
# Table 2 - maximum divergence from UTC per clock, by trading activity.
# Sourced ceilings, not engineering targets, and EU-scoped: a US CAT reporter
# is bound by FINRA Rule 6820 to 50 ms instead. See references/standards.md.
MIFID_HFT_MAX_UTC_DIVERGENCE_MS = 0.1
MIFID_OTHER_ALGO_MAX_UTC_DIVERGENCE_MS = 1.0

# Pairwise drift is bounded by the sum of the two clocks' UTC divergences, so a
# pair of RTS 25-compliant clocks admits twice the single-clock ceiling. These
# are the *implied* pairwise budgets, derived from the ceilings above - they are
# arithmetic, not a published regulatory figure.
MIFID_HFT_IMPLIED_PAIRWISE_BUDGET_MS = 2 * MIFID_HFT_MAX_UTC_DIVERGENCE_MS
MIFID_OTHER_ALGO_IMPLIED_PAIRWISE_BUDGET_MS = 2 * MIFID_OTHER_ALGO_MAX_UTC_DIVERGENCE_MS

# Quantization of a float64 Unix-epoch second, in milliseconds. Drift figures
# derived from ``timestamp_sec`` are not meaningful below this. See the
# "Resolution floor" section of the module docstring.
RESOLUTION_FLOOR_MS = 2.5e-4


class ClockProbeError(ValueError):
    """
    Raised when a probe set cannot yield a trustworthy verdict.

    Non-finite values are the specific hazard this guards. Every threshold
    comparison against NaN evaluates ``False``, so a corrupted or unparsed
    probe that reaches the tier logic unchecked leaves the running maximum
    drift at 0.0 and is classified ``EXCELLENT`` with arbitration permitted.
    Rejecting the probe set loudly is the only safe handling.
    """


class ClockSyncHealth(Enum):
    """
    Health tier of the *worst* pair in the probe set.

    Tier boundaries are configured on the validator, not fixed by this enum.
    ``UNKNOWN`` is not a degree of health - it means no verdict could be
    formed, and it always denies arbitration.
    """

    EXCELLENT = "EXCELLENT"      # within the excellent (PTP-class) threshold
    ACCEPTABLE = "ACCEPTABLE"    # within max_allowed_drift_ms
    DEGRADED = "DEGRADED"        # above the limit, below the degraded ceiling
    BREACH = "BREACH"            # above the degraded ceiling
    UNKNOWN = "UNKNOWN"          # insufficient or unusable probes; fail closed


@dataclass
class DatacenterClockProbe:
    """
    One node's clock state, sampled as part of a simultaneous snapshot.

    See the module docstring for the precise meaning of every field; in
    particular ``rtt_ms`` is the node's sync-path root delay, not the
    monitoring query's round-trip time.
    """

    region_id: str
    datacenter_name: str
    timestamp_sec: float
    reported_offset_ms: float
    rtt_ms: float
    root_dispersion_ms: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.region_id, str) or not self.region_id.strip():
            raise ClockProbeError("region_id must be a non-empty string")
        if not isinstance(self.datacenter_name, str) or not self.datacenter_name.strip():
            raise ClockProbeError(
                f"datacenter_name must be a non-empty string for region {self.region_id!r}"
            )
        for name in ("timestamp_sec", "reported_offset_ms", "rtt_ms", "root_dispersion_ms"):
            _require_finite(f"{name} for region {self.region_id!r}", getattr(self, name))
        # A negative path delay or dispersion is physically meaningless and
        # would subtract from the uncertainty bound, understating it.
        if self.rtt_ms < 0.0:
            raise ClockProbeError(
                f"rtt_ms must be >= 0 for region {self.region_id!r}, got {self.rtt_ms}"
            )
        if self.root_dispersion_ms < 0.0:
            raise ClockProbeError(
                f"root_dispersion_ms must be >= 0 for region {self.region_id!r}, "
                f"got {self.root_dispersion_ms}"
            )

    @property
    def clock_error_bound_ms(self) -> float:
        """
        Absolute error bound on this node's clock, per chrony's published
        formula: ``|offset| + root_dispersion + 0.5 * root_delay``.
        """
        return abs(self.reported_offset_ms) + self.root_dispersion_ms + 0.5 * self.rtt_ms


@dataclass
class CrossDatacenterSyncReport:
    """Verdict for one probe snapshot."""

    pairwise_drift_ms: Dict[str, float]
    health: ClockSyncHealth
    is_arbitration_allowed: bool
    max_drift_ms: float
    message: str
    # Worst-case relative clock error across the pair set, combining the point
    # estimate with the measurement uncertainty of both nodes.
    max_worst_case_drift_ms: float = 0.0
    # False when the measurement is too imprecise to certify the configured
    # limit at all, regardless of the point estimate. Always denies arbitration.
    is_measurement_conclusive: bool = True
    vetoed_pairs: List[str] = field(default_factory=list)


def _require_finite(name: str, value: float) -> None:
    """
    Rejects non-numeric, boolean and non-finite values.

    A bare ``math.isfinite`` check would accept ``True`` as 1.0 and raise
    ``TypeError`` rather than ``ClockProbeError`` on a string, so callers
    could not handle bad probe data uniformly.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ClockProbeError(f"{name} must be a finite number, got {value!r}")


class CrossDatacenterClockSyncValidator:
    """
    Evaluates pairwise clock agreement across datacenter nodes and returns a
    fail-closed verdict on whether cross-region arbitration may proceed.

    This module reports a verdict; it does not halt trading. Wire
    ``is_arbitration_allowed`` into the caller's arbitration gate.

    Thresholds are constructor arguments because no published rule sets a
    pairwise inter-datacenter drift limit. The defaults are engineering tiers,
    not regulatory ceilings; ``MIFID_HFT_IMPLIED_PAIRWISE_BUDGET_MS`` is
    available for a book that must stay inside the RTS 25 HFT row.
    """

    def __init__(
        self,
        max_allowed_drift_ms: float = 1.0,
        excellent_drift_ms: float = 0.1,
        degraded_ceiling_ms: float = 5.0,
        max_sampling_skew_ms: float = 0.0,
    ):
        _require_finite("max_allowed_drift_ms", max_allowed_drift_ms)
        _require_finite("excellent_drift_ms", excellent_drift_ms)
        _require_finite("degraded_ceiling_ms", degraded_ceiling_ms)
        _require_finite("max_sampling_skew_ms", max_sampling_skew_ms)
        if max_allowed_drift_ms <= 0.0:
            raise ClockProbeError(
                f"max_allowed_drift_ms must be > 0, got {max_allowed_drift_ms}"
            )
        if not 0.0 <= excellent_drift_ms <= max_allowed_drift_ms:
            raise ClockProbeError(
                "excellent_drift_ms must be within [0, max_allowed_drift_ms], got "
                f"{excellent_drift_ms} with max_allowed_drift_ms={max_allowed_drift_ms}"
            )
        if degraded_ceiling_ms < max_allowed_drift_ms:
            raise ClockProbeError(
                "degraded_ceiling_ms must be >= max_allowed_drift_ms, got "
                f"{degraded_ceiling_ms} with max_allowed_drift_ms={max_allowed_drift_ms}"
            )
        if max_sampling_skew_ms < 0.0:
            raise ClockProbeError(
                f"max_sampling_skew_ms must be >= 0, got {max_sampling_skew_ms}"
            )
        self.max_allowed_drift_ms = max_allowed_drift_ms
        self.excellent_drift_ms = excellent_drift_ms
        self.degraded_ceiling_ms = degraded_ceiling_ms
        # 0.0 disables the sampling-skew guard. It cannot be enabled by
        # default: the guard needs the caller's snapshot budget, which this
        # module cannot infer, and a wrong non-zero default would veto healthy
        # infrastructure.
        self.max_sampling_skew_ms = max_sampling_skew_ms

    def validate_datacenter_sync(
        self,
        node_probes: Sequence[DatacenterClockProbe],
    ) -> CrossDatacenterSyncReport:
        """
        Evaluates every unordered pair of probes and returns the verdict for
        the worst pair.

        Raises :class:`ClockProbeError` if any probe is unusable. Returns an
        ``UNKNOWN`` / denied report when fewer than two probes are supplied -
        a partially failed collection must not read as healthy.
        """
        probes = list(node_probes)

        if len(probes) < 2:
            # Fail closed. Cross-region arbitration is meaningless with one
            # region's clock, and a dropped remote probe is indistinguishable
            # here from a deliberately single-region deployment.
            msg = (
                f"CLOCK_UNSYNC_VETO: {len(probes)} usable probe(s) supplied; at least 2 "
                "are required to evaluate cross-region clock agreement. Arbitration denied."
            )
            logger.error(msg)
            return CrossDatacenterSyncReport(
                pairwise_drift_ms={},
                health=ClockSyncHealth.UNKNOWN,
                is_arbitration_allowed=False,
                max_drift_ms=0.0,
                message=msg,
                max_worst_case_drift_ms=0.0,
                is_measurement_conclusive=False,
            )

        for probe in probes:
            if not isinstance(probe, DatacenterClockProbe):
                raise ClockProbeError(
                    f"node_probes must contain DatacenterClockProbe instances, got {type(probe).__name__}"
                )

        # Distinct region_ids are required because the pair key is built from
        # them; duplicates silently overwrite entries and drop pairs from the
        # report, which would hide a breaching pair entirely.
        region_ids = [p.region_id for p in probes]
        duplicates = sorted({r for r in region_ids if region_ids.count(r) > 1})
        if duplicates:
            raise ClockProbeError(
                f"region_id must be unique across probes; duplicated: {duplicates}"
            )

        pairwise_drift: Dict[str, float] = {}
        vetoed_pairs: List[str] = []
        max_observed_drift = 0.0
        max_worst_case = 0.0
        max_uncertainty = 0.0

        for i in range(len(probes)):
            for j in range(i + 1, len(probes)):
                node_a = probes[i]
                node_b = probes[j]

                # Point estimate of relative clock disagreement. The two
                # epoch-magnitude readings are differenced *first*, then the
                # offset difference is applied in milliseconds. Folding a
                # sub-millisecond offset into a ~1.8e9 magnitude first would
                # quantize it at that magnitude's ULP (~0.24 us) before the
                # subtraction ever happens. See RESOLUTION_FLOOR_MS.
                drift_ms = abs(
                    (node_a.timestamp_sec - node_b.timestamp_sec) * 1000.0
                    + (node_a.reported_offset_ms - node_b.reported_offset_ms)
                )

                # Uncertainty adds; it never subtracts. See module docstring.
                uncertainty_ms = (
                    node_a.root_dispersion_ms
                    + node_b.root_dispersion_ms
                    + 0.5 * node_a.rtt_ms
                    + 0.5 * node_b.rtt_ms
                )
                worst_case_ms = drift_ms + uncertainty_ms

                pair_key = f"{node_a.region_id}<->{node_b.region_id}"
                pairwise_drift[pair_key] = round(drift_ms, 4)
                if drift_ms > self.max_allowed_drift_ms:
                    vetoed_pairs.append(pair_key)
                max_observed_drift = max(max_observed_drift, drift_ms)
                max_worst_case = max(max_worst_case, worst_case_ms)
                max_uncertainty = max(max_uncertainty, uncertainty_ms)

        # A measurement whose own uncertainty exceeds the limit cannot evidence
        # compliance with that limit, however small the point estimate is.
        is_conclusive = max_uncertainty <= self.max_allowed_drift_ms

        skew_exceeded = (
            self.max_sampling_skew_ms > 0.0
            and max_observed_drift > self.max_sampling_skew_ms
        )

        if max_observed_drift <= self.excellent_drift_ms:
            health = ClockSyncHealth.EXCELLENT
        elif max_observed_drift <= self.max_allowed_drift_ms:
            health = ClockSyncHealth.ACCEPTABLE
        elif max_observed_drift <= self.degraded_ceiling_ms:
            health = ClockSyncHealth.DEGRADED
        else:
            health = ClockSyncHealth.BREACH

        within_limit = max_observed_drift <= self.max_allowed_drift_ms
        is_allowed = within_limit and is_conclusive

        if not within_limit:
            msg = (
                f"CLOCK_UNSYNC_VETO: max inter-region drift {max_observed_drift:.4f}ms "
                f"exceeds limit {self.max_allowed_drift_ms:.4f}ms "
                f"(health={health.value}, pairs={vetoed_pairs}). "
                "Cross-region arbitration blocked."
            )
            if skew_exceeded:
                msg += (
                    f" Drift also exceeds the {self.max_sampling_skew_ms:.4f}ms sampling-skew "
                    "budget: verify the probes were captured as one simultaneous snapshot "
                    "before treating this as genuine clock drift."
                )
            logger.error(msg)
        elif not is_conclusive:
            msg = (
                f"CLOCK_UNSYNC_VETO: measurement uncertainty {max_uncertainty:.4f}ms exceeds "
                f"the {self.max_allowed_drift_ms:.4f}ms limit, so observed drift "
                f"{max_observed_drift:.4f}ms cannot evidence synchronization. "
                "Cross-region arbitration blocked."
            )
            logger.error(msg)
        else:
            msg = (
                f"Clock sync verified across {len(probes)} datacenters. "
                f"Max drift {max_observed_drift:.4f}ms (worst case {max_worst_case:.4f}ms), "
                f"health={health.value}."
            )
            logger.info(msg)

        return CrossDatacenterSyncReport(
            pairwise_drift_ms=pairwise_drift,
            health=health,
            is_arbitration_allowed=is_allowed,
            max_drift_ms=round(max_observed_drift, 4),
            message=msg,
            max_worst_case_drift_ms=round(max_worst_case, 4),
            is_measurement_conclusive=is_conclusive,
            vetoed_pairs=vetoed_pairs,
        )
