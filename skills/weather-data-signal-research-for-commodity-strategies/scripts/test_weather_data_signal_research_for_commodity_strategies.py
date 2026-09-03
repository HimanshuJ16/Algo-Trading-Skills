import unittest
import datetime
import math
from weather_data_signal_research_for_commodity_strategies import (
    CommoditySector,
    SignalDirection,
    WeatherObservation,
    DegreeDayMetrics,
    ClimateBaseline,
    WeatherSignalResult,
    WeatherCommoditySignalEngine,
    WeatherEngineError,
)


class TestWeatherCommoditySignalEngine(unittest.TestCase):
    def setUp(self):
        self.engine = WeatherCommoditySignalEngine(zscore_threshold=1.5)
        self.today = datetime.date(2025, 1, 15)

    def test_degree_day_calculations_hdd_cdd_gdd(self):
        # Cold winter day: T_min = 20, T_max = 40 -> T_mean = 30
        # HDD = max(0, 65 - 30) = 35. CDD = 0.
        # Modified GDD floors T_min at 50 -> (40 + 50) / 2 - 50 = -5 -> clipped to 0.
        obs = [
            WeatherObservation("ST1", "US_MIDWEST", self.today, t_min_f=20.0, t_max_f=40.0, population_weight=1.0)
        ]
        dd = self.engine.calculate_degree_days(obs)

        self.assertEqual(dd.raw_hdd, 35.0)
        self.assertEqual(dd.raw_cdd, 0.0)
        self.assertEqual(dd.raw_gdd, 0.0)
        self.assertEqual(dd.weighted_hdd, 35.0)
        self.assertEqual(dd.station_count, 1)

    def test_population_weighted_degree_days(self):
        # Station 1 (NY, weight=3.0): T_mean = 30 -> HDD = 35
        # Station 2 (TX, weight=1.0): T_mean = 50 -> HDD = 15
        # Weighted HDD = (35 * 3/4) + (15 * 1/4) = 26.25 + 3.75 = 30.0
        obs = [
            WeatherObservation("NY", "US_EAST", self.today, 20.0, 40.0, population_weight=3.0),
            WeatherObservation("TX", "US_SOUTH", self.today, 40.0, 60.0, population_weight=1.0),
        ]
        dd = self.engine.calculate_degree_days(obs)

        self.assertEqual(dd.weighted_hdd, 30.0)

    # --- Modified Growing Degree Day math (NOAA CPC / Barger 1969) --------------------
    # Worked examples below are taken from Purdue Extension's heat-unit reference and
    # match the NOAA CPC definition: T_max is capped at 86F and T_min floored at 50F
    # BEFORE the daily mean is taken.

    def test_modified_gdd_caps_daily_maximum_at_86f(self):
        # T_max = 90, T_min = 72 -> capped T_max = 86 -> (86 + 72) / 2 - 50 = 29.0
        # The unclamped average would give (90 + 72) / 2 - 50 = 31.0, so this test
        # fails against the pre-fix simple-average implementation.
        obs = [
            WeatherObservation("IA1", "US_CORN_BELT", self.today, t_min_f=72.0, t_max_f=90.0)
        ]
        dd = self.engine.calculate_degree_days(obs)

        self.assertEqual(dd.raw_gdd, 29.0)
        self.assertEqual(dd.weighted_gdd, 29.0)

    def test_modified_gdd_floors_daily_minimum_at_50f(self):
        # T_max = 68, T_min = 41 -> floored T_min = 50 -> (68 + 50) / 2 - 50 = 9.0
        # The unclamped average would give (68 + 41) / 2 - 50 = 4.5.
        obs = [
            WeatherObservation("IL1", "US_CORN_BELT", self.today, t_min_f=41.0, t_max_f=68.0)
        ]
        dd = self.engine.calculate_degree_days(obs)

        self.assertEqual(dd.raw_gdd, 9.0)

    def test_modified_gdd_inside_band_is_plain_average(self):
        # T_max = 80, T_min = 55: both inside the 50-86 band -> (80 + 55) / 2 - 50 = 17.5
        obs = [
            WeatherObservation("NE1", "US_CORN_BELT", self.today, t_min_f=55.0, t_max_f=80.0)
        ]
        dd = self.engine.calculate_degree_days(obs)

        self.assertEqual(dd.raw_gdd, 17.5)

    def test_gdd_clamps_can_be_disabled_for_unclamped_models(self):
        # Escape hatch for agronomic models that do not clamp: 90/72 -> 31.0
        obs = [
            WeatherObservation("IA1", "US_CORN_BELT", self.today, t_min_f=72.0, t_max_f=90.0)
        ]
        dd = self.engine.calculate_degree_days(
            obs, gdd_upper_cap_f=math.inf, gdd_lower_floor_f=-math.inf
        )

        self.assertEqual(dd.raw_gdd, 31.0)

    def test_gdd_clamp_does_not_affect_hdd_or_cdd(self):
        # HDD/CDD use the unclamped mean: (90 + 72) / 2 = 81 -> CDD = 16, HDD = 0
        obs = [
            WeatherObservation("TX1", "US_SOUTH", self.today, t_min_f=72.0, t_max_f=90.0)
        ]
        dd = self.engine.calculate_degree_days(obs)

        self.assertEqual(dd.raw_cdd, 16.0)
        self.assertEqual(dd.raw_hdd, 0.0)

    def test_acreage_weighted_gdd_aggregation(self):
        # IA (weight 3): 80/55 -> 17.5 GDD.  IL (weight 1): 68/41 -> 9.0 GDD.
        # Weighted GDD = 17.5 * 0.75 + 9.0 * 0.25 = 13.125 + 2.25 = 15.375 -> 15.38
        obs = [
            WeatherObservation("IA1", "US_CORN_BELT", self.today, 55.0, 80.0, population_weight=3.0),
            WeatherObservation("IL1", "US_CORN_BELT", self.today, 41.0, 68.0, population_weight=1.0),
        ]
        dd = self.engine.calculate_degree_days(obs)

        self.assertEqual(dd.weighted_gdd, 15.38)

    # --- Degree-day input validation --------------------------------------------------

    def test_mixed_dates_are_rejected(self):
        obs = [
            WeatherObservation("ST1", "US_EAST", self.today, 20.0, 40.0),
            WeatherObservation("ST2", "US_EAST", datetime.date(2025, 1, 16), 25.0, 45.0),
        ]
        with self.assertRaises(WeatherEngineError):
            self.engine.calculate_degree_days(obs)

    def test_duplicate_station_id_is_rejected(self):
        obs = [
            WeatherObservation("ST1", "US_EAST", self.today, 20.0, 40.0),
            WeatherObservation("ST1", "US_EAST", self.today, 20.0, 40.0),
        ]
        with self.assertRaises(WeatherEngineError):
            self.engine.calculate_degree_days(obs)

    def test_negative_station_weight_is_rejected(self):
        # Total weight stays positive (3 - 1 = 2), so a total-only check would pass
        # while the negative weight silently corrupts the aggregate.
        obs = [
            WeatherObservation("ST1", "US_EAST", self.today, 20.0, 40.0, population_weight=3.0),
            WeatherObservation("ST2", "US_EAST", self.today, 40.0, 60.0, population_weight=-1.0),
        ]
        with self.assertRaises(WeatherEngineError):
            self.engine.calculate_degree_days(obs)

    def test_transposed_min_max_temperature_is_rejected(self):
        obs = [WeatherObservation("ST1", "US_EAST", self.today, t_min_f=70.0, t_max_f=40.0)]
        with self.assertRaises(WeatherEngineError):
            self.engine.calculate_degree_days(obs)

    def test_nan_temperature_is_rejected_rather_than_propagated(self):
        obs = [WeatherObservation("ST1", "US_EAST", self.today, t_min_f=float("nan"), t_max_f=40.0)]
        with self.assertRaises(WeatherEngineError):
            self.engine.calculate_degree_days(obs)

    def test_nan_zscore_input_is_rejected(self):
        # A NaN Z-score compares False against every threshold and would masquerade
        # as a deliberate NEUTRAL signal.
        with self.assertRaises(WeatherEngineError):
            self.engine.compute_anomaly_zscore(float("nan"), baseline_mean=30.0, baseline_std=10.0)

    # --- Z-score and threshold behaviour ----------------------------------------------

    def test_anomaly_zscore_computation(self):
        # Current HDD = 45, baseline mean = 30, baseline std = 10 -> Z = (45 - 30) / 10 = +1.5
        z = self.engine.compute_anomaly_zscore(current_value=45.0, baseline_mean=30.0, baseline_std=10.0)
        self.assertEqual(z, 1.5)

    def test_zscore_is_not_rounded_before_threshold_comparison(self):
        # Z = 1496 / 1000 = 1.496, which is BELOW the 1.5 trigger. Rounding to two
        # decimals first would promote it to exactly 1.50 and fire a LONG signal.
        signal = self.engine.generate_commodity_trade_signal(
            sector=CommoditySector.ENERGY_NATGAS,
            symbol="NGF6",
            current_value=1496.0,
            baseline_mean=0.0,
            baseline_std=1000.0,
            signal_date=self.today,
        )

        self.assertAlmostEqual(signal.anomaly_zscore, 1.496, places=9)
        self.assertEqual(signal.direction, SignalDirection.NEUTRAL)

    def test_exact_threshold_triggers_directional_signal(self):
        # Z = exactly +1.5; the documented rule is inclusive (Z >= +threshold).
        signal = self.engine.generate_commodity_trade_signal(
            sector=CommoditySector.ENERGY_NATGAS,
            symbol="NGF6",
            current_value=45.0,
            baseline_mean=30.0,
            baseline_std=10.0,
            signal_date=self.today,
        )

        self.assertEqual(signal.direction, SignalDirection.LONG)
        self.assertEqual(signal.confidence_score, 0.5)

    def test_neutral_signal_carries_zero_confidence(self):
        # An untriggered signal must not hand a position sizer a non-zero weight.
        signal = self.engine.generate_commodity_trade_signal(
            sector=CommoditySector.ENERGY_NATGAS,
            symbol="NGF6",
            current_value=35.0,
            baseline_mean=30.0,
            baseline_std=10.0,
            signal_date=self.today,
        )

        self.assertEqual(signal.direction, SignalDirection.NEUTRAL)
        self.assertEqual(signal.confidence_score, 0.0)

    def test_non_positive_threshold_is_rejected(self):
        # Zero would fire on every observation and divide by zero in the confidence scaling.
        with self.assertRaises(WeatherEngineError):
            WeatherCommoditySignalEngine(zscore_threshold=0.0)
        with self.assertRaises(WeatherEngineError):
            WeatherCommoditySignalEngine(zscore_threshold=-1.5)

    def test_invalid_sector_type_raises_domain_error(self):
        with self.assertRaises(WeatherEngineError):
            self.engine.generate_commodity_trade_signal(
                sector="ENERGY_NATGAS",
                symbol="NGF6",
                current_value=50.0,
                baseline_mean=30.0,
                baseline_std=10.0,
                signal_date=self.today,
            )

    # --- Directional signal mapping ---------------------------------------------------

    def test_natgas_long_signal_on_severe_cold_hdd_spike(self):
        # Current HDD = 50, mean = 30, std = 10 -> Z = +2.0 >= +1.5 -> LONG NG1
        signal = self.engine.generate_commodity_trade_signal(
            sector=CommoditySector.ENERGY_NATGAS,
            symbol="NG1",
            current_value=50.0,
            baseline_mean=30.0,
            baseline_std=10.0,
            signal_date=self.today,
        )

        self.assertEqual(signal.direction, SignalDirection.LONG)
        self.assertEqual(signal.anomaly_zscore, 2.0)
        self.assertTrue(signal.confidence_score > 0.5)

    def test_agri_corn_short_signal_on_favorable_mild_weather(self):
        # Crop-stress metric (e.g. EDDI) 20 units BELOW its norm, std = 10 -> Z = -2.0
        # <= -1.5 -> SHORT ZC (benign conditions, surplus yield).
        signal = self.engine.generate_commodity_trade_signal(
            sector=CommoditySector.AGRI_CORN,
            symbol="ZCZ5",
            current_value=-20.0,
            baseline_mean=0.0,
            baseline_std=10.0,
            signal_date=self.today,
        )

        self.assertEqual(signal.direction, SignalDirection.SHORT)
        self.assertEqual(signal.anomaly_zscore, -2.0)

    def test_invalid_inputs_raise_error(self):
        with self.assertRaises(WeatherEngineError):
            self.engine.calculate_degree_days([])

        with self.assertRaises(WeatherEngineError):
            self.engine.compute_anomaly_zscore(30.0, baseline_mean=30.0, baseline_std=-5.0)


class TestClimateBaseline(unittest.TestCase):
    """Look-ahead safety of the strictly-trailing climate norm."""

    def setUp(self):
        self.engine = WeatherCommoditySignalEngine(zscore_threshold=1.5)
        self.as_of = datetime.date(2025, 1, 15)

    def test_baseline_excludes_current_and_future_observations(self):
        # Prior-year values 10..50 -> mean 30, sample std sqrt(250) = 15.8113883...
        # The 1000.0 entry dated ON as_of and the 2000.0 entry dated AFTER it must be
        # excluded; including either would move the mean far off 30.
        # The 2024-03-01 entry is 45 calendar days away seasonally and falls outside
        # the +/-7 day window.
        history = [
            (datetime.date(2020, 1, 15), 10.0),
            (datetime.date(2021, 1, 15), 20.0),
            (datetime.date(2022, 1, 15), 30.0),
            (datetime.date(2023, 1, 15), 40.0),
            (datetime.date(2024, 1, 15), 50.0),
            (datetime.date(2024, 3, 1), 999.0),
            (datetime.date(2025, 1, 15), 1000.0),
            (datetime.date(2025, 1, 20), 2000.0),
        ]
        baseline = self.engine.compute_climate_baseline(history, as_of=self.as_of)

        self.assertEqual(baseline.sample_size, 5)
        self.assertAlmostEqual(baseline.mean, 30.0, places=9)
        self.assertAlmostEqual(baseline.std, math.sqrt(250.0), places=9)
        self.assertEqual(baseline.as_of, self.as_of)

    def test_seasonal_window_wraps_across_the_year_boundary(self):
        # as_of Jan 3; Dec 30 is 4 calendar days away once the year boundary wraps.
        as_of = datetime.date(2025, 1, 3)
        history = [
            (datetime.date(year, 12, 30), float(value))
            for year, value in zip(range(2020, 2025), range(1, 6))
        ]
        baseline = self.engine.compute_climate_baseline(history, as_of=as_of)

        self.assertEqual(baseline.sample_size, 5)
        self.assertAlmostEqual(baseline.mean, 3.0, places=9)

    def test_lookback_horizon_excludes_older_observations(self):
        # lookback_years=3 -> earliest allowed is round(3 * 365.2425) = 1096 days
        # before 2025-01-15, which is exactly 2022-01-15. The 2021 entry is older.
        history = [
            (datetime.date(2021, 1, 15), 10.0),
            (datetime.date(2022, 1, 15), 20.0),
            (datetime.date(2023, 1, 15), 30.0),
            (datetime.date(2024, 1, 15), 40.0),
        ]
        baseline = self.engine.compute_climate_baseline(
            history, as_of=self.as_of, lookback_years=3, min_observations=2
        )

        self.assertEqual(baseline.sample_size, 3)
        self.assertAlmostEqual(baseline.mean, 30.0, places=9)

    def test_insufficient_sample_raises_rather_than_returning_a_thin_baseline(self):
        history = [
            (datetime.date(2023, 1, 15), 10.0),
            (datetime.date(2024, 1, 15), 20.0),
        ]
        with self.assertRaises(WeatherEngineError):
            self.engine.compute_climate_baseline(history, as_of=self.as_of)

    def test_zero_dispersion_baseline_raises(self):
        history = [(datetime.date(2020 + i, 1, 15), 25.0) for i in range(5)]
        with self.assertRaises(WeatherEngineError):
            self.engine.compute_climate_baseline(history, as_of=self.as_of)

    def test_nan_history_value_is_rejected(self):
        history = [
            (datetime.date(2020, 1, 15), 10.0),
            (datetime.date(2021, 1, 15), 20.0),
            (datetime.date(2022, 1, 15), float("nan")),
            (datetime.date(2023, 1, 15), 40.0),
            (datetime.date(2024, 1, 15), 50.0),
        ]
        with self.assertRaises(WeatherEngineError):
            self.engine.compute_climate_baseline(history, as_of=self.as_of)

    def test_leap_day_as_of_does_not_crash_the_seasonal_index(self):
        # Feb 29 has no counterpart in the non-leap reference year; it folds onto Feb 28.
        as_of = datetime.date(2024, 2, 29)
        history = [(datetime.date(2019 + i, 2, 27), float(i)) for i in range(5)]
        baseline = self.engine.compute_climate_baseline(history, as_of=as_of)

        self.assertEqual(baseline.sample_size, 5)
        self.assertAlmostEqual(baseline.mean, 2.0, places=9)

    def test_datetime_instances_are_rejected_with_an_actionable_message(self):
        # datetime.datetime subclasses datetime.date, so a naive isinstance guard admits
        # it and the first date/datetime comparison raises an opaque TypeError.
        history = [(datetime.date(2020 + i, 1, 15), float(i)) for i in range(5)]

        with self.assertRaises(WeatherEngineError):
            self.engine.compute_climate_baseline(
                history, as_of=datetime.datetime(2025, 1, 15, 12, 0)
            )
        with self.assertRaises(WeatherEngineError):
            self.engine.compute_climate_baseline(
                [(datetime.datetime(2024, 1, 15, 12, 0), 10.0)] + history, as_of=self.as_of
            )

    def test_invalid_baseline_parameters_are_rejected(self):
        history = [(datetime.date(2020 + i, 1, 15), float(i)) for i in range(5)]

        with self.assertRaises(WeatherEngineError):
            self.engine.compute_climate_baseline(history, as_of=self.as_of, lookback_years=0)
        with self.assertRaises(WeatherEngineError):
            self.engine.compute_climate_baseline(history, as_of=self.as_of, day_window=200)
        with self.assertRaises(WeatherEngineError):
            self.engine.compute_climate_baseline(history, as_of=self.as_of, min_observations=1)

    def test_baseline_feeds_signal_generation_end_to_end(self):
        # Baseline mean 30, std sqrt(250) ~= 15.8114. A forecast of 65 gives
        # Z = (65 - 30) / 15.8114 = 2.2136 -> LONG natural gas.
        history = [
            (datetime.date(2020, 1, 15), 10.0),
            (datetime.date(2021, 1, 15), 20.0),
            (datetime.date(2022, 1, 15), 30.0),
            (datetime.date(2023, 1, 15), 40.0),
            (datetime.date(2024, 1, 15), 50.0),
        ]
        baseline = self.engine.compute_climate_baseline(history, as_of=self.as_of)
        signal = self.engine.generate_commodity_trade_signal(
            sector=CommoditySector.ENERGY_NATGAS,
            symbol="NGF6",
            current_value=65.0,
            baseline_mean=baseline.mean,
            baseline_std=baseline.std,
            signal_date=self.as_of,
        )

        self.assertEqual(signal.direction, SignalDirection.LONG)
        self.assertAlmostEqual(signal.anomaly_zscore, 35.0 / math.sqrt(250.0), places=9)


if __name__ == "__main__":
    unittest.main()
