import unittest
from capacity_planning_for_symbol_universe_growth import (
    CapacityPlanner,
    HardwareSpec,
    UniverseSpec
)

class TestCapacityPlanner(unittest.TestCase):
    
    def setUp(self):
        # 10 GbE NIC, JSON parsing (100k msgs/sec/core), 128GB RAM
        self.hw = HardwareSpec(
            nic_bandwidth_mbps=10000.0,
            cpu_msgs_per_sec_per_core=100_000,
            available_ram_gb=128.0
        )
        self.planner = CapacityPlanner(self.hw)

    def test_small_universe(self):
        # 100 symbols, 500 msgs/sec each (50k msgs/sec total)
        # 200 bytes per msg -> 10 MB/s -> 80 Mbps
        universe = UniverseSpec(
            num_symbols=100,
            peak_ticks_per_sec_per_symbol=500,
            bytes_per_msg=200,
            state_memory_mb_per_symbol=10.0 # 1GB total
        )
        report = self.planner.evaluate(universe)
        
        self.assertTrue(report.is_viable)
        self.assertEqual(report.total_peak_msgs_per_sec, 50_000)
        self.assertAlmostEqual(report.required_network_mbps, 80.0)
        self.assertEqual(report.required_cpu_cores, 1)
        self.assertTrue(report.required_ram_gb < 1.0)

    def test_network_bottleneck_massive_universe(self):
        # 10,000 symbols, 1,000 msgs/sec each (10M msgs/sec total)
        # 200 bytes per msg -> 2 GB/s -> 16,000 Mbps
        # This will crush a 10GbE link
        universe = UniverseSpec(
            num_symbols=10_000,
            peak_ticks_per_sec_per_symbol=1000,
            bytes_per_msg=200,
            state_memory_mb_per_symbol=10.0
        )
        report = self.planner.evaluate(universe)
        
        self.assertFalse(report.is_viable)
        self.assertTrue(report.network_bottleneck)
        self.assertEqual(report.required_network_mbps, 16000.0)
        
        # 10M msgs / 100k per core = 100 cores -> CPU bottleneck too
        self.assertTrue(report.cpu_bottleneck)
        self.assertEqual(report.required_cpu_cores, 100)

if __name__ == '__main__':
    unittest.main()
