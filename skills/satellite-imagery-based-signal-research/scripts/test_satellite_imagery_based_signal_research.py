import logging
import math
import unittest

from satellite_imagery_based_signal_research import (
    HIGH_READING_DIRECTION,
    ImagerySignalType,
    QuantitativeSatelliteSignal,
    SatelliteImageryBasedSignalResearchEngine,
    SatelliteObservation,
)

ACQUIRED = "2026-08-05T12:00:00Z"


def observation(**overrides) -> SatelliteObservation:
    """A valid retail observation, overridable field by field."""
    defaults = dict(
        timestamp_iso=ACQUIRED,
        asset_id="WMT",
        signal_type=ImagerySignalType.RETAIL_PARKING_OCCUPANCY,
        observed_metric=12000.0,
        baseline_historical_mean=10000.0,
        baseline_historical_std=1000.0,
    )
    defaults.update(overrides)
    return SatelliteObservation(**defaults)


class TestDirectionalMapping(unittest.TestCase):
    """Every (signal type, sign) pair, against hand-derived Z-scores."""

    def setUp(self):
        self.engine = SatelliteImageryBasedSignalResearchEngine(z_score_threshold=1.5)

    def test_retail_high_traffic_is_long_the_retailer(self):
        # (12000 - 10000) / 1000 = +2.0
        sig = self.engine.compute_satellite_signal(observation())
        self.assertEqual(sig.z_score, 2.0)
        self.assertEqual(sig.trading_signal_direction, 1.0)
        self.assertGreater(sig.confidence_pct, 60.0)

    def test_retail_empty_lot_is_short_the_retailer(self):
        # (7500 - 10000) / 1000 = -2.5
        sig = self.engine.compute_satellite_signal(observation(observed_metric=7500.0))
        self.assertEqual(sig.z_score, -2.5)
        self.assertEqual(sig.trading_signal_direction, -1.0)

    def test_full_oil_tanks_are_short_crude(self):
        # (85 - 60) / 10 = +2.5 -> inventory build -> bearish crude
        sig = self.engine.compute_satellite_signal(observation(
            asset_id="CL_FUTURES",
            signal_type=ImagerySignalType.FLOATING_ROOF_OIL_STORAGE,
            observed_metric=85.0,
            baseline_historical_mean=60.0,
            baseline_historical_std=10.0,
        ))
        self.assertEqual(sig.z_score, 2.5)
        self.assertEqual(sig.trading_signal_direction, -1.0)

    def test_draining_oil_tanks_are_long_crude(self):
        # (40 - 60) / 10 = -2.0 -> inventory draw -> bullish crude
        sig = self.engine.compute_satellite_signal(observation(
            asset_id="CL_FUTURES",
            signal_type=ImagerySignalType.FLOATING_ROOF_OIL_STORAGE,
            observed_metric=40.0,
            baseline_historical_mean=60.0,
            baseline_historical_std=10.0,
        ))
        self.assertEqual(sig.z_score, -2.0)
        self.assertEqual(sig.trading_signal_direction, 1.0)

    def test_vigorous_ndvi_is_short_the_crop(self):
        # (0.78 - 0.60) / 0.06 = +3.0 -> bigger harvest -> bearish crop price
        sig = self.engine.compute_satellite_signal(observation(
            asset_id="ZC_FUTURES",
            signal_type=ImagerySignalType.AGRICULTURAL_NDVI,
            observed_metric=0.78,
            baseline_historical_mean=0.60,
            baseline_historical_std=0.06,
        ))
        self.assertAlmostEqual(sig.z_score, 3.0, places=9)
        self.assertEqual(sig.trading_signal_direction, -1.0)

    def test_stressed_ndvi_is_long_the_crop(self):
        # (0.48 - 0.60) / 0.06 = -2.0 -> crop stress -> bullish crop price
        sig = self.engine.compute_satellite_signal(observation(
            asset_id="ZC_FUTURES",
            signal_type=ImagerySignalType.AGRICULTURAL_NDVI,
            observed_metric=0.48,
            baseline_historical_mean=0.60,
            baseline_historical_std=0.06,
        ))
        self.assertAlmostEqual(sig.z_score, -2.0, places=9)
        self.assertEqual(sig.trading_signal_direction, 1.0)

    def test_every_declared_signal_type_has_a_direction(self):
        # A new enum member must not silently inherit another domain's economics.
        self.assertEqual(
            set(HIGH_READING_DIRECTION), set(ImagerySignalType),
            "every ImagerySignalType needs an explicit HIGH_READING_DIRECTION entry",
        )

    def test_unmapped_signal_type_raises(self):
        stray = object()
        with self.assertRaises(KeyError):
            self.engine.compute_satellite_signal(observation(signal_type=stray))

    def test_unknown_signal_type_string_raises(self):
        with self.assertRaises(KeyError):
            self.engine.compute_satellite_signal(
                observation(signal_type="SHIPPING_PORT_CONGESTION"))

    def test_bare_string_signal_type_is_normalised_to_the_enum(self):
        # ImagerySignalType is a str enum, so the raw string hashes equal to the
        # member and passes the mapping check; without normalisation the audit
        # note then crashed on `.value`.
        sig = self.engine.compute_satellite_signal(
            observation(signal_type="RETAIL_PARKING_OCCUPANCY"))
        self.assertIs(sig.signal_type, ImagerySignalType.RETAIL_PARKING_OCCUPANCY)
        self.assertEqual(sig.trading_signal_direction, 1.0)
        self.assertIn("RETAIL_PARKING_OCCUPANCY", sig.audit_notes)


class TestThresholdBoundary(unittest.TestCase):
    """The threshold is inclusive and must be applied to the unrounded Z."""

    def setUp(self):
        self.engine = SatelliteImageryBasedSignalResearchEngine(z_score_threshold=1.5)

    def test_exactly_at_threshold_fires(self):
        # (11500 - 10000) / 1000 = exactly +1.5
        sig = self.engine.compute_satellite_signal(observation(observed_metric=11500.0))
        self.assertEqual(sig.z_score, 1.5)
        self.assertEqual(sig.trading_signal_direction, 1.0)

    def test_just_below_threshold_is_neutral(self):
        # (11499 - 10000) / 1000 = +1.499
        sig = self.engine.compute_satellite_signal(observation(observed_metric=11499.0))
        self.assertEqual(sig.trading_signal_direction, 0.0)

    def test_display_rounding_does_not_promote_a_neutral_to_a_trade(self):
        # Regression: the previous implementation rounded Z to 2dp BEFORE
        # thresholding, so 1.4951 became 1.5 and fired a full-conviction trade.
        sig = self.engine.compute_satellite_signal(observation(observed_metric=11495.1))
        self.assertAlmostEqual(sig.z_score, 1.4951, places=9)
        self.assertEqual(round(sig.z_score, 2), 1.5)
        self.assertEqual(sig.trading_signal_direction, 0.0)

    def test_small_deviation_is_neutral(self):
        # (10200 - 10000) / 1000 = +0.2
        sig = self.engine.compute_satellite_signal(observation(
            asset_id="TGT", observed_metric=10200.0))
        self.assertAlmostEqual(sig.z_score, 0.2, places=9)
        self.assertEqual(sig.trading_signal_direction, 0.0)


class TestAvailabilityLag(unittest.TestCase):
    """`tradeable_from_iso` is the point-in-time boundary this skill exists for."""

    def setUp(self):
        self.engine = SatelliteImageryBasedSignalResearchEngine()

    def test_default_two_day_lag_is_stamped(self):
        sig = self.engine.compute_satellite_signal(observation())
        self.assertEqual(sig.tradeable_from_iso, "2026-08-07T12:00:00Z")

    def test_fractional_lag_is_honoured(self):
        sig = self.engine.compute_satellite_signal(
            observation(availability_lag_days=0.5))
        self.assertEqual(sig.tradeable_from_iso, "2026-08-06T00:00:00Z")

    def test_zero_lag_is_permitted_and_stamps_acquisition_time(self):
        sig = self.engine.compute_satellite_signal(observation(availability_lag_days=0))
        self.assertEqual(sig.tradeable_from_iso, "2026-08-05T12:00:00Z")

    def test_non_utc_acquisition_is_normalised_before_lagging(self):
        # 09:00-03:00 == 12:00Z, so the +2d stamp must match the UTC case.
        sig = self.engine.compute_satellite_signal(
            observation(timestamp_iso="2026-08-05T09:00:00-03:00"))
        self.assertEqual(sig.tradeable_from_iso, "2026-08-07T12:00:00Z")

    def test_negative_lag_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_satellite_signal(observation(availability_lag_days=-1))
        self.assertIn("availability_lag_days", str(ctx.exception))

    def test_absurd_lag_raises_valueerror_not_overflowerror(self):
        with self.assertRaises(ValueError):
            self.engine.compute_satellite_signal(
                observation(availability_lag_days=1e9))

    def test_naive_timestamp_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_satellite_signal(
                observation(timestamp_iso="2026-08-05T12:00:00"))
        self.assertIn("timezone-aware", str(ctx.exception))

    def test_malformed_timestamp_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_satellite_signal(observation(timestamp_iso="last Tuesday"))

    def test_empty_timestamp_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_satellite_signal(observation(timestamp_iso="   "))


class TestBaselineProvenance(unittest.TestCase):
    """A baseline that reaches past the acquisition time is look-ahead bias."""

    def setUp(self):
        self.engine = SatelliteImageryBasedSignalResearchEngine()

    def test_baseline_ending_after_acquisition_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_satellite_signal(
                observation(baseline_window_end_iso="2026-08-06T00:00:00Z"))
        self.assertIn("look-ahead", str(ctx.exception))

    def test_baseline_ending_before_acquisition_is_accepted(self):
        sig = self.engine.compute_satellite_signal(
            observation(baseline_window_end_iso="2026-08-04T12:00:00Z"))
        self.assertEqual(sig.trading_signal_direction, 1.0)

    def test_baseline_ending_at_acquisition_warns_about_self_inclusion(self):
        with self.assertLogs(
            "satellite_imagery_based_signal_research", level=logging.WARNING
        ) as captured:
            self.engine.compute_satellite_signal(
                observation(baseline_window_end_iso=ACQUIRED))
        self.assertIn("inside its own baseline", "\n".join(captured.output))

    def test_baseline_window_across_zones_is_compared_in_utc(self):
        # 2026-08-05T14:00:00+03:00 == 11:00Z, one hour BEFORE acquisition.
        sig = self.engine.compute_satellite_signal(
            observation(baseline_window_end_iso="2026-08-05T14:00:00+03:00"))
        self.assertEqual(sig.trading_signal_direction, 1.0)

    def test_observation_count_is_surfaced_in_the_audit_note(self):
        sig = self.engine.compute_satellite_signal(
            observation(baseline_observation_count=52))
        self.assertIn("n=52", sig.audit_notes)

    def test_unverified_baseline_size_is_flagged(self):
        sig = self.engine.compute_satellite_signal(observation())
        self.assertIn("n=unverified", sig.audit_notes)


class TestDegenerateBaseline(unittest.TestCase):
    """Regression: a bad baseline must not become a full-conviction trade."""

    def setUp(self):
        self.engine = SatelliteImageryBasedSignalResearchEngine()

    def test_zero_std_raises_instead_of_defaulting_to_one(self):
        # The previous implementation substituted std=1.0, turning a raw
        # deviation of 2000 cars into "Z = 2000" and a +1.0 direction.
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_satellite_signal(observation(baseline_historical_std=0.0))
        self.assertIn("baseline_historical_std", str(ctx.exception))

    def test_negative_std_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_satellite_signal(
                observation(baseline_historical_std=-1000.0))

    def test_nan_std_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_satellite_signal(
                observation(baseline_historical_std=float("nan")))


class TestNonFiniteInputs(unittest.TestCase):
    """Regression: NaN must not propagate silently into a trading signal."""

    def setUp(self):
        self.engine = SatelliteImageryBasedSignalResearchEngine()

    def test_nan_metric_raises(self):
        # Previously: z=nan, both comparisons False -> direction 0.0 and a NaN
        # confidence, indistinguishable from a genuine neutral reading.
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_satellite_signal(
                observation(observed_metric=float("nan")))
        self.assertIn("observed_metric", str(ctx.exception))

    def test_infinite_metric_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_satellite_signal(
                observation(observed_metric=float("inf")))

    def test_nan_mean_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_satellite_signal(
                observation(baseline_historical_mean=float("nan")))

    def test_no_signal_ever_carries_a_nan_field(self):
        sig = self.engine.compute_satellite_signal(observation())
        for value in (sig.z_score, sig.trading_signal_direction, sig.confidence_pct):
            self.assertTrue(math.isfinite(value))


class TestCloudQualityGate(unittest.TestCase):
    """Optical sensors see nothing through cloud."""

    def test_gate_disabled_by_default(self):
        engine = SatelliteImageryBasedSignalResearchEngine()
        sig = engine.compute_satellite_signal(observation(usable_pixel_fraction=0.05))
        self.assertTrue(sig.quality_gate_passed)
        self.assertEqual(sig.trading_signal_direction, 1.0)
        self.assertIn("Quality gate = OFF", sig.audit_notes)

    def test_clouded_scene_is_forced_neutral_when_gate_is_on(self):
        engine = SatelliteImageryBasedSignalResearchEngine(min_usable_pixel_fraction=0.7)
        sig = engine.compute_satellite_signal(observation(usable_pixel_fraction=0.3))
        self.assertFalse(sig.quality_gate_passed)
        self.assertEqual(sig.trading_signal_direction, 0.0)
        self.assertIn("BLOCKED", sig.audit_notes)
        # The Z-score is still reported so the observation stays auditable.
        self.assertEqual(sig.z_score, 2.0)

    def test_gate_is_inclusive_at_the_configured_minimum(self):
        engine = SatelliteImageryBasedSignalResearchEngine(min_usable_pixel_fraction=0.7)
        sig = engine.compute_satellite_signal(observation(usable_pixel_fraction=0.7))
        self.assertTrue(sig.quality_gate_passed)
        self.assertEqual(sig.trading_signal_direction, 1.0)

    def test_usable_fraction_above_one_raises(self):
        engine = SatelliteImageryBasedSignalResearchEngine()
        with self.assertRaises(ValueError):
            engine.compute_satellite_signal(observation(usable_pixel_fraction=1.4))

    def test_negative_usable_fraction_raises(self):
        engine = SatelliteImageryBasedSignalResearchEngine()
        with self.assertRaises(ValueError):
            engine.compute_satellite_signal(observation(usable_pixel_fraction=-0.1))


class TestConfidenceScore(unittest.TestCase):
    """`confidence_pct` is an uncalibrated rank, and must stay bounded."""

    def test_saturates_at_one_hundred(self):
        engine = SatelliteImageryBasedSignalResearchEngine(strength_saturation_z=3.0)
        # (16000 - 10000) / 1000 = +6.0, well past saturation.
        sig = engine.compute_satellite_signal(observation(observed_metric=16000.0))
        self.assertEqual(sig.confidence_pct, 100.0)

    def test_linear_below_saturation(self):
        engine = SatelliteImageryBasedSignalResearchEngine(strength_saturation_z=4.0)
        # |Z| = 2.0 of a 4.0 saturation -> 50%.
        sig = engine.compute_satellite_signal(observation())
        self.assertEqual(sig.confidence_pct, 50.0)

    def test_symmetric_in_sign(self):
        engine = SatelliteImageryBasedSignalResearchEngine()
        up = engine.compute_satellite_signal(observation(observed_metric=12000.0))
        down = engine.compute_satellite_signal(observation(observed_metric=8000.0))
        self.assertEqual(up.confidence_pct, down.confidence_pct)
        self.assertEqual(up.trading_signal_direction, -down.trading_signal_direction)

    def test_audit_note_does_not_claim_calibrated_confidence(self):
        engine = SatelliteImageryBasedSignalResearchEngine()
        sig = engine.compute_satellite_signal(observation())
        self.assertIn("uncalibrated", sig.audit_notes)


class TestEngineConfiguration(unittest.TestCase):

    def test_non_positive_threshold_raises(self):
        with self.assertRaises(ValueError):
            SatelliteImageryBasedSignalResearchEngine(z_score_threshold=0.0)

    def test_negative_threshold_raises(self):
        with self.assertRaises(ValueError):
            SatelliteImageryBasedSignalResearchEngine(z_score_threshold=-1.5)

    def test_non_positive_saturation_raises(self):
        with self.assertRaises(ValueError):
            SatelliteImageryBasedSignalResearchEngine(strength_saturation_z=0.0)

    def test_out_of_range_quality_gate_raises(self):
        with self.assertRaises(ValueError):
            SatelliteImageryBasedSignalResearchEngine(min_usable_pixel_fraction=1.5)

    def test_signal_shape_is_stable(self):
        engine = SatelliteImageryBasedSignalResearchEngine()
        sig = engine.compute_satellite_signal(observation())
        self.assertIsInstance(sig, QuantitativeSatelliteSignal)
        self.assertEqual(sig.timestamp_iso, ACQUIRED)
        self.assertEqual(sig.asset_id, "WMT")
        self.assertEqual(sig.raw_metric, 12000.0)
        self.assertEqual(sig.signal_type, ImagerySignalType.RETAIL_PARKING_OCCUPANCY)


if __name__ == "__main__":
    unittest.main()
