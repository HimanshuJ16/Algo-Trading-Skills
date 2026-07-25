"""Unit tests for adjusted-vs-unadjusted-price-series-pitfalls."""
import unittest
from price_adjustment_auditor import PriceAdjustmentAuditor, CorporateAction


class TestPriceAdjustmentAuditor(unittest.TestCase):
    def setUp(self):
        self.auditor = PriceAdjustmentAuditor(discontinuity_threshold_pct=30.0)

    def test_detects_unadjusted_split_discontinuity(self):
        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
        closes = [200.0, 198.0, 100.0, 101.0]  # 2:1 split on day 3
        actions = [CorporateAction("2024-01-03", "SPLIT", 2.0)]
        report = self.auditor.detect_discontinuities("AAPL", closes, dates, actions)
        self.assertFalse(report.is_consistent)
        self.assertEqual(len(report.discontinuities_found), 1)

    def test_adjusted_series_passes(self):
        closes = [100.0, 101.0, 99.5, 100.5, 102.0]
        dates = [f"2024-01-{i:02d}" for i in range(1, 6)]
        report = self.auditor.detect_discontinuities("MSFT", closes, dates)
        self.assertTrue(report.is_consistent)
        self.assertEqual(report.detected_adjustment_type, "ADJUSTED")

    def test_split_backward_adjustment(self):
        closes = [200.0, 198.0, 100.0, 101.0]
        adjusted = self.auditor.apply_split_adjustment(closes, split_index=2, split_ratio=2.0)
        self.assertAlmostEqual(adjusted[0], 100.0)
        self.assertAlmostEqual(adjusted[1], 99.0)
        self.assertEqual(adjusted[2], 100.0)  # Unchanged post-split

if __name__ == "__main__":
    unittest.main()
