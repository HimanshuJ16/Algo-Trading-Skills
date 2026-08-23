import math
import unittest

from data_quality_monitoring_dashboard import (
    DataQualityMonitoringEngine, FeedTelemetryBatch
)


def make_batch(**overrides) -> FeedTelemetryBatch:
    """Clean 1000-record batch; override only the fields under test."""
    fields = dict(
        vendor_id="BLOOMBERG", symbol="AAPL", total_records=1000,
        null_records_count=0, duplicate_records_count=0, outlier_records_count=0,
        avg_latency_ms=0.0, ticks_per_second=50.0,
    )
    fields.update(overrides)
    return FeedTelemetryBatch(**fields)


class TestCompositeScoring(unittest.TestCase):
    """Expected composites are derived by hand from the documented pillar formulas."""

    def setUp(self):
        self.engine = DataQualityMonitoringEngine(min_healthy_score=85.0, critical_failover_score=70.0)

    def test_perfect_feed_scores_exactly_100(self):
        report = self.engine.audit_feed_quality(make_batch())
        # All five pillars = 100; weights sum to 1.0 => 100.0
        self.assertEqual(report.composite_dq_score, 100.0)
        self.assertEqual(report.status, "HEALTHY")
        self.assertFalse(report.is_failover_recommended)

    def test_healthy_feed_with_2ms_latency_scores_99_9(self):
        # timeliness = 100 * (1 - 2/500) = 99.6; others 100.
        # 0.25*100 + 0.25*99.6 + 0.25*100 + 0.15*100 + 0.10*100 = 99.9
        report = self.engine.audit_feed_quality(make_batch(avg_latency_ms=2.0))
        self.assertEqual(report.composite_dq_score, 99.9)
        self.assertEqual(report.status, "HEALTHY")

    def test_pillar_scores_and_weights_are_reported(self):
        # 10% nulls -> 100 - 10*2 = 80; 5% outliers -> 100 - 5*5 = 75; 4% dups -> 100 - 4*2 = 92
        report = self.engine.audit_feed_quality(
            make_batch(null_records_count=100, outlier_records_count=50, duplicate_records_count=40)
        )
        by_name = {d.dimension_name: d for d in report.dimensions}
        self.assertEqual(by_name["COMPLETENESS"].score, 80.0)
        self.assertEqual(by_name["ACCURACY"].score, 75.0)
        self.assertEqual(by_name["UNIQUENESS"].score, 92.0)
        self.assertEqual(by_name["TIMELINESS"].score, 100.0)
        self.assertEqual(by_name["LIVENESS"].score, 100.0)
        self.assertAlmostEqual(math.fsum(d.weight for d in report.dimensions), 1.0)


class TestStatusBands(unittest.TestCase):

    def setUp(self):
        self.engine = DataQualityMonitoringEngine(min_healthy_score=85.0, critical_failover_score=70.0)

    def test_score_based_failover_on_live_feed(self):
        # Live feed (TPS > 0) whose composite alone must trigger failover, exercising the
        # branch a dead-feed fixture short-circuits past.
        # comp 100-20=80, time 100*(1-400/500)=20, acc 100-25=75, uniq 100-20=80, live 100
        # 0.25*80 + 0.25*20 + 0.25*75 + 0.15*80 + 0.10*100 = 65.75
        report = self.engine.audit_feed_quality(make_batch(
            vendor_id="REFINITIV", null_records_count=100, outlier_records_count=50,
            duplicate_records_count=100, avg_latency_ms=400.0, ticks_per_second=25.0,
        ))
        self.assertEqual(report.composite_dq_score, 65.75)
        self.assertEqual(report.status, "CRITICAL")
        self.assertTrue(report.is_failover_recommended)
        self.assertNotIn("DEAD FEED", report.alerts[0])
        self.assertIn("CRITICAL DATA QUALITY BREACH", report.alerts[0])

    def test_warning_band_does_not_recommend_failover(self):
        # comp 100-4=96, time 100*(1-300/500)=40, acc 100-5=95, uniq 100, live 100
        # 0.25*96 + 0.25*40 + 0.25*95 + 0.15*100 + 0.10*100 = 82.75
        report = self.engine.audit_feed_quality(make_batch(
            null_records_count=20, outlier_records_count=10, avg_latency_ms=300.0,
        ))
        self.assertEqual(report.composite_dq_score, 82.75)
        self.assertEqual(report.status, "WARNING")
        self.assertFalse(report.is_failover_recommended)

    def test_score_exactly_at_healthy_threshold_is_healthy(self):
        # Thresholds are strict '<': 85.0 must NOT be degraded.
        # time = 100*(1-300/500) = 40 -> 0.25*40 + 25 + 25 + 15 + 10 ... recompute:
        # comp 100, time 40, acc 100, uniq 100, live 100
        # 0.25*100 + 0.25*40 + 0.25*100 + 0.15*100 + 0.10*100 = 85.0
        report = self.engine.audit_feed_quality(make_batch(avg_latency_ms=300.0))
        self.assertEqual(report.composite_dq_score, 85.0)
        self.assertEqual(report.status, "HEALTHY")
        self.assertFalse(report.is_failover_recommended)

    def test_score_exactly_at_critical_threshold_is_warning_not_failover(self):
        # comp 100-40=60, time 100*(1-150/500)=70, acc 100-50=50, uniq 100, live 100
        # 0.25*60 + 0.25*70 + 0.25*50 + 0.15*100 + 0.10*100 = 70.0 exactly
        report = self.engine.audit_feed_quality(make_batch(
            null_records_count=200, outlier_records_count=100, avg_latency_ms=150.0,
        ))
        self.assertEqual(report.composite_dq_score, 70.0)
        self.assertEqual(report.status, "WARNING")
        self.assertFalse(report.is_failover_recommended)


class TestLiveness(unittest.TestCase):

    def setUp(self):
        self.engine = DataQualityMonitoringEngine()

    def test_stalled_feed_with_clean_records_still_fails_over(self):
        # A stalled feed's last records are clean, so the count pillars stay near-perfect:
        # comp 100, time 100*(1-1/500)=99.8, acc 100, uniq 100, live 0 => 89.95, above
        # min_healthy_score. Liveness must still force CRITICAL + failover.
        report = self.engine.audit_feed_quality(make_batch(avg_latency_ms=1.0, ticks_per_second=0.0))
        self.assertEqual(report.composite_dq_score, 89.95)
        self.assertGreater(report.composite_dq_score, self.engine.min_healthy_score)
        self.assertEqual(report.status, "CRITICAL")
        self.assertTrue(report.is_failover_recommended)
        self.assertIn("DEAD FEED", report.alerts[0])

    def test_zero_records_returns_dead_feed_report_without_dimensions(self):
        report = self.engine.audit_feed_quality(make_batch(total_records=0, ticks_per_second=0.0))
        self.assertEqual(report.composite_dq_score, 0.0)
        self.assertEqual(report.status, "CRITICAL")
        self.assertTrue(report.is_failover_recommended)
        self.assertEqual(report.dimensions, [])
        self.assertIn("Zero records received", report.alerts[0])


class TestBatchValidation(unittest.TestCase):

    def setUp(self):
        self.engine = DataQualityMonitoringEngine()

    def test_nan_latency_is_rejected(self):
        # Regression: an unguarded NaN latency was floored to a TIMELINESS of 0.0
        # (composite 75.0, WARNING), making corrupt telemetry indistinguishable from a
        # feed that is merely late. Corrupt input must surface as an error, not a score.
        with self.assertRaises(ValueError):
            self.engine.audit_feed_quality(make_batch(avg_latency_ms=float("nan")))

    def test_infinite_latency_is_rejected(self):
        # Same failure mode as NaN: previously floored to TIMELINESS 0.0 / composite 75.0.
        with self.assertRaises(ValueError):
            self.engine.audit_feed_quality(make_batch(avg_latency_ms=float("inf")))

    def test_negative_latency_is_rejected(self):
        # Regression: -50 ms previously scored TIMELINESS 110.0 for a composite of 102.5,
        # above the documented 0-100 range, and reported the corrupt batch as HEALTHY.
        with self.assertRaises(ValueError):
            self.engine.audit_feed_quality(make_batch(avg_latency_ms=-50.0))

    def test_defect_count_exceeding_total_records_is_rejected(self):
        for field in ("null_records_count", "duplicate_records_count", "outlier_records_count"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.engine.audit_feed_quality(make_batch(total_records=10, **{field: 11}))

    def test_negative_counts_are_rejected(self):
        # Regression: null_records_count=-1000 on a 1000-record batch previously scored
        # COMPLETENESS 300.0 for a composite of 150.0, reported as HEALTHY.
        for field in ("total_records", "null_records_count", "duplicate_records_count",
                      "outlier_records_count"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.engine.audit_feed_quality(make_batch(**{field: -1}))

    def test_negative_tick_rate_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_feed_quality(make_batch(ticks_per_second=-1.0))

    def test_blank_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_feed_quality(make_batch(vendor_id="   "))
        with self.assertRaises(ValueError):
            self.engine.audit_feed_quality(make_batch(symbol=""))

    def test_all_records_defective_floors_pillars_at_zero(self):
        report = self.engine.audit_feed_quality(make_batch(
            total_records=100, null_records_count=100, outlier_records_count=100,
            duplicate_records_count=100, avg_latency_ms=1000.0,
        ))
        by_name = {d.dimension_name: d.score for d in report.dimensions}
        self.assertEqual(by_name["COMPLETENESS"], 0.0)
        self.assertEqual(by_name["ACCURACY"], 0.0)
        self.assertEqual(by_name["UNIQUENESS"], 0.0)
        self.assertEqual(by_name["TIMELINESS"], 0.0)
        # Only liveness contributes: 0.10 * 100 = 10.0
        self.assertEqual(report.composite_dq_score, 10.0)
        self.assertEqual(report.status, "CRITICAL")


class TestEngineConfiguration(unittest.TestCase):

    def test_critical_threshold_at_or_above_healthy_is_rejected(self):
        # Would make the WARNING band unreachable, escalating every degraded feed to failover.
        with self.assertRaises(ValueError):
            DataQualityMonitoringEngine(min_healthy_score=85.0, critical_failover_score=85.0)
        with self.assertRaises(ValueError):
            DataQualityMonitoringEngine(min_healthy_score=70.0, critical_failover_score=85.0)

    def test_out_of_range_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            DataQualityMonitoringEngine(min_healthy_score=150.0)
        with self.assertRaises(ValueError):
            DataQualityMonitoringEngine(critical_failover_score=-1.0)

    def test_weights_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            DataQualityMonitoringEngine(dimension_weights=(0.5, 0.5, 0.5, 0.5, 0.5))
        with self.assertRaises(ValueError):
            DataQualityMonitoringEngine(dimension_weights=(0.25, 0.25, 0.25, 0.15))

    def test_negative_penalty_factor_is_rejected(self):
        with self.assertRaises(ValueError):
            DataQualityMonitoringEngine(outlier_penalty_factor=-1.0)

    def test_zero_latency_scale_is_rejected(self):
        with self.assertRaises(ValueError):
            DataQualityMonitoringEngine(latency_zero_score_ms=0.0)

    def test_custom_penalty_factor_changes_pillar_score(self):
        # With outlier_penalty_factor=1.0, 5% outliers -> 95.0 instead of the default 75.0.
        engine = DataQualityMonitoringEngine(outlier_penalty_factor=1.0)
        report = engine.audit_feed_quality(make_batch(outlier_records_count=50))
        by_name = {d.dimension_name: d.score for d in report.dimensions}
        self.assertEqual(by_name["ACCURACY"], 95.0)
        # 0.25*100 + 0.25*100 + 0.25*95 + 0.15*100 + 0.10*100 = 98.75
        self.assertEqual(report.composite_dq_score, 98.75)

    def test_custom_latency_scale_changes_timeliness(self):
        # 100 ms budget: a 50 ms batch scores 100*(1-50/100) = 50.0.
        engine = DataQualityMonitoringEngine(latency_zero_score_ms=100.0)
        report = engine.audit_feed_quality(make_batch(avg_latency_ms=50.0))
        by_name = {d.dimension_name: d.score for d in report.dimensions}
        self.assertEqual(by_name["TIMELINESS"], 50.0)


if __name__ == '__main__':
    unittest.main()
