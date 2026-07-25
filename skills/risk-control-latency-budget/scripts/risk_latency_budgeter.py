"""
risk-control-latency-budget: Risk pipeline end-to-end latency profiler,
SLA budget auditor, and bottleneck diagnostics engine.
"""
from dataclasses import dataclass
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RiskLatencyTrace:
    control_name: str
    event_timestamp_ms: float
    start_eval_timestamp_ms: float
    end_eval_timestamp_ms: float
    order_sent_timestamp_ms: float
    ingest_latency_ms: float
    eval_latency_ms: float
    transmission_latency_ms: float
    total_latency_ms: float
    sla_budget_ms: float
    is_sla_violated: bool
    primary_bottleneck: str


@dataclass
class LatencyAuditSummary:
    total_traces: int
    sla_breaches_count: int
    avg_total_latency_ms: float
    p99_total_latency_ms: float
    is_risk_pipeline_healthy: bool
    message: str


class RiskControlLatencyBudgeter:
    """
    Profiles end-to-end latency across risk ingestion, evaluation, and transmission
    to enforce SLA latency budgets and catch slow risk controls.
    """

    def __init__(self, default_sla_budget_ms: float = 50.0):
        self.default_sla_budget_ms = default_sla_budget_ms
        self._traces: List[RiskLatencyTrace] = []

    def record_trace(
        self,
        control_name: str,
        t_event_ms: float,
        t_start_ms: float,
        t_end_ms: float,
        t_order_sent_ms: float,
        sla_budget_ms: Optional[float] = None,
    ) -> RiskLatencyTrace:
        budget = sla_budget_ms if sla_budget_ms is not None else self.default_sla_budget_ms

        ingest_lat = max(0.0, t_start_ms - t_event_ms)
        eval_lat = max(0.0, t_end_ms - t_start_ms)
        trans_lat = max(0.0, t_order_sent_ms - t_end_ms)
        total_lat = ingest_lat + eval_lat + trans_lat

        is_violated = total_lat > budget

        # Identify bottleneck
        lats = {"INGESTION": ingest_lat, "EVALUATION": eval_lat, "TRANSMISSION": trans_lat}
        bottleneck = max(lats, key=lats.get)

        trace = RiskLatencyTrace(
            control_name=control_name,
            event_timestamp_ms=round(t_event_ms, 2),
            start_eval_timestamp_ms=round(t_start_ms, 2),
            end_eval_timestamp_ms=round(t_end_ms, 2),
            order_sent_timestamp_ms=round(t_order_sent_ms, 2),
            ingest_latency_ms=round(ingest_lat, 2),
            eval_latency_ms=round(eval_lat, 2),
            transmission_latency_ms=round(trans_lat, 2),
            total_latency_ms=round(total_lat, 2),
            sla_budget_ms=round(budget, 2),
            is_sla_violated=is_violated,
            primary_bottleneck=bottleneck,
        )

        self._traces.append(trace)

        if is_violated:
            msg = (
                f"RISK CONTROL LATENCY SLA BREACH in '{control_name}': "
                f"Total={total_lat:.1f}ms > Budget={budget:.1f}ms. Bottleneck: {bottleneck} ({lats[bottleneck]:.1f}ms)."
            )
            logger.error(msg)
        else:
            logger.info(f"Risk Control '{control_name}' latency OK ({total_lat:.1f}ms <= {budget:.1f}ms).")

        return trace

    def summarize_audit(self) -> LatencyAuditSummary:
        n = len(self._traces)
        if n == 0:
            return LatencyAuditSummary(0, 0, 0.0, 0.0, True, "No traces recorded.")

        breaches = sum(1 for t in self._traces if t.is_sla_violated)
        totals = sorted(t.total_latency_ms for t in self._traces)

        avg_lat = sum(totals) / float(n)
        p99_idx = int(math.ceil(0.99 * n)) - 1
        p99_lat = totals[max(0, p99_idx)]

        is_healthy = breaches == 0

        msg = (
            f"Risk Latency Audit ({n} traces): {breaches} SLA breaches. "
            f"Avg={avg_lat:.1f}ms, P99={p99_lat:.1f}ms. Healthy={is_healthy}."
        )
        logger.info(msg)

        return LatencyAuditSummary(
            total_traces=n,
            sla_breaches_count=breaches,
            avg_total_latency_ms=round(avg_lat, 2),
            p99_total_latency_ms=round(p99_lat, 2),
            is_risk_pipeline_healthy=is_healthy,
            message=msg,
        )
