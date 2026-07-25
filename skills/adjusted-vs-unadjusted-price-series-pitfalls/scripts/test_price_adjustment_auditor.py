"""Unit tests for adjusted-vs-unadjusted-price-series-pitfalls."""
import unittest
from price_adjustment_auditor import PriceAdjustmentAuditor, CorporateAction

class TestPriceAdjustmentAuditor(unittest.TestCase):
    def setUp(self):
        self.auditor = PriceAdjustmentAuditor(discontinuity_threshold_pct=30.0)

    def test_detects_unadjusted_split_discontinuity_with_volume(self):
        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
        closes = [200.0, 198.0, 100.0, 101.0]  # 2:1 split on day 3
        volumes = [1000, 1100, 2200, 2100]     # Volume doubles naturally on split day
        actions = [CorporateAction("2024-01-03", "SPLIT", 2.0)]
        
        report = self.auditor.detect_discontinuities("AAPL", closes, volumes, dates, actions)
        
        self.assertFalse(report.is_consistent)
        self.assertEqual(len(report.discontinuities_found), 1)
        self.assertTrue(report.discontinuities_found[0].volume_consistent)

    def test_detects_look_ahead_bias_from_dividends(self):
        dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
        closes = [100.0, 60.0, 61.0] 
        volumes = [1000, 1000, 1000]
        # A massive 40% dividend drop
        actions = [CorporateAction("2024-01-02", "DIVIDEND", 40.0)]
        
        report = self.auditor.detect_discontinuities("SPY", closes, volumes, dates, actions)
        self.assertTrue(report.has_look_ahead_bias_risk)

    def test_split_backward_adjustment_price_and_volume(self):
        closes = [200.0, 198.0, 100.0, 101.0]
        volumes = [1000.0, 1100.0, 2200.0, 2100.0]
        
        adj_closes, adj_volumes = self.auditor.apply_split_adjustment(
            closes, volumes, split_index=2, split_ratio=2.0
        )
        
        # Prices before split are halved
        self.assertAlmostEqual(adj_closes[0], 100.0)
        self.assertAlmostEqual(adj_closes[1], 99.0)
        self.assertEqual(adj_closes[2], 100.0)  # Unchanged post-split
        
        # Volumes before split are doubled
        self.assertAlmostEqual(adj_volumes[0], 2000.0)
        self.assertAlmostEqual(adj_volumes[1], 2200.0)
        self.assertEqual(adj_volumes[2], 2200.0) # Unchanged post-split

if __name__ == "__main__":
    unittest.main()
