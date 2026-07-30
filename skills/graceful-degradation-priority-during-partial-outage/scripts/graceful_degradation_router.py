import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Priority Levels:
# P1_CRITICAL: Risk Limit Checks, Emergency Stop / MassCancel Orders, Heartbeats
# P2_HIGH: Position Exit Orders, Stop-Loss Executions, Fill Reconciliations
# P3_MEDIUM: New Signal Entry Orders, Child Order Slices
# P4_LOW: Non-critical Analytics, Historical Tick Logging, GUI Streaming

@dataclass
class TradingTask:
    task_id: str
    task_type: str                      # e.g. 'MASS_CANCEL', 'STOP_LOSS_EXIT', 'NEW_ENTRY_ORDER', 'TICK_LOGGING'
    priority: str                       # 'P1_CRITICAL', 'P2_HIGH', 'P3_MEDIUM', 'P4_LOW'
    payload: Dict[str, str] = field(default_factory=dict)

@dataclass
class SystemHealthMetrics:
    cpu_utilization_pct: float
    network_packet_loss_pct: float
    db_connection_latency_ms: float

@dataclass
class GracefulDegradationAuditReport:
    system_mode: str                    # 'NORMAL_HEALTHY', 'PARTIAL_DEGRADATION', 'CRITICAL_OUTAGE'
    total_tasks_received: int
    processed_tasks_count: int
    shed_tasks_count: int
    processed_task_ids: List[str]
    shed_task_ids: List[str]
    audit_notes: str

class GracefulDegradationRouterEngine:
    """
    Fault-tolerant trading architecture engine for managing multi-tier priority load shedding
    (P1 Risk/Cancel, P2 Exits, P3 Entries, P4 Analytics) during partial system outages.
    """
    def __init__(
        self,
        partial_degradation_cpu_pct: float = 75.0,
        partial_degradation_packet_loss_pct: float = 1.0,
        critical_outage_packet_loss_pct: float = 10.0
    ):
        self.partial_degradation_cpu_pct = partial_degradation_cpu_pct
        self.partial_degradation_packet_loss_pct = partial_degradation_packet_loss_pct
        self.critical_outage_packet_loss_pct = critical_outage_packet_loss_pct

    def determine_system_mode(self, health: SystemHealthMetrics) -> str:
        """
        Determines current system operating mode based on health metrics.
        """
        if health.network_packet_loss_pct >= self.critical_outage_packet_loss_pct or health.cpu_utilization_pct >= 90.0:
            return "CRITICAL_OUTAGE"
        elif health.network_packet_loss_pct >= self.partial_degradation_packet_loss_pct or health.cpu_utilization_pct >= self.partial_degradation_cpu_pct:
            return "PARTIAL_DEGRADATION"
        else:
            return "NORMAL_HEALTHY"

    def process_and_filter_tasks(
        self,
        health: SystemHealthMetrics,
        tasks: List[TradingTask]
    ) -> GracefulDegradationAuditReport:
        """
        Filters and load-sheds tasks according to priority hierarchy and current system mode.
        """
        mode = self.determine_system_mode(health)

        processed: List[str] = []
        shed: List[str] = []

        for task in tasks:
            p = task.priority.upper()

            if mode == "NORMAL_HEALTHY":
                # Process all tasks
                processed.append(task.task_id)
            elif mode == "PARTIAL_DEGRADATION":
                # Drop P4 (low analytics/logs). Process P1, P2, P3.
                if p == "P4_LOW":
                    shed.append(task.task_id)
                else:
                    processed.append(task.task_id)
            elif mode == "CRITICAL_OUTAGE":
                # CAPITAL PRESERVATION MODE: Drop P4, P3, P2. Execute P1 ONLY!
                if p == "P1_CRITICAL":
                    processed.append(task.task_id)
                else:
                    shed.append(task.task_id)

        if mode == "CRITICAL_OUTAGE":
            notes = (
                f"SYSTEM CRITICAL OUTAGE ACTIVE (Packet Loss = {health.network_packet_loss_pct:.1f}%, CPU = {health.cpu_utilization_pct:.1f}%): "
                f"CAPITAL PRESERVATION MODE ENABLED. Processed {len(processed)} P1 Critical Risk tasks. Shed {len(shed)} lower priority tasks."
            )
            logger.critical(notes)
        elif mode == "PARTIAL_DEGRADATION":
            notes = (
                f"SYSTEM PARTIAL DEGRADATION ACTIVE (Packet Loss = {health.network_packet_loss_pct:.1f}%, CPU = {health.cpu_utilization_pct:.1f}%): "
                f"LOAD SHEDDING ENABLED. Shed {len(shed)} P4 Low-priority analytics tasks. Processed {len(processed)} P1-P3 execution tasks."
            )
            logger.warning(notes)
        else:
            notes = f"SYSTEM NORMAL HEALTHY: All {len(processed)} tasks processed across priorities P1-P4."
            logger.info(notes)

        return GracefulDegradationAuditReport(
            system_mode=mode,
            total_tasks_received=len(tasks),
            processed_tasks_count=len(processed),
            shed_tasks_count=len(shed),
            processed_task_ids=processed,
            shed_task_ids=shed,
            audit_notes=notes
        )
