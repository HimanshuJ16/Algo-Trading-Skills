"""
Unit tests for data-vendor-cross-validation-for-backtests skill.
"""
import math
import unittest

from vendor_cross_validator import DataVendorCrossValidator, OHLCVBar


def bar(ts: str, close: float, volume: float = 10000.0) -> OHLCVBar:
    """Helper for tests that only care about close and volume."""
    return OHLCVBar(ts, close, close, close, close, volume)


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
        self.assertEqual(report.comparable_bars, 2)

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
        # 1.00 / 100.00 = 1% = 100 bps, derived independently of the implementation.
        self.assertAlmostEqual(report.flagged_bars[0].delta_bps, 100.0, places=6)
        self.assertIn("CROSS-VALIDATION FAILED", report.message)

    def test_missing_bars_detected(self):
        bars_a = [OHLCVBar(f"2024-01-{i:02d}", 100.0, 101.0, 99.0, 100.0, 1000) for i in range(1, 11)]
        bars_b = [OHLCVBar(f"2024-01-{i:02d}", 100.0, 101.0, 99.0, 100.0, 1000) for i in range(1, 9)]  # Missing 2

        report = self.validator.validate("MSFT", bars_a, bars_b)

        self.assertFalse(report.is_passed)
        self.assertEqual(report.missing_in_b, 2)
        self.assertAlmostEqual(report.missing_ratio_pct, 20.0, places=6)

    def test_missing_bars_within_tolerance_pass(self):
        # 1 missing out of 200 union bars = 0.5% <= 1.0% tolerance.
        bars_a = [bar(f"2024-{i:04d}", 100.0) for i in range(200)]
        bars_b = bars_a[:-1]

        report = self.validator.validate("MSFT", bars_a, bars_b)

        self.assertTrue(report.is_passed)
        self.assertAlmostEqual(report.missing_ratio_pct, 0.5, places=6)

    def test_delta_exactly_at_threshold_is_not_flagged(self):
        # 50 bps exactly: threshold is exclusive (> threshold flags).
        bars_a = [bar("2024-01-01", 100.0)]
        bars_b = [bar("2024-01-01", 100.5)]

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertEqual(report.flagged_bars, [])
        self.assertTrue(report.is_passed)
        self.assertAlmostEqual(report.max_close_delta_bps, 50.0, places=6)

    # --- Regression tests: conditions that previously returned a false PASS ---

    def test_both_datasets_empty_raises(self):
        with self.assertRaises(ValueError):
            self.validator.validate("AAPL", [], [])

    def test_nan_close_is_an_integrity_failure_not_a_match(self):
        bars_a = [bar("2024-01-01", float("nan"))]
        bars_b = [bar("2024-01-01", 100.0)]

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertFalse(report.is_passed)
        self.assertEqual(len(report.integrity_issues), 1)
        self.assertEqual(report.comparable_bars, 0)
        self.assertFalse(math.isnan(report.avg_close_delta_bps))

    def test_zero_reference_close_does_not_mask_discrepancy(self):
        # Vendor A emits a 0.0 no-trade sentinel while Vendor B has a real price.
        bars_a = [bar("2024-01-01", 0.0)]
        bars_b = [bar("2024-01-01", 100.0)]

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertFalse(report.is_passed)
        self.assertEqual(len(report.integrity_issues), 1)
        self.assertIn("zero reference close", report.integrity_issues[0].reason)

    def test_negative_close_uses_absolute_denominator(self):
        # Negative settlement prices are real (e.g. WTI crude, April 2020).
        bars_a = [bar("2024-01-01", -40.0)]
        bars_b = [bar("2024-01-01", -41.0)]

        report = self.validator.validate("CL", bars_a, bars_b)

        # 1.0 / 40.0 = 2.5% = 250 bps, derived independently.
        self.assertAlmostEqual(report.max_close_delta_bps, 250.0, places=2)
        self.assertFalse(report.is_passed)
        self.assertEqual(len(report.flagged_bars), 1)

    def test_duplicate_timestamps_are_reported(self):
        bars_a = [bar("2024-01-01", 100.0), bar("2024-01-01", 100.0)]
        bars_b = [bar("2024-01-01", 100.0)]

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertFalse(report.is_passed)
        self.assertEqual(len(report.integrity_issues), 1)
        self.assertIn("duplicate timestamp", report.integrity_issues[0].reason)
        self.assertEqual(report.integrity_issues[0].vendor, "A")

    def test_volume_spike_is_flagged_but_does_not_fail_verdict(self):
        bars_a = [bar("2024-01-01", 100.0, volume=10000.0)]
        bars_b = [bar("2024-01-01", 100.0, volume=50000.0)]  # 5.0x

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertEqual(len(report.volume_flagged_bars), 1)
        self.assertAlmostEqual(report.volume_flagged_bars[0].ratio, 5.0, places=6)
        self.assertTrue(report.is_passed)

    def test_volume_within_tolerance_is_not_flagged(self):
        bars_a = [bar("2024-01-01", 100.0, volume=10000.0)]
        bars_b = [bar("2024-01-01", 100.0, volume=30000.0)]  # exactly 3.0x

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertEqual(report.volume_flagged_bars, [])

    def test_negative_volume_is_an_integrity_failure(self):
        bars_a = [bar("2024-01-01", 100.0, volume=-1.0)]
        bars_b = [bar("2024-01-01", 100.0, volume=1000.0)]

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertFalse(report.is_passed)
        self.assertIn("negative volume", report.integrity_issues[0].reason)

    def test_zero_overlap_reports_timestamp_normalisation_hint(self):
        bars_a = [bar("2024-01-01T00:00:00Z", 100.0)]
        bars_b = [bar("2024-01-01 00:00:00", 100.0)]

        report = self.validator.validate("AAPL", bars_a, bars_b)

        self.assertFalse(report.is_passed)
        self.assertEqual(report.matched_bars, 0)
        self.assertIn("zero overlapping timestamps", report.message)


if __name__ == "__main__":
    unittest.main()
