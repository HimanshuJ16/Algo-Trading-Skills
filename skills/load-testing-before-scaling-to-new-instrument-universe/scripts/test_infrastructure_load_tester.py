import unittest
from infrastructure_load_tester import (
    InfrastructureLoadTesterEngine, UniverseScaleSpec, HardwareCapacitySpec, LoadTestReport
)

class TestInfrastructureLoadTesterEngine(unittest.TestCase):

    def setUp(self):
        self.engine = InfrastructureLoadTesterEngine(max_safe_utilization_pct=80.0)
        self.hardware = HardwareCapacitySpec(
            available_ram_gb=64.0, max_network_mbps=1000.0, max_db_iops=50000.0
        )

    def test_successful_universe_scaling_load(self):
        # Scale 50 -> 500 stocks (Peak Msg Rate = 500 * 10 * 5 = 25,000 msg/sec)
        # RAM = 500 * 20MB * 1.25 / 1024 = 12.2 GB (19.1% util <= 80%)
        # Net = 25,000 * 320 * 8 / 1e6 = 64.0 Mbps (6.4% util <= 80%) -> PASSED!
        scale_spec = UniverseScaleSpec(
            universe_name="S&P 500 Scaling", current_universe_size=50, target_universe_size=500
        )
        report = self.engine.audit_universe_scaling_load(scale_spec, self.hardware)

        self.assertEqual(report.status, "LOAD_TEST_PASSED_READY_TO_SCALE")
        self.assertEqual(report.projected_peak_msg_rate_per_sec, 25000.0)
        self.assertTrue(report.is_ram_capacity_ok)
        self.assertTrue(report.is_network_capacity_ok)
        self.assertTrue(report.is_db_iops_capacity_ok)

    def test_memory_exceeded_load_failure(self):
        # Scale to 5,000 stocks -> RAM = 5,000 * 20MB * 1.25 / 1024 = 122.07 GB (> 64 GB limit) -> FAILED_MEMORY_EXCEEDED!
        scale_spec = UniverseScaleSpec(
            universe_name="Russell 3000 Scaling", current_universe_size=50, target_universe_size=5000
        )
        report = self.engine.audit_universe_scaling_load(scale_spec, self.hardware)

        self.assertEqual(report.status, "LOAD_TEST_FAILED_MEMORY_EXCEEDED")
        self.assertFalse(report.is_ram_capacity_ok)
        self.assertGreater(report.ram_utilization_pct, 100.0)

if __name__ == '__main__':
    unittest.main()
