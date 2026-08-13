import unittest
from datetime import datetime

from app_download_and_usage_data_for_consumer_companies import (
    AppUsageDataPoint,
    AppUsageSignal,
    AppUsageSignalEngine,
    EngineConfig,
)


class TestAppUsageSignalEngine(unittest.TestCase):
    def setUp(self):
        self.engine = AppUsageSignalEngine()

    # --- Normal cases -------------------------------------------------------
    def test_world_class_stickiness(self):
        # A highly engaging app (e.g., major social media)
        data = AppUsageDataPoint(
            ticker="META",
            date=datetime(2026, 1, 1),
            downloads=50000,
            dau=6000000,
            mau=10000000,
        )
        # Stickiness = 60%
        signal = self.engine.process(data)

        self.assertEqual(signal.stickiness_ratio, 0.60)
        self.assertTrue(signal.is_world_class)
        self.assertFalse(signal.churn_risk_warning)

    def test_leaky_bucket_churn_risk(self):
        # An over-marketed game app losing players fast
        data = AppUsageDataPoint(
            ticker="MOBILE",
            date=datetime(2026, 1, 1),
            downloads=200000,   # High acquisition (>10% of MAU)
            dau=150000,
            mau=1000000,
        )
        # Stickiness = 15%
        signal = self.engine.process(data)

        self.assertEqual(signal.stickiness_ratio, 0.15)
        self.assertFalse(signal.is_world_class)
        self.assertTrue(signal.churn_risk_warning)
        self.assertIn("LEAKY BUCKET", signal.signal_summary)

    def test_average_engagement(self):
        # A utility app (e.g., airline booking) - low daily use, but not
        # bleeding money on ads
        data = AppUsageDataPoint(
            ticker="AIRLINE",
            date=datetime(2026, 1, 1),
            downloads=10000,    # Low acquisition (1% of MAU)
            dau=150000,
            mau=1000000,
        )
        # Stickiness = 15%
        signal = self.engine.process(data)

        self.assertEqual(signal.stickiness_ratio, 0.15)
        self.assertFalse(signal.is_world_class)
        self.assertFalse(signal.churn_risk_warning)  # Not a leaky bucket

    # --- Boundary / threshold cases ----------------------------------------
    def test_world_class_threshold_is_inclusive(self):
        # Exactly 50% stickiness => world-class (inclusive boundary)
        data = AppUsageDataPoint(
            ticker="EDGE",
            date=datetime(2026, 1, 1),
            downloads=0,
            dau=500,
            mau=1000,
        )
        signal = self.engine.process(data)
        self.assertEqual(signal.stickiness_ratio, 0.50)
        self.assertTrue(signal.is_world_class)

    def test_just_below_world_class_is_not_world_class(self):
        data = AppUsageDataPoint(
            ticker="EDGE",
            date=datetime(2026, 1, 1),
            downloads=0,
            dau=499,
            mau=1000,
        )
        signal = self.engine.process(data)
        self.assertAlmostEqual(signal.stickiness_ratio, 0.499)
        self.assertFalse(signal.is_world_class)

    def test_high_acquisition_boundary_is_inclusive(self):
        # downloads == exactly 10% of MAU, stickiness just below 0.20.
        # Both boundary conditions are inclusive => churn warning fires.
        data = AppUsageDataPoint(
            ticker="EDGE",
            date=datetime(2026, 1, 1),
            downloads=100,   # exactly 10% of MAU=1000
            dau=199,         # stickiness 0.199 < 0.20
            mau=1000,
        )
        signal = self.engine.process(data)
        self.assertAlmostEqual(signal.stickiness_ratio, 0.199)
        self.assertTrue(signal.churn_risk_warning)

    def test_low_stickiness_low_acquisition_no_churn_warning(self):
        # Stickiness is low but acquisition is below the high-acquisition
        # fraction => not a leaky bucket.
        data = AppUsageDataPoint(
            ticker="QUIET",
            date=datetime(2026, 1, 1),
            downloads=99,    # 9.9% of MAU=1000, below 10%
            dau=100,         # stickiness 0.10
            mau=1000,
        )
        signal = self.engine.process(data)
        self.assertAlmostEqual(signal.stickiness_ratio, 0.10)
        self.assertFalse(signal.churn_risk_warning)

    # --- Anomaly handling --------------------------------------------------
    def test_dau_exceeds_mau_anomaly(self):
        # Bad data handling: DAU clamped to MAU without mutating the input
        data = AppUsageDataPoint(
            ticker="ERR",
            date=datetime(2026, 1, 1),
            downloads=0,
            dau=200,
            mau=100,  # DAU > MAU is mathematically impossible
        )
        signal = self.engine.process(data)
        self.assertEqual(signal.stickiness_ratio, 1.0)  # clamped to 100%
        # Input must NOT be mutated.
        self.assertEqual(data.dau, 200)

    def test_zero_downloads_allowed(self):
        data = AppUsageDataPoint(
            ticker="ZERO",
            date=datetime(2026, 1, 1),
            downloads=0,
            dau=300,
            mau=1000,
        )
        signal = self.engine.process(data)
        self.assertEqual(signal.stickiness_ratio, 0.30)
        self.assertFalse(signal.is_world_class)
        self.assertFalse(signal.churn_risk_warning)

    # --- Invalid inputs -----------------------------------------------------
    def test_non_positive_mau_raises(self):
        data = AppUsageDataPoint(
            ticker="BAD", date=datetime(2026, 1, 1),
            downloads=10, dau=10, mau=0,
        )
        with self.assertRaises(ValueError):
            self.engine.process(data)

    def test_negative_downloads_raises(self):
        data = AppUsageDataPoint(
            ticker="BAD", date=datetime(2026, 1, 1),
            downloads=-1, dau=10, mau=100,
        )
        with self.assertRaises(ValueError):
            self.engine.process(data)

    def test_negative_dau_raises(self):
        data = AppUsageDataPoint(
            ticker="BAD", date=datetime(2026, 1, 1),
            downloads=0, dau=-5, mau=100,
        )
        with self.assertRaises(ValueError):
            self.engine.process(data)

    def test_empty_ticker_raises(self):
        data = AppUsageDataPoint(
            ticker="   ", date=datetime(2026, 1, 1),
            downloads=0, dau=10, mau=100,
        )
        with self.assertRaises(ValueError):
            self.engine.process(data)

    def test_wrong_type_raises(self):
        with self.assertRaises(TypeError):
            self.engine.process("not-a-datapoint")  # type: ignore[arg-type]

    # --- Configurability ---------------------------------------------------
    def test_custom_config_lowers_world_class_bar(self):
        engine = AppUsageSignalEngine(
            EngineConfig(world_class_threshold=0.30)
        )
        data = AppUsageDataPoint(
            ticker="LOW",
            date=datetime(2026, 1, 1),
            downloads=0, dau=350, mau=1000,  # stickiness 0.35
        )
        signal = engine.process(data)
        self.assertTrue(signal.is_world_class)

    def test_invalid_config_raises(self):
        with self.assertRaises(ValueError):
            EngineConfig(world_class_threshold=1.5)
        with self.assertRaises(ValueError):
            EngineConfig(low_stickiness_threshold=0.9, world_class_threshold=0.5)
        with self.assertRaises(ValueError):
            EngineConfig(high_acquisition_fraction=-0.1)

    # --- Batch processing --------------------------------------------------
    def test_process_many_preserves_order(self):
        points = [
            AppUsageDataPoint(ticker="A", date=datetime(2026, 1, 1),
                              downloads=0, dau=600, mau=1000),
            AppUsageDataPoint(ticker="B", date=datetime(2026, 1, 1),
                              downloads=200, dau=100, mau=1000),
        ]
        signals = self.engine.process_many(points)
        self.assertEqual(len(signals), 2)
        self.assertEqual([s.ticker for s in signals], ["A", "B"])
        self.assertTrue(signals[0].is_world_class)
        self.assertTrue(signals[1].churn_risk_warning)

    def test_process_many_fail_fast_on_invalid(self):
        points = [
            AppUsageDataPoint(ticker="A", date=datetime(2026, 1, 1),
                              downloads=0, dau=600, mau=1000),
            AppUsageDataPoint(ticker="B", date=datetime(2026, 1, 1),
                              downloads=0, dau=10, mau=0),  # invalid
        ]
        with self.assertRaises(ValueError):
            self.engine.process_many(points)

    def test_process_many_empty(self):
        self.assertEqual(self.engine.process_many([]), [])

    # --- Return type / determinism -----------------------------------------
    def test_signal_is_frozen(self):
        data = AppUsageDataPoint(
            ticker="META", date=datetime(2026, 1, 1),
            downloads=0, dau=600, mau=1000,
        )
        signal = self.engine.process(data)
        with self.assertRaises(dataclasses_FrozenInstanceError):
            signal.stickiness_ratio = 0.99  # type: ignore[misc]

    def test_process_is_deterministic(self):
        data = AppUsageDataPoint(
            ticker="META", date=datetime(2026, 1, 1),
            downloads=0, dau=600, mau=1000,
        )
        s1 = self.engine.process(data)
        s2 = self.engine.process(data)
        self.assertEqual(s1, s2)


# dataclasses.FrozenInstanceError is only exposed via the instance at runtime
# in older Pythons; import lazily so the module imports on 3.9+.
try:
    from dataclasses import FrozenInstanceError as dataclasses_FrozenInstanceError
except ImportError:  # pragma: no cover - Python < 3.10 fallback path
    dataclasses_FrozenInstanceError = AttributeError  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
