"""Unit tests for the model inference latency budget engine.

Expected percentile values are derived by hand from the nearest-rank definition
(``ceil(p/100 * N)`` into the ascending-sorted series) and, for the jitter case, from
the closed form for the variance of 1..n -- not by re-running the implementation's own
arithmetic, so a regression in the estimator cannot hide behind a test that computes
the same wrong answer.
"""

import logging
import math
import unittest

from inference_latency_budgeter import (
    FALLBACK_ALERT_ONLY,
    FALLBACK_LINEAR_HEURISTIC,
    FALLBACK_QUANTIZED_ONNX,
    PERCENTILE_LINEAR,
    PERCENTILE_NEAREST_RANK,
    STATUS_BREACH,
    STATUS_INSUFFICIENT_SAMPLES,
    STATUS_NORMAL,
    STATUS_WARNING,
    InferenceBudgetConfig,
    InferenceBudgetConfigError,
    InferenceBudgetError,
    InferenceBudgetReport,
    InferenceSampleError,
    LatencyPercentiles,
    ModelInferenceLatencyBudgeterEngine,
    is_percentile_resolvable,
    min_samples_for_percentile,
    rank_for_percentile,
    summarize_report,
    validate_inference_samples,
)

# The engine logs CRITICAL on every breach and WARNING on every advisory; keep the
# test output readable without suppressing the assertions that check them.
logging.getLogger("inference_latency_budgeter").setLevel(logging.CRITICAL + 1)


class TestPercentileEstimator(unittest.TestCase):
    """Percentile arithmetic, independent of any SLA verdict."""

    def setUp(self):
        self.engine = ModelInferenceLatencyBudgeterEngine()

    def test_nearest_rank_matches_hand_derived_values(self):
        # Samples 1..100 ms. Nearest rank = ceil(p/100 * 100), 1-based into the
        # sorted list, so P25 -> rank 25 -> 25.0 and P99 -> rank 99 -> 99.0.
        samples = [float(x) for x in range(1, 101)]
        for percentile, expected in [
            (25.0, 25.0), (50.0, 50.0), (75.0, 75.0),
            (90.0, 90.0), (95.0, 95.0), (99.0, 99.0),
        ]:
            with self.subTest(percentile=percentile):
                self.assertEqual(rank_for_percentile(100, percentile), int(expected))

        cfg = InferenceBudgetConfig("HAND_DERIVED", max_inference_budget_ms=1e6)
        report = self.engine.evaluate_inference_latency_budget(cfg, samples)
        self.assertEqual(report.percentiles.p25_ms, 25.0)
        self.assertEqual(report.percentiles.p50_ms, 50.0)
        self.assertEqual(report.percentiles.p75_ms, 75.0)
        self.assertEqual(report.percentiles.p90_ms, 90.0)
        self.assertEqual(report.percentiles.p95_ms, 95.0)
        self.assertEqual(report.percentiles.p99_ms, 99.0)

    def test_p999_ulp_nudge_resolves_at_exactly_1000_samples(self):
        # 99.9/100 is stored as 0.9990000000000001, so without HdrHistogram's
        # nextAfter nudge ceil(0.999... * 1000) is 1000 and P99.9 would be pinned to
        # the maximum at the very sample count that should first resolve it.
        self.assertEqual(rank_for_percentile(1000, 99.9), 999)
        self.assertTrue(is_percentile_resolvable(1000, 99.9))
        self.assertFalse(is_percentile_resolvable(999, 99.9))
        self.assertEqual(min_samples_for_percentile(99.9), 1000)
        self.assertEqual(min_samples_for_percentile(99.0), 100)

    def test_nearest_rank_returns_an_observed_latency_where_linear_does_not(self):
        # A model that is either 0.3 ms (warm path) or 2.5 ms (stall) and nothing in
        # between. Linear interpolation of the median blends the two neighbours:
        # k = 99 * 0.5 = 49.5 -> 0.5*0.3 + 0.5*2.5 = 1.4 ms, a latency the model
        # never produced. Nearest rank returns rank 50 -> 0.3 ms.
        samples = [0.3] * 50 + [2.5] * 50
        nearest = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig("BIMODAL", max_inference_budget_ms=1e6), samples
        )
        interpolated = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig(
                "BIMODAL", max_inference_budget_ms=1e6, percentile_method=PERCENTILE_LINEAR
            ),
            samples,
        )
        self.assertEqual(nearest.percentiles.p50_ms, 0.3)
        self.assertIn(nearest.percentiles.p50_ms, samples)
        self.assertEqual(interpolated.percentiles.p50_ms, 1.4)
        self.assertNotIn(interpolated.percentiles.p50_ms, samples)
        self.assertEqual(nearest.percentile_method, PERCENTILE_NEAREST_RANK)

    def test_jitter_reports_sigma_and_iqr(self):
        # Samples 1..100. Sum of squared deviations about the mean is n(n^2-1)/12 =
        # 100 * 9999 / 12 = 83325, so the Bessel-corrected sigma is sqrt(83325/99).
        # IQR = P75 - P25 = 75 - 25 = 50.
        samples = [float(x) for x in range(1, 101)]
        expected_sigma = math.sqrt((100 * (100 ** 2 - 1) / 12) / 99)
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig("JITTER", max_inference_budget_ms=1e6), samples
        )
        self.assertAlmostEqual(report.percentiles.jitter_std_dev_ms, expected_sigma, places=3)
        self.assertEqual(report.percentiles.jitter_iqr_ms, 50.0)


class TestSlaVerdicts(unittest.TestCase):
    """Status, compliance flags and fallback recommendation."""

    def setUp(self):
        self.engine = ModelInferenceLatencyBudgeterEngine()

    def test_normal_inference_latency_compliance(self):
        # 100 samples from 0.2 ms rising by 0.004 ms. Nearest-rank P99 = rank 99 ->
        # 0.2 + 98*0.004 = 0.592 ms, inside the 0.8 ms warning threshold.
        samples = [0.2 + (i * 0.004) for i in range(100)]
        cfg = InferenceBudgetConfig(
            "XGB_ALPHA_01", max_inference_budget_ms=1.0, warning_threshold_ms=0.8
        )

        report = self.engine.evaluate_inference_latency_budget(cfg, samples)

        self.assertTrue(report.is_sla_compliant)
        self.assertFalse(report.is_sla_breached)
        self.assertEqual(report.status, STATUS_NORMAL)
        self.assertEqual(report.percentiles.p99_ms, 0.592)
        self.assertIsNone(report.recommended_fallback_action)
        self.assertTrue(report.is_p99_resolvable)

    def test_sla_breach_and_quantized_onnx_fallback(self):
        # 95 samples at 0.3 ms plus 5 stalls at 2.5 ms. Nearest-rank P99 = rank 99 ->
        # index 98, which is inside the stall block -> 2.5 ms > 1.0 ms budget.
        samples = [0.3] * 95 + [2.5] * 5
        cfg = InferenceBudgetConfig(
            "LSTM_NEURAL_02",
            max_inference_budget_ms=1.0,
            fallback_action=FALLBACK_QUANTIZED_ONNX,
        )

        report = self.engine.evaluate_inference_latency_budget(cfg, samples)

        self.assertFalse(report.is_sla_compliant)
        self.assertTrue(report.is_sla_breached)
        self.assertEqual(report.status, STATUS_BREACH)
        self.assertEqual(report.recommended_fallback_action, FALLBACK_QUANTIZED_ONNX)
        self.assertEqual(report.percentiles.p99_ms, 2.5)

    def test_warning_band_between_thresholds(self):
        # 100 samples at 0.9 ms: above the 0.8 ms warning threshold, inside the
        # 1.0 ms budget. Compliant, but not silent.
        samples = [0.9] * 100
        cfg = InferenceBudgetConfig(
            "GBM_03", max_inference_budget_ms=1.0, warning_threshold_ms=0.8
        )

        report = self.engine.evaluate_inference_latency_budget(cfg, samples)

        self.assertEqual(report.status, STATUS_WARNING)
        self.assertTrue(report.is_sla_compliant)
        self.assertFalse(report.is_sla_breached)
        self.assertIsNone(report.recommended_fallback_action)

    def test_rounding_no_longer_converts_a_breach_into_a_pass(self):
        # Regression: the previous revision rounded the P99 to 3 dp *before* the
        # budget comparison, so 1.0004 ms became 1.0 ms and a breach was reported as
        # compliant. Comparisons now run on the unrounded value.
        samples = [1.0004] * 100
        cfg = InferenceBudgetConfig("ROUNDING_EDGE", max_inference_budget_ms=1.0)

        report = self.engine.evaluate_inference_latency_budget(cfg, samples)

        self.assertEqual(round(1.0004, 3), 1.0)          # what the old code compared
        self.assertEqual(report.status, STATUS_BREACH)
        self.assertTrue(report.is_sla_breached)
        self.assertEqual(report.percentiles.p99_ms, 1.0004)

    def test_exact_budget_value_is_not_a_breach(self):
        # The SLA is "P99 must not exceed the budget": equality passes.
        samples = [1.0] * 100
        cfg = InferenceBudgetConfig(
            "EXACT", max_inference_budget_ms=1.0, warning_threshold_ms=1.0
        )

        report = self.engine.evaluate_inference_latency_budget(cfg, samples)

        self.assertFalse(report.is_sla_breached)
        self.assertEqual(report.status, STATUS_NORMAL)


class TestSampleResolution(unittest.TestCase):
    """A percentile the window cannot resolve must not approve a model."""

    def setUp(self):
        self.engine = ModelInferenceLatencyBudgeterEngine()

    def test_short_healthy_window_is_not_compliance(self):
        # 50 samples: the P99 nearest rank is ceil(0.99*50) = 50 = N, so the reported
        # "P99" is just the observed maximum. Nothing breached, but nothing is proven.
        samples = [0.3] * 50
        cfg = InferenceBudgetConfig("SHORT_WINDOW", max_inference_budget_ms=1.0)

        report = self.engine.evaluate_inference_latency_budget(cfg, samples)

        self.assertEqual(report.status, STATUS_INSUFFICIENT_SAMPLES)
        self.assertFalse(report.is_sla_compliant)
        self.assertFalse(report.is_sla_breached)
        self.assertFalse(report.is_p99_resolvable)
        self.assertEqual(report.min_samples_for_p99, 100)
        self.assertIsNone(report.recommended_fallback_action)

    def test_same_series_at_100_samples_is_compliant(self):
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig("FULL_WINDOW", max_inference_budget_ms=1.0), [0.3] * 100
        )
        self.assertEqual(report.status, STATUS_NORMAL)
        self.assertTrue(report.is_sla_compliant)
        self.assertTrue(report.is_p99_resolvable)

    def test_breach_is_reported_at_any_sample_count(self):
        # Asymmetry: observing one over-budget latency proves a breach even though 10
        # samples cannot resolve P99. Observing none over 10 samples proves nothing.
        samples = [0.3] * 9 + [5.0]
        cfg = InferenceBudgetConfig("TINY_WINDOW", max_inference_budget_ms=1.0)

        report = self.engine.evaluate_inference_latency_budget(cfg, samples)

        self.assertEqual(report.status, STATUS_BREACH)
        self.assertTrue(report.is_sla_breached)
        self.assertFalse(report.is_p99_resolvable)
        self.assertEqual(report.recommended_fallback_action, FALLBACK_QUANTIZED_ONNX)

    def test_p999_over_a_100_sample_window_is_flagged_as_the_maximum(self):
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig("P999_CLAIM", max_inference_budget_ms=1e6),
            [0.3] * 99 + [9.0],
        )
        self.assertFalse(report.is_p99_9_resolvable)
        self.assertEqual(report.percentiles.p99_9_ms, 9.0)   # i.e. the observed max
        self.assertTrue(any("1-in-1000" in w for w in report.warnings))


class TestInputRejection(unittest.TestCase):
    """Corrupted series are rejected, never filtered and audited."""

    def setUp(self):
        self.engine = ModelInferenceLatencyBudgeterEngine()
        self.cfg = InferenceBudgetConfig("REJECT", max_inference_budget_ms=1.0)

    def test_nan_would_otherwise_read_as_a_passing_audit(self):
        # NaN compares False against every bound, so an unchecked audit would report
        # INFERENCE_LATENCY_NORMAL over a corrupted series.
        corrupted = [0.3] * 99 + [float("nan")]
        self.assertFalse(float("nan") > 1.0)             # the trap being closed
        with self.assertRaises(InferenceSampleError):
            self.engine.evaluate_inference_latency_budget(self.cfg, corrupted)

    def test_infinity_is_rejected(self):
        with self.assertRaises(InferenceSampleError):
            self.engine.evaluate_inference_latency_budget(self.cfg, [0.3, float("inf")])

    def test_negative_duration_rejects_the_whole_window(self):
        with self.assertRaises(InferenceSampleError):
            self.engine.evaluate_inference_latency_budget(self.cfg, [0.3] * 99 + [-0.001])

    def test_booleans_and_strings_are_rejected(self):
        for bad in ([True, False], [0.3, "0.4"], [0.3, None]):
            with self.subTest(bad=bad):
                with self.assertRaises(InferenceSampleError):
                    self.engine.evaluate_inference_latency_budget(self.cfg, bad)

    def test_unit_error_magnitude_is_rejected(self):
        with self.assertRaises(InferenceSampleError):
            self.engine.evaluate_inference_latency_budget(self.cfg, [0.3, 1e12])

    def test_empty_and_none_series_are_rejected_as_value_errors(self):
        # InferenceSampleError subclasses ValueError, so callers written against the
        # previous `raise ValueError` keep working.
        for bad in ([], None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.engine.evaluate_inference_latency_budget(self.cfg, bad)

    def test_zero_millisecond_sample_flags_a_coarse_clock(self):
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig("COARSE_CLOCK", max_inference_budget_ms=1.0),
            [0.0] * 50 + [0.3] * 50,
        )
        self.assertTrue(any("0.0 ms" in w for w in report.warnings))

    def test_validate_helper_returns_floats(self):
        self.assertEqual(validate_inference_samples([1, 2.5]), [1.0, 2.5])


class TestConfigValidation(unittest.TestCase):
    """A misconfigured budget must fail loudly at construction."""

    def test_non_positive_budget_is_rejected(self):
        for budget in (0.0, -1.0, float("nan")):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    InferenceBudgetConfig("M", max_inference_budget_ms=budget)

    def test_warning_threshold_above_budget_is_rejected(self):
        # Otherwise the warning band is unreachable and every breach arrives with no
        # prior notice.
        with self.assertRaises(InferenceBudgetConfigError):
            InferenceBudgetConfig(
                "M", max_inference_budget_ms=1.0, warning_threshold_ms=1.5
            )

    def test_unknown_fallback_action_is_rejected(self):
        # A typo here is silently ignored by the model router, leaving an over-budget
        # model serving live signals.
        with self.assertRaises(InferenceBudgetConfigError):
            InferenceBudgetConfig("M", fallback_action="QUANTISED_ONNX_FALLBACK")

    def test_unknown_percentile_method_is_rejected(self):
        with self.assertRaises(InferenceBudgetConfigError):
            InferenceBudgetConfig("M", percentile_method="P2_QUANTILE")

    def test_blank_model_id_is_rejected(self):
        with self.assertRaises(InferenceBudgetConfigError):
            InferenceBudgetConfig("   ")

    def test_config_errors_are_value_errors(self):
        self.assertTrue(issubclass(InferenceBudgetConfigError, InferenceBudgetError))
        self.assertTrue(issubclass(InferenceBudgetError, ValueError))


class TestFallbackHeadroom(unittest.TestCase):
    """The recommended fallback must be shown to relieve the budget."""

    def setUp(self):
        self.engine = ModelInferenceLatencyBudgeterEngine()
        self.breaching = [0.3] * 95 + [2.5] * 5      # nearest-rank P99 = 2.5 ms

    def test_unprofiled_fallback_is_flagged(self):
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig("UNPROFILED", max_inference_budget_ms=1.0),
            self.breaching,
        )
        self.assertEqual(report.status, STATUS_BREACH)
        self.assertTrue(any("no profiled P99" in w for w in report.warnings))

    def test_fallback_slower_than_the_model_it_replaces(self):
        # INT8 quantization is not faster on hardware without int8 instruction
        # support; a fallback that is no faster does not relieve anything.
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig(
                "SLOW_FALLBACK",
                max_inference_budget_ms=1.0,
                fallback_profiled_p99_ms=3.0,
            ),
            self.breaching,
        )
        self.assertTrue(any("not faster" in w for w in report.warnings))
        self.assertTrue(any("above the" in w for w in report.warnings))

    def test_fallback_faster_but_still_over_budget(self):
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig(
                "MARGINAL_FALLBACK",
                max_inference_budget_ms=1.0,
                fallback_action=FALLBACK_LINEAR_HEURISTIC,
                fallback_profiled_p99_ms=1.5,
            ),
            self.breaching,
        )
        self.assertFalse(any("not faster" in w for w in report.warnings))
        self.assertTrue(any("above the" in w for w in report.warnings))

    def test_adequate_fallback_raises_no_headroom_warning(self):
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig(
                "GOOD_FALLBACK",
                max_inference_budget_ms=1.0,
                fallback_profiled_p99_ms=0.4,
            ),
            self.breaching,
        )
        self.assertEqual(report.recommended_fallback_action, FALLBACK_QUANTIZED_ONNX)
        self.assertFalse(any("not faster" in w or "above the" in w for w in report.warnings))

    def test_alert_only_says_the_model_keeps_serving(self):
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig(
                "ALERTING", max_inference_budget_ms=1.0, fallback_action=FALLBACK_ALERT_ONLY
            ),
            self.breaching,
        )
        self.assertEqual(report.recommended_fallback_action, FALLBACK_ALERT_ONLY)
        self.assertTrue(any("keeps serving" in w for w in report.warnings))


class TestReportSurface(unittest.TestCase):
    """The report is the audit artifact; its shape is part of the contract."""

    def setUp(self):
        self.engine = ModelInferenceLatencyBudgeterEngine()

    def test_report_types_and_summary(self):
        report = self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig("SHAPE", max_inference_budget_ms=1.0), [0.3] * 100
        )
        self.assertIsInstance(report, InferenceBudgetReport)
        self.assertIsInstance(report.percentiles, LatencyPercentiles)
        self.assertEqual(report.sample_count, 100)
        self.assertEqual(report.max_inference_budget_ms, 1.0)
        self.assertIn("SHAPE", summarize_report(report))
        self.assertIn(STATUS_NORMAL, summarize_report(report))

    def test_engine_is_stateless_across_audits(self):
        breach_cfg = InferenceBudgetConfig("A", max_inference_budget_ms=1.0)
        healthy_cfg = InferenceBudgetConfig("B", max_inference_budget_ms=1.0)
        first = self.engine.evaluate_inference_latency_budget(breach_cfg, [5.0] * 1000)
        second = self.engine.evaluate_inference_latency_budget(healthy_cfg, [0.3] * 1000)
        self.assertEqual(first.status, STATUS_BREACH)
        self.assertTrue(first.warnings)
        self.assertEqual(second.status, STATUS_NORMAL)
        self.assertIsNone(second.recommended_fallback_action)
        # 1,000 samples resolve both P99 and P99.9, so a clean audit is silent: no
        # state from the breaching audit above leaks into this one.
        self.assertEqual(second.warnings, [])

    def test_input_series_is_not_mutated(self):
        samples = [2.5, 0.3, 1.1]
        original = list(samples)
        self.engine.evaluate_inference_latency_budget(
            InferenceBudgetConfig("NO_MUTATE", max_inference_budget_ms=1e6), samples
        )
        self.assertEqual(samples, original)


if __name__ == "__main__":
    unittest.main()
