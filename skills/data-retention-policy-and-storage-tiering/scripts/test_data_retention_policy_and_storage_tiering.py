import unittest

from data_retention_policy_and_storage_tiering import (
    DataRetentionAuditReport,
    DataRetentionPolicyEngine,
    DataRetentionPolicyError,
    GLACIER_METADATA_ARCHIVE_KB,
    GLACIER_METADATA_STANDARD_KB,
    MarketDatasetInfo,
    TIER_COLD,
    TIER_DEEP_ARCHIVE,
    TIER_HOT,
    TIER_PURGE,
    TIER_WARM,
)


def make_dataset(**overrides) -> MarketDatasetInfo:
    """Baseline dataset; override only the field a test is actually about."""
    kwargs = dict(
        dataset_id="DS",
        data_type="L2_ORDER_BOOK",
        size_gb=1_000.0,
        age_days=120,
        current_tier=TIER_HOT,
        regulatory_retention_years=6.0,
    )
    kwargs.update(overrides)
    return MarketDatasetInfo(**kwargs)


class TestTierLadder(unittest.TestCase):
    """The ladder is purely age-driven and independent of the retention period."""

    def setUp(self):
        self.engine = DataRetentionPolicyEngine()

    def test_fresh_data_stays_hot(self):
        rec = self.engine.evaluate_dataset_lifecycle(make_dataset(age_days=10))
        self.assertEqual(rec.recommended_tier, TIER_HOT)
        self.assertEqual(rec.action_required, "NO_CHANGE")

    def test_hot_boundary_is_inclusive_at_30_and_flips_at_31(self):
        self.assertEqual(
            self.engine.evaluate_dataset_lifecycle(make_dataset(age_days=30)).recommended_tier,
            TIER_HOT)
        self.assertEqual(
            self.engine.evaluate_dataset_lifecycle(make_dataset(age_days=31)).recommended_tier,
            TIER_WARM)

    def test_warm_boundary_is_inclusive_at_365_and_flips_at_366(self):
        self.assertEqual(
            self.engine.evaluate_dataset_lifecycle(make_dataset(age_days=365)).recommended_tier,
            TIER_WARM)
        self.assertEqual(
            self.engine.evaluate_dataset_lifecycle(make_dataset(age_days=366)).recommended_tier,
            TIER_COLD)

    def test_cold_boundary_is_inclusive_at_2555_and_flips_at_2556(self):
        self.assertEqual(
            self.engine.evaluate_dataset_lifecycle(
                make_dataset(age_days=2555)).recommended_tier,
            TIER_COLD)
        self.assertEqual(
            self.engine.evaluate_dataset_lifecycle(
                make_dataset(age_days=2556)).recommended_tier,
            TIER_DEEP_ARCHIVE)

    def test_ladder_is_independent_of_retention_period(self):
        # Regression: the old engine used retention_days as the COLD upper bound,
        # so a dataset with no retention obligation fell straight through to the
        # purge branch at day 366. The ladder must not move with retention.
        for retention_years in (0.0, 1.0, 6.0, 30.0):
            rec = self.engine.evaluate_dataset_lifecycle(
                make_dataset(age_days=500, regulatory_retention_years=retention_years))
            self.assertEqual(
                rec.recommended_tier, TIER_COLD,
                f"retention_years={retention_years} moved the 500-day ladder rung")


class TestRetentionSafety(unittest.TestCase):
    """PURGE must be unreachable except for expired, unregulated, opted-in data."""

    def test_unregulated_data_is_never_purged_at_366_days(self):
        # Regression for the data-loss defect: retention_years=0.0 meant
        # retention_days=0, so any dataset older than 365 days was recommended
        # for deletion while the documentation promised a 7-year floor.
        engine = DataRetentionPolicyEngine(purge_expired_records=True)
        rec = engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=366, data_type="FEATURE_MATRIX",
                         regulatory_retention_years=0.0))
        self.assertEqual(rec.recommended_tier, TIER_COLD)
        self.assertNotEqual(rec.action_required, "PURGE")

    def test_purge_requires_explicit_opt_in(self):
        dataset = make_dataset(age_days=4_000, data_type="FEATURE_MATRIX",
                               regulatory_retention_years=1.0)
        default_engine = DataRetentionPolicyEngine()
        rec = default_engine.evaluate_dataset_lifecycle(dataset)
        self.assertEqual(rec.recommended_tier, TIER_DEEP_ARCHIVE)
        self.assertTrue(rec.retention_expired)
        self.assertTrue(any("purge-eligible" in n for n in rec.notes))

        opted_in = DataRetentionPolicyEngine(purge_expired_records=True)
        self.assertEqual(
            opted_in.evaluate_dataset_lifecycle(dataset).recommended_tier, TIER_PURGE)

    def test_unexpired_data_is_never_purged_even_with_opt_in(self):
        engine = DataRetentionPolicyEngine(purge_expired_records=True)
        rec = engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=4_000, data_type="FEATURE_MATRIX",
                         regulatory_retention_years=20.0))
        self.assertFalse(rec.retention_expired)
        self.assertEqual(rec.recommended_tier, TIER_DEEP_ARCHIVE)

    def test_regulated_record_types_are_never_purged(self):
        engine = DataRetentionPolicyEngine(purge_expired_records=True)
        rec = engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=10_000, data_type="TRADE_AUDIT_LOG",
                         regulatory_retention_years=6.0))
        self.assertTrue(rec.retention_expired)
        self.assertEqual(rec.recommended_tier, TIER_DEEP_ARCHIVE)
        self.assertTrue(any("compliance sign-off" in n for n in rec.notes))

    def test_retention_days_uses_365_25_and_rounds_up(self):
        # 6 years = 2191.5 days -> 2192. A record at day 2191 is NOT expired;
        # truncating to 6 * 365 = 2190 would have expired it ~2 days early.
        engine = DataRetentionPolicyEngine()
        self.assertEqual(engine.retention_days(make_dataset()), 2192)
        self.assertFalse(
            engine.evaluate_dataset_lifecycle(make_dataset(age_days=2192)).retention_expired)
        self.assertTrue(
            engine.evaluate_dataset_lifecycle(make_dataset(age_days=2193)).retention_expired)


class TestEasilyAccessibleFloor(unittest.TestCase):
    """SEC 17a-4(a)/(b)(1): first two years must stay in an easily accessible place."""

    def test_deep_archive_is_clamped_inside_the_two_year_window(self):
        # A short deep-archive threshold must not push a 400-day-old record into
        # a tier whose restore takes hours.
        engine = DataRetentionPolicyEngine(
            deep_archive_after_days=380, purge_expired_records=True)
        rec = engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=400, data_type="TRADE_AUDIT_LOG",
                         regulatory_retention_years=1.0))
        self.assertEqual(rec.recommended_tier, TIER_COLD)
        self.assertTrue(any("easily-accessible window" in n for n in rec.notes))

    def test_floor_releases_at_730_days(self):
        engine = DataRetentionPolicyEngine(deep_archive_after_days=380)
        rec = engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=730, data_type="FEATURE_MATRIX",
                         regulatory_retention_years=1.0))
        self.assertEqual(rec.recommended_tier, TIER_DEEP_ARCHIVE)


class TestCosting(unittest.TestCase):

    def setUp(self):
        self.engine = DataRetentionPolicyEngine()

    def test_hot_to_warm_savings_independently_derived(self):
        # 100,000 GB at $0.20 = $20,000/mo; at $0.023 = $2,300/mo.
        rec = self.engine.evaluate_dataset_lifecycle(
            make_dataset(size_gb=100_000.0, age_days=120))
        self.assertEqual(rec.recommended_tier, TIER_WARM)
        self.assertEqual(rec.current_monthly_cost_usd, 20_000.0)
        self.assertEqual(rec.recommended_monthly_cost_usd, 2_300.0)
        self.assertEqual(rec.monthly_cost_savings_usd, 17_700.0)

    def test_warm_to_cold_savings_independently_derived(self):
        # 50,000 GB at $0.023 = $1,150/mo; at $0.004 = $200/mo.
        rec = self.engine.evaluate_dataset_lifecycle(
            make_dataset(size_gb=50_000.0, age_days=500, current_tier=TIER_WARM))
        self.assertEqual(rec.recommended_tier, TIER_COLD)
        self.assertEqual(rec.current_monthly_cost_usd, 1_150.0)
        self.assertEqual(rec.recommended_monthly_cost_usd, 200.0)
        self.assertEqual(rec.monthly_cost_savings_usd, 950.0)

    def test_deep_archive_cost_includes_per_object_metadata(self):
        # 10,000,000 objects x 32 KB at $0.00099 + x 8 KB at $0.023.
        object_count = 10_000_000
        dataset = make_dataset(size_gb=1_000.0, age_days=3_000,
                               current_tier=TIER_COLD, object_count=object_count,
                               regulatory_retention_years=1.0)
        rec = self.engine.evaluate_dataset_lifecycle(dataset)
        self.assertEqual(rec.recommended_tier, TIER_DEEP_ARCHIVE)

        kb_per_gb = 1024.0 * 1024.0
        archive_gb = object_count * GLACIER_METADATA_ARCHIVE_KB / kb_per_gb
        standard_gb = object_count * GLACIER_METADATA_STANDARD_KB / kb_per_gb
        expected = 1_000.0 * 0.00099 + archive_gb * 0.00099 + standard_gb * 0.023
        self.assertAlmostEqual(rec.recommended_monthly_cost_usd, round(expected, 2), places=2)
        # The 8 KB Standard-rate slice alone (~$1.75/mo) exceeds the $0.99 of
        # actual archive storage, which is the whole point of the warning.
        self.assertGreater(rec.recommended_monthly_cost_usd, 1_000.0 * 0.00099 * 2)

    def test_small_objects_raise_a_compaction_warning(self):
        # 1,000 GB across 20,000,000 objects -> ~52 KB average.
        rec = self.engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=500, object_count=20_000_000))
        self.assertTrue(any("Compact before transitioning" in n for n in rec.notes))

    def test_large_objects_raise_no_compaction_warning(self):
        # 1,000 GB across 1,000 objects -> ~1 GB average.
        rec = self.engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=500, object_count=1_000))
        self.assertFalse(any("Compact before transitioning" in n for n in rec.notes))

    def test_early_deletion_exposure_is_flagged(self):
        rec = self.engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=3_000, current_tier=TIER_COLD,
                         days_in_current_tier=40, regulatory_retention_years=1.0))
        self.assertTrue(any("remaining 50 days" in n for n in rec.notes))

    def test_no_early_deletion_note_once_minimum_duration_elapsed(self):
        rec = self.engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=3_000, current_tier=TIER_COLD,
                         days_in_current_tier=120, regulatory_retention_years=1.0))
        self.assertFalse(any("Early-deletion exposure" in n for n in rec.notes))

    def test_transition_request_cost_and_payback(self):
        engine = DataRetentionPolicyEngine(transition_price_per_1000_requests_usd=0.05)
        rec = engine.evaluate_dataset_lifecycle(
            make_dataset(size_gb=1_000.0, age_days=500, current_tier=TIER_WARM,
                         object_count=1_000_000))
        # 1,000,000 / 1,000 * $0.05 = $50.00 one-off.
        self.assertEqual(rec.one_off_transition_cost_usd, 50.0)
        # Monthly saving: 1,000 * (0.023 - 0.004) = $19.00 -> ~2.63 months.
        self.assertEqual(rec.monthly_cost_savings_usd, 19.0)
        self.assertAlmostEqual(rec.transition_payback_months, 2.63, places=2)

    def test_no_change_incurs_no_transition_cost(self):
        engine = DataRetentionPolicyEngine(transition_price_per_1000_requests_usd=0.05)
        rec = engine.evaluate_dataset_lifecycle(
            make_dataset(age_days=500, current_tier=TIER_COLD, object_count=1_000_000))
        self.assertEqual(rec.action_required, "NO_CHANGE")
        self.assertIsNone(rec.one_off_transition_cost_usd)

    def test_zero_size_dataset_costs_nothing(self):
        rec = self.engine.evaluate_dataset_lifecycle(make_dataset(size_gb=0.0))
        self.assertEqual(rec.current_monthly_cost_usd, 0.0)
        self.assertEqual(rec.monthly_cost_savings_usd, 0.0)


class TestValidation(unittest.TestCase):

    def setUp(self):
        self.engine = DataRetentionPolicyEngine()

    def test_unknown_current_tier_raises_instead_of_defaulting_to_hot_price(self):
        with self.assertRaises(DataRetentionPolicyError):
            self.engine.evaluate_dataset_lifecycle(make_dataset(current_tier="S3_GLACIER"))

    def test_negative_and_nonfinite_inputs_raise(self):
        for bad in (
            {"size_gb": -1.0},
            {"size_gb": float("nan")},
            {"size_gb": float("inf")},
            {"age_days": -1},
            {"age_days": float("nan")},
            {"regulatory_retention_years": -1.0},
            {"object_count": -5},
            {"days_in_current_tier": -5},
            {"dataset_id": ""},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(DataRetentionPolicyError):
                    self.engine.evaluate_dataset_lifecycle(make_dataset(**bad))

    def test_incomplete_pricing_map_raises(self):
        with self.assertRaises(DataRetentionPolicyError):
            DataRetentionPolicyEngine(pricing_map={TIER_HOT: 0.20})

    def test_negative_price_raises(self):
        prices = {TIER_HOT: 0.20, TIER_WARM: 0.023, TIER_COLD: 0.004,
                  TIER_DEEP_ARCHIVE: 0.00099, TIER_PURGE: -1.0}
        with self.assertRaises(DataRetentionPolicyError):
            DataRetentionPolicyEngine(pricing_map=prices)

    def test_out_of_order_thresholds_raise(self):
        with self.assertRaises(DataRetentionPolicyError):
            DataRetentionPolicyEngine(hot_max_days=400, warm_max_days=365)

    def test_custom_pricing_map_is_copied_not_aliased(self):
        prices = dict(
            {TIER_HOT: 1.0, TIER_WARM: 0.5, TIER_COLD: 0.1,
             TIER_DEEP_ARCHIVE: 0.01, TIER_PURGE: 0.0})
        engine = DataRetentionPolicyEngine(pricing_map=prices)
        prices[TIER_HOT] = 999.0
        rec = engine.evaluate_dataset_lifecycle(make_dataset(size_gb=10.0))
        self.assertEqual(rec.current_monthly_cost_usd, 10.0)


class TestFleetAudit(unittest.TestCase):

    def test_totals_aggregate_unrounded_costs(self):
        engine = DataRetentionPolicyEngine()
        # 0.001 GB at $0.20 = $0.0002/mo: rounds to $0.00 per row, but 10,000
        # rows are $2.00 in aggregate. The old per-row rounding lost all of it.
        datasets = [
            make_dataset(dataset_id=f"DS{i}", size_gb=0.001, age_days=10)
            for i in range(10_000)
        ]
        report = engine.audit_storage_fleet(datasets)
        self.assertIsInstance(report, DataRetentionAuditReport)
        self.assertEqual(report.total_datasets_audited, 10_000)
        self.assertEqual(report.total_current_monthly_cost_usd, 2.0)

    def test_total_size_tb_uses_decimal_terabytes(self):
        engine = DataRetentionPolicyEngine()
        report = engine.audit_storage_fleet([make_dataset(size_gb=2_500.0)])
        self.assertEqual(report.total_size_tb, 2.5)

    def test_empty_fleet_is_a_valid_zero_report(self):
        report = DataRetentionPolicyEngine().audit_storage_fleet([])
        self.assertEqual(report.total_datasets_audited, 0)
        self.assertEqual(report.total_monthly_savings_usd, 0.0)
        self.assertEqual(report.recommendations, [])

    def test_fleet_savings_equals_current_minus_recommended(self):
        engine = DataRetentionPolicyEngine()
        report = engine.audit_storage_fleet([
            make_dataset(dataset_id="A", size_gb=100_000.0, age_days=120),
            make_dataset(dataset_id="B", size_gb=50_000.0, age_days=500,
                         current_tier=TIER_WARM),
        ])
        self.assertEqual(
            report.total_monthly_savings_usd,
            round(report.total_current_monthly_cost_usd
                  - report.total_recommended_monthly_cost_usd, 2))


if __name__ == "__main__":
    unittest.main()
