"""Bounded, thread-safe latency accounting for trading risk-control decisions.

The module audits one question: did this risk control reach its *required end state* inside
its budget?  That end state is explicit, because the two candidates are not equivalent:

* ``LatencyEndState.DISPATCH`` -- the order left this process.  This proves local work only.
* ``LatencyEndState.ACKNOWLEDGEMENT`` -- the broker/exchange confirmed it.  This is the only
  end state that evidences containment, and it is the correct one for kill switches and
  cancel paths, where MiFID II RTS 6 Article 12 requires an investment firm to be able to
  "cancel immediately, as an emergency measure, any or all of its unexecuted orders".

All timestamps must come from one synchronized, monotonic clock domain and are in
milliseconds.  Durations taken from a settable wall clock (``time.time()``,
``CLOCK_REALTIME``) are "affected by discontinuous jumps in the system time" and by NTP
frequency adjustment; use ``time.perf_counter_ns()`` / ``CLOCK_MONOTONIC_RAW`` instead.
When the clock domain is not known to be synchronized, pass ``clock_synchronized=False``:
the trace is then reported ``UNCERTAIN`` rather than certified either way.

No regulator publishes a numeric latency budget for a pre-trade risk check itself, so the
50 ms default here is an engineering placeholder, not policy.  See ``references/standards.md``.
"""
from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_P99 = 99.0


class LatencyError(ValueError):
    """Raised when an input cannot support a trustworthy latency measurement."""


class MeasurementStatus(str, Enum):
    PASS = "PASS"
    BREACH = "BREACH"
    UNCERTAIN = "UNCERTAIN"


class LatencyEndState(str, Enum):
    """The point at which the control's budget is considered met.

    ``DISPATCH`` is the weaker claim: it stops the clock when the order leaves this
    process.  ``ACKNOWLEDGEMENT`` stops it when the venue confirms, and is required for
    any control whose protection depends on the venue having acted.
    """

    DISPATCH = "DISPATCH"
    ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"


@dataclass(frozen=True)
class RiskLatencyTrace:
    """One measured risk-control decision.

    ``audited_latency_ms`` is the value actually compared against ``sla_budget_ms``; it is
    ``None`` when the required end state was never observed.  ``budget_exceeded`` records
    that raw comparison independently of clock health, so a gross overrun stays visible on
    a trace whose ``status`` is ``UNCERTAIN``.
    """

    control_name: str
    event_timestamp_ms: float
    start_eval_timestamp_ms: float
    end_eval_timestamp_ms: float
    order_sent_timestamp_ms: float
    acknowledgement_timestamp_ms: Optional[float]
    ingest_latency_ms: float
    eval_latency_ms: float
    transmission_latency_ms: float
    acknowledgement_latency_ms: Optional[float]
    total_to_send_ms: float
    total_to_ack_ms: Optional[float]
    required_end_state: LatencyEndState
    audited_latency_ms: Optional[float]
    sla_budget_ms: float
    budget_exceeded: Optional[bool]
    status: MeasurementStatus
    primary_bottleneck: str
    clock_synchronized: bool

    @property
    def is_sla_violated(self) -> bool:
        """True only for a trustworthy over-budget measurement.

        An ``UNCERTAIN`` trace is never a certified breach; read ``budget_exceeded`` to see
        whether it was over budget anyway.
        """
        return self.status is MeasurementStatus.BREACH

    @property
    def total_latency_ms(self) -> float:
        """Compatibility alias: end-to-end latency through local dispatch only."""
        return self.total_to_send_ms


@dataclass(frozen=True)
class LatencyAuditSummary:
    """Aggregate view over recorded traces.

    ``avg_total_latency_ms`` and ``p99_total_latency_ms`` are computed over the *audited*
    latency of ``measured_traces`` only -- traces with an unsynchronized clock or a missing
    acknowledgement are excluded from the distribution, because folding an untrustworthy
    number into a percentile silently corrupts it.
    """

    total_traces: int
    measured_traces: int
    sla_breaches_count: int
    uncertain_count: int
    budget_exceeded_count: int
    avg_total_latency_ms: float
    p99_total_latency_ms: float
    p99_resolvable: bool
    is_risk_pipeline_healthy: bool
    message: str


class RiskControlLatencyBudgeter:
    """Records risk-control latency traces and audits them against an SLA budget."""

    def __init__(
        self,
        default_sla_budget_ms: float = 50.0,
        max_traces: int = 10_000,
        *,
        default_end_state: LatencyEndState = LatencyEndState.DISPATCH,
    ) -> None:
        _positive(default_sla_budget_ms, "default_sla_budget_ms")
        if not isinstance(max_traces, int) or isinstance(max_traces, bool) or max_traces < 1:
            raise LatencyError("max_traces must be a positive integer")
        if not isinstance(default_end_state, LatencyEndState):
            raise LatencyError("default_end_state must be a LatencyEndState")
        self.default_sla_budget_ms = float(default_sla_budget_ms)
        self.default_end_state = default_end_state
        self._traces: Deque[RiskLatencyTrace] = deque(maxlen=max_traces)
        self._lock = threading.RLock()

    def record_trace(
        self,
        control_name: str,
        t_event_ms: float,
        t_start_ms: float,
        t_end_ms: float,
        t_order_sent_ms: float,
        sla_budget_ms: Optional[float] = None,
        *,
        t_ack_ms: Optional[float] = None,
        end_state: Optional[LatencyEndState] = None,
        clock_synchronized: bool = True,
    ) -> RiskLatencyTrace:
        """Measure one risk-control decision and audit it against its budget.

        Raises ``LatencyError`` on any input that cannot support a trustworthy
        measurement: non-finite timestamps, timestamps that go backwards (which means the
        boundaries were stamped against clocks that disagree), or a non-positive budget.
        Out-of-order timestamps are never clamped to zero -- a clamped negative interval
        reads as an implausibly fast risk control rather than as the clock fault it is.
        """
        if not isinstance(control_name, str) or not control_name.strip():
            raise LatencyError("control_name is required")
        values = (t_event_ms, t_start_ms, t_end_ms, t_order_sent_ms)
        for value in values:
            _finite(value, "timestamp")
        if t_ack_ms is not None:
            _finite(t_ack_ms, "t_ack_ms")
        ordered = (*values, t_ack_ms) if t_ack_ms is not None else values
        if any(right < left for left, right in zip(ordered, ordered[1:])):
            raise LatencyError("timestamps must be non-decreasing in one clock domain")
        if not isinstance(clock_synchronized, bool):
            raise LatencyError("clock_synchronized must be a boolean")
        if sla_budget_ms is None:
            budget = self.default_sla_budget_ms
        else:
            _positive(sla_budget_ms, "sla_budget_ms")
            budget = float(sla_budget_ms)
        required_end_state = self.default_end_state if end_state is None else end_state
        if not isinstance(required_end_state, LatencyEndState):
            raise LatencyError("end_state must be a LatencyEndState")

        ingest = t_start_ms - t_event_ms
        evaluation = t_end_ms - t_start_ms
        transmission = t_order_sent_ms - t_end_ms
        acknowledgement = None if t_ack_ms is None else t_ack_ms - t_order_sent_ms
        total_to_send = t_order_sent_ms - t_event_ms
        total_to_ack = None if t_ack_ms is None else t_ack_ms - t_event_ms
        audited = (
            total_to_ack
            if required_end_state is LatencyEndState.ACKNOWLEDGEMENT
            else total_to_send
        )

        stages: Dict[str, float] = {
            "INGESTION": ingest,
            "EVALUATION": evaluation,
            "TRANSMISSION": transmission,
        }
        if required_end_state is LatencyEndState.ACKNOWLEDGEMENT and acknowledgement is not None:
            stages["ACKNOWLEDGEMENT"] = acknowledgement

        budget_exceeded = None if audited is None else audited > budget
        if audited is None or not clock_synchronized:
            # Either the required end state was never observed, or the clock domain cannot
            # support the comparison. Neither may be certified as a pass or as a breach.
            status = MeasurementStatus.UNCERTAIN
        elif budget_exceeded:
            status = MeasurementStatus.BREACH
        else:
            status = MeasurementStatus.PASS

        trace = RiskLatencyTrace(
            control_name=control_name.strip(),
            event_timestamp_ms=float(t_event_ms),
            start_eval_timestamp_ms=float(t_start_ms),
            end_eval_timestamp_ms=float(t_end_ms),
            order_sent_timestamp_ms=float(t_order_sent_ms),
            acknowledgement_timestamp_ms=None if t_ack_ms is None else float(t_ack_ms),
            ingest_latency_ms=round(ingest, 3),
            eval_latency_ms=round(evaluation, 3),
            transmission_latency_ms=round(transmission, 3),
            acknowledgement_latency_ms=(
                None if acknowledgement is None else round(acknowledgement, 3)
            ),
            total_to_send_ms=round(total_to_send, 3),
            total_to_ack_ms=None if total_to_ack is None else round(total_to_ack, 3),
            required_end_state=required_end_state,
            audited_latency_ms=None if audited is None else round(audited, 3),
            sla_budget_ms=round(budget, 3),
            budget_exceeded=budget_exceeded,
            status=status,
            primary_bottleneck=max(stages, key=stages.get),
            clock_synchronized=clock_synchronized,
        )
        with self._lock:
            self._traces.append(trace)
        # Recording sits on the risk critical path: a passing trace must not cost an
        # operator-visible log line, and must not bury the breach alerts that do.
        logger.log(
            logging.DEBUG if status is MeasurementStatus.PASS else logging.WARNING,
            "risk control latency status=%s control=%s end_state=%s audited_ms=%s "
            "budget_ms=%.3f bottleneck=%s",
            status.value,
            trace.control_name,
            required_end_state.value,
            "unmeasured" if audited is None else f"{audited:.3f}",
            budget,
            trace.primary_bottleneck,
        )
        return trace

    def summarize_audit(self, control_name: Optional[str] = None) -> LatencyAuditSummary:
        """Aggregate recorded traces, optionally filtered to one control.

        An empty result is reported as *unhealthy*: a risk pipeline that has produced no
        latency evidence has not been shown to meet its budget, and silent instrumentation
        is itself one of the failure modes this skill exists to catch.
        """
        with self._lock:
            traces: Tuple[RiskLatencyTrace, ...] = tuple(self._traces)
        if control_name is not None:
            traces = tuple(t for t in traces if t.control_name == control_name)
        if not traces:
            return LatencyAuditSummary(
                total_traces=0,
                measured_traces=0,
                sla_breaches_count=0,
                uncertain_count=0,
                budget_exceeded_count=0,
                avg_total_latency_ms=0.0,
                p99_total_latency_ms=0.0,
                p99_resolvable=False,
                is_risk_pipeline_healthy=False,
                message="No traces recorded: risk-control latency is unevidenced, not compliant.",
            )

        breaches = sum(t.status is MeasurementStatus.BREACH for t in traces)
        uncertain = sum(t.status is MeasurementStatus.UNCERTAIN for t in traces)
        exceeded = sum(t.budget_exceeded is True for t in traces)
        totals = sorted(
            t.audited_latency_ms
            for t in traces
            if t.status is not MeasurementStatus.UNCERTAIN and t.audited_latency_ms is not None
        )
        measured = len(totals)
        if measured:
            rank = _nearest_rank(measured, _P99)
            average = round(sum(totals) / measured, 3)
            p99 = round(totals[rank - 1], 3)
            p99_resolvable = rank < measured
        else:
            average = 0.0
            p99 = 0.0
            p99_resolvable = False
        healthy = breaches == 0 and uncertain == 0
        return LatencyAuditSummary(
            total_traces=len(traces),
            measured_traces=measured,
            sla_breaches_count=breaches,
            uncertain_count=uncertain,
            budget_exceeded_count=exceeded,
            avg_total_latency_ms=average,
            p99_total_latency_ms=p99,
            p99_resolvable=p99_resolvable,
            is_risk_pipeline_healthy=healthy,
            message=(
                f"Risk latency audit: traces={len(traces)} measured={measured} "
                f"breaches={breaches} uncertain={uncertain} over_budget={exceeded} "
                f"avg_ms={average:.3f} p99_ms={p99:.3f} p99_resolvable={p99_resolvable}."
            ),
        )


def _nearest_rank(sample_count: int, percentile: float) -> int:
    """HdrHistogram-compatible nearest rank (1-based) into an ascending series.

    A rank equal to ``sample_count`` means the reported "percentile" is just the maximum --
    the sample count cannot resolve it. P99 therefore needs at least 100 samples.
    """
    return math.ceil(sample_count * percentile / 100.0)


def _finite(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LatencyError(f"{name} must be finite")


def _positive(value: float, name: str) -> None:
    _finite(value, name)
    if value <= 0:
        raise LatencyError(f"{name} must be positive")
