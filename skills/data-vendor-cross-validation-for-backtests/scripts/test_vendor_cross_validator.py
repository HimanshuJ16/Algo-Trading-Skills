"""
Unit tests for data-vendor-cross-validation-for-backtests skill.
"""
import unittest
from vendor_cross_validator import DataVendorCrossValidator, OHLCVBar


class TestDataVendorCrossValidator(unittest.TestCase):

    def setUp(self):
        self.validator = DataVendorCrossValidator(
            price_discrepancy_threshold_bps=50.0,
            missing_bar_tolerance_pct=1.0,
        )

    def test_matching_data_passes(self):
        bars_a = [
            OHLCVBar("2024-01-01", 100.0, 101.0, 99.0, 100.5, 10000),
            OHLCVBar("2024-01-02", 100.5, 102.0, 100.0, 101.0, 12000),
        ]
        bars_b = [
            OHLCVBar("2024-01-01", 100.0, 101.0, 99.0, 100.52, 10000),
            OHLCVBar("2024-01-02", 100.5, 102.0, 100.0, 101.02, 12000),
        ]

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertTrue(report.is_passed)
        self.assertEqual(report.matched_bars, 2)

    def test_price_discrepancy_flags_bars(self):
        bars_a = [
            OHLCVBar("2024-01-01", 100.0, 101.0, 99.0, 100.0, 10000),
            OHLCVBar("2024-01-02", 100.0, 102.0, 99.0, 101.0, 12000),
        ]
        bars_b = [
            OHLCVBar("2024-01-01", 100.0, 101.0, 99.0, 101.0, 10000),  # 100 bps off
            OHLCVBar("2024-01-02", 100.0, 102.0, 99.0, 101.0, 12000),
        ]

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertFalse(report.is_passed)
        self.assertEqual(len(report.flagged_bars), 1)
        self.assertIn("CROSS-VALIDATION FAILED", report.message)

    def test_missing_bars_detected(self):
        bars_a = [OHLCVBar(f"2024-01-{i:02d}", 100.0, 101.0, 99.0, 100.0, 1000) for i in range(1, 11)]
        bars_b = [OHLCVBar(f"2024-01-{i:02d}", 100.0, 101.0, 99.0, 100.0, 1000) for i in range(1, 9)]  # Missing 2

        report = self.validator.validate("MSFT", bars_a, bars_b)

        self.assertFalse(report.is_passed)
        self.assertEqual(report.missing_in_b, 2)


if __name__ == "__main__":
    unittest.main()
