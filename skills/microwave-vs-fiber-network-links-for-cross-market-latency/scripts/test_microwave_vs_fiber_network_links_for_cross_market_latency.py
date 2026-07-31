import unittest
from microwave_vs_fiber_network_links_for_cross_market_latency import (
    NetworkLinkArbitratorEngine, NetworkLinkConfig, WeatherLinkTelemetry, NetworkLinkReport
)

class TestNetworkLinkArbitratorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = NetworkLinkArbitratorEngine(max_packet_loss_threshold_pct=1.0)
        # Chicago CME to NJ Nasdaq Corridor (~1,200 km Microwave line-of-sight vs ~1,350 km Fiber cable)
        self.micro_cfg = NetworkLinkConfig("MW_CHI_NJ_01", "MICROWAVE", "CHICAGO_CME_TO_NJ_NASDAQ", distance_km=1200.0)
        self.fiber_cfg = NetworkLinkConfig("FB_CHI_NJ_01", "FIBER", "CHICAGO_CME_TO_NJ_NASDAQ", distance_km=1350.0)

    def test_microwave_primary_route_clear_weather(self):
        # Clear weather telemetry -> ROUTE_MICROWAVE_PRIMARY!
        telemetry = WeatherLinkTelemetry("MW_CHI_NJ_01", current_weather="CLEAR", packet_loss_pct=0.05, signal_to_noise_ratio_db=35.0)

        report = self.engine.arbitrate_cross_market_links(self.micro_cfg, self.fiber_cfg, telemetry)

        self.assertEqual(report.status, "ROUTE_MICROWAVE_PRIMARY")
        self.assertFalse(report.is_failover_active)
        self.assertEqual(report.selected_link_type, "MICROWAVE")
        self.assertGreater(report.latency_savings_ms, 5.0) # > 5ms RTT savings!
        self.assertGreater(report.latency_advantage_pct, 30.0)

    def test_fiber_failover_during_heavy_rain(self):
        # Heavy rain telemetry -> FAILOVER_TO_FIBER_RAIN_FADE!
        telemetry = WeatherLinkTelemetry("MW_CHI_NJ_01", current_weather="HEAVY_RAIN", packet_loss_pct=2.5, signal_to_noise_ratio_db=12.0)

        report = self.engine.arbitrate_cross_market_links(self.micro_cfg, self.fiber_cfg, telemetry)

        self.assertEqual(report.status, "FAILOVER_TO_FIBER_RAIN_FADE")
        self.assertTrue(report.is_failover_active)
        self.assertEqual(report.selected_link_type, "FIBER")
        self.assertEqual(report.selected_routing_link_id, "FB_CHI_NJ_01")

if __name__ == '__main__':
    unittest.main()
