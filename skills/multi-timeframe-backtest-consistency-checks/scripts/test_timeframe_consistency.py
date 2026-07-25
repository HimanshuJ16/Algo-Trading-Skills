"""Unit tests for multi-timeframe-backtest-consistency-checks."""
import unittest
from timeframe_consistency import TimeframeConsistencyChecker, Bar


class TestTimeframeConsistencyChecker(unittest.TestCase):
    def setUp(self):
        self.checker = TimeframeConsistencyChecker(divergence_tolerance_pct=1.0)

    def test_consistent_resampling(self):
        bars = [Bar(i*60, 100+i*0.1, 100+i*0.1+0.5, 100+i*0.1-0.5, 100+i*0.1, 1000) for i in range(50)]
        report = self.checker.check_consistency(bars, resample_factor=5, sma_period=3)
        self.assertTrue(report.is_consistent)

    def test_resampling_preserves_ohlcv(self):
        bars = [
            Bar(0, 10, 12, 9, 11, 100),
            Bar(60, 11, 15, 10, 14, 200),
            Bar(120, 14, 16, 13, 15, 150),
        ]
        resampled = self.checker.resample_bars(bars, 3)
        self.assertEqual(len(resampled), 1)
        self.assertEqual(resampled[0].open, 10)
        self.assertEqual(resampled[0].high, 16)
        self.assertEqual(resampled[0].low, 9)
        self.assertEqual(resampled[0].close, 15)
        self.assertEqual(resampled[0].volume, 450)

if __name__ == "__main__":
    unittest.main()
