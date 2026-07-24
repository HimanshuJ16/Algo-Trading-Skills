"""
Unit tests for regime-detection-for-strategy-switching skill.

Tests:
1. High volatility crash regime detection via ATR z-score.
2. Bull trending regime classification via ADX and +DI.
3. Hysteresis transition filter delaying regime switches until 3 consecutive bars.
4. Strategy variant routing mapping.
"""
import unittest
from regime_detector import MarketRegime, MarketRegimeDetector, RegimeAnalysis, RegimeDetectorError


class TestMarketRegimeDetector(unittest.TestCase):

    def setUp(self):
        self.detector = MarketRegimeDetector(
            adx_trend_threshold=25.0, hysteresis_bars=3, volatility_z_threshold=2.0
        )

    def test_high_volatility_crash_detection(self):
        # Generate synthetic price series with huge volatility spike at the end
        closes = [100.0 + (i * 0.1) for i in range(40)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]

        # Add massive volatility spike in last bar
        closes.append(70.0)
        highs.append(100.0)
        lows.append(60.0)

        # Submit 3 times to trigger hysteresis
        res1 = self.detector.detect_regime(highs, lows, closes)
        res2 = self.detector.detect_regime(highs, lows, closes)
        res3 = self.detector.detect_regime(highs, lows, closes)

        self.assertEqual(res3.confirmed_regime, MarketRegime.HIGH_VOLATILITY_CRASH)
        self.assertEqual(res3.active_strategy_variant, "RiskOffHaltStrategy")

    def test_hysteresis_filter_delay(self):
        # Base ranging bars
        closes = [100.0 + (i % 2) * 0.5 for i in range(30)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]

        res = self.detector.detect_regime(highs, lows, closes)
        self.assertEqual(res.confirmed_regime, MarketRegime.MEAN_REVERTING_RANGING)

        # Create smooth steady trending bars
        trend_closes = [100.0 + (i * 0.5) for i in range(40)]
        trend_highs = [c + 0.5 for c in trend_closes]
        trend_lows = [c - 0.2 for c in trend_closes]

        r1 = self.detector.detect_regime(trend_highs, trend_lows, trend_closes)
        self.assertEqual(r1.raw_candidate_regime, MarketRegime.BULL_TRENDING)
        # Hysteresis counter = 1, so confirmed regime remains RANGING until 3 bars
        self.assertEqual(r1.confirmed_regime, MarketRegime.MEAN_REVERTING_RANGING)
        self.assertEqual(r1.consecutive_candidate_bars, 1)

    def test_strategy_variant_routing(self):
        self.assertEqual(
            self.detector.route_strategy_variant(MarketRegime.BULL_TRENDING),
            "TrendFollowingLongStrategy",
        )
        self.assertEqual(
            self.detector.route_strategy_variant(MarketRegime.HIGH_VOLATILITY_CRASH),
            "RiskOffHaltStrategy",
        )


if __name__ == "__main__":
    unittest.main()
