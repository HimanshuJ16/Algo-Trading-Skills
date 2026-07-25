"""Unit tests for backtest-outlier-and-bad-tick-filtering."""
import unittest
from outlier_filter import OutlierBadTickFilter


class TestOutlierBadTickFilter(unittest.TestCase):
    def setUp(self):
        self.filter = OutlierBadTickFilter(window_size=10, z_threshold=5.0, max_single_tick_jump_pct=20.0)

    def test_purges_single_bad_tick_print(self):
        # 100.0 price series with a single 10.0 bad print
        prices = [100.0 + (i * 0.1) for i in range(15)]
        prices.insert(8, 10.0)  # Bad print at index 8

        cleaned, report = self.filter.filter_prices(prices)

        self.assertEqual(report.purged_bad_ticks_count, 1)
        self.assertNotIn(10.0, cleaned)
        self.assertEqual(report.total_input_ticks, 16)

    def test_purges_zero_and_negative_prices(self):
        prices = [100.0, 0.0, -5.0, 101.0]
        cleaned, report = self.filter.filter_prices(prices)
        self.assertEqual(cleaned, [100.0, 101.0])
        self.assertEqual(report.purged_bad_ticks_count, 2)


if __name__ == "__main__":
    unittest.main()
