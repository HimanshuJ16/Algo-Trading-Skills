import unittest
from iceberg_order_native_broker_support_vs_simulation import (
    IcebergExecutionRouterEngine, BrokerVenueConfig, IcebergOrderRequest, IcebergExecutionReport
)

class TestIcebergExecutionRouterEngine(unittest.TestCase):

    def setUp(self):
        self.router = IcebergExecutionRouterEngine()

    def test_native_iceberg_routing(self):
        venue = BrokerVenueConfig("IBKR", supports_native_iceberg=True, native_parameter_name="displaySize")
        req = IcebergOrderRequest("AAPL", "BUY", total_quantity=10_000, target_display_quantity=500, limit_price=150.00)

        report = self.router.route_iceberg_order(req, venue)

        self.assertEqual(report.execution_mode, "NATIVE_ICEBERG")
        self.assertEqual(report.status, "ROUTED_NATIVE_EXCHANGE")
        self.assertEqual(report.native_display_parameter_sent, "displaySize=500")
        self.assertEqual(report.total_refill_latency_penalty_ms, 0.0)

    def test_synthetic_iceberg_simulation_routing(self):
        venue = BrokerVenueConfig("RETAIL_REST_BROKER", supports_native_iceberg=False, client_network_latency_ms=30.0)
        req = IcebergOrderRequest(
            "AAPL", "BUY", total_quantity=2_000, target_display_quantity=500, limit_price=150.00, slice_randomization_pct=0.20
        )

        report = self.router.route_iceberg_order(req, venue)

        self.assertEqual(report.execution_mode, "SYNTHETIC_SIMULATION")
        self.assertEqual(report.status, "INITIALIZED_SYNTHETIC_SIMULATION")
        self.assertGreater(len(report.synthetic_child_slices), 1)
        self.assertGreater(report.total_refill_latency_penalty_ms, 0.0)

        # Verify total child slice quantity equals total parent quantity
        total_child_qty = sum(s.slice_quantity for s in report.synthetic_child_slices)
        self.assertEqual(total_child_qty, 2_000)

if __name__ == '__main__':
    unittest.main()
