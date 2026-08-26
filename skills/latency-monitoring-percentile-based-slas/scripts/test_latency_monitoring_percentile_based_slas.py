"""Unit tests for the percentile-based latency SLA engine.

Expected percentile values are derived by hand from the nearest-rank definition
(``ceil(p/100 * N)`` into the ascending-sorted series) rather than by re-running the
implementation's own arithmetic, so a regression in the estimator cannot hide behind a
test that computes the same wrong answer.
"""

import logging
import math
import random
import unittest

from latency_monitoring_percentile_based_slas import (
    PERCENTILE_LINEAR,
    PERCENTILE_NEAREST_RANK,
    STATUS_APPROVED,
    STATUS_INSUFFICIENT_SAMPLES,
    STATUS_P50_WARNING,
    STATUS_P99_WARNING,
    STATUS_P999_CRITICAL,
    LatencyPercentileSlaEngine,
    LatencySampleError,
    LatencySampleSeries,
    LatencySlaReport,
    correct_for_coordinated_omission,
    is_percentile_resolvable,
    min_samples_for_percentile,
    pool_latency_samples,
    rank_for_percentile,
)

# The engine logs a CRITICAL line on every tail breach; keep test output readable.
logging.getLogger("latency_monitoring_percentile_based_slas").setLevel(logging.CRITICAL + 1)


def _series(samples, **kwargs):
    """Build a series with permissive budgets unless a test overrides them."""
    params = {
        "sla_p50_target_us": 1e9,
        "sla_p99_target_us": 1e9,
        "sla_p999_target_us": 1e9,
    }
    params.update(kwargs)
    return LatencySampleSeries("TICK_TO_TRADE", samples, **params)


class TestPercentileEstimator(unittest.TestCase):
    """Percentile arithmetic, independent of any SLA verdict."""

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()

    def test_nearest_rank_matches_hand_derived_values(self):
        # Samples 1..100. Nearest rank = ceil(p/100 * 100), 1-based into the sorted list,
        # so P25 -> rank 25 -> value 25, P99 -> rank 99 -> value 99.
        samples = [float(x) for x in range(1, 101)]
        for percentile, expected in [
            (25.0, 25.0), (50.0, 50.0), (75.0, 75.0),
            (90.0, 90.0), (95.0, 95.0), (99.0, 99.0),
        ]:
            with self.subTest(percentile=percentile):
                self.assertEqual(self.engine.calculate_percentile(samples, percentile), expected)

    def test_nearest_rank_always_returns_an_observed_value(self):
        # The evidentiary property the default estimator is chosen for: every reported
        # figure is a latency the system actually exhibited.
        observed = {10.0, 900.0}
        samples = sorted([10.0] * 500 + [900.0] * 500)
        for percentile in (0.0, 25.0, 50.0, 75.0, 99.0, 99.9, 100.0):
            with self.subTest(percentile=percentile):
                self.assertIn(self.engine.calculate_percentile(samples, percentile), observed)

    def test_linear_interpolation_can_report_a_latency_never_measured(self):
        # A stage that is either 10 us or 900 us and nothing between. Interpolation puts
        # the median at 455 us -- a value the system never produced. Documents exactly
        # what the non-default estimator does rather than asserting it is "wrong".
        samples = sorted([10.0] * 500 + [900.0] * 500)
        self.assertEqual(
            self.engine.calculate_percentile(samples, 50.0, PERCENTILE_LINEAR), 455.0
        )
        self.assertEqual(self.engine.calculate_percentile(samples, 50.0), 10.0)

    def test_single_sample_series_returns_that_sample(self):
        self.assertEqual(self.engine.calculate_percentile([42.0], 99.9), 42.0)

    def test_unknown_percentile_method_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.calculate_percentile([1.0, 2.0], 50.0, "MEDIAN_OF_MEDIANS")

    def test_percentile_of_empty_series_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.calculate_percentile([], 50.0)

    def test_out_of_range_percentile_is_rejected(self):
        for method in (PERCENTILE_NEAREST_RANK, PERCENTILE_LINEAR):
            for percentile in (-1.0, 100.5):
                with self.subTest(method=method, percentile=percentile):
                    with self.assertRaises(LatencySampleError):
                        self.engine.calculate_percentile([1.0, 2.0], percentile, method)


class TestPercentileResolution(unittest.TestCase):
    """Whether a sample count can resolve a percentile at all."""

    def test_rank_rule_is_ceiling_based_and_clamped(self):
        self.assertEqual(rank_for_percentile(100, 99.0), 99)
        self.assertEqual(rank_for_percentile(10, 0.0), 1)     # clamped up to rank 1
        self.assertEqual(rank_for_percentile(10, 100.0), 10)  # clamped down to N

    def test_p999_resolvable_at_exactly_1000_samples(self):
        # Regression: 99.9/100 evaluates to 0.9990000000000001, so without the one-ULP
        # nudge ceil(0.999... * 1000) is 1000 and P99.9 is pinned to the maximum at the
        # very sample count that is meant to resolve it.
        self.assertTrue(is_percentile_resolvable(1000, 99.9))
        self.assertFalse(is_percentile_resolvable(999, 99.9))
        self.assertEqual(rank_for_percentile(1000, 99.9), 999)

    def test_min_samples_is_exactly_the_first_resolvable_count(self):
        for percentile, expected in [
            (50.0, 2), (90.0, 10), (95.0, 20), (99.0, 100), (99.9, 1000), (99.99, 10000),
        ]:
            with self.subTest(percentile=percentile):
                minimum = min_samples_for_percentile(percentile)
                self.assertEqual(minimum, expected)
                self.assertTrue(is_percentile_resolvable(minimum, percentile))
                self.assertFalse(is_percentile_resolvable(minimum - 1, percentile))

    def test_unresolvable_percentile_equals_the_maximum(self):
        # The concrete meaning of "unresolvable": the reported P99.9 is just the worst
        # sample seen, carrying no information about a 1-in-1000 event.
        engine = LatencyPercentileSlaEngine()
        samples = [float(x) for x in range(1, 51)]
        self.assertFalse(is_percentile_resolvable(50, 99.9))
        self.assertEqual(engine.calculate_percentile(samples, 99.9), max(samples))


class TestSampleValidation(unittest.TestCase):
    """Inputs that would make every downstream percentile meaningless."""

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()

    def test_empty_series_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(_series([]))

    def test_empty_series_still_raises_value_error_for_legacy_callers(self):
        # LatencySampleError subclasses ValueError so pre-existing callers keep working.
        with self.assertRaises(ValueError):
            self.engine.audit_latency_sla(_series([]))

    def test_nan_sample_is_rejected_rather_than_silently_approved(self):
        # Regression: NaN compares False against every bound, so sorted() leaves the list
        # unordered and `NaN <= budget` is False for every budget -- which a naive
        # implementation folds into a *passing* verdict on a corrupted series.
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(_series([5.0, float("nan"), 1.0, 2.0]))

    def test_infinite_sample_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(_series([5.0, float("inf")]))

    def test_negative_sample_is_rejected_as_clock_skew(self):
        # A negative duration means the bracketing timestamps came from clocks that
        # disagree, so the positive samples in the window are untrustworthy too.
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(_series([100.0] * 10 + [-500.0]))

    def test_zero_latency_is_accepted(self):
        # 0 us is degenerate but not impossible (coarse timestamp granularity).
        report = self.engine.audit_latency_sla(_series([0.0] * 1000))
        self.assertEqual(report.min_latency_us, 0.0)

    def test_non_numeric_sample_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(_series(["120"]))

    def test_boolean_sample_is_rejected(self):
        # bool is a subclass of int; True would otherwise be silently read as 1 us.
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(_series([True, 10.0]))

    def test_negative_clock_uncertainty_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(_series([10.0] * 1000, clock_uncertainty_us=-1.0))

    def test_unknown_series_percentile_method_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(_series([10.0] * 1000, percentile_method="BOGUS"))

    def test_implausibly_large_sample_is_rejected_before_it_overflows(self):
        # A sample near the float ceiling makes the mean and variance overflow, which
        # previously escaped as an OverflowError from math.fsum rather than a domain error.
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(_series([1e308, 1.0] * 500))

    def test_plausible_multi_second_stall_is_still_accepted(self):
        # The magnitude bound must not reject genuinely awful but real latencies.
        report = self.engine.audit_latency_sla(_series([10.0] * 999 + [5_000_000.0]))
        self.assertEqual(report.max_latency_us, 5_000_000.0)


class TestBudgetValidation(unittest.TestCase):
    """SLA budgets that cannot produce a coherent verdict."""

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()

    def test_non_finite_budget_is_rejected(self):
        # NaN fails every comparison, so the audit would report a breach that no amount of
        # tuning could ever clear.
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(
                LatencySampleSeries("T", [10.0] * 1000, sla_p99_target_us=float("nan"))
            )

    def test_negative_budget_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(
                LatencySampleSeries("T", [10.0] * 1000, sla_p50_target_us=-1.0)
            )

    def test_decreasing_budgets_are_rejected(self):
        # Percentiles are non-decreasing, so a P99.9 budget below the P99 budget
        # guarantees a tail breach on a perfectly healthy system.
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(
                LatencySampleSeries(
                    "T",
                    [10.0] * 1000,
                    sla_p50_target_us=500.0,
                    sla_p99_target_us=200.0,
                    sla_p999_target_us=100.0,
                )
            )

    def test_equal_budgets_are_accepted(self):
        report = self.engine.audit_latency_sla(
            LatencySampleSeries(
                "T",
                [10.0] * 1000,
                sla_p50_target_us=200.0,
                sla_p99_target_us=200.0,
                sla_p999_target_us=200.0,
            )
        )
        self.assertEqual(report.status, STATUS_APPROVED)


class TestSlaVerdict(unittest.TestCase):
    """Status selection, precedence, and the approval/breach asymmetry."""

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()

    def test_healthy_series_is_approved(self):
        random.seed(42)
        samples = [random.uniform(20.0, 40.0) for _ in range(9990)] + [
            random.uniform(100.0, 150.0) for _ in range(10)
        ]
        report = self.engine.audit_latency_sla(
            LatencySampleSeries(
                pipeline_stage="TICK_TO_TRADE",
                samples_microseconds=samples,
                sla_p50_target_us=50.0,
                sla_p99_target_us=200.0,
                sla_p999_target_us=1000.0,
            )
        )
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_p50_sla_passed)
        self.assertTrue(report.is_p99_sla_passed)
        self.assertTrue(report.is_p999_sla_passed)
        self.assertTrue(report.is_p999_resolvable)
        self.assertEqual(report.warnings, [])

    def test_critical_tail_breach_is_reported(self):
        random.seed(42)
        samples = [random.uniform(20.0, 40.0) for _ in range(9900)] + [
            random.uniform(2000.0, 3000.0) for _ in range(100)
        ]
        report = self.engine.audit_latency_sla(
            LatencySampleSeries(
                pipeline_stage="ORDER_GATEWAY_ACK",
                samples_microseconds=samples,
                sla_p50_target_us=50.0,
                sla_p99_target_us=200.0,
                sla_p999_target_us=1000.0,
            )
        )
        self.assertEqual(report.status, STATUS_P999_CRITICAL)
        self.assertFalse(report.is_p999_sla_passed)
        self.assertGreater(report.p999_latency_us, 1000.0)

    def test_p50_breach_is_not_reported_as_approved(self):
        # Regression: a median over budget with healthy tails previously produced
        # SLA_COMPLIANCE_APPROVED and audit notes reading "All SLAs passed", while
        # is_p50_sla_passed was False. A whole-distribution shift is not compliance.
        report = self.engine.audit_latency_sla(
            LatencySampleSeries("TICK_TO_TRADE", [100.0] * 1000, sla_p50_target_us=50.0)
        )
        self.assertEqual(report.status, STATUS_P50_WARNING)
        self.assertFalse(report.is_p50_sla_passed)
        self.assertNotIn("All audited SLAs passed", report.audit_notes)

    def test_tail_breach_outranks_median_breach(self):
        report = self.engine.audit_latency_sla(
            LatencySampleSeries(
                "TICK_TO_TRADE",
                [100.0] * 990 + [5000.0] * 10,
                sla_p50_target_us=50.0,
                sla_p99_target_us=200.0,
                sla_p999_target_us=1000.0,
            )
        )
        self.assertEqual(report.status, STATUS_P999_CRITICAL)
        self.assertFalse(report.is_p50_sla_passed)  # both breached, tail reported

    def test_p99_breach_outranks_median_breach(self):
        report = self.engine.audit_latency_sla(
            LatencySampleSeries(
                "TICK_TO_TRADE",
                [300.0] * 1000,
                sla_p50_target_us=50.0,
                sla_p99_target_us=200.0,
                sla_p999_target_us=1000.0,
            )
        )
        self.assertEqual(report.status, STATUS_P99_WARNING)

    def test_rounding_cannot_mask_a_breach(self):
        # Regression: rounding percentiles to 2 dp *before* the threshold comparison
        # turned a true P99 of 200.004 us into a passing 200.00 us.
        report = self.engine.audit_latency_sla(
            LatencySampleSeries("TICK_TO_TRADE", [200.004] * 1000, sla_p99_target_us=200.0)
        )
        self.assertFalse(report.is_p99_sla_passed)
        self.assertEqual(report.status, STATUS_P99_WARNING)
        self.assertEqual(report.p99_latency_us, 200.0)  # display value still rounded

    def test_budget_is_inclusive_at_the_exact_threshold(self):
        report = self.engine.audit_latency_sla(
            LatencySampleSeries("TICK_TO_TRADE", [200.0] * 1000, sla_p99_target_us=200.0)
        )
        self.assertTrue(report.is_p99_sla_passed)

    def test_small_sample_cannot_be_approved(self):
        # 50 samples cannot resolve P99.9. "No breach observed" is not "compliant".
        report = self.engine.audit_latency_sla(
            LatencySampleSeries("TICK_TO_TRADE", [10.0] * 50)
        )
        self.assertEqual(report.status, STATUS_INSUFFICIENT_SAMPLES)
        self.assertFalse(report.is_p999_resolvable)
        self.assertTrue(any("not resolvable" in w for w in report.warnings))

    def test_small_sample_breach_is_still_reported(self):
        # The asymmetry: a small sample can prove a breach even though it cannot prove
        # compliance, because an over-budget latency was genuinely observed.
        report = self.engine.audit_latency_sla(
            LatencySampleSeries(
                "TICK_TO_TRADE", [10.0] * 9 + [5000.0], sla_p999_target_us=1000.0
            )
        )
        self.assertEqual(report.status, STATUS_P999_CRITICAL)

    def test_resolution_boundary_flips_verdict_at_1000_samples(self):
        healthy = LatencySampleSeries("TICK_TO_TRADE", [10.0] * 999)
        self.assertEqual(self.engine.audit_latency_sla(healthy).status, STATUS_INSUFFICIENT_SAMPLES)
        healthy = LatencySampleSeries("TICK_TO_TRADE", [10.0] * 1000)
        self.assertEqual(self.engine.audit_latency_sla(healthy).status, STATUS_APPROVED)


class TestJitterAndDistribution(unittest.TestCase):
    """Dispersion metrics, derived independently of the implementation."""

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()

    def test_mean_std_and_iqr_on_a_uniform_series(self):
        # Samples 1..100. Mean = 50.5. Population variance of the integers 1..n is
        # (n^2 - 1)/12 = 9999/12 = 833.25, so sigma = sqrt(833.25) = 28.8660...
        # Nearest-rank P25 = 25 and P75 = 75, so IQR = 50.
        report = self.engine.audit_latency_sla(_series([float(x) for x in range(1, 101)]))
        self.assertEqual(report.mean_latency_us, 50.5)
        self.assertEqual(report.jitter_std_dev_us, round(math.sqrt(833.25), 2))
        self.assertEqual(report.jitter_iqr_us, 50.0)
        self.assertEqual(report.min_latency_us, 1.0)
        self.assertEqual(report.max_latency_us, 100.0)

    def test_constant_series_has_zero_jitter(self):
        report = self.engine.audit_latency_sla(_series([25.0] * 1000))
        self.assertEqual(report.jitter_std_dev_us, 0.0)
        self.assertEqual(report.jitter_iqr_us, 0.0)

    def test_iqr_is_robust_where_std_dev_is_not(self):
        # One 100 ms stall moves the standard deviation by orders of magnitude while the
        # interquartile range is unmoved -- which is the point of reporting both.
        clean = self.engine.audit_latency_sla(_series([10.0] * 999 + [12.0]))
        spiked = self.engine.audit_latency_sla(_series([10.0] * 999 + [100000.0]))
        self.assertEqual(clean.jitter_iqr_us, spiked.jitter_iqr_us)
        self.assertGreater(spiked.jitter_std_dev_us, clean.jitter_std_dev_us * 100)


class TestCoordinatedOmission(unittest.TestCase):
    """HdrHistogram's documented expected-interval correction."""

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()

    def test_correction_expansion_matches_hdrhistogram(self):
        # A 50,000 us stall at a 1,000 us cadence: the original sample plus the
        # decreasingly-smaller series 49,000 ... 1,000, i.e. 1 + 49 = 50 values.
        corrected = correct_for_coordinated_omission([50000.0], 1000.0)
        self.assertEqual(len(corrected), 50)
        self.assertEqual(corrected[0], 50000.0)
        self.assertEqual(corrected[-1], 1000.0)
        self.assertEqual(corrected[1], 49000.0)

    def test_values_at_or_below_the_interval_are_untouched(self):
        self.assertEqual(correct_for_coordinated_omission([500.0], 1000.0), [500.0])
        self.assertEqual(correct_for_coordinated_omission([1000.0], 1000.0), [1000.0])
        self.assertEqual(correct_for_coordinated_omission([2000.0], 1000.0), [2000.0, 1000.0])

    def test_non_positive_interval_is_rejected(self):
        for interval in (0.0, -1.0, float("nan")):
            with self.subTest(interval=interval):
                with self.assertRaises(LatencySampleError):
                    correct_for_coordinated_omission([100.0], interval)

    def test_correction_exposes_a_stall_the_raw_series_hides(self):
        # 999 healthy samples plus one 50 ms stall. Uncorrected, the stall is a single
        # outlier: P99.9 lands on rank 999 and reads 20 us, so the audit passes. The
        # sampler was blocked for 50 intervals, though, so the corrected series carries
        # 50 degrading observations and the tail budget is breached by 49x.
        base = [20.0] * 999 + [50000.0]
        uncorrected = self.engine.audit_latency_sla(
            LatencySampleSeries("TICK_TO_TRADE", base, sla_p999_target_us=1000.0)
        )
        self.assertEqual(uncorrected.status, STATUS_APPROVED)
        self.assertEqual(uncorrected.p999_latency_us, 20.0)

        corrected = self.engine.audit_latency_sla(
            LatencySampleSeries(
                "TICK_TO_TRADE",
                base,
                sla_p999_target_us=1000.0,
                expected_sample_interval_us=1000.0,
            )
        )
        self.assertEqual(corrected.status, STATUS_P999_CRITICAL)
        self.assertEqual(corrected.total_samples_count, 1049)
        self.assertEqual(corrected.p999_latency_us, 49000.0)
        self.assertTrue(corrected.coordinated_omission_corrected)
        self.assertTrue(any("Coordinated-omission" in w for w in corrected.warnings))

    def test_unit_error_in_the_interval_is_refused_not_expanded(self):
        # The correction multiplies each sample by roughly value/interval. An interval
        # supplied in nanoseconds where microseconds were meant turns 1,000 samples into
        # 50 million records; refusing beats exhausting memory.
        with self.assertRaises(LatencySampleError):
            self.engine.audit_latency_sla(
                LatencySampleSeries(
                    "T", [50000.0] * 1000, expected_sample_interval_us=1.0
                )
            )

    def test_correction_is_off_by_default(self):
        report = self.engine.audit_latency_sla(_series([20.0] * 1000))
        self.assertFalse(report.coordinated_omission_corrected)
        self.assertEqual(report.total_samples_count, 1000)


class TestFleetAggregation(unittest.TestCase):
    """Pooling raw samples instead of averaging per-node percentiles."""

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()

    def test_pooled_percentile_differs_from_the_mean_of_percentiles(self):
        # Node A is uniformly fast, node B uniformly slow. Averaging the two P99 values
        # gives 455 us; the fleet's actual P99 over the pooled observations is 900 us.
        node_a = LatencySampleSeries("GW_A", [10.0] * 1000)
        node_b = LatencySampleSeries("GW_B", [900.0] * 1000)
        p99_a = self.engine.audit_latency_sla(node_a).p99_latency_us
        p99_b = self.engine.audit_latency_sla(node_b).p99_latency_us
        self.assertEqual((p99_a + p99_b) / 2.0, 455.0)

        pooled = self.engine.audit_latency_sla(
            LatencySampleSeries("FLEET", pool_latency_samples([node_a, node_b]))
        )
        self.assertEqual(pooled.p99_latency_us, 900.0)
        self.assertEqual(pooled.total_samples_count, 2000)

    def test_pooling_an_empty_group_is_rejected(self):
        with self.assertRaises(LatencySampleError):
            pool_latency_samples([])


class TestClockUncertainty(unittest.TestCase):
    """The measurement noise floor, reported but never allowed to change a verdict."""

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()

    def test_verdict_inside_the_noise_floor_is_flagged(self):
        # A P99 of 195 us against a 200 us budget is a 5 us margin. Two hosts each
        # permitted 100 us of divergence from UTC cannot resolve a 5 us difference.
        report = self.engine.audit_latency_sla(
            LatencySampleSeries(
                "TICK_TO_TRADE",
                [195.0] * 1000,
                sla_p50_target_us=200.0,
                sla_p99_target_us=200.0,
                sla_p999_target_us=1000.0,
                clock_uncertainty_us=200.0,
            )
        )
        self.assertTrue(report.is_p99_sla_passed)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(any("noise floor" in w for w in report.warnings))

    def test_noise_floor_does_not_alter_the_status(self):
        samples = [195.0] * 1000
        budgets = {
            "sla_p50_target_us": 200.0,
            "sla_p99_target_us": 200.0,
            "sla_p999_target_us": 1000.0,
        }
        without = self.engine.audit_latency_sla(
            LatencySampleSeries("T", samples, **budgets)
        )
        with_uncertainty = self.engine.audit_latency_sla(
            LatencySampleSeries("T", samples, clock_uncertainty_us=200.0, **budgets)
        )
        self.assertEqual(without.status, with_uncertainty.status)
        self.assertEqual(without.warnings, [])
        self.assertTrue(with_uncertainty.warnings)

    def test_comfortable_margin_is_not_flagged(self):
        report = self.engine.audit_latency_sla(
            LatencySampleSeries(
                "TICK_TO_TRADE",
                [10.0] * 1000,
                sla_p50_target_us=50.0,
                sla_p99_target_us=200.0,
                sla_p999_target_us=1000.0,
                clock_uncertainty_us=1.0,
            )
        )
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertEqual(report.warnings, [])


class TestReportContract(unittest.TestCase):
    """Shape and reproducibility of the emitted report."""

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()

    def test_report_records_the_estimator_it_used(self):
        for method in (PERCENTILE_NEAREST_RANK, PERCENTILE_LINEAR):
            with self.subTest(method=method):
                report = self.engine.audit_latency_sla(
                    _series([10.0] * 1000, percentile_method=method)
                )
                self.assertIsInstance(report, LatencySlaReport)
                self.assertEqual(report.percentile_method, method)

    def test_percentiles_are_monotonically_non_decreasing(self):
        random.seed(7)
        report = self.engine.audit_latency_sla(
            _series([random.expovariate(1 / 40.0) for _ in range(5000)])
        )
        ladder = [
            report.min_latency_us, report.p25_latency_us, report.p50_latency_us,
            report.p75_latency_us, report.p90_latency_us, report.p95_latency_us,
            report.p99_latency_us, report.p999_latency_us, report.max_latency_us,
        ]
        self.assertEqual(ladder, sorted(ladder))

    def test_engine_is_stateless_across_audits(self):
        # No audit may influence another; a shared engine must be reusable.
        breach = LatencySampleSeries("T", [5000.0] * 1000, sla_p999_target_us=1000.0)
        healthy = LatencySampleSeries("T", [10.0] * 1000, sla_p999_target_us=1000.0)
        self.assertEqual(self.engine.audit_latency_sla(breach).status, STATUS_P999_CRITICAL)
        first = self.engine.audit_latency_sla(healthy)
        self.assertEqual(self.engine.audit_latency_sla(breach).status, STATUS_P999_CRITICAL)
        second = self.engine.audit_latency_sla(healthy)
        self.assertEqual(first, second)

    def test_audit_does_not_mutate_the_input_series(self):
        samples = [30.0, 10.0, 20.0] * 400
        original = list(samples)
        series = LatencySampleSeries("T", samples)
        self.engine.audit_latency_sla(series)
        self.assertEqual(series.samples_microseconds, original)


if __name__ == "__main__":
    unittest.main()
