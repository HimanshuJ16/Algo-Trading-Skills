"""Tick-to-trade (T2T) latency budget accounting for co-located trading hosts.

Decomposes a hot-path trace (NIC ingress -> decode -> alpha -> risk -> encode ->
NIC egress) into per-phase nanosecond durations, audits each trace against a
total and per-phase SLA, and reports tail-latency percentiles across a batch.

Clock-domain contract
---------------------
``T0``/``T5`` are normally NIC *hardware* timestamps, which the kernel takes in
the NIC's own PTP hardware clock (PHC) domain, while ``T1..T4`` are software
timestamps taken with ``CLOCK_MONOTONIC``. Those are **different clocks**.
Every timestamp handed to :class:`HotPathTrace` MUST already be converted to a
single, monotonically non-decreasing nanosecond time base; this module cannot
detect a constant offset between clock domains, only the non-monotonicity that
a large offset produces. :class:`HotPathTrace` rejects out-of-order timestamps
at construction so that a mis-converted trace fails loudly instead of feeding
negative phase durations into an SLA decision.

Units
-----
All timestamps, durations and SLAs are **nanoseconds**. Percentile outputs are
floats (interpolated); everything else is an integer count of nanoseconds.
"""
from __future__ import annotations

import logging
import operator
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: Canonical phase names, in hot-path execution order. Bottleneck ties are
#: broken by this order, so it is part of the module's observable behaviour.
PHASE_NAMES: Tuple[str, ...] = (
    "ingress_to_decode_ns",
    "decode_to_signal_ns",
    "signal_to_risk_ns",
    "risk_to_encode_ns",
    "encode_to_egress_ns",
)

#: Default per-phase budgets (ns). Sum = 8,000 ns against a 10,000 ns default
#: total, leaving 2,000 ns of unallocated headroom.
DEFAULT_PHASE_SLAS_NS: Dict[str, int] = {
    "ingress_to_decode_ns": 1500,
    "decode_to_signal_ns": 2000,
    "signal_to_risk_ns": 1500,
    "risk_to_encode_ns": 1500,
    "encode_to_egress_ns": 1500,
}

DEFAULT_TOTAL_SLA_NS: int = 10000


def _require_ns(value: object, name: str) -> int:
    """Coerce a nanosecond timestamp/duration to ``int``, rejecting junk.

    Acceptance uses the ``__index__`` protocol, so anything that is losslessly
    an integer -- including ``np.int64`` from a NumPy/pandas telemetry pipeline
    -- is taken without an explicit cast, while floats are rejected: a
    fractional nanosecond means precision was already lost upstream. ``bool``
    is excluded explicitly - it is an ``int`` subclass, and silently reading
    ``True`` as 1 ns hides an instrumentation bug.
    """
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer nanosecond value, got {value!r}")
    try:
        return operator.index(value)
    except TypeError:
        raise TypeError(
            f"{name} must be an integer nanosecond value, got {value!r}"
        ) from None


@dataclass(frozen=True)
class HotPathTrace:
    """One instrumented tick-to-trade execution.

    All fields are nanosecond timestamps on a **single** monotonic time base
    (see the module docstring). Timestamps must be non-decreasing; a trace that
    goes backwards raises ``ValueError`` rather than producing a negative phase
    duration.

    Callers ingesting raw telemetry should construct traces inside a
    ``try/except ValueError`` and route rejects to a quarantine counter, so one
    bad record does not abort a whole accounting batch.
    """

    trace_id: str
    t0_nic_ingress_ns: int      # Hardware timestamp at NIC packet arrival
    t1_decode_ns: int           # Data decoded
    t2_signal_ns: int           # Alpha signal computed
    t3_risk_ns: int             # Pre-trade risk check completed
    t4_encode_ns: int           # Binary message formatted
    t5_nic_egress_ns: int       # Hardware timestamp at NIC packet egress

    def __post_init__(self) -> None:
        if not isinstance(self.trace_id, str) or not self.trace_id.strip():
            raise ValueError(f"trace_id must be a non-empty string, got {self.trace_id!r}")

        field_names = (
            "t0_nic_ingress_ns",
            "t1_decode_ns",
            "t2_signal_ns",
            "t3_risk_ns",
            "t4_encode_ns",
            "t5_nic_egress_ns",
        )
        # Normalise to builtin int so downstream arithmetic and the declared
        # field types stay honest even when the caller passes np.int64.
        for name in field_names:
            object.__setattr__(self, name, _require_ns(getattr(self, name), name))

        ordered = tuple((name, getattr(self, name)) for name in field_names)
        for (prev_name, prev), (name, value) in zip(ordered, ordered[1:]):
            if value < prev:
                raise ValueError(
                    f"trace {self.trace_id!r}: {name}={value} precedes {prev_name}={prev}. "
                    "Hot-path timestamps must be non-decreasing on a single time base - "
                    "this usually means NIC hardware timestamps (PHC domain) were mixed "
                    "with CLOCK_MONOTONIC software timestamps without conversion."
                )


@dataclass(frozen=True)
class PhaseBreakdown:
    """Per-phase durations in nanoseconds. All values are >= 0 by construction."""

    ingress_to_decode_ns: int
    decode_to_signal_ns: int
    signal_to_risk_ns: int
    risk_to_encode_ns: int
    encode_to_egress_ns: int
    total_tick_to_trade_ns: int

    def as_dict(self) -> Dict[str, int]:
        """Phase durations keyed by canonical phase name (total excluded)."""
        return {name: getattr(self, name) for name in PHASE_NAMES}


@dataclass(frozen=True)
class SlaAuditReport:
    """Outcome of auditing one trace against the configured budgets."""

    trace_id: str
    total_t2t_ns: int
    total_sla_ns: int
    is_sla_breach: bool
    primary_bottleneck_phase: Optional[str]
    phase_breakdown: PhaseBreakdown
    #: ``duration - phase_sla`` per phase. Negative means the phase was inside
    #: its own budget. Populated only when ``is_sla_breach`` is True.
    phase_excess_ns: Dict[str, int] = field(default_factory=dict)


class LatencyBudgetAccountingEngine:
    """Decomposes, audits and aggregates tick-to-trade latency traces.

    ``is_sla_breach`` uses a strict ``>``: a trace landing exactly on
    ``total_sla_ns`` consumes the whole budget but is **not** a breach.
    """

    def __init__(
        self,
        phase_slas_ns: Optional[Mapping[str, int]] = None,
        total_sla_ns: int = DEFAULT_TOTAL_SLA_NS,
    ) -> None:
        total = _require_ns(total_sla_ns, "total_sla_ns")
        if total <= 0:
            raise ValueError(f"total_sla_ns must be positive, got {total_sla_ns}")
        self.total_sla_ns: int = total

        if phase_slas_ns is None:
            self.phase_slas_ns: Dict[str, int] = dict(DEFAULT_PHASE_SLAS_NS)
        else:
            unknown = set(phase_slas_ns) - set(PHASE_NAMES)
            if unknown:
                # A typo'd key would otherwise fall through to a 0 ns budget and
                # make an innocent phase look like the bottleneck.
                raise ValueError(
                    f"unknown phase SLA key(s): {sorted(unknown)}. "
                    f"Valid phases are {list(PHASE_NAMES)}"
                )
            missing = set(PHASE_NAMES) - set(phase_slas_ns)
            if missing:
                raise ValueError(
                    f"phase_slas_ns must cover every phase; missing {sorted(missing)}"
                )
            resolved: Dict[str, int] = {}
            for phase in PHASE_NAMES:
                budget = _require_ns(phase_slas_ns[phase], phase)
                if budget < 0:
                    raise ValueError(f"phase SLA {phase} must be non-negative, got {budget}")
                resolved[phase] = budget
            self.phase_slas_ns = resolved

        phase_sum = sum(self.phase_slas_ns.values())
        if phase_sum > self.total_sla_ns:
            # Legal, but it means every phase can be inside its own budget while
            # the total still breaches. Say so rather than let it surprise later.
            logger.warning(
                "Phase SLAs sum to %d ns, above the %d ns total budget; "
                "a trace can breach the total with no phase over its own SLA.",
                phase_sum,
                self.total_sla_ns,
            )

    def decompose_trace(self, trace: HotPathTrace) -> PhaseBreakdown:
        """Calculate nanosecond durations for each execution phase."""
        return PhaseBreakdown(
            ingress_to_decode_ns=trace.t1_decode_ns - trace.t0_nic_ingress_ns,
            decode_to_signal_ns=trace.t2_signal_ns - trace.t1_decode_ns,
            signal_to_risk_ns=trace.t3_risk_ns - trace.t2_signal_ns,
            risk_to_encode_ns=trace.t4_encode_ns - trace.t3_risk_ns,
            encode_to_egress_ns=trace.t5_nic_egress_ns - trace.t4_encode_ns,
            total_tick_to_trade_ns=trace.t5_nic_egress_ns - trace.t0_nic_ingress_ns,
        )

    def audit_trace(self, trace: HotPathTrace) -> SlaAuditReport:
        """Audit one trace and, on breach, name the phase most over its budget.

        The bottleneck is the phase with the greatest ``duration - phase_sla``.
        A breached trace **always** names a bottleneck, even when every phase is
        inside its own budget (possible whenever the phase budgets sum above the
        total): in that case the named phase is the one closest to its limit.
        Ties are broken by hot-path order (``PHASE_NAMES``).
        """
        breakdown = self.decompose_trace(trace)
        is_breach = breakdown.total_tick_to_trade_ns > self.total_sla_ns

        bottleneck_phase: Optional[str] = None
        excesses: Dict[str, int] = {}
        if is_breach:
            durations = breakdown.as_dict()
            excesses = {
                phase: durations[phase] - self.phase_slas_ns[phase]
                for phase in PHASE_NAMES
            }
            # max() over the canonical order keeps tie-breaking deterministic.
            bottleneck_phase = max(PHASE_NAMES, key=lambda phase: excesses[phase])

            logger.warning(
                "SLA breach for trace %s: %d ns > %d ns. "
                "Primary bottleneck: %s (%+d ns vs its budget)",
                trace.trace_id,
                breakdown.total_tick_to_trade_ns,
                self.total_sla_ns,
                bottleneck_phase,
                excesses[bottleneck_phase],
            )

        return SlaAuditReport(
            trace_id=trace.trace_id,
            total_t2t_ns=breakdown.total_tick_to_trade_ns,
            total_sla_ns=self.total_sla_ns,
            is_sla_breach=is_breach,
            primary_bottleneck_phase=bottleneck_phase,
            phase_breakdown=breakdown,
            phase_excess_ns=excesses,
        )

    def compute_percentiles(
        self, traces: Sequence[HotPathTrace]
    ) -> Dict[str, Dict[str, float]]:
        """Compute mean and P50/P95/P99/P99.9 latency stats across traces.

        Each metric carries a ``count``. Percentiles use NumPy's default linear
        interpolation, so a percentile finer than the sample size can resolve is
        an interpolation between the top two observations, not a measurement:
        P99.9 needs ~1,000 samples and P99 ~100 before it means anything. A
        warning is logged when the batch is too small, but the figure is still
        returned alongside its ``count`` so the caller can judge it.
        """
        if not traces:
            logger.warning("compute_percentiles called with no traces; returning no stats.")
            return {}

        breakdowns = [self.decompose_trace(t) for t in traces]
        n = len(breakdowns)

        metrics: Dict[str, List[int]] = {
            "total_t2t_ns": [b.total_tick_to_trade_ns for b in breakdowns]
        }
        for phase in PHASE_NAMES:
            metrics[phase] = [getattr(b, phase) for b in breakdowns]

        percentiles: Tuple[Tuple[str, float], ...] = (
            ("p50", 50.0),
            ("p95", 95.0),
            ("p99", 99.0),
            ("p99_9", 99.9),
        )
        under_resolved = [
            label for label, q in percentiles if n < int(round(1.0 / (1.0 - q / 100.0)))
        ]
        if under_resolved:
            logger.warning(
                "Only %d sample(s): %s are interpolated between the top observations, "
                "not measured tail values.",
                n,
                ", ".join(under_resolved),
            )

        stats: Dict[str, Dict[str, float]] = {}
        for name, values in metrics.items():
            arr = np.asarray(values, dtype=np.float64)
            entry: Dict[str, float] = {
                "count": float(n),
                "mean": round(float(np.mean(arr)), 1),
            }
            for label, q in percentiles:
                entry[label] = round(float(np.percentile(arr, q)), 1)
            stats[name] = entry

        return stats
