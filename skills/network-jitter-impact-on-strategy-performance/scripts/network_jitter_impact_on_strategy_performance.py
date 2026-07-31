import math
import logging
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class LatencyPacketSample:
    packet_id: str
    send_timestamp_ns: int
    receive_timestamp_ns: int

@dataclass
class JitterSimulationConfig:
    base_sharpe: float = 2.5             # Baseline Sharpe ratio with 0 jitter
    jitter_penalty_coeff: float = 0.5    # Gamma coefficient for Sharpe degradation per ms jitter
    target_sharpe_min: float = 1.0       # Minimum acceptable Sharpe ratio
    max_acceptable_jitter_ms: float = 3.0

@dataclass
class JitterImpactReport:
    total_packets_analyzed: int
    mean_latency_ms: float
    jitter_std_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    simulated_degraded_sharpe: float
    max_jitter_tolerance_ms: float
    is_jitter_acceptable: bool
    status: str                          # 'JITTER_HEALTHY', 'JITTER_HIGH_RISK_WARNING'
    audit_notes: str

class NetworkJitterImpactAnalyzerEngine:
    """
    Network latency jitter analyzer evaluating empirical P50/P95/P99 percentiles,
    simulating adverse selection PnL degradation, and auditing jitter tolerance thresholds.
    """
    def __init__(self, config: Optional[JitterSimulationConfig] = None):
        self.config = config or JitterSimulationConfig()

    def analyze_jitter_impact(
        self, samples: List[LatencyPacketSample]
    ) -> JitterImpactReport:
        """
        Computes latency percentiles, models Sharpe ratio decay under jitter, and audits risk thresholds.
        """
        if not samples:
            raise ValueError("Latency packet samples cannot be empty.")

        n = len(samples)
        latencies_ms = [(s.receive_timestamp_ns - s.send_timestamp_ns) / 1000000.0 for s in samples]
        latencies_sorted = sorted(latencies_ms)

        mean_lat = statistics.mean(latencies_ms)
        jitter_std = statistics.stdev(latencies_ms) if n > 1 else 0.0

        p50 = latencies_sorted[int(n * 0.50)]
        p95 = latencies_sorted[min(int(n * 0.95), n - 1)]
        p99 = latencies_sorted[min(int(n * 0.99), n - 1)]

        # 1. Model Sharpe Ratio Degradation
        gamma = self.config.jitter_penalty_coeff
        base_s = self.config.base_sharpe
        degraded_sharpe = max(0.0, base_s - (gamma * jitter_std))

        # 2. Compute Max Jitter Tolerance Threshold
        min_s = self.config.target_sharpe_min
        max_jitter_tolerance = (base_s - min_s) / gamma if gamma > 0 else 999.0

        is_acceptable = (jitter_std <= max_jitter_tolerance) and (degraded_sharpe >= min_s)
        status = "JITTER_HEALTHY" if is_acceptable else "JITTER_HIGH_RISK_WARNING"

        notes = (
            f"JITTER AUDIT [{status}]: Packets = {n}, Mean Latency = {mean_lat:.3f}ms, Jitter Std = {jitter_std:.3f}ms. "
            f"Percentiles: P50={p50:.3f}ms, P95={p95:.3f}ms, P99={p99:.3f}ms. "
            f"Simulated Degraded Sharpe = {degraded_sharpe:.2f} (Base: {base_s:.2f}, Max Jitter Tolerance: {max_jitter_tolerance:.2f}ms)."
        )
        if not is_acceptable:
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
            audit_notes=notes
        )
