import numpy as np
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class HotPathTrace:
    trace_id: str
    t0_nic_ingress_ns: int      # Hardware timestamp at NIC packet arrival
    t1_decode_ns: int           # Data decoded
    t2_signal_ns: int           # Alpha signal computed
    t3_risk_ns: int             # Pre-trade risk check completed
    t4_encode_ns: int           # Binary message formatted
    t5_nic_egress_ns: int       # Hardware timestamp at NIC packet egress

@dataclass
class PhaseBreakdown:
    ingress_to_decode_ns: int
    decode_to_signal_ns: int
    signal_to_risk_ns: int
    risk_to_encode_ns: int
    encode_to_egress_ns: int
    total_tick_to_trade_ns: int

@dataclass
class SlaAuditReport:
    trace_id: str
    total_t2t_ns: int
    total_sla_ns: int
    is_sla_breach: bool
    primary_bottleneck_phase: Optional[str]
    phase_breakdown: PhaseBreakdown

class LatencyBudgetAccountingEngine:
    """
    Decomposes tick-to-trade (T2T) latency traces into nanosecond phases,
    audits execution against phase/total SLAs, and identifies primary bottlenecks.
    """
    def __init__(self, phase_slas_ns: Optional[Dict[str, int]] = None, total_sla_ns: int = 10000):
        # Default SLAs in nanoseconds
        self.total_sla_ns = total_sla_ns
        self.phase_slas_ns = phase_slas_ns or {
            "ingress_to_decode_ns": 1500,
            "decode_to_signal_ns": 2000,
            "signal_to_risk_ns": 1500,
            "risk_to_encode_ns": 1500,
            "encode_to_egress_ns": 1500,
        }

    def decompose_trace(self, trace: HotPathTrace) -> PhaseBreakdown:
        """
        Calculates nanosecond durations for each execution phase.
        """
        return PhaseBreakdown(
            ingress_to_decode_ns=trace.t1_decode_ns - trace.t0_nic_ingress_ns,
            decode_to_signal_ns=trace.t2_signal_ns - trace.t1_decode_ns,
            signal_to_risk_ns=trace.t3_risk_ns - trace.t2_signal_ns,
            risk_to_encode_ns=trace.t4_encode_ns - trace.t3_risk_ns,
            encode_to_egress_ns=trace.t5_nic_egress_ns - trace.t4_encode_ns,
            total_tick_to_trade_ns=trace.t5_nic_egress_ns - trace.t0_nic_ingress_ns
        )

    def audit_trace(self, trace: HotPathTrace) -> SlaAuditReport:
        """
        Audits a single trace against SLAs and identifies the bottleneck phase if breached.
        """
        breakdown = self.decompose_trace(trace)
        is_breach = breakdown.total_tick_to_trade_ns > self.total_sla_ns
        
        bottleneck_phase = None
        if is_breach:
            # Find phase with largest excess over its individual SLA
            max_excess = -1
            durations = {
                "ingress_to_decode_ns": breakdown.ingress_to_decode_ns,
                "decode_to_signal_ns": breakdown.decode_to_signal_ns,
                "signal_to_risk_ns": breakdown.signal_to_risk_ns,
                "risk_to_encode_ns": breakdown.risk_to_encode_ns,
                "encode_to_egress_ns": breakdown.encode_to_egress_ns,
            }
            for phase, duration in durations.items():
                sla = self.phase_slas_ns.get(phase, 0)
                excess = duration - sla
                if excess > max_excess:
                    max_excess = excess
                    bottleneck_phase = phase
                    
            logger.warning(
                f"SLA Breach for trace {trace.trace_id}: {breakdown.total_tick_to_trade_ns}ns > {self.total_sla_ns}ns. "
                f"Primary Bottleneck: {bottleneck_phase}"
            )

        return SlaAuditReport(
            trace_id=trace.trace_id,
            total_t2t_ns=breakdown.total_tick_to_trade_ns,
            total_sla_ns=self.total_sla_ns,
            is_sla_breach=is_breach,
            primary_bottleneck_phase=bottleneck_phase,
            phase_breakdown=breakdown
        )

    def compute_percentiles(self, traces: List[HotPathTrace]) -> Dict[str, Dict[str, float]]:
        """
        Computes P50, P95, P99, and P99.9 percentile latency stats across traces.
        """
        if not traces:
            return {}
            
        breakdowns = [self.decompose_trace(t) for t in traces]
        metrics = {
            "total_t2t_ns": [b.total_tick_to_trade_ns for b in breakdowns],
            "ingress_to_decode_ns": [b.ingress_to_decode_ns for b in breakdowns],
            "decode_to_signal_ns": [b.decode_to_signal_ns for b in breakdowns],
            "signal_to_risk_ns": [b.signal_to_risk_ns for b in breakdowns],
            "risk_to_encode_ns": [b.risk_to_encode_ns for b in breakdowns],
            "encode_to_egress_ns": [b.encode_to_egress_ns for b in breakdowns],
        }
        
        stats = {}
        for name, values in metrics.items():
            arr = np.array(values)
            stats[name] = {
                "mean": round(float(np.mean(arr)), 1),
                "p50": round(float(np.percentile(arr, 50)), 1),
                "p95": round(float(np.percentile(arr, 95)), 1),
                "p99": round(float(np.percentile(arr, 99)), 1),
                "p99_9": round(float(np.percentile(arr, 99.9)), 1),
            }
            
        return stats
