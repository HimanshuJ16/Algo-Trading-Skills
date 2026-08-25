import dataclasses
import logging
import unittest

from hardware_timestamping_vs_software_timestamping_accuracy import (
    NS_PER_MICROSECOND,
    NS_PER_MILLISECOND,
    RTS25_ACCURACY_REQUIREMENTS,
    RTS25_COMPLIANT,
    RTS25_NON_COMPLIANT_DIVERGENCE,
    RTS25_NON_COMPLIANT_DIVERGENCE_AND_GRANULARITY,
    RTS25_NON_COMPLIANT_GRANULARITY,
    PacketTimestampSample,
    TimestampAccuracyAnalyzerEngine,
    percentile_nearest_rank,
)

# Traceable UTC time of the packet arrival used by every fixture below. All
# expected values are derived by hand from the offsets applied to this base, not
# by re-running the engine's own arithmetic.
UTC_REF = 1_000_000_000

# The engine logs a CRITICAL line for every out-of-tolerance clock, which is the
# intended production behaviour but only noise in a test run.
logging.disable(logging.CRITICAL)


def make_sample(
    packet_id,
    hardware_offset_from_utc,
    kernel_delay,
    application_delay,
    granularity_nanos=1,
):
    """Build a sample from an offset-from-UTC plus two capture-path delays."""
    hardware = UTC_REF + hardware_offset_from_utc
    kernel = hardware + kernel_delay
    application = kernel + application_delay
    return PacketTimestampSample(
        packet_id=packet_id,
        hardware_mac_nanos=hardware,
        kernel_stack_nanos=kernel,
        application_layer_nanos=application,
        utc_reference_nanos=UTC_REF,
        timestamp_granularity_nanos=granularity_nanos,
    )


class TestLatencyDecomposition(unittest.TestCase):

    def setUp(self):
        self.engine = TimestampAccuracyAnalyzerEngine()

    def test_capture_delays_are_separated_from_clock_divergence(self):
        # Hardware clock 10 us ahead of UTC; 10 us kernel stack delay; 115 us of
        # application delay on top. The 125 us total is ELAPSED TIME, not clock
        # error: the hardware clock divergence is still only +10,000 ns.
        report = self.engine.analyze_sample(make_sample("PKT_001", 10_000, 10_000, 115_000))

        self.assertEqual(report.kernel_capture_delay_nanos, 10_000)
        self.assertEqual(report.application_capture_delay_nanos, 115_000)
        self.assertEqual(report.software_capture_delay_nanos, 125_000)
        self.assertEqual(report.hardware_clock_divergence_nanos, 10_000)
        # 10,000 (divergence) + 125,000 (capture path) = 135,000.
        self.assertEqual(report.application_timestamp_error_nanos, 135_000)
        self.assertTrue(report.hardware_within_divergence_limit)
        self.assertFalse(report.application_within_divergence_limit)
        self.assertEqual(report.status, "HARDWARE_WITHIN_LIMIT_APPLICATION_EXCEEDS")

    def test_divergence_sign_is_preserved(self):
        # A clock running BEHIND UTC and one running AHEAD are different faults;
        # abs() would report both as the same number.
        behind = self.engine.analyze_sample(make_sample("PKT_BEHIND", -7_000, 1_000, 1_000))
        ahead = self.engine.analyze_sample(make_sample("PKT_AHEAD", 7_000, 1_000, 1_000))

        self.assertEqual(behind.hardware_clock_divergence_nanos, -7_000)
        self.assertEqual(ahead.hardware_clock_divergence_nanos, 7_000)
        self.assertTrue(behind.hardware_within_divergence_limit)
        self.assertTrue(ahead.hardware_within_divergence_limit)

    def test_kernel_and_application_errors_are_reported_per_layer(self):
        report = self.engine.analyze_sample(make_sample("PKT_LAYERS", 2_000, 3_000, 4_000))
        self.assertEqual(report.hardware_clock_divergence_nanos, 2_000)
        self.assertEqual(report.kernel_timestamp_error_nanos, 5_000)
        self.assertEqual(report.application_timestamp_error_nanos, 9_000)


class TestTimebaseValidation(unittest.TestCase):

    def setUp(self):
        self.engine = TimestampAccuracyAnalyzerEngine()

    def test_kernel_before_hardware_is_rejected(self):
        # Signature of an undisciplined NIC PHC: the kernel appears to have seen
        # the packet 40 us before the MAC did. Previously this produced a
        # negative "kernel stack latency" that was reported as a valid figure.
        sample = PacketTimestampSample(
            packet_id="PKT_PHC_SKEW",
            hardware_mac_nanos=UTC_REF + 40_000,
            kernel_stack_nanos=UTC_REF,
            application_layer_nanos=UTC_REF + 60_000,
            utc_reference_nanos=UTC_REF,
        )
        with self.assertRaises(ValueError) as ctx:
            self.engine.analyze_sample(sample)
        self.assertIn("common timebase", str(ctx.exception))

    def test_application_before_kernel_is_rejected(self):
        sample = PacketTimestampSample(
            packet_id="PKT_APP_SKEW",
            hardware_mac_nanos=UTC_REF,
            kernel_stack_nanos=UTC_REF + 50_000,
            application_layer_nanos=UTC_REF + 10_000,
            utc_reference_nanos=UTC_REF,
        )
        with self.assertRaises(ValueError):
            self.engine.analyze_sample(sample)

    def test_zero_delay_between_layers_is_accepted(self):
        report = self.engine.analyze_sample(make_sample("PKT_ZERO", 0, 0, 0))
        self.assertEqual(report.software_capture_delay_nanos, 0)
        self.assertEqual(report.rts25_verdict, RTS25_COMPLIANT)

    def test_non_int_timestamps_are_rejected(self):
        with self.assertRaises(TypeError):
            PacketTimestampSample("PKT", 1.0, 2, 3, 4)
        with self.assertRaises(TypeError):
            PacketTimestampSample("PKT", True, 2, 3, 4)

    def test_negative_epoch_and_blank_id_are_rejected(self):
        with self.assertRaises(ValueError):
            PacketTimestampSample("PKT", -1, 2, 3, 4)
        with self.assertRaises(ValueError):
            PacketTimestampSample("   ", 1, 2, 3, 4)

    def test_non_sample_input_is_rejected(self):
        with self.assertRaises(TypeError):
            TimestampAccuracyAnalyzerEngine().analyze_sample({"packet_id": "PKT"})


class TestFourStateComparison(unittest.TestCase):

    def setUp(self):
        self.engine = TimestampAccuracyAnalyzerEngine()

    def test_both_within_limit(self):
        report = self.engine.analyze_sample(make_sample("PKT_002", 5_000, 5_000, 15_000))
        self.assertTrue(report.hardware_within_divergence_limit)
        self.assertTrue(report.application_within_divergence_limit)
        self.assertEqual(report.status, "BOTH_WITHIN_DIVERGENCE_LIMIT")
        self.assertEqual(report.rts25_verdict, RTS25_COMPLIANT)

    def test_hardware_exceeds_while_application_is_within_limit(self):
        # Regression: the hardware clock is 150 us BEHIND UTC (a clear breach)
        # but 120 us of capture delay pulls the application timestamp back to
        # -30 us, inside the limit. The earlier three-state classifier reported
        # "BOTH_NON_COMPLIANT" here and asserted in the audit note that both
        # layers exceeded 100 us - a false statement in a compliance artifact.
        report = self.engine.analyze_sample(make_sample("PKT_003", -150_000, 60_000, 60_000))

        self.assertEqual(report.hardware_clock_divergence_nanos, -150_000)
        self.assertEqual(report.application_timestamp_error_nanos, -30_000)
        self.assertFalse(report.hardware_within_divergence_limit)
        self.assertTrue(report.application_within_divergence_limit)
        self.assertEqual(report.status, "HARDWARE_EXCEEDS_APPLICATION_WITHIN_LIMIT")
        self.assertEqual(report.rts25_verdict, RTS25_NON_COMPLIANT_DIVERGENCE)
        self.assertNotIn("Both", report.audit_notes)

    def test_both_exceed_limit(self):
        report = self.engine.analyze_sample(make_sample("PKT_004", 250_000, 10_000, 10_000))
        self.assertFalse(report.hardware_within_divergence_limit)
        self.assertFalse(report.application_within_divergence_limit)
        self.assertEqual(report.status, "BOTH_EXCEED_DIVERGENCE_LIMIT")


class TestDivergenceBoundary(unittest.TestCase):

    def setUp(self):
        self.engine = TimestampAccuracyAnalyzerEngine()

    def test_exactly_at_the_limit_is_within_tolerance(self):
        report = self.engine.analyze_sample(make_sample("PKT_EDGE", 100_000, 0, 0))
        self.assertEqual(report.hardware_clock_divergence_nanos, 100_000)
        self.assertTrue(report.hardware_within_divergence_limit)

    def test_one_nanosecond_past_the_limit_breaches(self):
        report = self.engine.analyze_sample(make_sample("PKT_EDGE_PLUS", 100_001, 0, 0))
        self.assertFalse(report.hardware_within_divergence_limit)

    def test_negative_side_of_the_limit_is_symmetric(self):
        self.assertTrue(
            self.engine.analyze_sample(
                make_sample("PKT_NEG_EDGE", -100_000, 0, 0)
            ).hardware_within_divergence_limit
        )
        self.assertFalse(
            self.engine.analyze_sample(
                make_sample("PKT_NEG_OVER", -100_001, 0, 0)
            ).hardware_within_divergence_limit
        )


class TestRecordedTimestampSource(unittest.TestCase):

    def test_verdict_follows_the_declared_timestamping_point(self):
        # Same packet, same clocks. Whether the firm is compliant depends on
        # which layer's value it writes into the reportable record - the
        # question RTS 25 Art. 4 requires to be documented.
        sample = make_sample("PKT_005", 10_000, 10_000, 115_000)

        from_hardware = TimestampAccuracyAnalyzerEngine(
            recorded_timestamp_source="HARDWARE_MAC"
        ).analyze_sample(sample)
        from_application = TimestampAccuracyAnalyzerEngine(
            recorded_timestamp_source="APPLICATION"
        ).analyze_sample(sample)

        self.assertEqual(from_hardware.recorded_timestamp_error_nanos, 10_000)
        self.assertEqual(from_hardware.rts25_verdict, RTS25_COMPLIANT)
        self.assertEqual(from_application.recorded_timestamp_error_nanos, 135_000)
        self.assertEqual(from_application.rts25_verdict, RTS25_NON_COMPLIANT_DIVERGENCE)

    def test_kernel_source_is_supported(self):
        engine = TimestampAccuracyAnalyzerEngine(recorded_timestamp_source="KERNEL_STACK")
        report = engine.analyze_sample(make_sample("PKT_006", 4_000, 6_000, 90_000))
        self.assertEqual(report.recorded_timestamp_error_nanos, 10_000)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            TimestampAccuracyAnalyzerEngine(recorded_timestamp_source="NIC_PHY_EGRESS")


class TestGranularityRequirement(unittest.TestCase):

    def test_millisecond_granularity_fails_the_hft_row(self):
        # RTS 25 Annex Table 2 bounds granularity (1 us or better for the
        # high-frequency technique) independently of divergence: a perfectly
        # disciplined clock recorded into a millisecond field still fails.
        engine = TimestampAccuracyAnalyzerEngine()
        report = engine.analyze_sample(
            make_sample("PKT_007", 1_000, 1_000, 1_000, granularity_nanos=NS_PER_MILLISECOND)
        )
        self.assertTrue(report.recorded_within_divergence_limit)
        self.assertFalse(report.granularity_sufficient)
        self.assertEqual(report.rts25_verdict, RTS25_NON_COMPLIANT_GRANULARITY)

    def test_both_bounds_can_fail_together(self):
        engine = TimestampAccuracyAnalyzerEngine()
        report = engine.analyze_sample(
            make_sample("PKT_008", 400_000, 0, 0, granularity_nanos=NS_PER_MILLISECOND)
        )
        self.assertEqual(report.rts25_verdict, RTS25_NON_COMPLIANT_DIVERGENCE_AND_GRANULARITY)

    def test_microsecond_granularity_exactly_meets_the_hft_row(self):
        engine = TimestampAccuracyAnalyzerEngine()
        report = engine.analyze_sample(
            make_sample("PKT_009", 1_000, 0, 0, granularity_nanos=NS_PER_MICROSECOND)
        )
        self.assertTrue(report.granularity_sufficient)
        self.assertEqual(report.rts25_verdict, RTS25_COMPLIANT)


class TestActivityTiers(unittest.TestCase):

    def test_annex_figures(self):
        # Transcribed from the RTS 25 Annex; see references/standards.md.
        self.assertEqual(
            RTS25_ACCURACY_REQUIREMENTS["HIGH_FREQUENCY_ALGORITHMIC_TRADING"].max_divergence_nanos,
            100 * NS_PER_MICROSECOND,
        )
        self.assertEqual(
            RTS25_ACCURACY_REQUIREMENTS["OTHER_TRADING_ACTIVITY"].max_divergence_nanos,
            NS_PER_MILLISECOND,
        )
        self.assertEqual(
            RTS25_ACCURACY_REQUIREMENTS["VOICE_TRADING"].granularity_nanos,
            1_000_000_000,
        )

    def test_same_sample_passes_at_the_other_activity_row(self):
        # 500 us of divergence breaches the 100 us high-frequency row but sits
        # inside the 1 ms row that applies to any other trading activity.
        sample = make_sample("PKT_010", 500_000, 0, 0, granularity_nanos=NS_PER_MILLISECOND)

        hft = TimestampAccuracyAnalyzerEngine("HIGH_FREQUENCY_ALGORITHMIC_TRADING")
        other = TimestampAccuracyAnalyzerEngine("OTHER_TRADING_ACTIVITY")

        self.assertEqual(
            hft.analyze_sample(sample).rts25_verdict,
            RTS25_NON_COMPLIANT_DIVERGENCE_AND_GRANULARITY,
        )
        self.assertEqual(other.analyze_sample(sample).rts25_verdict, RTS25_COMPLIANT)

    def test_unknown_activity_is_rejected(self):
        with self.assertRaises(ValueError):
            TimestampAccuracyAnalyzerEngine("SCALPING")

    def test_override_may_tighten_but_never_relax_the_annex_bound(self):
        tightened = TimestampAccuracyAnalyzerEngine(max_divergence_nanos=50_000)
        self.assertEqual(tightened.max_divergence_nanos, 50_000)
        self.assertFalse(
            tightened.analyze_sample(
                make_sample("PKT_011", 60_000, 0, 0)
            ).hardware_within_divergence_limit
        )

        with self.assertRaises(ValueError):
            TimestampAccuracyAnalyzerEngine(max_divergence_nanos=200_000)
        with self.assertRaises(ValueError):
            TimestampAccuracyAnalyzerEngine(required_granularity_nanos=NS_PER_MILLISECOND)


class TestPercentileNearestRank(unittest.TestCase):

    def test_nearest_rank_returns_observed_values(self):
        values = [10, 20, 30, 40, 50]
        # ceil(0.50 * 5) = 3 -> third smallest.
        self.assertEqual(percentile_nearest_rank(values, 50), 30)
        # ceil(0.99 * 5) = 5 -> largest.
        self.assertEqual(percentile_nearest_rank(values, 99), 50)
        self.assertEqual(percentile_nearest_rank(values, 100), 50)
        # ceil(0.01 * 5) = 1 -> smallest.
        self.assertEqual(percentile_nearest_rank(values, 1), 10)

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            percentile_nearest_rank([], 50)
        with self.assertRaises(ValueError):
            percentile_nearest_rank([1, 2], 0)
        with self.assertRaises(ValueError):
            percentile_nearest_rank([1, 2], 101)


class TestBatchBenchmark(unittest.TestCase):

    def setUp(self):
        self.engine = TimestampAccuracyAnalyzerEngine()
        # Divergences +1, -2, +3, -4, +5 us; capture delays 10/20/30/40/500 us
        # split evenly between the kernel and application legs.
        self.samples = [
            make_sample("PKT_A", 1_000, 5_000, 5_000),
            make_sample("PKT_B", -2_000, 10_000, 10_000),
            make_sample("PKT_C", 3_000, 15_000, 15_000),
            make_sample("PKT_D", -4_000, 20_000, 20_000),
            make_sample("PKT_E", 5_000, 250_000, 250_000),
        ]

    def test_distribution_figures(self):
        summary = self.engine.analyze_batch(self.samples)

        self.assertEqual(summary.sample_count, 5)
        # |divergence| sorted = [1000, 2000, 3000, 4000, 5000].
        self.assertEqual(summary.hardware_divergence_abs_p50_nanos, 3_000)
        self.assertEqual(summary.hardware_divergence_abs_p99_nanos, 5_000)
        self.assertEqual(summary.hardware_divergence_abs_max_nanos, 5_000)
        # kernel delays sorted = [5000, 10000, 15000, 20000, 250000].
        self.assertEqual(summary.kernel_capture_delay_p50_nanos, 15_000)
        self.assertEqual(summary.kernel_capture_delay_p99_nanos, 250_000)
        # software delays sorted = [10000, 20000, 30000, 40000, 500000].
        self.assertEqual(summary.software_capture_delay_p50_nanos, 30_000)
        self.assertEqual(summary.software_capture_delay_p99_nanos, 500_000)
        self.assertEqual(summary.software_capture_delay_min_nanos, 10_000)
        self.assertEqual(summary.software_capture_delay_max_nanos, 500_000)
        self.assertEqual(summary.software_capture_delay_jitter_peak_to_peak_nanos, 490_000)

    def test_compliance_counts_and_worst_offender(self):
        summary = self.engine.analyze_batch(self.samples)
        self.assertEqual(summary.divergence_breach_count, 0)
        self.assertEqual(summary.granularity_breach_count, 0)
        self.assertEqual(summary.rts25_compliant_sample_count, 5)
        self.assertEqual(summary.worst_recorded_error_packet_id, "PKT_E")
        self.assertEqual(len(summary.reports), 5)

    def test_a_single_tail_breach_is_not_hidden_by_a_healthy_median(self):
        # Four well-behaved packets and one 300 us clock excursion: the median
        # divergence is 2 us, but RTS 25 bounds every timestamp, so the batch
        # must still surface one breach.
        samples = self.samples[:4] + [make_sample("PKT_TAIL", 300_000, 1_000, 1_000)]
        summary = self.engine.analyze_batch(samples)

        self.assertEqual(summary.hardware_divergence_abs_p50_nanos, 3_000)
        self.assertEqual(summary.divergence_breach_count, 1)
        self.assertEqual(summary.rts25_compliant_sample_count, 4)
        self.assertEqual(summary.worst_recorded_error_packet_id, "PKT_TAIL")

    def test_granularity_breaches_are_counted_separately(self):
        samples = [
            make_sample("PKT_G1", 1_000, 0, 0, granularity_nanos=NS_PER_MILLISECOND),
            make_sample("PKT_G2", 1_000, 0, 0, granularity_nanos=NS_PER_MICROSECOND),
        ]
        summary = self.engine.analyze_batch(samples)
        self.assertEqual(summary.divergence_breach_count, 0)
        self.assertEqual(summary.granularity_breach_count, 1)
        self.assertEqual(summary.rts25_compliant_sample_count, 1)

    def test_empty_batch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.analyze_batch([])


class TestMisuseHardening(unittest.TestCase):
    """Behaviours that protect the engine from an agent or caller misusing it."""

    def setUp(self):
        self.engine = TimestampAccuracyAnalyzerEngine()

    def test_sample_cannot_be_mutated_past_validation(self):
        # A mutable record could be constructed with valid ints and then have a
        # float assigned to it, reaching the arithmetic unchecked.
        sample = make_sample("PKT_FROZEN", 1_000, 1_000, 1_000)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            sample.hardware_mac_nanos = 1.5

    def test_batch_accepts_a_generator_and_still_rejects_an_empty_one(self):
        # `if not samples` is always False for a generator, so an exhausted one
        # would otherwise reach the percentile step and raise IndexError.
        summary = self.engine.analyze_batch(
            make_sample(f"PKT_GEN_{i}", 1_000, 1_000, 1_000) for i in range(3)
        )
        self.assertEqual(summary.sample_count, 3)
        with self.assertRaises(ValueError):
            self.engine.analyze_batch(x for x in [])

    def test_batch_does_not_emit_one_log_line_per_packet(self):
        # A clock failure across a large capture must not flood the log with one
        # CRITICAL line per packet during the incident whose logs matter.
        logging.disable(logging.NOTSET)
        try:
            samples = [make_sample(f"PKT_LOUD_{i}", 400_000, 0, 0) for i in range(20)]
            with self.assertLogs(
                "hardware_timestamping_vs_software_timestamping_accuracy", level="WARNING"
            ) as captured:
                self.engine.analyze_batch(samples)
            self.assertEqual(len(captured.records), 1)
            self.assertIn("TIMESTAMP BENCHMARK", captured.records[0].getMessage())
        finally:
            logging.disable(logging.CRITICAL)

    def test_single_sample_still_logs_by_default(self):
        logging.disable(logging.NOTSET)
        try:
            with self.assertLogs(
                "hardware_timestamping_vs_software_timestamping_accuracy", level="CRITICAL"
            ) as captured:
                self.engine.analyze_sample(make_sample("PKT_LOG", 400_000, 0, 0))
            self.assertEqual(len(captured.records), 1)
        finally:
            logging.disable(logging.CRITICAL)


if __name__ == '__main__':
    unittest.main()
