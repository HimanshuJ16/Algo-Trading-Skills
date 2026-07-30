import unittest
from google_trends_and_search_volume_signal_research import (
    GoogleTrendsSignalEngine, SviDataPoint, GoogleTrendsSignalReport
)

class TestGoogleTrendsSignalEngine(unittest.TestCase):

    def setUp(self):
        self.engine = GoogleTrendsSignalEngine(lookback_window=30, z_score_threshold=2.0)
        # Create 29 baseline SVI data points around mean = 40.0, std = 2.0
        self.series = [
            SviDataPoint(f"2026-06-{i:02d}", "NVDA", svi_score=40.0 + (i % 3))
            for i in range(1, 30)
        ]

    def test_bullish_attention_surge_signal(self):
        # Day 30: SVI spikes to 90.0 (Z > +10.0) with positive price momentum +5.2%
        full_series = self.series + [SviDataPoint("2026-06-30", "NVDA", svi_score=90.0)]
        report = self.engine.generate_trends_signal("NVDA", full_series, asset_price_momentum_pct=5.2)

        self.assertTrue(report.is_attention_spike)
        self.assertGreater(report.svi_z_score, 2.0)
        self.assertEqual(report.signal_type, "BULLISH_ATTENTION_SURGE")

    def test_bearish_panic_spike_signal(self):
        # Day 30: SVI spikes to 95.0 (Z > +10.0) with negative price momentum -8.5% -> PANIC!
        full_series = self.series + [SviDataPoint("2026-06-30", "NVDA", svi_score=95.0)]
        report = self.engine.generate_trends_signal("NVDA", full_series, asset_price_momentum_pct=-8.5)

        self.assertTrue(report.is_attention_spike)
        self.assertEqual(report.signal_type, "BEARISH_PANIC_SPIKE")

    def test_neutral_attention_signal(self):
        # Day 30: Normal SVI = 41.0 (Z < 2.0)
        full_series = self.series + [SviDataPoint("2026-06-30", "NVDA", svi_score=41.0)]
        report = self.engine.generate_trends_signal("NVDA", full_series, asset_price_momentum_pct=1.0)

        self.assertFalse(report.is_attention_spike)
        self.assertEqual(report.signal_type, "NEUTRAL_ATTENTION")

if __name__ == '__main__':
    unittest.main()
