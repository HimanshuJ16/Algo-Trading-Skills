import unittest
from counterparty_monitor import (
    CounterpartyConcentrationMonitor, BrokerProfile, RoutingDecision
)

class TestCounterpartyConcentrationMonitor(unittest.TestCase):

    def setUp(self):
        self.b1 = BrokerProfile(
            broker_id="PB_ALPHA", name="Alpha Prime", max_nav_pct_limit=0.35,
            cds_spread_bps=80.0, max_cds_bps_threshold=250.0,
            current_cash=150_000, current_margin=100_000, current_positions_value=50_000 # $300k (50% NAV)
        )
        self.b2 = BrokerProfile(
            broker_id="PB_BETA", name="Beta Prime", max_nav_pct_limit=0.35,
            cds_spread_bps=90.0, max_cds_bps_threshold=250.0,
            current_cash=100_000, current_margin=50_000, current_positions_value=0.0    # $150k (25% NAV)
        )
        self.b3 = BrokerProfile(
            broker_id="PB_GAMMA", name="Gamma Prime", max_nav_pct_limit=0.35,
            cds_spread_bps=300.0, max_cds_bps_threshold=250.0, # Distressed CDS!
            current_cash=150_000, current_margin=0.0, current_positions_value=0.0       # $150k (25% NAV)
        )
        # Total NAV = $300k + $150k + $150k = $600k
        self.monitor = CounterpartyConcentrationMonitor([self.b1, self.b2, self.b3])

    def test_approved_primary_routing(self):
        # Route small order $5,000 to PB_BETA ($150k + $5k = $155k / $600k = 25.8% <= 35%)
        decision = self.monitor.route_order("PB_BETA", proposed_order_value=5000.0)
        self.assertFalse(decision.is_rerouted)
        self.assertEqual(decision.selected_broker_id, "PB_BETA")

    def test_failover_routing_on_limit_breach(self):
        # Attempt to route $10,000 to PB_ALPHA ($300k + $10k = $310k / $600k = 51.6% > 35%)
        # Breaches PB_ALPHA limit -> Re-routes to PB_BETA ($150k + $10k = $160k / $600k = 26.6% <= 35%)
        decision = self.monitor.route_order("PB_ALPHA", proposed_order_value=10000.0)
        self.assertTrue(decision.is_rerouted)
        self.assertEqual(decision.selected_broker_id, "PB_BETA")

    def test_cds_distress_blocks_routing(self):
        # Attempt to route to PB_GAMMA (CDS 300 bps > 250 bps threshold)
        decision = self.monitor.route_order("PB_GAMMA", proposed_order_value=5000.0)
        # PB_GAMMA is distressed, so it re-routes to PB_BETA
        self.assertTrue(decision.is_rerouted)
        self.assertEqual(decision.selected_broker_id, "PB_BETA")

    def test_compute_hhi(self):
        # Total = 600k. Weights: PB_ALPHA = 300k/600k = 0.5, PB_BETA = 150k/600k = 0.25, PB_GAMMA = 150k/600k = 0.25
        # HHI = 0.5^2 + 0.25^2 + 0.25^2 = 0.25 + 0.0625 + 0.0625 = 0.375
        hhi = self.monitor.compute_hhi()
        self.assertEqual(hhi, 0.375)

if __name__ == '__main__':
    unittest.main()
