"""Bounded, thread-safe latency accounting for trading risk-control decisions.

All timestamps must come from one synchronized clock domain.  A send timestamp proves only
local dispatch; provide broker/exchange acknowledgement when the control's outcome requires it.
"""
from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Optional

logger = logging.getLogger(__name__)


class LatencyError(ValueError):
    pass


class MeasurementStatus(str, Enum):
    PASS = "PASS"
    BREACH = "BREACH"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class RiskLatencyTrace:
    control_name: str
    event_timestamp_ms: float
    start_eval_timestamp_ms: float
    end_eval_timestamp_ms: float
    order_sent_timestamp_ms: float
    acknowledgement_timestamp_ms: float | None
    ingest_latency_ms: float
    eval_latency_ms: float
    transmission_latency_ms: float
    acknowledgement_latency_ms: float | None
    total_to_send_ms: float
    total_to_ack_ms: float | None
    sla_budget_ms: float
    status: MeasurementStatus
    primary_bottleneck: str
    clock_synchronized: bool

    @property
    def is_sla_violated(self) -> bool:
        return self.status is MeasurementStatus.BREACH

    @property
    def total_latency_ms(self) -> float:
        """Compatibility alias: end-to-end latency through local dispatch only."""
        return self.total_to_send_ms


@dataclass(frozen=True)
class LatencyAuditSummary:
    total_traces: int
    sla_breaches_count: int
    uncertain_count: int
    avg_total_latency_ms: float
    p99_total_latency_ms: float
    is_risk_pipeline_healthy: bool
    message: str


class RiskControlLatencyBudgeter:
    def __init__(self, default_sla_budget_ms: float = 50.0, max_traces: int = 10_000):
        _positive(default_sla_budget_ms, "default_sla_budget_ms")
        if not isinstance(max_traces, int) or isinstance(max_traces, bool) or max_traces < 1:
            raise LatencyError("max_traces must be a positive integer")
        self.default_sla_budget_ms = float(default_sla_budget_ms)
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
        t_ack_ms: float | None = None,
        clock_synchronized: bool = True,
    ) -> RiskLatencyTrace:
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
        budget = self.default_sla_budget_ms if sla_budget_ms is None else float(sla_budget_ms)
        _positive(budget, "sla_budget_ms")

        ingest, evaluation, transmission = t_start_ms - t_event_ms, t_end_ms - t_start_ms, t_order_sent_ms - t_end_ms
        acknowledgement = None if t_ack_ms is None else t_ack_ms - t_order_sent_ms
        total_to_send = t_order_sent_ms - t_event_ms
        total_to_ack = None if t_ack_ms is None else t_ack_ms - t_event_ms
        lats = {"INGESTION": ingest, "EVALUATION": evaluation, "TRANSMISSION": transmission}
        if acknowledgement is not None:
            lats["ACKNOWLEDGEMENT"] = acknowledgement
        status = MeasurementStatus.UNCERTAIN if not clock_synchronized else (MeasurementStatus.BREACH if total_to_send > budget else MeasurementStatus.PASS)
        trace = RiskLatencyTrace(control_name.strip(), *map(float, values), None if t_ack_ms is None else float(t_ack_ms), round(ingest, 3), round(evaluation, 3), round(transmission, 3), None if acknowledgement is None else round(acknowledgement, 3), round(total_to_send, 3), None if total_to_ack is None else round(total_to_ack, 3), round(budget, 3), status, max(lats, key=lats.get), clock_synchronized)
        with self._lock:
            self._traces.append(trace)
        logger.warning("risk control latency status=%s control=%s total_to_send_ms=%.3f budget_ms=%.3f bottleneck=%s", status.value, trace.control_name, total_to_send, budget, trace.primary_bottleneck)
        return trace

    def summarize_audit(self, control_name: str | None = None) -> LatencyAuditSummary:
        with self._lock:
            traces = tuple(self._traces)
        if control_name is not None:
            traces = tuple(t for t in traces if t.control_name == control_name)
        totals = sorted(t.total_to_send_ms for t in traces)
        if not totals:
            return LatencyAuditSummary(0, 0, 0, 0.0, 0.0, True, "No matching traces recorded.")
        breaches = sum(t.status is MeasurementStatus.BREACH for t in traces)
        uncertain = sum(t.status is MeasurementStatus.UNCERTAIN for t in traces)
        p99 = totals[math.ceil(len(totals) * 0.99) - 1]
        average = sum(totals) / len(totals)
        healthy = breaches == 0 and uncertain == 0
        return LatencyAuditSummary(len(traces), breaches, uncertain, round(average, 3), round(p99, 3), healthy, f"Risk latency audit: traces={len(traces)} breaches={breaches} uncertain={uncertain} avg_ms={average:.3f} p99_ms={p99:.3f}.")


def _finite(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LatencyError(f"{name} must be finite")


def _positive(value: float, name: str) -> None:
    _finite(value, name)
    if value <= 0:
        raise LatencyError(f"{name} must be positive")
