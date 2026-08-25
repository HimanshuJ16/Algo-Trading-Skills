import math
import unittest
from datetime import datetime, timedelta, timezone

from google_trends_and_search_volume_signal_research import (
    GoogleTrendsSignalEngine,
    GoogleTrendsSignalReport,
    SviDataPoint,
)

UTC = timezone.utc
BASE_DAY = datetime(2026, 6, 1, tzinfo=UTC)


def make_series(scores, lag_hours=24.0, keyword="NVDA", start=BASE_DAY):
    """Builds a daily, ascending, timezone-aware SVI series from raw scores."""
    return [
        SviDataPoint(
            timestamp_iso=(start + timedelta(days=i)).isoformat(),
            keyword=keyword,
            svi_score=float(score),
            publication_lag_hours=lag_hours,
        )
        for i, score in enumerate(scores)
    ]


# --- Independently derived baseline statistics -------------------------------
# Baseline B = fifteen 38.0 values interleaved with fifteen 42.0 values (n = 30).
#   mean          = (15*38 + 15*42) / 30 = 40.0                       exactly
#   sum of sq dev = 30 * 2^2 = 120                                    exactly
#   sample var    = 120 / 29        (ddof = 1)
#   sample sd     = sqrt(120/29) ~= 2.034425...
# Derived from the algebra above, NOT by calling statistics.stdev, so the test
# does not simply restate the implementation's own formula.
BASELINE_SCORES = [38.0, 42.0] * 15
BASELINE_MEAN = 40.0
BASELINE_SD = math.sqrt(120.0 / 29.0)

# Baseline F = fifteen 39.8 values interleaved with fifteen 40.2 values (n = 30).
#   mean          = 40.0                                              exactly
#   sum of sq dev = 30 * 0.2^2 = 1.2                                  exactly
#   sample sd     = sqrt(1.2/29) ~= 0.20342...   (deliberately below 1.0)
FLAT_ISH_SCORES = [39.8, 40.2] * 15
FLAT_ISH_SD = math.sqrt(1.2 / 29.0)


class TestZScoreMath(unittest.TestCase):
    """The Z-score itself: trailing baseline, exact values, boundary, degeneracy."""

    def setUp(self):
        self.engine = GoogleTrendsSignalEngine(lookback_window=30, z_score_threshold=2.0)

    def test_baseline_excludes_the_observation_under_test(self):
        # Preis et al. (2013) / Da et al. (2011): the baseline covers only periods
        # PRECEDING the observation. With the 90.0 spike excluded, the baseline
        # mean must be exactly 40.0 and the sd exactly sqrt(120/29).
        z, mean, sd = self.engine.calculate_svi_z_score(BASELINE_SCORES + [90.0])

        self.assertAlmostEqual(mean, BASELINE_MEAN, places=4)
        self.assertAlmostEqual(sd, BASELINE_SD, places=4)
        self.assertAlmostEqual(z, (90.0 - BASELINE_MEAN) / BASELINE_SD, places=3)

        # Regression guard: the previous implementation standardized against a
        # window that CONTAINED the 90.0 observation, which gave mean 42.6 and a
        # materially smaller Z. Both must now be excluded.
        self.assertNotAlmostEqual(mean, 42.6, places=1)

    def test_z_score_at_exact_threshold_counts_as_a_spike(self):
        # Construct the observation that lands Z exactly on 2.0.
        at_threshold = BASELINE_MEAN + 2.0 * BASELINE_SD
        report = self.engine.generate_trends_signal(
            "NVDA", make_series(BASELINE_SCORES + [at_threshold]), 1.0
        )
        self.assertTrue(report.is_attention_spike)
        self.assertAlmostEqual(report.svi_z_score, 2.0, places=3)

    def test_z_score_just_below_threshold_is_not_a_spike(self):
        just_below = BASELINE_MEAN + 1.99 * BASELINE_SD
        report = self.engine.generate_trends_signal(
            "NVDA", make_series(BASELINE_SCORES + [just_below]), 1.0
        )
        self.assertFalse(report.is_attention_spike)
        self.assertEqual(report.signal_type, "NEUTRAL_ATTENTION")

    def test_short_history_is_rejected(self):
        # lookback_window=30 needs 31 points: 30 baseline plus the observation.
        with self.assertRaises(ValueError):
            self.engine.calculate_svi_z_score(BASELINE_SCORES)

    def test_flat_baseline_yields_nan_rather_than_a_fabricated_std_dev(self):
        z, mean, sd = self.engine.calculate_svi_z_score([40.0] * 30 + [95.0])
        self.assertTrue(math.isnan(z))
        self.assertEqual(mean, 40.0)
        self.assertEqual(sd, 0.0)  # reported as observed, never floored to 1.0

    def test_min_baseline_std_gates_but_never_substitutes(self):
        engine = GoogleTrendsSignalEngine(
            lookback_window=30, z_score_threshold=2.0, min_baseline_std=1.0
        )
        z, _, sd = engine.calculate_svi_z_score(FLAT_ISH_SCORES + [40.5])
        self.assertTrue(math.isnan(z))
        self.assertAlmostEqual(sd, FLAT_ISH_SD, places=4)  # NOT 1.0


class TestStdDevFloorRegression(unittest.TestCase):
    """
    Regression for the removed `std_val = max(1.0, std_val)` floor.

    Baseline F has sd ~= 0.2034. An observation of 40.5 sits 2.46 sd above the
    baseline mean - a genuine spike. The old floor replaced the sd with 1.0 and
    reported Z ~= 0.48, i.e. NEUTRAL. This test fails against the old behavior.
    """

    def test_small_sigma_spike_is_no_longer_suppressed(self):
        engine = GoogleTrendsSignalEngine(lookback_window=30, z_score_threshold=2.0)
        report = engine.generate_trends_signal(
            "LOWVOL", make_series(FLAT_ISH_SCORES + [40.5]), 3.0
        )

        expected_z = (40.5 - 40.0) / FLAT_ISH_SD  # ~= 2.458
        self.assertAlmostEqual(report.svi_z_score, expected_z, places=3)
        self.assertGreater(report.svi_z_score, 2.0)
        self.assertTrue(report.is_attention_spike)
        self.assertEqual(report.signal_type, "BULLISH_ATTENTION_SURGE")

        # The audit record must carry the observed sd, not the floored one.
        self.assertAlmostEqual(report.rolling_std_dev_svi, FLAT_ISH_SD, places=4)
        self.assertNotEqual(report.rolling_std_dev_svi, 1.0)


class TestSignalClassification(unittest.TestCase):
    def setUp(self):
        self.engine = GoogleTrendsSignalEngine(lookback_window=30, z_score_threshold=2.0)

    def test_bullish_attention_surge_signal(self):
        report = self.engine.generate_trends_signal(
            "NVDA", make_series(BASELINE_SCORES + [90.0]), asset_price_momentum_pct=5.2
        )
        self.assertIsInstance(report, GoogleTrendsSignalReport)
        self.assertTrue(report.is_attention_spike)
        self.assertEqual(report.signal_type, "BULLISH_ATTENTION_SURGE")
        self.assertAlmostEqual(
            report.svi_z_score, (90.0 - BASELINE_MEAN) / BASELINE_SD, places=3
        )
        self.assertEqual(report.baseline_periods, 30)

    def test_bearish_panic_spike_signal(self):
        report = self.engine.generate_trends_signal(
            "NVDA", make_series(BASELINE_SCORES + [95.0]), asset_price_momentum_pct=-8.5
        )
        self.assertTrue(report.is_attention_spike)
        self.assertEqual(report.signal_type, "BEARISH_PANIC_SPIKE")

    def test_neutral_attention_signal(self):
        report = self.engine.generate_trends_signal(
            "NVDA", make_series(BASELINE_SCORES + [41.0]), asset_price_momentum_pct=1.0
        )
        self.assertFalse(report.is_attention_spike)
        self.assertEqual(report.signal_type, "NEUTRAL_ATTENTION")

    def test_spike_with_exactly_zero_momentum_is_undirected_not_directional(self):
        report = self.engine.generate_trends_signal(
            "NVDA", make_series(BASELINE_SCORES + [90.0]), asset_price_momentum_pct=0.0
        )
        # The spike is real, but zero momentum gives it no direction. The report
        # must not silently look like a plain no-spike NEUTRAL.
        self.assertTrue(report.is_attention_spike)
        self.assertEqual(report.signal_type, "NEUTRAL_ATTENTION")
        self.assertIn("UNDIRECTED SPIKE", report.audit_notes)

    def test_flat_baseline_emits_insufficient_data_not_a_trade(self):
        report = self.engine.generate_trends_signal(
            "OBSCURE", make_series([0.0] * 30 + [95.0]), asset_price_momentum_pct=6.0
        )
        self.assertEqual(report.signal_type, "INSUFFICIENT_DATA")
        self.assertFalse(report.is_attention_spike)
        self.assertTrue(math.isnan(report.svi_z_score))


class TestPointInTimeAvailability(unittest.TestCase):
    """The publication lag must be enforced, not merely recorded."""

    def setUp(self):
        self.engine = GoogleTrendsSignalEngine(lookback_window=30, z_score_threshold=2.0)
        # 31 daily points: BASE_DAY .. BASE_DAY+30. The spike is the last one,
        # stamped 2026-07-01T00:00Z and published 24h later.
        self.series = make_series(BASELINE_SCORES + [90.0], lag_hours=24.0)
        self.spike_ts = BASE_DAY + timedelta(days=30)

    def test_spike_is_invisible_before_it_publishes(self):
        # One second before the 24h lag elapses the spike has not published, so
        # only 30 points are observable and 31 are required.
        as_of = self.spike_ts + timedelta(hours=24) - timedelta(seconds=1)
        report = self.engine.generate_trends_signal("NVDA", self.series, 5.2, as_of=as_of)

        self.assertEqual(report.signal_type, "INSUFFICIENT_DATA")
        self.assertEqual(report.dropped_unobservable_points, 1)
        self.assertEqual(report.as_of_timestamp, as_of)

    def test_spike_becomes_visible_exactly_when_the_lag_elapses(self):
        as_of = self.spike_ts + timedelta(hours=24)
        report = self.engine.generate_trends_signal("NVDA", self.series, 5.2, as_of=as_of)

        self.assertEqual(report.signal_type, "BULLISH_ATTENTION_SURGE")
        self.assertEqual(report.dropped_unobservable_points, 0)
        self.assertEqual(report.observation_timestamp, self.spike_ts)
        self.assertEqual(report.observable_at, as_of)

    def test_longer_lag_delays_the_signal_further(self):
        series = make_series(BASELINE_SCORES + [90.0], lag_hours=48.0)
        as_of = self.spike_ts + timedelta(hours=36)
        report = self.engine.generate_trends_signal("NVDA", series, 5.2, as_of=as_of)
        self.assertEqual(report.signal_type, "INSUFFICIENT_DATA")
        self.assertEqual(report.dropped_unobservable_points, 1)

    def test_out_of_order_series_is_sorted_before_scoring(self):
        shuffled = list(self.series)
        shuffled.reverse()
        as_of = self.spike_ts + timedelta(hours=24)

        ordered_report = self.engine.generate_trends_signal("NVDA", self.series, 5.2, as_of=as_of)
        shuffled_report = self.engine.generate_trends_signal("NVDA", shuffled, 5.2, as_of=as_of)

        self.assertEqual(shuffled_report.signal_type, ordered_report.signal_type)
        self.assertAlmostEqual(shuffled_report.svi_z_score, ordered_report.svi_z_score, places=6)
        self.assertEqual(shuffled_report.observation_timestamp, self.spike_ts)

    def test_naive_as_of_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.generate_trends_signal(
                "NVDA", self.series, 5.2, as_of=datetime(2026, 7, 2)
            )


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = GoogleTrendsSignalEngine(lookback_window=30, z_score_threshold=2.0)
        self.valid = make_series(BASELINE_SCORES + [90.0])

    def test_timezone_naive_observation_timestamp_is_rejected(self):
        series = list(self.valid)
        series[-1] = SviDataPoint("2026-07-01", "NVDA", 90.0)
        with self.assertRaises(ValueError) as ctx:
            self.engine.generate_trends_signal("NVDA", series, 5.2)
        self.assertIn("timezone-naive", str(ctx.exception))

    def test_duplicate_timestamp_is_rejected(self):
        series = list(self.valid)
        series.append(
            SviDataPoint(series[-1].timestamp_iso, "NVDA", 91.0, publication_lag_hours=24.0)
        )
        with self.assertRaises(ValueError) as ctx:
            self.engine.generate_trends_signal("NVDA", series, 5.2)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_svi_above_scale_max_is_rejected_for_ui_sourced_data(self):
        series = list(self.valid)
        series[-1] = SviDataPoint(series[-1].timestamp_iso, "NVDA", 140.0)
        with self.assertRaises(ValueError):
            self.engine.generate_trends_signal("NVDA", series, 5.2)

    def test_scale_max_none_admits_consistently_scaled_api_values(self):
        engine = GoogleTrendsSignalEngine(lookback_window=30, svi_scale_max=None)
        series = make_series([s * 100 for s in BASELINE_SCORES] + [9000.0])
        report = engine.generate_trends_signal("NVDA", series, 5.2)
        self.assertEqual(report.signal_type, "BULLISH_ATTENTION_SURGE")

    def test_negative_svi_is_rejected(self):
        series = list(self.valid)
        series[-1] = SviDataPoint(series[-1].timestamp_iso, "NVDA", -1.0)
        with self.assertRaises(ValueError):
            self.engine.generate_trends_signal("NVDA", series, 5.2)

    def test_nan_momentum_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.generate_trends_signal("NVDA", self.valid, float("nan"))

    def test_blank_keyword_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.generate_trends_signal("   ", self.valid, 5.2)

    def test_empty_series_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.generate_trends_signal("NVDA", [], 5.2)

    def test_negative_publication_lag_is_rejected(self):
        series = list(self.valid)
        series[-1] = SviDataPoint(series[-1].timestamp_iso, "NVDA", 90.0, publication_lag_hours=-1.0)
        with self.assertRaises(ValueError):
            self.engine.generate_trends_signal("NVDA", series, 5.2)

    def test_lookback_window_below_two_is_rejected(self):
        with self.assertRaises(ValueError):
            GoogleTrendsSignalEngine(lookback_window=1)

    def test_negative_min_baseline_std_is_rejected(self):
        with self.assertRaises(ValueError):
            GoogleTrendsSignalEngine(min_baseline_std=-0.5)


if __name__ == "__main__":
    unittest.main()
