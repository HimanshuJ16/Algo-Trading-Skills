"""
Unit tests for cross-datacenter-clock-sync-validation skill.
"""
import unittest
from clock_sync_validator import ClockSyncHealth, CrossDatacenterClockSyncValidator, DatacenterClockProbe


class TestCrossDatacenterClockSyncValidator(unittest.TestCase):

    def setUp(self):
        self.validator = CrossDatacenterClockSyncValidator(max_allowed_drift_ms=1.0)

    def test_acceptable_sync_across_regions(self):
        t0 = 1784948000.0
        probes = [
            DatacenterClockProbe("us-east-1", "NY4", t0, 0.05, 10.0),
            DatacenterClockProbe("eu-west-1", "LD4", t0 + 0.0003, 0.02, 70.0),  # 0.3ms delta
        ]

        report = self.validator.validate_datacenter_sync(probes)

        self.assertTrue(report.is_arbitration_allowed)
        self.assertIn(report.health, [ClockSyncHealth.EXCELLENT, ClockSyncHealth.ACCEPTABLE])
        self.assertLessEqual(report.max_drift_ms, 1.0)

    def test_clock_sync_drift_breach_veto(self):
        t0 = 1784948000.0
        probes = [
            DatacenterClockProbe("us-east-1", "NY4", t0, 0.0, 10.0),
            DatacenterClockProbe("ap-southeast-1", "SG1", t0 + 0.008, 0.0, 180.0),  # 8.0ms drift
        ]

        report = self.validator.validate_datacenter_sync(probes)

        self.assertFalse(report.is_arbitration_allowed)
        self.assertEqual(report.health, ClockSyncHealth.BREACH)
        self.assertGreater(report.max_drift_ms, 1.0)


if __name__ == "__main__":
    unittest.main()
