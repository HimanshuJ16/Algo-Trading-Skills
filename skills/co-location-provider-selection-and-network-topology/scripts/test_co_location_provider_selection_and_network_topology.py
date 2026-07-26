import unittest
from co_location_provider_selection_and_network_topology import (
    ColocationTopologyEvaluator, FacilitySpec, NetworkLinkSpec, LatencyBudgetResult
)

class TestColocationTopologyEvaluator(unittest.TestCase):

    def setUp(self):
        self.evaluator = ColocationTopologyEvaluator()

    def test_fiber_latency_calculation(self):
        # 100 km fiber link with 2 switches (200ns each = 0.4us)
        # Propagation: 100 km * 5 us/km = 500 us
        # Total one way = 500.4 us, RTT = 1000.8 us
        link = NetworkLinkSpec(
            source="NY4", destination="CARTERET",
            distance_km=100.0, medium_type="FIBER",
            num_switches=2, switch_latency_us=0.200
        )
        res = self.evaluator.calculate_latency_budget(link)
        
        self.assertEqual(res.propagation_delay_us, 500.0)
        self.assertEqual(res.switch_delay_us, 0.4)
        self.assertEqual(res.total_one_way_us, 500.4)
        self.assertEqual(res.total_rtt_us, 1000.8)

    def test_microwave_vs_fiber_speed(self):
        # 1200 km link (e.g. Secaucus NY4 to CME Aurora IL)
        fiber_link = NetworkLinkSpec("NY4", "AURORA", distance_km=1200.0, medium_type="FIBER")
        mw_link = NetworkLinkSpec("NY4", "AURORA", distance_km=1200.0, medium_type="MICROWAVE")
        
        res_fiber = self.evaluator.calculate_latency_budget(fiber_link)
        res_mw = self.evaluator.calculate_latency_budget(mw_link)
        
        # Fiber: 1200 * 5.0 = 6000 us (6.0 ms)
        # MW: 1200 * 3.333 = 3999.6 us (4.0 ms)
        self.assertEqual(res_fiber.propagation_delay_us, 6000.0)
        self.assertAlmostEqual(res_mw.propagation_delay_us, 3999.6, places=1)
        self.assertLess(res_mw.total_one_way_us, res_fiber.total_one_way_us)

    def test_tco_calculation(self):
        fac = FacilitySpec(
            name="Equinix NY4", location_code="NY4",
            rack_cost_mrc=2500.0, power_kw=5.0,
            power_cost_per_kw=200.0, cross_connect_mrc=500.0
        )
        # Power = 5.0 * 200 = 1000. TCO = 2500 + 1000 + 500 = 4000.0
        tco = self.evaluator.calculate_facility_tco(fac)
        self.assertEqual(tco, 4000.0)

if __name__ == '__main__':
    unittest.main()
