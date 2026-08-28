"""
Unit tests for regime-detection-for-strategy-switching.

Indicator expectations are derived independently of the implementation:

* Analytic cases. A strictly monotone ramp has -DM = 0 on every bar, so -DI = 0,
  DX = 100 on every bar and ADX = 100 exactly. A series with a constant range and
  no gaps has TR = R on every bar, so ATR = R exactly.
* Cross-checked against TA-Lib 0.6.8 (``talib.ADX``/``PLUS_DI``/``MINUS_DI``/
  ``ATR``, period 14) on pseudo-random OHLC series. ATR agrees bit-for-bit;
  ADX and DI agree to <1e-15 relative once Wilder's seed has decayed (600 bars)
  and to <1e-3 relative on short histories, where the two libraries seed the
  recursion slightly differently. TA-Lib is a verification aid only and is
  deliberately not imported here -- the repo test suite has no third-party
  dependencies.

Regression coverage for the 1.0.0 -> 2.0.0 fixes:

* ``test_adx_is_the_smoothed_average_of_dx_not_dx`` fails against 1.0.0, which
  returned DX under the name ``adx``.
* ``test_volatility_zscore_is_not_bounded_by_self_inclusion`` fails against
  1.0.0, which included the scored observation in its own mean and stdev.
* ``test_short_history_raises_instead_of_fabricating_adx`` fails against 1.0.0,
  which returned a hard-coded ``(15.0, 20.0, 20.0)`` below 2 * period bars.
* ``test_non_finite_bar_raises`` fails against 1.0.0, which let a NaN fall
  through every comparison and reported MEAN_REVERTING_RANGING.
"""
import math
import random
import unittest

from regime_detector import (
    DEFAULT_STRATEGY_VARIANTS,
    MAX_VOLATILITY_ZSCORE,
    MIN_VOLATILITY_HISTORY,
    MarketRegime,
    MarketRegimeDetector,
    RegimeAnalysis,
    RegimeDetectorError,
)


def monotone_ramp(n=40, step=0.5, half_range=0.4):
    """Strictly rising bars: -DM = 0 on every bar, so ADX is analytically 100."""
    closes = [100.0 + step * i for i in range(n)]
    return [c + half_range for c in closes], [c - half_range for c in closes], closes


def constant_range(n=40, price=100.0, half_range=1.0):
    """Identical bars: TR = 2 * half_range on every bar, so ATR is analytically that."""
    closes = [price] * n
    return [price + half_range] * n, [price - half_range] * n, closes


def ranging_series(n=80, seed=11):
    """Mean-reverting walk with a stable range: low ADX, stable ATR."""
    rng = random.Random(seed)
    highs, lows, closes = [], [], []
    price = 100.0
    for _ in range(n):
        price += rng.uniform(-0.6, 0.6)
        price = 100.0 + (price - 100.0) * 0.85
        half = 0.4 + rng.uniform(0.0, 0.3)
        highs.append(price + half)
        lows.append(price - half)
        closes.append(price)
    return highs, lows, closes


def trending_series(n=80, seed=3, drift=0.8):
    """Steady directional drift with a stable range: high ADX, +DI > -DI."""
    rng = random.Random(seed)
    highs, lows, closes = [], [], []
    price = 100.0
    for _ in range(n):
        price += drift + rng.uniform(-0.15, 0.15)
        half = 0.4 + rng.uniform(0.0, 0.2)
        highs.append(price + half)
        lows.append(price - half)
        closes.append(price)
    return highs, lows, closes


def volatility_shock_series(quiet_bars=60, shock_bars=6, seed=5):
    """A quiet range followed by a genuine expansion of the range itself."""
    highs, lows, closes = ranging_series(quiet_bars, seed=seed)
    price = closes[-1]
    for i in range(shock_bars):
        price -= 8.0 + i
        highs.append(price + 6.0)
        lows.append(price - 6.0)
        closes.append(price)
    return highs, lows, closes


class TestIndicatorCorrectness(unittest.TestCase):
    """Wilder ADX/DMI and ATR, checked against analytically known values."""

    def setUp(self):
        self.detector = MarketRegimeDetector()

    def test_atr_equals_true_range_on_a_constant_range_series(self):
        highs, lows, closes = constant_range(n=40, half_range=1.0)
        atr_series = self.detector._compute_atr_series(highs, lows, closes)
        # One ATR value per bar from index `period` onward.
        self.assertEqual(len(atr_series), len(closes) - self.detector.indicator_period)
        for value in atr_series:
            self.assertAlmostEqual(value, 2.0, places=12)

    def test_adx_is_100_on_a_strictly_monotone_ramp(self):
        highs, lows, closes = monotone_ramp()
        adx, plus_di, minus_di = self.detector._compute_adx(highs, lows, closes)
        self.assertAlmostEqual(adx, 100.0, places=10)
        self.assertAlmostEqual(minus_di, 0.0, places=12)
        self.assertGreater(plus_di, 0.0)

    def test_adx_is_100_and_minus_di_leads_on_a_monotone_decline(self):
        highs, lows, closes = monotone_ramp(step=-0.5)
        adx, plus_di, minus_di = self.detector._compute_adx(highs, lows, closes)
        self.assertAlmostEqual(adx, 100.0, places=10)
        self.assertAlmostEqual(plus_di, 0.0, places=12)
        self.assertGreater(minus_di, plus_di)

    def test_adx_is_the_smoothed_average_of_dx_not_dx(self):
        """Regression: 1.0.0 returned the single-bar DX under the name `adx`.

        A long range followed by a short directional push leaves DX high on the
        final bar while the 14-period smoothed ADX is still low. Wilder's ADX >= 25
        threshold applied to DX classifies this range as a confirmed trend.
        """
        closes = [100.0, 100.6] * 22 + [100.6 + 0.9 * i for i in range(1, 8)]
        highs = [c + 0.3 for c in closes]
        lows = [c - 0.3 for c in closes]

        adx, plus_di, minus_di = self.detector._compute_adx(highs, lows, closes)
        dx = 100.0 * abs(plus_di - minus_di) / (plus_di + minus_di)

        self.assertGreater(dx, 25.0, "fixture should leave DX above the trend threshold")
        self.assertLess(adx, 25.0, "ADX must smooth DX, not echo it")
        self.assertEqual(
            self.detector._classify_raw(adx, plus_di, minus_di, vol_zscore=0.0),
            MarketRegime.MEAN_REVERTING_RANGING,
        )

    def test_adx_and_di_are_bounded(self):
        highs, lows, closes = trending_series(n=120)
        adx, plus_di, minus_di = self.detector._compute_adx(highs, lows, closes)
        for value in (adx, plus_di, minus_di):
            self.assertTrue(math.isfinite(value))
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 100.0)

    def test_flat_market_yields_zero_adx_without_dividing_by_zero(self):
        highs, lows, closes = constant_range(n=40, half_range=0.0)
        adx, plus_di, minus_di = self.detector._compute_adx(highs, lows, closes)
        self.assertEqual((adx, plus_di, minus_di), (0.0, 0.0, 0.0))

    def test_short_history_raises_instead_of_fabricating_adx(self):
        """Regression: 1.0.0 returned a hard-coded (15.0, 20.0, 20.0) below 2 * period."""
        highs, lows, closes = trending_series(n=27)
        with self.assertRaises(RegimeDetectorError):
            self.detector._compute_adx(highs, lows, closes)

    def test_min_bars_required_matches_the_indicator_warmup(self):
        self.assertEqual(MarketRegimeDetector(indicator_period=14).min_bars_required, 28)
        # Below 2 * period the volatility leg's history requirement binds instead.
        self.assertEqual(
            MarketRegimeDetector(indicator_period=5).min_bars_required,
            5 + MIN_VOLATILITY_HISTORY + 1,
        )


class TestVolatilityZScore(unittest.TestCase):

    def setUp(self):
        self.detector = MarketRegimeDetector()

    def test_volatility_zscore_is_not_bounded_by_self_inclusion(self):
        """Regression: 1.0.0 scored the latest ATR against a sample containing it.

        With n observations that caps z at (n-1)/sqrt(n); at the 28-bar minimum
        the ceiling is ~3.6 regardless of how violent the shock is.
        """
        atr_series = [1.0 + 0.01 * i for i in range(15)] + [500.0]
        ceiling = (len(atr_series) - 1) / math.sqrt(len(atr_series))
        zscore = self.detector._volatility_zscore(atr_series)
        self.assertGreater(zscore, ceiling)

    def test_volatility_zscore_grows_with_the_size_of_the_shock(self):
        history = [1.0 + 0.01 * i for i in range(15)]
        small = self.detector._volatility_zscore(history + [5.0])
        large = self.detector._volatility_zscore(history + [50.0])
        self.assertGreater(large, small)

    def test_zero_dispersion_history_reports_a_capped_extreme_not_zero(self):
        constant = [2.0] * 15
        self.assertEqual(self.detector._volatility_zscore(constant + [2.0]), 0.0)
        self.assertEqual(
            self.detector._volatility_zscore(constant + [9.0]), MAX_VOLATILITY_ZSCORE
        )
        self.assertEqual(
            self.detector._volatility_zscore(constant + [0.5]), -MAX_VOLATILITY_ZSCORE
        )

    def test_too_few_prior_atr_observations_raises(self):
        with self.assertRaises(RegimeDetectorError):
            self.detector._volatility_zscore([1.0] * MIN_VOLATILITY_HISTORY)

    def test_zscore_matches_the_textbook_formula(self):
        history = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]
        # Sample mean 6.0, sample stdev (n-1 divisor) of 1..11 is exactly sqrt(11).
        self.assertAlmostEqual(
            self.detector._volatility_zscore(history + [20.0]),
            (20.0 - 6.0) / math.sqrt(11.0),
            places=12,
        )


class TestRawClassification(unittest.TestCase):
    """The four-way decision, exercised directly on indicator values."""

    def setUp(self):
        self.detector = MarketRegimeDetector()

    def test_volatility_outranks_a_strong_trend(self):
        self.assertEqual(
            self.detector._classify_raw(adx=90.0, plus_di=60.0, minus_di=5.0, vol_zscore=2.5),
            MarketRegime.HIGH_VOLATILITY_CRASH,
        )

    def test_volatility_threshold_is_inclusive_at_the_boundary(self):
        self.assertEqual(
            self.detector._classify_raw(adx=10.0, plus_di=10.0, minus_di=10.0, vol_zscore=2.0),
            MarketRegime.HIGH_VOLATILITY_CRASH,
        )
        self.assertEqual(
            self.detector._classify_raw(
                adx=10.0, plus_di=10.0, minus_di=10.0, vol_zscore=math.nextafter(2.0, 0.0)
            ),
            MarketRegime.MEAN_REVERTING_RANGING,
        )

    def test_adx_trend_threshold_is_inclusive_at_the_boundary(self):
        self.assertEqual(
            self.detector._classify_raw(adx=25.0, plus_di=30.0, minus_di=10.0, vol_zscore=0.0),
            MarketRegime.BULL_TRENDING,
        )
        self.assertEqual(
            self.detector._classify_raw(
                adx=math.nextafter(25.0, 0.0), plus_di=30.0, minus_di=10.0, vol_zscore=0.0
            ),
            MarketRegime.MEAN_REVERTING_RANGING,
        )

    def test_bear_trend_requires_minus_di_to_lead(self):
        self.assertEqual(
            self.detector._classify_raw(adx=40.0, plus_di=10.0, minus_di=35.0, vol_zscore=0.0),
            MarketRegime.BEAR_TRENDING,
        )

    def test_strong_adx_with_tied_di_is_not_a_tradeable_trend(self):
        self.assertEqual(
            self.detector._classify_raw(adx=60.0, plus_di=20.0, minus_di=20.0, vol_zscore=0.0),
            MarketRegime.MEAN_REVERTING_RANGING,
        )

    def test_grey_zone_holds_an_in_force_trend(self):
        """ADX in Wilder's 20-25 band: a confirmed trend is held, never entered."""
        self.detector.confirmed_regime = MarketRegime.BULL_TRENDING
        self.assertEqual(
            self.detector._classify_raw(adx=22.0, plus_di=30.0, minus_di=10.0, vol_zscore=0.0),
            MarketRegime.BULL_TRENDING,
        )

    def test_grey_zone_does_not_enter_a_trend_from_a_range(self):
        self.detector.confirmed_regime = MarketRegime.MEAN_REVERTING_RANGING
        self.assertEqual(
            self.detector._classify_raw(adx=22.0, plus_di=30.0, minus_di=10.0, vol_zscore=0.0),
            MarketRegime.MEAN_REVERTING_RANGING,
        )

    def test_grey_zone_releases_a_trend_once_the_di_pair_flips(self):
        self.detector.confirmed_regime = MarketRegime.BULL_TRENDING
        self.assertEqual(
            self.detector._classify_raw(adx=22.0, plus_di=10.0, minus_di=30.0, vol_zscore=0.0),
            MarketRegime.MEAN_REVERTING_RANGING,
        )

    def test_below_the_ranging_threshold_a_trend_is_released(self):
        self.detector.confirmed_regime = MarketRegime.BULL_TRENDING
        self.assertEqual(
            self.detector._classify_raw(adx=19.0, plus_di=30.0, minus_di=10.0, vol_zscore=0.0),
            MarketRegime.MEAN_REVERTING_RANGING,
        )


class TestHysteresisFilter(unittest.TestCase):

    def setUp(self):
        self.detector = MarketRegimeDetector(hysteresis_bars=3)
        self.ranging = ranging_series(n=60, seed=11)
        self.trending = trending_series(n=60, seed=3)

    def _feed(self, series, upto, bar_key):
        highs, lows, closes = series
        return self.detector.detect_regime(
            highs[:upto], lows[:upto], closes[:upto], bar_key=bar_key
        )

    def test_confirmed_regime_lags_the_candidate_by_hysteresis_bars(self):
        first = self._feed(self.ranging, 60, "range")
        self.assertEqual(first.confirmed_regime, MarketRegime.MEAN_REVERTING_RANGING)

        highs, lows, closes = self.trending
        for bar in range(1, 3):
            result = self.detector.detect_regime(
                highs[: 40 + bar], lows[: 40 + bar], closes[: 40 + bar], bar_key=("t", bar)
            )
            self.assertEqual(result.raw_candidate_regime, MarketRegime.BULL_TRENDING)
            self.assertEqual(result.confirmed_regime, MarketRegime.MEAN_REVERTING_RANGING)
            self.assertEqual(result.consecutive_candidate_bars, bar)
            self.assertFalse(result.regime_changed)

        third = self.detector.detect_regime(
            highs[:43], lows[:43], closes[:43], bar_key=("t", 3)
        )
        self.assertEqual(third.confirmed_regime, MarketRegime.BULL_TRENDING)
        self.assertTrue(third.regime_changed)
        self.assertEqual(third.consecutive_candidate_bars, 0)
        self.assertEqual(third.active_strategy_variant, "TrendFollowingLongStrategy")

    def test_regime_changed_is_true_only_on_the_switching_bar(self):
        highs, lows, closes = self.trending
        results = [
            self.detector.detect_regime(
                highs[: 40 + i], lows[: 40 + i], closes[: 40 + i], bar_key=i
            )
            for i in range(1, 6)
        ]
        self.assertEqual([r.regime_changed for r in results], [False, False, True, False, False])

    def test_an_interrupting_bar_resets_the_candidate_counter(self):
        trend_h, trend_l, trend_c = self.trending
        range_h, range_l, range_c = self.ranging

        for i in range(1, 3):
            self.detector.detect_regime(
                trend_h[: 40 + i], trend_l[: 40 + i], trend_c[: 40 + i], bar_key=("t", i)
            )
        self.assertEqual(self.detector.candidate_count, 2)

        interrupt = self.detector.detect_regime(range_h, range_l, range_c, bar_key="range")
        self.assertEqual(interrupt.raw_candidate_regime, MarketRegime.MEAN_REVERTING_RANGING)
        self.assertEqual(self.detector.candidate_count, 0)

        resumed = self.detector.detect_regime(
            trend_h[:43], trend_l[:43], trend_c[:43], bar_key=("t", 3)
        )
        self.assertEqual(resumed.confirmed_regime, MarketRegime.MEAN_REVERTING_RANGING)
        self.assertEqual(resumed.consecutive_candidate_bars, 1)

    def test_hysteresis_bars_of_one_switches_immediately(self):
        detector = MarketRegimeDetector(hysteresis_bars=1)
        highs, lows, closes = self.trending
        result = detector.detect_regime(highs[:41], lows[:41], closes[:41], bar_key=1)
        self.assertEqual(result.confirmed_regime, MarketRegime.BULL_TRENDING)

    def test_repeated_bar_key_does_not_advance_the_filter(self):
        """A retry or duplicated tick must not count as another confirming bar."""
        highs, lows, closes = self.trending
        for _ in range(5):
            result = self.detector.detect_regime(highs[:41], lows[:41], closes[:41], bar_key="b41")
        self.assertEqual(result.raw_candidate_regime, MarketRegime.BULL_TRENDING)
        self.assertEqual(result.confirmed_regime, MarketRegime.MEAN_REVERTING_RANGING)
        self.assertEqual(self.detector.candidate_count, 1)

    def test_without_a_bar_key_repeated_calls_do_advance_the_filter(self):
        """Documented behaviour: the counter is per call when no bar identity is given."""
        highs, lows, closes = self.trending
        for _ in range(3):
            result = self.detector.detect_regime(highs[:41], lows[:41], closes[:41])
        self.assertEqual(result.confirmed_regime, MarketRegime.BULL_TRENDING)


class TestEndToEndRegimes(unittest.TestCase):

    def test_ranging_series_stays_ranging(self):
        detector = MarketRegimeDetector()
        highs, lows, closes = ranging_series(n=80, seed=11)
        for i in range(28, 80):
            result = detector.detect_regime(highs[:i], lows[:i], closes[:i], bar_key=i)
        self.assertEqual(result.confirmed_regime, MarketRegime.MEAN_REVERTING_RANGING)
        self.assertLess(result.adx_value, 25.0)
        self.assertEqual(result.active_strategy_variant, "MeanReversionBollingerStrategy")

    def test_trending_series_confirms_a_bull_regime(self):
        detector = MarketRegimeDetector()
        highs, lows, closes = trending_series(n=80, seed=3)
        for i in range(28, 80):
            result = detector.detect_regime(highs[:i], lows[:i], closes[:i], bar_key=i)
        self.assertEqual(result.confirmed_regime, MarketRegime.BULL_TRENDING)
        self.assertGreaterEqual(result.adx_value, 25.0)
        self.assertGreater(result.plus_di, result.minus_di)

    def test_downtrend_confirms_a_bear_regime_and_routes_short(self):
        detector = MarketRegimeDetector()
        highs, lows, closes = trending_series(n=80, seed=3, drift=-0.8)
        for i in range(28, 80):
            result = detector.detect_regime(highs[:i], lows[:i], closes[:i], bar_key=i)
        self.assertEqual(result.confirmed_regime, MarketRegime.BEAR_TRENDING)
        self.assertGreater(result.minus_di, result.plus_di)
        self.assertEqual(result.active_strategy_variant, "TrendFollowingShortStrategy")

    def test_volatility_shock_confirms_risk_off_after_hysteresis(self):
        detector = MarketRegimeDetector()
        highs, lows, closes = volatility_shock_series(quiet_bars=60, shock_bars=6)
        confirmations = []
        for i in range(28, len(closes) + 1):
            result = detector.detect_regime(highs[:i], lows[:i], closes[:i], bar_key=i)
            confirmations.append(result.confirmed_regime)
        self.assertEqual(result.confirmed_regime, MarketRegime.HIGH_VOLATILITY_CRASH)
        self.assertEqual(result.active_strategy_variant, "RiskOffHaltStrategy")
        # The quiet section must not have been called a shock.
        self.assertNotIn(MarketRegime.HIGH_VOLATILITY_CRASH, confirmations[:30])

    def test_analysis_fields_are_internally_consistent(self):
        detector = MarketRegimeDetector()
        highs, lows, closes = trending_series(n=60, seed=3)
        result = detector.detect_regime(highs, lows, closes, bar_key=1)
        self.assertIsInstance(result, RegimeAnalysis)
        self.assertEqual(
            result.active_strategy_variant,
            detector.route_strategy_variant(result.confirmed_regime),
        )
        self.assertGreater(result.atr_value, 0.0)
        self.assertTrue(math.isfinite(result.volatility_zscore))


class TestStrategyRouting(unittest.TestCase):

    def test_every_regime_maps_to_a_variant(self):
        detector = MarketRegimeDetector()
        for regime in MarketRegime:
            self.assertEqual(
                detector.route_strategy_variant(regime), DEFAULT_STRATEGY_VARIANTS[regime]
            )

    def test_custom_variant_map_is_used(self):
        custom = {regime: f"custom::{regime.value}" for regime in MarketRegime}
        detector = MarketRegimeDetector(strategy_variants=custom)
        self.assertEqual(
            detector.route_strategy_variant(MarketRegime.BEAR_TRENDING),
            "custom::BEAR_TRENDING",
        )

    def test_incomplete_variant_map_is_rejected(self):
        with self.assertRaises(RegimeDetectorError):
            MarketRegimeDetector(
                strategy_variants={MarketRegime.BULL_TRENDING: "OnlyOne"}
            )

    def test_default_variant_map_is_not_shared_between_instances(self):
        detector = MarketRegimeDetector()
        detector.strategy_variants[MarketRegime.BULL_TRENDING] = "Mutated"
        self.assertEqual(
            MarketRegimeDetector().route_strategy_variant(MarketRegime.BULL_TRENDING),
            "TrendFollowingLongStrategy",
        )


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.detector = MarketRegimeDetector()
        self.highs, self.lows, self.closes = trending_series(n=60, seed=3)

    def test_mismatched_series_lengths_raise(self):
        with self.assertRaises(RegimeDetectorError):
            self.detector.detect_regime(self.highs, self.lows[:-1], self.closes)

    def test_insufficient_bars_raise(self):
        with self.assertRaises(RegimeDetectorError):
            self.detector.detect_regime(self.highs[:27], self.lows[:27], self.closes[:27])

    def test_empty_input_raises(self):
        with self.assertRaises(RegimeDetectorError):
            self.detector.detect_regime([], [], [])

    def test_non_finite_bar_raises(self):
        """Regression: an unvalidated NaN fails every comparison and reads as RANGING."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            highs = list(self.highs)
            highs[-1] = bad
            with self.assertRaises(RegimeDetectorError):
                self.detector.detect_regime(highs, self.lows, self.closes)

    def test_non_numeric_bar_raises(self):
        closes = list(self.closes)
        closes[-1] = "100.0"
        with self.assertRaises(RegimeDetectorError):
            self.detector.detect_regime(self.highs, self.lows, closes)

    def test_inverted_bar_raises(self):
        highs = list(self.highs)
        highs[10] = self.lows[10] - 1.0
        with self.assertRaises(RegimeDetectorError):
            self.detector.detect_regime(highs, self.lows, self.closes)

    def test_validation_runs_before_the_bar_key_cache(self):
        self.detector.detect_regime(self.highs, self.lows, self.closes, bar_key="b")
        with self.assertRaises(RegimeDetectorError):
            self.detector.detect_regime(self.highs[:10], self.lows[:10], self.closes[:10], bar_key="b")


class TestConfigurationValidation(unittest.TestCase):

    def test_hysteresis_below_one_is_rejected(self):
        for value in (0, -1):
            with self.assertRaises(RegimeDetectorError):
                MarketRegimeDetector(hysteresis_bars=value)

    def test_non_integer_hysteresis_is_rejected(self):
        with self.assertRaises(RegimeDetectorError):
            MarketRegimeDetector(hysteresis_bars=3.0)

    def test_ranging_threshold_above_trend_threshold_is_rejected(self):
        with self.assertRaises(RegimeDetectorError):
            MarketRegimeDetector(adx_trend_threshold=20.0, adx_ranging_threshold=25.0)

    def test_adx_threshold_outside_zero_to_one_hundred_is_rejected(self):
        with self.assertRaises(RegimeDetectorError):
            MarketRegimeDetector(adx_trend_threshold=120.0)
        with self.assertRaises(RegimeDetectorError):
            MarketRegimeDetector(adx_trend_threshold=-1.0)

    def test_non_finite_threshold_is_rejected(self):
        with self.assertRaises(RegimeDetectorError):
            MarketRegimeDetector(volatility_z_threshold=float("nan"))

    def test_indicator_period_below_two_is_rejected(self):
        with self.assertRaises(RegimeDetectorError):
            MarketRegimeDetector(indicator_period=1)

    def test_initial_regime_must_be_a_member(self):
        with self.assertRaises(RegimeDetectorError):
            MarketRegimeDetector(initial_regime="BULL_TRENDING")

    def test_initial_regime_is_honoured_on_restart(self):
        detector = MarketRegimeDetector(initial_regime=MarketRegime.BEAR_TRENDING)
        self.assertEqual(detector.confirmed_regime, MarketRegime.BEAR_TRENDING)
        self.assertEqual(
            detector.route_strategy_variant(detector.confirmed_regime),
            "TrendFollowingShortStrategy",
        )


if __name__ == "__main__":
    unittest.main()
