import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class GatewayNodeConfig:
    gateway_id: str
    ip_address: str
    port: int
    role: str                           # 'PRIMARY' or 'SECONDARY'
    status: str                         # 'ACTIVE', 'STANDBY', 'DEGRADED', 'DISCONNECTED'
    last_sent_seq_num: int
    heartbeat_delay_ms: float
    latency_rtt_ms: float
    tcp_connected: bool

@dataclass
class InFlightOrder:
    cl_ord_id: str
    symbol: str
    side: str
    order_qty: int
    price: float
    order_status: str                   # 'PENDING_NEW', 'FILLED'
    poss_dup_flag: bool = False         # FIX Tag 43

@dataclass
class GatewayFailoverAuditReport:
    failed_gateway_id: str
    promoted_gateway_id: str
    failover_trigger_reason: str
    recovery_time_ms: float             # RTO achieved
    synced_seq_num: int
    in_flight_orders_retransmitted_count: int
    retransmitted_orders: List[InFlightOrder]
    status: str                         # 'FAILOVER_SUCCESS', 'FAILOVER_FAILED'
    audit_notes: str

class ExchangeGatewayRedundancyEngine:
    """
    Quantitative exchange connectivity and resilience engine for orchestrating Active-Standby FIX/ETI gateway failover,
    sequence number resynchronization, and in-flight order audit tests.
    """
    def __init__(
        self,
        primary_config: GatewayNodeConfig,
        secondary_config: GatewayNodeConfig,
        max_heartbeat_delay_ms: float = 3000.0,
        max_latency_rtt_ms: float = 100.0
    ):
        self.primary = primary_config
        self.secondary = secondary_config
        self.max_heartbeat_delay_ms = max_heartbeat_delay_ms
        self.max_latency_rtt_ms = max_latency_rtt_ms
        self.active_gateway_id = primary_config.gateway_id if primary_config.status == "ACTIVE" else secondary_config.gateway_id

    def audit_gateway_health_and_failover(
        self,
        in_flight_orders: List[InFlightOrder]
    ) -> Optional[GatewayFailoverAuditReport]:
        """
        Audits active gateway health metrics and executes failover if primary degrades or disconnects.
        """
        active_node = self.primary if self.active_gateway_id == self.primary.gateway_id else self.secondary
        standby_node = self.secondary if active_node == self.primary else self.primary

        # Evaluate failover triggers
        trigger_reason = None
        if not active_node.tcp_connected:
            trigger_reason = f"TCP DISCONNECT: Active gateway '{active_node.gateway_id}' lost socket connection."
        elif active_node.heartbeat_delay_ms > self.max_heartbeat_delay_ms:
            trigger_reason = f"HEARTBEAT TIMEOUT: Delay {active_node.heartbeat_delay_ms:.1f}ms exceeds threshold {self.max_heartbeat_delay_ms:.1f}ms."
        elif active_node.latency_rtt_ms > self.max_latency_rtt_ms:
            trigger_reason = f"LATENCY DEGRADATION: RTT {active_node.latency_rtt_ms:.1f}ms exceeds SLA {self.max_latency_rtt_ms:.1f}ms."

        if not trigger_reason:
            logger.info(f"GATEWAY HEALTHY [{active_node.gateway_id}]: Connection active. RTT={active_node.latency_rtt_ms:.1f}ms.")
            return None

        # Execute Failover
        logger.warning(f"EXECUTING GATEWAY FAILOVER: {trigger_reason}")
        start_t = time.perf_counter()

        # 1. Demote active node
        active_node.status = "DISCONNECTED" if not active_node.tcp_connected else "DEGRADED"

        # 2. Promote standby node
        standby_node.status = "ACTIVE"
        self.active_gateway_id = standby_node.gateway_id

        # 3. Synchronize Sequence Number
        synced_seq = max(active_node.last_sent_seq_num, standby_node.last_sent_seq_num)
        standby_node.last_sent_seq_num = synced_seq

        # 4. Reconcile In-Flight Orders with PossDupFlag = True (FIX Tag 43 = Y)
        retransmitted: List[InFlightOrder] = []
        for ord in in_flight_orders:
            if ord.order_status == "PENDING_NEW":
                ord.poss_dup_flag = True
                retransmitted.append(ord)
                logger.warning(f"RETRANSMITTING IN-FLIGHT ORDER [{ord.cl_ord_id}]: PossDupFlag=True on '{standby_node.gateway_id}'.")

        elapsed_ms = round((time.perf_counter() - start_t) * 1000.0, 2)

        notes = (
            f"GATEWAY FAILOVER COMPLETE: Switch from '{active_node.gateway_id}' to '{standby_node.gateway_id}' "
            f"in {elapsed_ms:.2f}ms. Synced MsgSeqNum={synced_seq}. {len(retransmitted)} in-flight orders resent with PossDupFlag=Y."
        )
        logger.info(notes)

        return GatewayFailoverAuditReport(
            failed_gateway_id=active_node.gateway_id,
            promoted_gateway_id=standby_node.gateway_id,
            failover_trigger_reason=trigger_reason,
            recovery_time_ms=elapsed_ms,
            synced_seq_num=synced_seq,
            in_flight_orders_retransmitted_count=len(retransmitted),
            retransmitted_orders=retransmitted,
            status="FAILOVER_SUCCESS",
            audit_notes=notes
        )
