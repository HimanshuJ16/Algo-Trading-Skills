import unittest
import datetime
from weather_data_signal_research_for_commodity_strategies import (
    CommoditySector,
    SignalDirection,
    WeatherObservation,
    DegreeDayMetrics,
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
        # HDD = max(0, 65 - 30) = 35. CDD = 0. GDD = max(0, 30 - 50) = 0.
        obs = [
            WeatherObservation("ST1", "US_MIDWEST", self.today, t_min_f=20.0, t_max_f=40.0, population_weight=1.0)
        ]
        dd = self.engine.calculate_degree_days(obs)

        self.assertEqual(dd.raw_hdd, 35.0)
        self.assertEqual(dd.raw_cdd, 0.0)
        self.assertEqual(dd.raw_gdd, 0.0)
        self.assertEqual(dd.weighted_hdd, 35.0)

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

    def test_anomaly_zscore_computation(self):
        # Current HDD = 45, baseline mean = 30, baseline std = 10 -> Z = (45 - 30) / 10 = +1.5
        z = self.engine.compute_anomaly_zscore(current_value=45.0, baseline_mean=30.0, baseline_std=10.0)
        self.assertEqual(z, 1.5)

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
        # Current temp anomaly = -20 (Mild/Rainy), mean = 0, std = 10 -> Z = -2.0 <= -1.5 -> SHORT C1 (Crop surplus)
        signal = self.engine.generate_commodity_trade_signal(
            sector=CommoditySector.AGRI_CORN,
            symbol="C1",
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


if __name__ == "__main__":
    unittest.main()

