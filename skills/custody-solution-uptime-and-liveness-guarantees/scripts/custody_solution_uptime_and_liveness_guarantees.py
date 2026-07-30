import numpy as np
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class CustodyHealthProbe:
    probe_id: str
    timestamp_ms: float
    is_api_healthy: bool
    signing_latency_ms: float
    active_mpc_nodes: int

@dataclass
class ProviderSlaConfig:
    provider_id: str
    provider_name: str
    target_uptime_pct: float            # e.g. 99.9%
    max_signing_latency_ms: float       # e.g. 2000.0 ms
    mpc_threshold_k: int                 # e.g. 2 of 3
    mpc_total_n: int                    # e.g. 3

@dataclass
class CustodyLivenessAuditReport:
    provider_id: str
    rolling_uptime_pct: float
    p99_signing_latency_ms: float
    current_active_mpc_nodes: int
    is_mpc_quorum_maintained: bool
    status: str                         # 'HEALTHY', 'DEGRADED_SLA_BREACH', 'QUORUM_LOST_LIVENESS_HALT'
    is_failover_recommended: bool
    recommendations: List[str]

class CustodyLivenessMonitorEngine:
    """
    Real-time custody liveness monitoring engine for tracking API uptime SLAs (99.9%+),
    MPC threshold quorum (k-of-n), and signing latency SLAs, triggering failover upon availability breaches.
    """
    def __init__(self, config: ProviderSlaConfig):
        self.config = config

    def audit_liveness(self, probes: List[CustodyHealthProbe]) -> CustodyLivenessAuditReport:
        """
        Audits rolling uptime, MPC quorum status, and P99 signing latency.
        """
        if not probes:
            return CustodyLivenessAuditReport(
                provider_id=self.config.provider_id, rolling_uptime_pct=100.0,
                p99_signing_latency_ms=0.0, current_active_mpc_nodes=self.config.mpc_total_n,
                is_mpc_quorum_maintained=True, status="HEALTHY", is_failover_recommended=False,
                recommendations=["No telemetry probes logged."]
            )

        total_probes = len(probes)
        healthy_probes = sum(1 for p in probes if p.is_api_healthy)
        uptime_pct = round((healthy_probes / float(total_probes)) * 100.0, 2)

        latencies = np.array([p.signing_latency_ms for p in probes if p.is_api_healthy])
        p99_lat = float(np.percentile(latencies, 99)) if len(latencies) > 0 else 0.0

        latest_probe = probes[-1]
        active_nodes = latest_probe.active_mpc_nodes
        is_quorum_ok = active_nodes >= self.config.mpc_threshold_k

        recommendations = []
        failover = False

        if not is_quorum_ok:
            status = "QUORUM_LOST_LIVENESS_HALT"
            failover = True
            msg = (
                f"MPC QUORUM LOST [{self.config.provider_name}]: Active nodes = {active_nodes} < "
                f"Threshold k={self.config.mpc_threshold_k}! Automated signing HALTED. Triggering failover."
            )
            recommendations.append(msg)
            logger.critical(msg)
        elif uptime_pct < self.config.target_uptime_pct:
            status = "DEGRADED_SLA_BREACH"
            failover = True
            msg = (
                f"UPTIME SLA BREACH [{self.config.provider_name}]: Uptime {uptime_pct:.2f}% < "
                f"Target {self.config.target_uptime_pct}%. Triggering failover to secondary provider."
            )
            recommendations.append(msg)
            logger.error(msg)
        elif p99_lat > self.config.max_signing_latency_ms:
            status = "DEGRADED_SLA_BREACH"
            failover = False
            msg = (
                f"SIGNING LATENCY SLA BREACH [{self.config.provider_name}]: P99 Latency {p99_lat:.1f}ms > "
                f"Max SLA {self.config.max_signing_latency_ms}ms."
            )
            recommendations.append(msg)
            logger.warning(msg)
        else:
            status = "HEALTHY"
            recommendations.append("Custody provider SLA compliant and MPC quorum healthy.")

        return CustodyLivenessAuditReport(
            provider_id=self.config.provider_id,
            rolling_uptime_pct=uptime_pct,
            p99_signing_latency_ms=round(p99_lat, 2),
            current_active_mpc_nodes=active_nodes,
            is_mpc_quorum_maintained=is_quorum_ok,
            status=status,
            is_failover_recommended=failover,
            recommendations=recommendations
        )
