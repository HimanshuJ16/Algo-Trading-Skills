import unittest
from hardware_timestamping_vs_software_timestamping_accuracy import (
    TimestampAccuracyAnalyzerEngine, PacketTimestampSample, TimestampAccuracyAuditReport
)

class TestTimestampAccuracyAnalyzerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = TimestampAccuracyAnalyzerEngine(mifid_rts25_max_drift_nanos=100_000) # 100 microseconds

    def test_hardware_compliant_software_fails_rts25(self):
        # UTC Ref = 1,000,000,000 ns
        # Hardware MAC = 1,000,010,000 ns (10 us drift <= 100 us OK!)
        # Kernel = 1,000,020,000 ns (10 us kernel stack delay)
        # Application = 1,000,135,000 ns (125 us app jitter -> Total software drift = 135 us > 100 us FAILED!)
        sample = PacketTimestampSample(
            packet_id="PKT_001",
            hardware_mac_nanos=1_000_010_000,
            kernel_stack_nanos=1_000_020_000,
            application_layer_nanos=1_000_135_000,
            utc_reference_nanos=1_000_000_000
        )
        report = self.engine.analyze_sample(sample)

        self.assertTrue(report.is_hardware_mifid_rts25_compliant)
        self.assertFalse(report.is_software_mifid_rts25_compliant)
        self.assertEqual(report.status, "HARDWARE_COMPLIANT_SOFTWARE_NON_COMPLIANT")
        self.assertEqual(report.total_software_latency_distortion_nanos, 125_000) # 125 us

    def test_both_compliant_under_low_jitter(self):
        # Hardware drift = 5 us, App drift = 25 us <= 100 us -> Both Compliant!
        sample = PacketTimestampSample(
            packet_id="PKT_002",
            hardware_mac_nanos=1_000_005_000,
            kernel_stack_nanos=1_000_010_000,
            application_layer_nanos=1_000_025_000,
            utc_reference_nanos=1_000_000_000
        )
        report = self.engine.analyze_sample(sample)

        self.assertTrue(report.is_hardware_mifid_rts25_compliant)
        self.assertTrue(report.is_software_mifid_rts25_compliant)
        self.assertEqual(report.status, "BOTH_COMPLIANT")

if __name__ == '__main__':
    unittest.main()
