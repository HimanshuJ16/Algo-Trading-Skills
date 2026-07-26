import numpy as np
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ReplicationHeartbeat:
    heartbeat_id: str
    origin_region: str                 # e.g. 'us-east-1'
    replica_region: str                # e.g. 'eu-west-1'
    primary_write_timestamp_ms: float
    replica_receive_timestamp_ms: float

@dataclass
class ReplicationLagHealthReport:
    origin_region: str
    replica_region: str
    sample_count: int
    mean_lag_ms: float
    p95_lag_ms: float
    p99_lag_ms: float
    status: str                         # 'HEALTHY', 'DEGRADED_WARNING', 'UNSAFE_STALE'
    is_read_failover_recommended: bool
    recommendation_message: str

class CrossRegionReplicationLagMonitor:
    """
    Quantitative observability engine for computing cross-region database/queue replication lag,
    tracking P95/P99 SLAs, and triggering stale-read failover directives.
    """
    def __init__(self, p99_warning_threshold_ms: float = 100.0, p99_unsafe_threshold_ms: float = 500.0):
        self.p99_warning_threshold_ms = p99_warning_threshold_ms
        self.p99_unsafe_threshold_ms = p99_unsafe_threshold_ms

    def compute_replication_lags(self, heartbeats: List[ReplicationHeartbeat]) -> List[float]:
        """
        Calculates replication lag delta t = t_replica - t_primary for each heartbeat.
        """
        lags = []
        for hb in heartbeats:
            lag = float(hb.replica_receive_timestamp_ms - hb.primary_write_timestamp_ms)
            lags.append(max(0.0, lag))
        return lags

    def evaluate_replica_health(
        self,
        origin_region: str,
        replica_region: str,
        heartbeats: List[ReplicationHeartbeat]
    ) -> ReplicationLagHealthReport:
        """
        Evaluates replication lag percentiles (P95/P99) for a region pair and returns health status.
        """
        filtered = [
            hb for hb in heartbeats 
            if hb.origin_region == origin_region and hb.replica_region == replica_region
        ]

        if not filtered:
            return ReplicationLagHealthReport(
                origin_region=origin_region, replica_region=replica_region, sample_count=0,
                mean_lag_ms=0.0, p95_lag_ms=0.0, p99_lag_ms=0.0, status="HEALTHY",
                is_read_failover_recommended=False, recommendation_message="No heartbeats logged."
            )

        lags = np.array(self.compute_replication_lags(filtered))
        mean_lag = float(np.mean(lags))
        p95_lag = float(np.percentile(lags, 95))
        p99_lag = float(np.percentile(lags, 99))

        if p99_lag >= self.p99_unsafe_threshold_ms:
            status = "UNSAFE_STALE"
            failover = True
            msg = (
                f"UNSAFE REPLICATION LAG [{origin_region} -> {replica_region}]: P99={p99_lag:.1f}ms >= "
                f"{self.p99_unsafe_threshold_ms}ms threshold. Secondary replica is STALE! Disabling local reads & triggering failover to primary."
            )
            logger.critical(msg)
        elif p99_lag >= self.p99_warning_threshold_ms:
            status = "DEGRADED_WARNING"
            failover = False
            msg = f"DEGRADED REPLICATION LAG [{origin_region} -> {replica_region}]: P99={p99_lag:.1f}ms."
            logger.warning(msg)
        else:
            status = "HEALTHY"
            failover = False
            msg = f"Replication healthy [{origin_region} -> {replica_region}]: P99={p99_lag:.1f}ms."

        return ReplicationLagHealthReport(
            origin_region=origin_region,
            replica_region=replica_region,
            sample_count=len(filtered),
            mean_lag_ms=round(mean_lag, 2),
            p95_lag_ms=round(p95_lag, 2),
            p99_lag_ms=round(p99_lag, 2),
            status=status,
            is_read_failover_recommended=failover,
            recommendation_message=msg
        )
