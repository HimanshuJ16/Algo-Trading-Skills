"""Tests for per-vendor market data latency decomposition and SLA auditing.

Expected percentile, jitter and attribution values are derived independently of the
implementation -- from the closed form for the variance of a uniform integer series,
from HdrHistogram's documented nearest-rank definition, and by hand-counting ranks --
rather than by re-running the module's own arithmetic.
"""

import math
import unittest

from market_data_latency_monitoring_per_vendor import (
    LatencySample,
    LatencySampleError,
    MarketDataLatencyMonitorEngine,
    PERCENTILE_LINEAR,
    PERCENTILE_NEAREST_RANK,
    REPORT_ALL_HEALTHY,
    REPORT_SLA_BREACH,
    REPORT_UNMEASURABLE,
    SEGMENT_APP_QUEUE,
    SEGMENT_NETWORK_WIRE,
    SEGMENT_VENDOR_TRANSPORT,
    STATUS_CLOCK_DOMAIN_ERROR,
    STATUS_HEALTHY,
    STATUS_INSUFFICIENT_SAMPLES,
    STATUS_SLA_BREACH,
    is_percentile_resolvable,
    min_samples_for_percentile,
    rank_for_percentile,
)


def make_sample(vendor, transport, wire, app_queue, t_exchange=1_000_000.0, symbol="AAPL"):
    """Build a sample from segment latencies, so tests state intent rather than stamps."""
    t_vendor = t_exchange + transport
    t_nic = t_vendor + wire
    t_app = t_nic + app_queue
    return LatencySample(vendor, symbol, t_exchange, t_vendor, t_nic, t_app)


class TestPercentileArithmetic(unittest.TestCase):
    """Nearest rank, the ULP guard, and the resolution predicate."""

    def setUp(self):
        self.engine = MarketDataLatencyMonitorEngine()

    def test_nearest_rank_returns_observed_values(self):
        # Over 1..100 the nearest rank ceil(p/100 * 100) is the value itself.
        series = [float(i) for i in range(1, 101)]
        self.assertEqual(self.engine.compute_percentile(series, 25.0), 25.0)
        self.assertEqual(self.engine.compute_percentile(series, 50.0), 50.0)
        self.assertEqual(self.engine.compute_percentile(series, 90.0), 90.0)
        self.assertEqual(self.engine.compute_percentile(series, 99.0), 99.0)
        self.assertEqual(self.engine.compute_percentile(series, 100.0), 100.0)

    def test_nearest_rank_never_reports_an_unobserved_value(self):
        # A feed that is either 10 us or 900 us and nothing between. Nearest rank must
        # report a latency the system actually produced; interpolation reports the
        # midpoint 455 us, which it never did.
        series = sorted([10.0] * 500 + [900.0] * 500)
        self.assertEqual(
            self.engine.compute_percentile(series, 50.0, PERCENTILE_NEAREST_RANK), 10.0)
        self.assertEqual(
            self.engine.compute_percentile(series, 50.0, PERCENTILE_LINEAR), 455.0)

    def test_ulp_guard_lets_p999_resolve_at_exactly_1000_samples(self):
        # 99.9/100 is 0.9990000000000001 in IEEE-754, so an unguarded
        # ceil(0.999... * 1000) is 1000 and pins P99.9 to the maximum at the very
        # sample count that should first resolve it.
        self.assertEqual(rank_for_percentile(1000, 99.9), 999)
        self.assertTrue(is_percentile_resolvable(1000, 99.9))
        self.assertFalse(is_percentile_resolvable(999, 99.9))
        self.assertEqual(min_samples_for_percentile(99.9), 1000)
        self.assertEqual(min_samples_for_percentile(99.0), 100)

    def test_empty_series_raises_rather_than_reporting_zero(self):
        # v1 returned 0.0 here, which reads as a perfect latency for a feed that
        # delivered nothing at all.
        with self.assertRaises(LatencySampleError):
            self.engine.compute_percentile([], 99.0)

    def test_percentile_bounds_are_enforced(self):
        for bad in (-0.1, 100.1):
            with self.assertRaises(LatencySampleError):
                self.engine.compute_percentile([1.0, 2.0], bad)


class TestClockDomainIntegrity(unittest.TestCase):
    """Regression tests for the v1 negative-delta clamp."""

    def setUp(self):
        self.engine = MarketDataLatencyMonitorEngine(max_allowed_p99_latency_us=500.0)

    def _skewed_samples(self, count=200):
        # The vendor gateway clock runs 2,000 us ahead of the exchange clock. The tick
        # therefore appears to reach the local NIC 1,940 us *before* the vendor stamped
        # it. v1 clamped that to 0.0 and returned VENDOR_LATENCY_HEALTHY.
        samples = []
        for i in range(count):
            t_ex = 1_000_000.0 + i * 100.0
            samples.append(
                LatencySample("SKEWED", "AAPL", t_ex, t_ex + 2000.0, t_ex + 60.0, t_ex + 80.0))
        return samples

    def test_negative_segment_rejects_the_window(self):
        report = self.engine.audit_vendor_latencies(self._skewed_samples())
        metrics = report.vendor_metrics["SKEWED"]

        self.assertEqual(metrics.status, STATUS_CLOCK_DOMAIN_ERROR)
        self.assertNotEqual(metrics.status, STATUS_HEALTHY)
        self.assertFalse(metrics.is_sla_compliant)
        self.assertEqual(metrics.clock_inconsistent_sample_count, 200)
        self.assertIn("SKEWED", report.unmeasurable_vendors)
        self.assertEqual(report.status, REPORT_UNMEASURABLE)

    def test_no_percentiles_are_published_for_a_broken_clock_window(self):
        metrics = self.engine.audit_vendor_latencies(
            self._skewed_samples()).vendor_metrics["SKEWED"]
        for value in (metrics.p50_us, metrics.p99_us, metrics.p99_9_us,
                      metrics.audited_percentile_us, metrics.mean_latency_us):
            self.assertEqual(value, 0.0)
        self.assertIsNone(metrics.dominant_tail_segment)
        self.assertTrue(metrics.warnings)

    def test_clamping_would_have_desynchronised_the_decomposition(self):
        # The arithmetic identity the v1 clamp broke: the three segments must sum to
        # the end-to-end total, sample by sample. Under the clamp this window reported
        # a total of 80 us against segments summing to 2,020 us.
        sample = self._skewed_samples(1)[0]
        segments = MarketDataLatencyMonitorEngine._segment_deltas(sample)
        self.assertAlmostEqual(sum(segments.values()),
                               sample.t_app_us - sample.t_exchange_us, places=9)
        self.assertLess(segments[SEGMENT_NETWORK_WIRE], 0.0)

    def test_override_reports_but_warns_loudly(self):
        engine = MarketDataLatencyMonitorEngine(reject_clock_inconsistent_windows=False)
        metrics = engine.audit_vendor_latencies(
            self._skewed_samples()).vendor_metrics["SKEWED"]
        self.assertNotEqual(metrics.status, STATUS_CLOCK_DOMAIN_ERROR)
        self.assertEqual(metrics.clock_inconsistent_sample_count, 200)
        self.assertTrue(any("negative segment latency" in w for w in metrics.warnings))


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = MarketDataLatencyMonitorEngine()

    def test_empty_sample_list_raises_valueerror_subclass(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_vendor_latencies([])
        # v1 raised a bare ValueError; callers catching that must keep working.
        with self.assertRaises(ValueError):
            self.engine.audit_vendor_latencies([])

    def test_nan_is_rejected_not_propagated(self):
        # A NaN does not raise, does not sort and does not compare: left in, it yields
        # an unordered series, an arbitrary median and a mean of nan.
        samples = [make_sample("V", 10.0, 10.0, 10.0) for _ in range(99)]
        samples.append(LatencySample("V", "AAPL", 0.0, 10.0, 20.0, float("nan")))
        with self.assertRaises(LatencySampleError):
            self.engine.audit_vendor_latencies(samples)

    def test_infinity_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_vendor_latencies(
                [LatencySample("V", "AAPL", 0.0, 10.0, 20.0, float("inf"))])

    def test_bool_is_rejected_rather_than_read_as_one_microsecond(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_vendor_latencies(
                [LatencySample("V", "AAPL", 0.0, 10.0, 20.0, True)])

    def test_non_numeric_timestamp_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_vendor_latencies(
                [LatencySample("V", "AAPL", 0.0, 10.0, 20.0, "30")])

    def test_blank_vendor_id_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_vendor_latencies([make_sample("   ", 10.0, 10.0, 10.0)])

    def test_nanosecond_timestamp_in_a_microsecond_field_is_rejected(self):
        # ~1.8e18 is "now" in nanoseconds since the Unix epoch. In a microsecond field
        # it is a date around the year 58,000.
        with self.assertRaises(LatencySampleError):
            self.engine.audit_vendor_latencies(
                [LatencySample("V", "AAPL", 0.0, 10.0, 20.0, 1.787e18)])

    def test_genuine_unix_epoch_microsecond_timestamps_are_accepted(self):
        # Regression: an earlier magnitude bound of 1e15 us rejected real
        # microseconds-since-Unix-epoch stamps, which sit near 1.8e15.
        base = 1_787_000_000_000_000.0
        samples = [make_sample("V", 30.0, 20.0, 50.0, t_exchange=base + i * 100.0)
                   for i in range(100)]
        metrics = self.engine.audit_vendor_latencies(samples).vendor_metrics["V"]
        self.assertEqual(metrics.status, STATUS_HEALTHY)
        self.assertEqual(metrics.p99_us, 100.0)


class TestTimestampResolution(unittest.TestCase):
    """A latency cannot be finer than the float64 spacing of its two operands."""

    def test_unix_epoch_microseconds_quantise_at_a_quarter_microsecond(self):
        # 1.787e15 lies in the binade [2^50, 2^51), where the float64 spacing is
        # 2^50 * 2^-52 = 0.25. Derived from the exponent, not from math.ulp.
        base = 1_787_000_000_000_000.0
        self.assertTrue(2 ** 50 <= base < 2 ** 51)
        samples = [make_sample("V", 30.0, 20.0, 50.0, t_exchange=base + i * 100.0)
                   for i in range(200)]
        metrics = MarketDataLatencyMonitorEngine(
            max_allowed_p99_latency_us=500.0
        ).audit_vendor_latencies(samples).vendor_metrics["V"]
        self.assertEqual(metrics.timestamp_quantum_us, 0.25)
        # 0.25 us still separates adjacent microseconds, so no warning is raised.
        self.assertFalse(any("quantisation noise" in w for w in metrics.warnings))

    def test_quantum_above_one_microsecond_is_flagged(self):
        # 1e16 lies in [2^53, 2^54), where the spacing is 2^53 * 2^-52 = 2.0 us --
        # coarser than the unit this module reports in.
        base = 1e16
        self.assertTrue(2 ** 53 <= base < 2 ** 54)
        samples = [make_sample("V", 30.0, 20.0, 50.0, t_exchange=base + i * 100.0)
                   for i in range(200)]
        metrics = MarketDataLatencyMonitorEngine(
            max_allowed_p99_latency_us=500.0
        ).audit_vendor_latencies(samples).vendor_metrics["V"]
        self.assertEqual(metrics.timestamp_quantum_us, 2.0)
        self.assertTrue(any("quantisation noise" in w for w in metrics.warnings))

    def test_engine_rejects_invalid_configuration(self):
        for kwargs in (
            {"max_allowed_p99_latency_us": 0.0},
            {"max_allowed_p99_latency_us": float("nan")},
            {"audited_percentile": 100.0},
            {"audited_percentile": 0.0},
            {"percentile_method": "MEDIAN_OF_MEANS"},
            {"clock_uncertainty_us": -1.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(LatencySampleError):
                    MarketDataLatencyMonitorEngine(**kwargs)


class TestSlaVerdicts(unittest.TestCase):
    def setUp(self):
        self.engine = MarketDataLatencyMonitorEngine(max_allowed_p99_latency_us=500.0)

    def test_healthy_vendor_at_sufficient_sample_count(self):
        samples = [make_sample("BLOOMBERG", 30.0, 20.0, 50.0) for _ in range(100)]
        report = self.engine.audit_vendor_latencies(samples)
        metrics = report.vendor_metrics["BLOOMBERG"]

        self.assertEqual(report.status, REPORT_ALL_HEALTHY)
        self.assertEqual(metrics.status, STATUS_HEALTHY)
        self.assertTrue(metrics.is_sla_compliant)
        self.assertEqual(metrics.p50_us, 100.0)
        self.assertEqual(metrics.p99_us, 100.0)
        self.assertEqual(metrics.avg_vendor_transport_us, 30.0)
        self.assertEqual(metrics.avg_network_wire_us, 20.0)
        self.assertEqual(metrics.avg_app_processing_us, 50.0)

    def test_no_breach_on_too_few_samples_is_not_compliance(self):
        # P99 needs 100 samples; at 99 its nearest rank is the observed maximum.
        ninety_nine = [make_sample("V", 30.0, 20.0, 50.0) for _ in range(99)]
        self.assertEqual(
            self.engine.audit_vendor_latencies(ninety_nine).vendor_metrics["V"].status,
            STATUS_INSUFFICIENT_SAMPLES)

        one_hundred = [make_sample("V", 30.0, 20.0, 50.0) for _ in range(100)]
        self.assertEqual(
            self.engine.audit_vendor_latencies(one_hundred).vendor_metrics["V"].status,
            STATUS_HEALTHY)

    def test_breach_is_reported_at_any_sample_count(self):
        # Ten samples cannot resolve P99, but one over-budget latency was genuinely
        # observed, and observing one is enough to prove it happened.
        samples = [make_sample("V", 30.0, 20.0, 50.0) for _ in range(9)]
        samples.append(make_sample("V", 30.0, 4900.0, 50.0))
        metrics = self.engine.audit_vendor_latencies(samples).vendor_metrics["V"]
        self.assertEqual(metrics.status, STATUS_SLA_BREACH)
        self.assertFalse(metrics.is_audited_percentile_resolvable)

    def test_sla_comparison_uses_unrounded_values(self):
        # 500.004 us rounds to 500.00 for display; rounding before the comparison
        # would turn the breach into a pass.
        samples = [make_sample("V", 0.0, 0.0, 500.004) for _ in range(1000)]
        metrics = self.engine.audit_vendor_latencies(samples).vendor_metrics["V"]
        self.assertEqual(metrics.status, STATUS_SLA_BREACH)
        self.assertEqual(metrics.audited_percentile_us, 500.0)

    def test_audited_percentile_is_configurable(self):
        # The EU consolidated tape timeliness rule is expressed as a 95% confidence
        # interval, not a hard maximum, so the audited percentile cannot be hard-coded.
        samples = [make_sample("V", 0.0, 0.0, 100.0) for _ in range(960)]
        samples += [make_sample("V", 0.0, 0.0, 900.0) for _ in range(40)]

        p95_engine = MarketDataLatencyMonitorEngine(
            max_allowed_p99_latency_us=500.0, audited_percentile=95.0)
        p99_engine = MarketDataLatencyMonitorEngine(
            max_allowed_p99_latency_us=500.0, audited_percentile=99.0)

        # Rank 950 of 1000 falls in the 960 fast samples; rank 990 falls in the slow tail.
        self.assertEqual(
            p95_engine.audit_vendor_latencies(samples).vendor_metrics["V"].status,
            STATUS_HEALTHY)
        self.assertEqual(
            p99_engine.audit_vendor_latencies(samples).vendor_metrics["V"].status,
            STATUS_SLA_BREACH)

    def test_clock_uncertainty_annotates_without_changing_the_verdict(self):
        samples = [make_sample("V", 0.0, 0.0, 480.0) for _ in range(200)]
        engine = MarketDataLatencyMonitorEngine(
            max_allowed_p99_latency_us=500.0, clock_uncertainty_us=100.0)
        metrics = engine.audit_vendor_latencies(samples).vendor_metrics["V"]
        self.assertEqual(metrics.status, STATUS_HEALTHY)
        self.assertTrue(any("noise floor" in w for w in metrics.warnings))


class TestTailAttribution(unittest.TestCase):
    """The segment that owns the tail is not the segment with the highest mean."""

    def setUp(self):
        self.engine = MarketDataLatencyMonitorEngine(max_allowed_p99_latency_us=500.0)
        # 985 fast ticks and 15 slow ones. The vendor transport hop is steadily the
        # largest *average* contributor (200 us against a wire average of 84.85 us),
        # but every slow tick is slow because of the wire.
        self.samples = [make_sample("V", 200.0, 10.0, 10.0) for _ in range(985)]
        self.samples += [make_sample("V", 200.0, 5000.0, 10.0) for _ in range(15)]

    def test_means_point_at_the_wrong_segment(self):
        metrics = self.engine.audit_vendor_latencies(self.samples).vendor_metrics["V"]
        # (985*10 + 15*5000) / 1000 = 84.85
        self.assertEqual(metrics.avg_network_wire_us, 84.85)
        self.assertEqual(metrics.avg_vendor_transport_us, 200.0)
        self.assertGreater(metrics.avg_vendor_transport_us, metrics.avg_network_wire_us)

    def test_tail_attribution_names_the_segment_that_was_slow(self):
        metrics = self.engine.audit_vendor_latencies(self.samples).vendor_metrics["V"]
        self.assertEqual(metrics.status, STATUS_SLA_BREACH)
        self.assertEqual(metrics.dominant_tail_segment, SEGMENT_NETWORK_WIRE)

        stats = metrics.segment_stats
        # Rank 990 of 1000 lands in the 15 slow samples, so the tail subset is exactly
        # those 15: wire 5000 us, transport 200 us, app queue 10 us.
        self.assertEqual(stats[SEGMENT_NETWORK_WIRE].tail_mean_us, 5000.0)
        self.assertEqual(stats[SEGMENT_VENDOR_TRANSPORT].tail_mean_us, 200.0)
        self.assertEqual(stats[SEGMENT_APP_QUEUE].tail_mean_us, 10.0)
        # 5000 / 5210 = 95.97%
        self.assertEqual(stats[SEGMENT_NETWORK_WIRE].tail_share_pct, 95.97)
        self.assertAlmostEqual(
            sum(s.tail_share_pct for s in stats.values()), 100.0, places=1)


class TestJitter(unittest.TestCase):
    def test_sigma_and_iqr_against_closed_form_values(self):
        # Totals 1..100. Population variance of a uniform integer series 1..n is
        # (n^2 - 1)/12 = 833.25, so sigma = 28.8660...; nearest-rank P75 - P25 = 50.
        engine = MarketDataLatencyMonitorEngine(max_allowed_p99_latency_us=1000.0)
        samples = [make_sample("V", 0.0, 0.0, float(i)) for i in range(1, 101)]
        metrics = engine.audit_vendor_latencies(samples).vendor_metrics["V"]

        self.assertEqual(metrics.mean_latency_us, 50.5)
        self.assertEqual(metrics.std_dev_jitter_us, round(math.sqrt(833.25), 2))
        self.assertEqual(metrics.std_dev_jitter_us, 28.87)
        self.assertEqual(metrics.iqr_jitter_us, 50.0)
        self.assertEqual(metrics.max_us, 100.0)

    def test_one_stall_separates_sigma_from_iqr(self):
        engine = MarketDataLatencyMonitorEngine(max_allowed_p99_latency_us=1e9)
        samples = [make_sample("V", 0.0, 0.0, 20.0) for _ in range(999)]
        samples.append(make_sample("V", 0.0, 0.0, 100_000.0))
        metrics = engine.audit_vendor_latencies(samples).vendor_metrics["V"]
        self.assertEqual(metrics.iqr_jitter_us, 0.0)
        self.assertGreater(metrics.std_dev_jitter_us, 3000.0)


class TestMultiVendorReport(unittest.TestCase):
    def setUp(self):
        self.engine = MarketDataLatencyMonitorEngine(max_allowed_p99_latency_us=500.0)

    def test_vendors_are_graded_independently(self):
        samples = []
        for i in range(200):
            samples.append(make_sample("VENDOR_A", 30.0, 20.0, 50.0))
            samples.append(make_sample("VENDOR_B", 30.0, 20.0, 50.0 if i < 180 else 1450.0))
            t_ex = 1_000_000.0 + i
            samples.append(
                LatencySample("VENDOR_C", "AAPL", t_ex, t_ex + 2000.0, t_ex + 60.0, t_ex + 80.0))

        report = self.engine.audit_vendor_latencies(samples)
        self.assertEqual(report.status, REPORT_SLA_BREACH)
        self.assertEqual(report.total_samples_processed, 600)
        self.assertEqual(report.sla_breaching_vendors, ["VENDOR_B"])
        self.assertEqual(report.unmeasurable_vendors, ["VENDOR_C"])
        self.assertEqual(report.vendor_metrics["VENDOR_A"].status, STATUS_HEALTHY)
        self.assertEqual(report.vendor_metrics["VENDOR_C"].status, STATUS_CLOCK_DOMAIN_ERROR)
        self.assertIn("VENDOR_C", report.audit_notes)

    def test_a_broken_vendor_alone_does_not_read_as_healthy(self):
        samples = []
        for i in range(50):
            t_ex = 1_000_000.0 + i
            samples.append(
                LatencySample("VENDOR_C", "AAPL", t_ex, t_ex + 2000.0, t_ex + 60.0, t_ex + 80.0))
        report = self.engine.audit_vendor_latencies(samples)
        self.assertEqual(report.status, REPORT_UNMEASURABLE)
        self.assertNotEqual(report.status, REPORT_ALL_HEALTHY)

    def test_vendor_ids_are_pooled_case_and_whitespace_insensitively(self):
        # Samples for one feed must land in one distribution: a percentile is a
        # quantile of a distribution, so splitting the feed by a stray space and
        # averaging the two P99s would not give the feed's P99.
        samples = [make_sample("bloomberg", 30.0, 20.0, 50.0) for _ in range(50)]
        samples += [make_sample("  BLOOMBERG  ", 30.0, 20.0, 50.0) for _ in range(50)]
        report = self.engine.audit_vendor_latencies(samples)
        self.assertEqual(list(report.vendor_metrics), ["BLOOMBERG"])
        self.assertEqual(report.vendor_metrics["BLOOMBERG"].sample_count, 100)

    def test_report_records_the_conditions_of_the_verdict(self):
        samples = [make_sample("V", 30.0, 20.0, 50.0) for _ in range(100)]
        report = self.engine.audit_vendor_latencies(samples)
        self.assertEqual(report.percentile_method, PERCENTILE_NEAREST_RANK)
        self.assertEqual(report.audited_percentile, 99.0)
        self.assertEqual(report.vendor_metrics["V"].min_samples_required, 100)


if __name__ == "__main__":
    unittest.main()
