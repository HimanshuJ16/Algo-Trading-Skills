import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class LatencySample:
    vendor_id: str                      # e.g. 'BLOOMBERG', 'REFINITIV', 'DIRECT_CME'
    symbol: str
    t_exchange_us: float                # Microseconds timestamp at Exchange
    t_vendor_us: float                  # Microseconds timestamp at Vendor Ingestion Gateway
    t_local_nic_us: float               # Microseconds timestamp at Local NIC (hardware)
    t_app_us: float                     # Microseconds timestamp at Application logic thread

@dataclass
class VendorLatencyMetrics:
    vendor_id: str
    sample_count: int
    mean_latency_us: float
    std_dev_jitter_us: float
    p50_us: float
    p90_us: float
    p95_us: float
    p99_us: float
    p99_9_us: float
    avg_vendor_transport_us: float
    avg_network_wire_us: float
    avg_app_processing_us: float
    is_sla_compliant: bool
    status: str                         # 'VENDOR_LATENCY_HEALTHY', 'VENDOR_LATENCY_SLA_BREACH_ALERT'

@dataclass
class VendorLatencyReport:
    total_samples_processed: int
    vendor_metrics: Dict[str, VendorLatencyMetrics]
    sla_breaching_vendors: List[str]
    status: str                         # 'ALL_VENDORS_HEALTHY', 'VENDOR_SLA_BREACH_ALERT'
    audit_notes: str

class MarketDataLatencyMonitorEngine:
    """
    Real-time microsecond market data latency decomposition engine,
    calculating P50, P90, P95, P99, P99.9 percentiles and jitter per vendor stream to audit exchange-to-app SLA thresholds.
    """
    def __init__(self, max_allowed_p99_latency_us: float = 500.0):
        self.max_allowed_p99_latency_us = max_allowed_p99_latency_us

    def compute_percentile(self, sorted_values: List[float], percentile: float) -> float:
        """
        Computes percentile value from a sorted list of numbers (0 <= percentile <= 100).
        """
        if not sorted_values:
            return 0.0
        n = len(sorted_values)
        if n == 1:
            return sorted_values[0]

        k = (n - 1) * (percentile / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_values[int(k)]
        d0 = sorted_values[int(f)] * (c - k)
        d1 = sorted_values[int(c)] * (k - f)
        return d0 + d1

    def audit_vendor_latencies(
        self,
        samples: List[LatencySample]
    ) -> VendorLatencyReport:
        """
        Groups samples by vendor, decomposes latency metrics, computes percentiles/jitter, and audits SLA.
        """
        if not samples:
            raise ValueError("Latency sample list cannot be empty.")

        vendor_groups: Dict[str, List[LatencySample]] = {}
        for s in samples:
            v_clean = s.vendor_id.strip().upper()
            if v_clean not in vendor_groups:
                vendor_groups[v_clean] = []
            vendor_groups[v_clean].append(s)

        vendor_metrics_dict: Dict[str, VendorLatencyMetrics] = {}
        breaching_vendors: List[str] = []

        for v_id, v_samples in vendor_groups.items():
            totals: List[float] = []
            transports: List[float] = []
            wires: List[float] = []
            procs: List[float] = []

            for s in v_samples:
                tot = max(s.t_app_us - s.t_exchange_us, 0.0)
                trans = max(s.t_vendor_us - s.t_exchange_us, 0.0)
                wire = max(s.t_local_nic_us - s.t_vendor_us, 0.0)
                proc = max(s.t_app_us - s.t_local_nic_us, 0.0)

                totals.append(tot)
                transports.append(trans)
                wires.append(wire)
                procs.append(proc)

            totals.sort()
            n = len(totals)
            mean_lat = sum(totals) / n
            variance = sum((x - mean_lat) ** 2 for x in totals) / n if n > 1 else 0.0
            jitter = math.sqrt(variance)

            p50 = round(self.compute_percentile(totals, 50.0), 2)
            p90 = round(self.compute_percentile(totals, 90.0), 2)
            p95 = round(self.compute_percentile(totals, 95.0), 2)
            p99 = round(self.compute_percentile(totals, 99.0), 2)
            p99_9 = round(self.compute_percentile(totals, 99.9), 2)

            avg_trans = round(sum(transports) / n, 2)
            avg_wire = round(sum(wires) / n, 2)
            avg_proc = round(sum(procs) / n, 2)

            is_sla_ok = (p99 <= self.max_allowed_p99_latency_us)

            if not is_sla_ok:
                v_status = "VENDOR_LATENCY_SLA_BREACH_ALERT"
                breaching_vendors.append(v_id)
                logger.error(
                    f"VENDOR LATENCY SLA BREACH [{v_id}]: P99 Latency = {p99:.1f} us "
                    f"exceeds SLA threshold ({self.max_allowed_p99_latency_us:.0f} us)!"
                )
            else:
                v_status = "VENDOR_LATENCY_HEALTHY"
                logger.info(
                    f"VENDOR LATENCY HEALTHY [{v_id}]: P50 = {p50:.1f} us, P99 = {p99:.1f} us "
                    f"(<= {self.max_allowed_p99_latency_us:.0f} us SLA limit)."
                )

            vendor_metrics_dict[v_id] = VendorLatencyMetrics(
                vendor_id=v_id,
                sample_count=n,
                mean_latency_us=round(mean_lat, 2),
                std_dev_jitter_us=round(jitter, 2),
                p50_us=p50,
                p90_us=p90,
                p95_us=p95,
                p99_us=p99,
                p99_9_us=p99_9,
                avg_vendor_transport_us=avg_trans,
                avg_network_wire_us=avg_wire,
                avg_app_processing_us=avg_proc,
                is_sla_compliant=is_sla_ok,
                status=v_status
            )

        if breaching_vendors:
            report_status = "VENDOR_SLA_BREACH_ALERT"
            notes = f"VENDOR SLA ALERT: Breaching vendors = {breaching_vendors} (P99 > {self.max_allowed_p99_latency_us} us)."
        else:
            report_status = "ALL_VENDORS_HEALTHY"
            notes = f"ALL VENDORS HEALTHY: All {len(vendor_groups)} vendor feeds meet P99 SLA <= {self.max_allowed_p99_latency_us} us."

        return VendorLatencyReport(
            total_samples_processed=len(samples),
            vendor_metrics=vendor_metrics_dict,
            sla_breaching_vendors=breaching_vendors,
            status=report_status,
            audit_notes=notes
        )
