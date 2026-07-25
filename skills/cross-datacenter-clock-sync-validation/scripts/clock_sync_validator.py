"""
cross-datacenter-clock-sync-validation: Multi-region PTP/NTP clock drift offset calculator
and cross-datacenter clock sync safety veto engine.
"""
from dataclasses import dataclass, field
import logging
import math
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ClockSyncHealth(Enum):
    EXCELLENT = "EXCELLENT"      # <= 0.1ms (PTP)
    ACCEPTABLE = "ACCEPTABLE"    # <= 1.0ms (NTP)
    DEGRADED = "DEGRADED"        # <= 5.0ms (Warn)
    BREACH = "BREACH"            # > 5.0ms (Veto)


@dataclass
class DatacenterClockProbe:
    region_id: str
    datacenter_name: str
    timestamp_sec: float
    reported_offset_ms: float
    rtt_ms: float


@dataclass
class CrossDatacenterSyncReport:
    pairwise_drift_ms: Dict[str, float]
    health: ClockSyncHealth
    is_arbitration_allowed: bool
    max_drift_ms: float
    message: str


class CrossDatacenterClockSyncValidator:
    """
    Validates clock synchronization accuracy across multi-region datacenters
    and enforces safety vetoes on cross-datacenter strategy arbitration.
    """

    def __init__(self, max_allowed_drift_ms: float = 1.0):
        self.max_allowed_drift_ms = max_allowed_drift_ms

    def validate_datacenter_sync(
        self,
        node_probes: List[DatacenterClockProbe],
    ) -> CrossDatacenterSyncReport:
        """
        Calculates pairwise clock drift across datacenter node probes and evaluates sync health.
        """
        if len(node_probes) < 2:
            return CrossDatacenterSyncReport(
                pairwise_drift_ms={},
                health=ClockSyncHealth.EXCELLENT,
                is_arbitration_allowed=True,
                max_drift_ms=0.0,
                message="Single region node probed. Arbitration permitted.",
            )

        pairwise_drift: Dict[str, float] = {}
        max_observed_drift = 0.0

        for i in range(len(node_probes)):
            for j in range(i + 1, len(node_probes)):
                node_a = node_probes[i]
                node_b = node_probes[j]

                # Drift = |(T_A + offset_A) - (T_B + offset_B)|
                time_a = node_a.timestamp_sec + (node_a.reported_offset_ms / 1000.0)
                time_b = node_b.timestamp_sec + (node_b.reported_offset_ms / 1000.0)
                drift_ms = abs(time_a - time_b) * 1000.0

                pair_key = f"{node_a.region_id}<->{node_b.region_id}"
                pairwise_drift[pair_key] = round(drift_ms, 4)
                if drift_ms > max_observed_drift:
                    max_observed_drift = drift_ms

        # Evaluate health tier
        if max_observed_drift <= 0.1:
            health = ClockSyncHealth.EXCELLENT
        elif max_observed_drift <= self.max_allowed_drift_ms:
            health = ClockSyncHealth.ACCEPTABLE
        elif max_observed_drift <= 5.0:
            health = ClockSyncHealth.DEGRADED
        else:
            health = ClockSyncHealth.BREACH

        is_allowed = max_observed_drift <= self.max_allowed_drift_ms

        if not is_allowed:
            msg = (
                f"CLOCK SYNC VETO: Max inter-region drift {max_observed_drift:.3f}ms "
                f"exceeds limit {self.max_allowed_drift_ms:.3f}ms. Cross-region arbitration blocked!"
            )
            logger.error(msg)
        else:
            msg = f"Clock sync verified across {len(node_probes)} datacenters. Max drift: {max_observed_drift:.3f}ms."
            logger.info(msg)

        return CrossDatacenterSyncReport(
            pairwise_drift_ms=pairwise_drift,
            health=health,
            is_arbitration_allowed=is_allowed,
            max_drift_ms=round(max_observed_drift, 4),
            message=msg,
        )
