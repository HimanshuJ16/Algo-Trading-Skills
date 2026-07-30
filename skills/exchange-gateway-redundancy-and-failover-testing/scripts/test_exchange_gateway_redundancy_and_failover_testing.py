import unittest
from exchange_gateway_redundancy_and_failover_testing import (
    ExchangeGatewayRedundancyEngine, GatewayNodeConfig, InFlightOrder, GatewayFailoverAuditReport
)

class TestExchangeGatewayRedundancyEngine(unittest.TestCase):

    def setUp(self):
        self.primary = GatewayNodeConfig(
            gateway_id="GW_PRIMARY_FIX", ip_address="192.168.1.10", port=9800,
            role="PRIMARY", status="ACTIVE", last_sent_seq_num=150,
            heartbeat_delay_ms=50.0, latency_rtt_ms=2.5, tcp_connected=True
        )
        self.secondary = GatewayNodeConfig(
            gateway_id="GW_SECONDARY_FIX", ip_address="192.168.1.11", port=9800,
            role="SECONDARY", status="STANDBY", last_sent_seq_num=145,
            heartbeat_delay_ms=0.0, latency_rtt_ms=2.8, tcp_connected=True
        )
        self.engine = ExchangeGatewayRedundancyEngine(
            primary_config=self.primary,
            secondary_config=self.secondary,
            max_heartbeat_delay_ms=3000.0,
            max_latency_rtt_ms=100.0
        )

    def test_healthy_primary_no_failover(self):
        report = self.engine.audit_gateway_health_and_failover([])
        self.assertIsNone(report)
        self.assertEqual(self.engine.active_gateway_id, "GW_PRIMARY_FIX")

    def test_heartbeat_timeout_triggers_failover(self):
        # Degrade primary with 4,500ms heartbeat delay (> 3,000ms threshold)
        self.primary.heartbeat_delay_ms = 4500.0
        
        in_flight = [
            InFlightOrder("ORD_101", "AAPL", "BUY", 100, 185.0, "PENDING_NEW"),
            InFlightOrder("ORD_102", "AAPL", "SELL", 50, 186.0, "FILLED")
        ]

        report = self.engine.audit_gateway_health_and_failover(in_flight)
        
        self.assertIsNotNone(report)
        self.assertEqual(report.status, "FAILOVER_SUCCESS")
        self.assertEqual(report.failed_gateway_id, "GW_PRIMARY_FIX")
        self.assertEqual(report.promoted_gateway_id, "GW_SECONDARY_FIX")
        self.assertEqual(report.synced_seq_num, 150)
        self.assertEqual(report.in_flight_orders_retransmitted_count, 1)
        self.assertTrue(report.retransmitted_orders[0].poss_dup_flag)
        self.assertEqual(self.secondary.status, "ACTIVE")

if __name__ == '__main__':
    unittest.main()
