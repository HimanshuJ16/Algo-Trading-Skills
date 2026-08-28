import math
import unittest

from network_jitter_impact_on_strategy_performance import (
    AUDIT_TAIL_PERCENTILE,
    JitterConfigError,
    JitterImpactReport,
    JitterSampleError,
    JitterSimulationConfig,
    LatencyPacketSample,
    NetworkJitterImpactAnalyzerEngine,
    SHARPE_MODEL_LINEAR,
    STATUS_HEALTHY,
    STATUS_HIGH_RISK,
    STATUS_INSUFFICIENT_SAMPLES,
    is_percentile_resolvable,
    min_samples_for_percentile,
    percentile_nearest_rank,
    rank_for_percentile,
)

T0 = 1_000_000_000
NS_PER_MS = 1_000_000


def make_samples(delays_ms, prefix="PKT"):
    """Build a sample series from a list of one-way delays in milliseconds."""
    return [
        LatencyPacketSample(f"{prefix}_{i}", T0, T0 + int(round(d * NS_PER_MS)))
        for i, d in enumerate(delays_ms)
    ]


class TestPercentileArithmetic(unittest.TestCase):
    """Nearest rank, checked against ranks derived by hand rather than from the code."""

    def test_nearest_rank_over_one_to_one_hundred(self):
        # Over the samples 1..100 the nearest rank ceil(p/100 * 100) is p itself, so the
        # P-th percentile is the value p. Derived by hand from HdrHistogram's rank rule.
        series = [float(i) for i in range(1, 101)]
        for percentile, expected in ((25.0, 25.0), (50.0, 50.0), (75.0, 75.0),
                                     (95.0, 95.0), (99.0, 99.0), (100.0, 100.0)):
            self.assertEqual(percentile_nearest_rank(series, percentile), expected)

    def test_rank_is_one_based_and_clamped(self):
        self.assertEqual(rank_for_percentile(100, 99.0), 99)
        self.assertEqual(rank_for_percentile(100, 100.0), 100)
        self.assertEqual(rank_for_percentile(100, 0.0), 1)   # clamped up from rank 0
        self.assertEqual(rank_for_percentile(7, 50.0), 4)    # ceil(3.5)

    def test_percentile_returns_an_observed_value_on_a_bimodal_link(self):
        # 500 packets at 10 ms and 500 at 900 ms. Linear interpolation would report a
        # median of 455 ms -- a delay the link never produced. Nearest rank must not.
        series = sorted([10.0] * 500 + [900.0] * 500)
        self.assertEqual(percentile_nearest_rank(series, 50.0), 10.0)
        self.assertIn(percentile_nearest_rank(series, 50.0), (10.0, 900.0))

    def test_resolution_boundary_for_p99(self):
        self.assertEqual(min_samples_for_percentile(99.0), 100)
        self.assertTrue(is_percentile_resolvable(100, 99.0))
        self.assertFalse(is_percentile_resolvable(99, 99.0))

    def test_invalid_percentile_and_count_rejected(self):
        with self.assertRaises(JitterSampleError):
            rank_for_percentile(0, 50.0)
        with self.assertRaises(JitterSampleError):
            rank_for_percentile(10, 101.0)
        with self.assertRaises(JitterSampleError):
            min_samples_for_percentile(100.0)


class TestPercentileRegression(unittest.TestCase):
    """Regression tests for the v1.0.0 ``int(n * p)`` index bug.

    Each of these fails against the old truncating index and passes against nearest
    rank; they are not restatements of the implementation's own formula.
    """

    def setUp(self):
        self.engine = NetworkJitterImpactAnalyzerEngine()

    def test_median_of_a_bimodal_window_is_the_lower_mode(self):
        # 50 packets at 1 ms then 50 at 9 ms. The 50th of 100 ascending values is 1.0 ms.
        # The old int(100 * 0.50) = index 50 returned the 51st value, 9.0 ms -- reporting
        # a median nine times the true one for the repo's own fixture.
        report = self.engine.analyze_jitter_impact(make_samples([1.0] * 50 + [9.0] * 50))
        self.assertEqual(report.p50_latency_ms, 1.0)

    def test_p99_is_not_the_observed_maximum(self):
        # 99 packets at 1 ms and one 50 ms outlier. The nearest rank for P99 over 100
        # samples is 99, i.e. the 99th ascending value = 1.0 ms. The old int(100 * 0.99)
        # = index 99 returned the 100th value, the 50 ms maximum, so the "P99" was
        # arithmetically the max and carried no tail information at all.
        report = self.engine.analyze_jitter_impact(make_samples([1.0] * 99 + [50.0]))
        self.assertEqual(report.p99_latency_ms, 1.0)
        self.assertEqual(report.min_latency_ms, 1.0)

    def test_p95_matches_hand_derived_rank(self):
        # Delays 1..200 ms. Nearest rank for P95 over 200 samples is ceil(190) = 190,
        # so P95 = 190 ms. The old int(200 * 0.95) = index 190 returned 191 ms.
        report = self.engine.analyze_jitter_impact(
            make_samples([float(i) for i in range(1, 201)])
        )
        self.assertEqual(report.p95_latency_ms, 190.0)


class TestJitterMetrics(unittest.TestCase):

    def setUp(self):
        self.engine = NetworkJitterImpactAnalyzerEngine()

    def test_mean_sigma_and_iqr_against_hand_derived_values(self):
        # Delays 1..100 ms. Mean = 50.5. Bessel-corrected variance of 1..n is
        # n(n+1)/12 = 100 * 101 / 12 = 841.666..., so sigma = 29.0115...
        # IQR = P75 - P25 = 75 - 25 = 50.
        report = self.engine.analyze_jitter_impact(
            make_samples([float(i) for i in range(1, 101)])
        )
        self.assertAlmostEqual(report.mean_latency_ms, 50.5, places=3)
        self.assertAlmostEqual(report.jitter_std_ms, math.sqrt(100 * 101 / 12.0), places=3)
        self.assertEqual(report.jitter_iqr_ms, 50.0)

    def test_pdv_is_measured_against_the_minimum_not_the_mean(self):
        # RFC 5481 PDV(i) = D(i) - D(min). With 99 packets at 2 ms and one at 40 ms,
        # nearest-rank P99 = 2.0 ms and min = 2.0 ms, so PDV P99 = 0.0 -- the tail
        # figure describes the link's own best case, not the distance from the mean.
        report = self.engine.analyze_jitter_impact(make_samples([2.0] * 99 + [40.0]))
        self.assertEqual(report.min_latency_ms, 2.0)
        self.assertEqual(report.pdv_p99_ms, 0.0)

        # Shift the tail into the resolvable range: 95 at 2 ms, 5 at 12 ms.
        report = self.engine.analyze_jitter_impact(make_samples([2.0] * 95 + [12.0] * 5))
        self.assertEqual(report.p99_latency_ms, 12.0)
        self.assertEqual(report.pdv_p99_ms, 10.0)

    def test_iqr_ignores_a_stall_that_moves_sigma(self):
        # One 5,000 ms stall in an otherwise uniform 1 ms window: sigma explodes, the
        # IQR does not move. This is why both are reported.
        report = self.engine.analyze_jitter_impact(make_samples([1.0] * 999 + [5000.0]))
        self.assertEqual(report.jitter_iqr_ms, 0.0)
        self.assertGreater(report.jitter_std_ms, 100.0)


class TestSharpeDegradationModel(unittest.TestCase):

    def test_degraded_sharpe_against_hand_computed_value(self):
        # Alternating 2.1 / 1.9 ms over 100 packets. Every deviation from the mean is
        # exactly 0.1 ms, so the Bessel-corrected sigma is sqrt(100 * 0.01 / 99)
        # = 0.100504 ms. With gamma = 0.5 the modelled Sharpe is
        # 2.5 - 0.5 * 0.100504 = 2.449748, which rounds to 2.45.
        engine = NetworkJitterImpactAnalyzerEngine()
        delays = [2.1 if i % 2 == 0 else 1.9 for i in range(100)]
        report = engine.analyze_jitter_impact(make_samples(delays))

        expected_sigma = math.sqrt(100 * 0.01 / 99)
        self.assertAlmostEqual(report.jitter_std_ms, round(expected_sigma, 3), places=3)
        self.assertEqual(report.simulated_degraded_sharpe, 2.45)
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertTrue(report.is_jitter_acceptable)
        self.assertEqual(report.breaches, [])
        self.assertEqual(report.sharpe_model, SHARPE_MODEL_LINEAR)

    def test_high_jitter_breaches_the_sharpe_floor(self):
        # Alternating 1 / 9 ms over 100 packets: sigma = sqrt(100 * 16 / 99) = 4.0202 ms.
        # Modelled Sharpe = 2.5 - 0.5 * 4.0202 = 0.4899 < 1.0 floor, and sigma also
        # exceeds the 3.0 ms absolute ceiling, so both findings are raised.
        engine = NetworkJitterImpactAnalyzerEngine(
            JitterSimulationConfig(base_sharpe=2.5, jitter_penalty_coeff=0.5,
                                   target_sharpe_min=1.0)
        )
        delays = [1.0 if i % 2 == 0 else 9.0 for i in range(100)]
        report = engine.analyze_jitter_impact(make_samples(delays))

        self.assertEqual(report.status, STATUS_HIGH_RISK)
        self.assertFalse(report.is_jitter_acceptable)
        self.assertAlmostEqual(report.jitter_std_ms, round(math.sqrt(100 * 16 / 99), 3),
                               places=3)
        self.assertEqual(report.simulated_degraded_sharpe, 0.49)
        self.assertEqual(report.max_jitter_tolerance_ms, 3.0)
        self.assertEqual(len(report.breaches), 2)
        self.assertTrue(any(b.startswith("SHARPE_BELOW_FLOOR") for b in report.breaches))
        self.assertTrue(any(b.startswith("JITTER_STD_OVER_CEILING")
                            for b in report.breaches))

    def test_sharpe_floor_is_tested_before_the_presentational_clamp(self):
        # A negative target_sharpe_min with jitter large enough to drive the modelled
        # Sharpe below zero. The reported figure is clamped to 0.0, but the breach test
        # must use the unclamped -1.51, which is below the -1.0 floor.
        engine = NetworkJitterImpactAnalyzerEngine(
            JitterSimulationConfig(base_sharpe=2.5, jitter_penalty_coeff=1.0,
                                   target_sharpe_min=-1.0,
                                   max_acceptable_jitter_ms=100.0)
        )
        delays = [1.0 if i % 2 == 0 else 9.0 for i in range(100)]  # sigma = 4.0202
        report = engine.analyze_jitter_impact(make_samples(delays))

        self.assertEqual(report.simulated_degraded_sharpe, 0.0)
        self.assertEqual(report.status, STATUS_HIGH_RISK)
        self.assertTrue(any(b.startswith("SHARPE_BELOW_FLOOR") for b in report.breaches))

    def test_tolerance_is_derived_from_the_configured_parameters(self):
        # (4.0 - 1.5) / 0.25 = 10.0 ms, computed by hand from the documented formula.
        cfg = JitterSimulationConfig(base_sharpe=4.0, jitter_penalty_coeff=0.25,
                                     target_sharpe_min=1.5)
        self.assertEqual(cfg.max_jitter_tolerance_ms, 10.0)


class TestAbsoluteBudgets(unittest.TestCase):
    """The v1.0.0 config field ``max_acceptable_jitter_ms`` was declared but never read."""

    def test_jitter_ceiling_is_enforced_independently_of_the_sharpe_model(self):
        # sigma = 4.0202 ms. A generous Sharpe model (gamma = 0.01 -> tolerance 150 ms)
        # would approve the window; the 1.0 ms absolute ceiling must still breach it.
        engine = NetworkJitterImpactAnalyzerEngine(
            JitterSimulationConfig(base_sharpe=2.5, jitter_penalty_coeff=0.01,
                                   target_sharpe_min=1.0, max_acceptable_jitter_ms=1.0)
        )
        delays = [1.0 if i % 2 == 0 else 9.0 for i in range(100)]
        report = engine.analyze_jitter_impact(make_samples(delays))

        self.assertEqual(report.status, STATUS_HIGH_RISK)
        self.assertEqual(len(report.breaches), 1)
        self.assertTrue(report.breaches[0].startswith("JITTER_STD_OVER_CEILING"))

    def test_p99_budget_is_off_by_default_and_enforced_when_set(self):
        delays = [1.0] * 95 + [12.0] * 5  # nearest-rank P99 = 12.0 ms, sigma ~ 2.4 ms
        samples = make_samples(delays)

        default_report = NetworkJitterImpactAnalyzerEngine().analyze_jitter_impact(samples)
        self.assertEqual(default_report.status, STATUS_HEALTHY)

        budgeted = NetworkJitterImpactAnalyzerEngine(
            JitterSimulationConfig(max_p99_latency_ms=5.0)
        ).analyze_jitter_impact(samples)
        self.assertEqual(budgeted.status, STATUS_HIGH_RISK)
        self.assertTrue(any(b.startswith("P99_LATENCY_OVER_BUDGET")
                            for b in budgeted.breaches))

    def test_budget_comparison_uses_unrounded_values(self):
        # A P99 of 5.0004 ms against a 5 ms budget is a breach, even though the report
        # displays it as 5.0 after rounding to 3 dp.
        delays = [5.0] * 95 + [5.0004] * 5
        report = NetworkJitterImpactAnalyzerEngine(
            JitterSimulationConfig(max_p99_latency_ms=5.0)
        ).analyze_jitter_impact(make_samples(delays))

        self.assertEqual(report.p99_latency_ms, 5.0)
        self.assertEqual(report.status, STATUS_HIGH_RISK)
        self.assertTrue(any(b.startswith("P99_LATENCY_OVER_BUDGET")
                            for b in report.breaches))


class TestSampleSufficiency(unittest.TestCase):

    def setUp(self):
        self.engine = NetworkJitterImpactAnalyzerEngine()

    def test_short_clean_window_is_not_measured_rather_than_approved(self):
        report = self.engine.analyze_jitter_impact(make_samples([2.0, 2.1] * 20))  # n=40
        self.assertEqual(report.status, STATUS_INSUFFICIENT_SAMPLES)
        self.assertFalse(report.is_jitter_acceptable)
        self.assertFalse(report.is_p99_resolvable)
        self.assertTrue(any(b.startswith("P99_NOT_RESOLVABLE") for b in report.breaches))

    def test_a_breach_is_reported_even_on_a_short_window(self):
        # Only 10 packets, but sigma is unambiguously over budget. An observed breach
        # needs no resolution guarantee -- it was observed.
        report = self.engine.analyze_jitter_impact(make_samples([1.0, 20.0] * 5))
        self.assertEqual(report.status, STATUS_HIGH_RISK)
        self.assertFalse(report.is_p99_resolvable)

    def test_exactly_one_hundred_packets_resolves_p99(self):
        report = self.engine.analyze_jitter_impact(make_samples([2.0] * 100))
        self.assertTrue(report.is_p99_resolvable)
        self.assertEqual(report.status, STATUS_HEALTHY)

    def test_ninety_nine_packets_does_not(self):
        report = self.engine.analyze_jitter_impact(make_samples([2.0] * 99))
        self.assertFalse(report.is_p99_resolvable)
        self.assertEqual(report.status, STATUS_INSUFFICIENT_SAMPLES)

    def test_single_packet_window_is_rejected_not_reported_as_jitter_free(self):
        with self.assertRaises(JitterSampleError):
            self.engine.analyze_jitter_impact(make_samples([2.0]))


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = NetworkJitterImpactAnalyzerEngine()

    def test_empty_series_rejected(self):
        with self.assertRaises(JitterSampleError):
            self.engine.analyze_jitter_impact([])

    def test_empty_series_still_raises_value_error_for_v1_callers(self):
        with self.assertRaises(ValueError):
            self.engine.analyze_jitter_impact([])

    def test_negative_delay_rejects_the_whole_window(self):
        # One reordered/clock-skewed packet among 99 good ones. The good samples came
        # from the same pair of disagreeing clocks, so the window is unusable.
        samples = make_samples([2.0] * 99)
        samples.append(LatencyPacketSample("PKT_BAD", T0, T0 - 1))
        with self.assertRaises(JitterSampleError) as ctx:
            self.engine.analyze_jitter_impact(samples)
        self.assertIn("clocks disagree", str(ctx.exception))

    def test_nan_timestamp_rejected_rather_than_silently_passing(self):
        samples = make_samples([2.0] * 99)
        samples.append(LatencyPacketSample("PKT_NAN", T0, float("nan")))
        with self.assertRaises(JitterSampleError):
            self.engine.analyze_jitter_impact(samples)

    def test_infinite_timestamp_rejected(self):
        samples = make_samples([2.0] * 99)
        samples.append(LatencyPacketSample("PKT_INF", T0, float("inf")))
        with self.assertRaises(JitterSampleError):
            self.engine.analyze_jitter_impact(samples)

    def test_boolean_timestamp_rejected(self):
        with self.assertRaises(JitterSampleError):
            self.engine.analyze_jitter_impact(
                [LatencyPacketSample("A", True, 5), LatencyPacketSample("B", 0, 5)]
            )

    def test_non_numeric_timestamp_rejected(self):
        with self.assertRaises(JitterSampleError):
            self.engine.analyze_jitter_impact(
                [LatencyPacketSample("A", "0", 5), LatencyPacketSample("B", 0, 5)]
            )

    def test_implausible_delay_rejected_as_a_unit_error(self):
        # A delay supplied in nanoseconds where nanosecond *timestamps* were expected.
        samples = make_samples([2.0] * 99)
        samples.append(LatencyPacketSample("PKT_UNIT", 0, 10**25))
        with self.assertRaises(JitterSampleError) as ctx:
            self.engine.analyze_jitter_impact(samples)
        self.assertIn("nanoseconds", str(ctx.exception))

    def test_duplicate_packet_ids_warn_but_do_not_reject(self):
        samples = [LatencyPacketSample("SAME", T0, T0 + 2 * NS_PER_MS) for _ in range(100)]
        with self.assertLogs(
            "network_jitter_impact_on_strategy_performance", level="WARNING"
        ) as logs:
            report = self.engine.analyze_jitter_impact(samples)
        self.assertEqual(report.total_packets_analyzed, 100)
        self.assertTrue(any("duplicate packet_id" in line for line in logs.output))


class TestConfigValidation(unittest.TestCase):

    def test_non_positive_gamma_rejected_instead_of_a_sentinel_tolerance(self):
        # v1.0.0 substituted a tolerance of 999.0 ms here, which approved every window.
        for gamma in (0.0, -0.5):
            with self.assertRaises(JitterConfigError):
                JitterSimulationConfig(jitter_penalty_coeff=gamma)

    def test_floor_above_base_sharpe_rejected(self):
        with self.assertRaises(JitterConfigError):
            JitterSimulationConfig(base_sharpe=1.0, target_sharpe_min=2.0)

    def test_floor_equal_to_base_sharpe_is_allowed_with_a_zero_tolerance(self):
        cfg = JitterSimulationConfig(base_sharpe=2.0, target_sharpe_min=2.0,
                                     jitter_penalty_coeff=0.5)
        self.assertEqual(cfg.max_jitter_tolerance_ms, 0.0)

    def test_non_positive_jitter_ceiling_rejected(self):
        with self.assertRaises(JitterConfigError):
            JitterSimulationConfig(max_acceptable_jitter_ms=0.0)

    def test_non_finite_parameter_rejected(self):
        with self.assertRaises(JitterConfigError):
            JitterSimulationConfig(base_sharpe=float("nan"))
        with self.assertRaises(JitterConfigError):
            JitterSimulationConfig(jitter_penalty_coeff=float("inf"))

    def test_invalid_p99_budget_rejected(self):
        for bad in (0.0, -1.0, float("nan"), "5"):
            with self.assertRaises(JitterConfigError):
                JitterSimulationConfig(max_p99_latency_ms=bad)

    def test_gamma_reassigned_after_construction_raises_config_error(self):
        # JitterSimulationConfig is a mutable dataclass, so __post_init__ can be
        # bypassed. The tolerance property must not divide by zero.
        cfg = JitterSimulationConfig()
        cfg.jitter_penalty_coeff = 0.0
        with self.assertRaises(JitterConfigError):
            _ = cfg.max_jitter_tolerance_ms
        with self.assertRaises(JitterConfigError):
            NetworkJitterImpactAnalyzerEngine(cfg).analyze_jitter_impact(
                make_samples([2.0] * 100)
            )

    def test_none_p99_budget_is_valid(self):
        self.assertIsNone(JitterSimulationConfig(max_p99_latency_ms=None).max_p99_latency_ms)


class TestReportContract(unittest.TestCase):

    def test_v1_field_order_still_supports_positional_construction(self):
        report = JitterImpactReport(
            100, 2.0, 0.1, 2.0, 2.1, 2.2, 2.45, 3.0, True, STATUS_HEALTHY, "notes"
        )
        self.assertEqual(report.total_packets_analyzed, 100)
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(report.sharpe_model, SHARPE_MODEL_LINEAR)
        self.assertEqual(report.breaches, [])

    def test_audit_notes_name_the_estimator_and_the_model(self):
        report = NetworkJitterImpactAnalyzerEngine().analyze_jitter_impact(
            make_samples([2.0] * 100)
        )
        self.assertIn("nearest rank", report.audit_notes)
        self.assertIn(SHARPE_MODEL_LINEAR, report.audit_notes)

    def test_audit_tail_percentile_constant_matches_the_reported_field(self):
        self.assertEqual(AUDIT_TAIL_PERCENTILE, 99.0)


if __name__ == "__main__":
    unittest.main()
