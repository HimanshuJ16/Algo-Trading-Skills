import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class LatencySampleSeries:
    pipeline_stage: str                 # e.g. 'TICK_TO_TRADE', 'ORDER_GATEWAY_ACK'
    samples_microseconds: List[float]   # List of latency values in microseconds
    sla_p50_target_us: float = 50.0     # Target P50 SLA (microseconds)
    sla_p99_target_us: float = 200.0    # Target P99 SLA (microseconds)
    sla_p999_target_us: float = 1000.0  # Target P99.9 SLA (microseconds)

@dataclass
class LatencySlaReport:
    pipeline_stage: str
    total_samples_count: int
    mean_latency_us: float
    p50_latency_us: float
    p90_latency_us: float
    p95_latency_us: float
    p99_latency_us: float
    p999_latency_us: float
    jitter_std_dev_us: float
    is_p50_sla_passed: bool
    is_p99_sla_passed: bool
    is_p999_sla_passed: bool
    status: str                         # 'SLA_COMPLIANCE_APPROVED', 'SLA_BREACH_P99_WARNING', 'SLA_BREACH_P999_CRITICAL'
    audit_notes: str

class LatencyPercentileSlaEngine:
    """
    Low-latency infrastructure SLA monitoring engine calculating microsecond-level P50, P90, P99, and P99.9 percentiles,
    monitoring latency jitter, and auditing tick-to-trade SLA breaches.
    """
    def __init__(self):
        pass

    def calculate_percentile(self, sorted_samples: List[float], percentile: float) -> float:
        """
        Calculates exact percentile value using linear interpolation.
        """
        n = len(sorted_samples)
        if n == 0:
            return 0.0
        if n == 1:
            return sorted_samples[0]

        idx = (percentile / 100.0) * (n - 1)
        lower_idx = int(idx)
        upper_idx = min(lower_idx + 1, n - 1)
        weight = idx - lower_idx

        return sorted_samples[lower_idx] + weight * (sorted_samples[upper_idx] - sorted_samples[lower_idx])

    def audit_latency_sla(self, series: LatencySampleSeries) -> LatencySlaReport:
        """
        Calculates P50, P90, P95, P99, P99.9 percentiles and audits against defined SLA budget targets.
        """
        samples = series.samples_microseconds
        n = len(samples)
        if n == 0:
            raise ValueError("Latency sample series cannot be empty.")

        sorted_samples = sorted(samples)

        mean_us = round(sum(sorted_samples) / float(n), 2)
        p50 = round(self.calculate_percentile(sorted_samples, 50.0), 2)
        p90 = round(self.calculate_percentile(sorted_samples, 90.0), 2)
        p95 = round(self.calculate_percentile(sorted_samples, 95.0), 2)
        p99 = round(self.calculate_percentile(sorted_samples, 99.0), 2)
        p999 = round(self.calculate_percentile(sorted_samples, 99.9), 2)

        # Calculate Latency Jitter (Standard Deviation)
        variance = sum((x - mean_us) ** 2 for x in sorted_samples) / float(n)
        jitter_std = round(math.sqrt(variance), 2)

        # Audit SLA Targets
        is_p50_ok = (p50 <= series.sla_p50_target_us)
        is_p99_ok = (p99 <= series.sla_p99_target_us)
        is_p999_ok = (p999 <= series.sla_p999_target_us)

        if not is_p999_ok:
            status = "SLA_BREACH_P999_CRITICAL"
            notes = (
                f"LATENCY SLA CRITICAL BREACH [{series.pipeline_stage}]: P99.9 Tail Latency ({p999:.1f} us) "
                f"exceeds critical SLA limit ({series.sla_p999_target_us:.1f} us)! Jitter = {jitter_std:.1f} us."
            )
            logger.critical(notes)
        elif not is_p99_ok:
            status = "SLA_BREACH_P99_WARNING"
            notes = (
                f"LATENCY SLA BREACH [{series.pipeline_stage}]: P99 Latency ({p99:.1f} us) "
                f"exceeds SLA limit ({series.sla_p99_target_us:.1f} us)."
            )
            logger.warning(notes)
        else:
            status = "SLA_COMPLIANCE_APPROVED"
            notes = (
                f"LATENCY SLA APPROVED [{series.pipeline_stage}]: N = {n:,} samples. "
                f"P50 = {p50:.1f} us, P90 = {p90:.1f} us, P99 = {p99:.1f} us, P99.9 = {p999:.1f} us. "
                f"Jitter (StdDev) = {jitter_std:.1f} us. All SLAs passed."
            )
            logger.info(notes)

        return LatencySlaReport(
            pipeline_stage=series.pipeline_stage,
            total_samples_count=n,
            mean_latency_us=mean_us,
            p50_latency_us=p50,
            p90_latency_us=p90,
            p95_latency_us=p95,
            p99_latency_us=p99,
            p999_latency_us=p999,
            jitter_std_dev_us=jitter_std,
            is_p50_sla_passed=is_p50_ok,
            is_p99_sla_passed=is_p99_ok,
            is_p999_sla_passed=is_p999_ok,
            status=status,
            audit_notes=notes
        )
