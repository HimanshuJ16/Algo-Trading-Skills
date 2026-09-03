import unittest

from tick_to_trade_latency_measurement import (
    PERCENTILE_LINEAR,
    PERCENTILE_NEAREST_RANK,
    STAGE_ORDER,
    STATUS_APPROVED,
    STATUS_BREACHED,
    STATUS_INSUFFICIENT_SAMPLES,
    STATUS_NOT_AUDITED,
    LatencyError,
    LatencySample,
    LatencyStage,
    SLAConfig,
    TickToTradeLatencyEngine,
    is_percentile_resolvable,
    min_samples_for_percentile,
    rank_for_percentile,
)

BASE_NS = 1_000_000_000

#: Five stage durations in ns: NIC ingress, decode, strategy, serialize, NIC egress.
#: Deliberately all different so a one-position label shift cannot pass unnoticed.
FAST_STAGES_NS = (300, 500, 1_000, 400, 300)  # total 2,500 ns = 2.5 us


def make_sample(sample_id, stage_deltas_ns, base_ns=BASE_NS, symbol="AAPL"):
    """Build a sample from five stage durations, so tests state durations directly."""
    t = base_ns
    cuts = [t]
    for delta in stage_deltas_ns:
        t += delta
        cuts.append(t)
    return LatencySample(sample_id, symbol, *cuts)


class TestLatencySampleValidation(unittest.TestCase):
    def test_valid_sample_totals_and_stage_deltas(self):
        sample = make_sample("SMPL-1", FAST_STAGES_NS)
        self.assertEqual(sample.total_t2t_ns, 2_500)
        self.assertEqual(sample.total_t2t_us, 2.5)

    def test_stage_deltas_are_labelled_with_their_own_stage(self):
        """Regression: revision 1.1.0 shifted every stage label by one position.

        Each stage duration here is unique, so any shift misattributes a value,
        and NIC_EGRESS was absent from the report entirely.
        """
        deltas = make_sample("SMPL-LABELS", FAST_STAGES_NS).stage_deltas_ns()
        self.assertEqual(deltas[LatencyStage.NIC_INGRESS], 300)
        self.assertEqual(deltas[LatencyStage.DECODER_PARSING], 500)
        self.assertEqual(deltas[LatencyStage.STRATEGY_EVALUATION], 1_000)
        self.assertEqual(deltas[LatencyStage.ORDER_SERIALIZATION], 400)
        self.assertEqual(deltas[LatencyStage.NIC_EGRESS], 300)
        self.assertEqual(sum(deltas.values()), 2_500)

    def test_non_monotonic_timestamps_raise(self):
        with self.assertRaises(LatencyError):
            LatencySample("SMPL-BAD", "AAPL", 1_000, 900, 2_000, 3_000, 4_000, 5_000)

    def test_negative_egress_stage_raises(self):
        """The last stage going backwards must raise, not report a negative latency."""
        with self.assertRaises(LatencyError):
            LatencySample("SMPL-BACK", "AAPL", 1_000, 2_000, 3_000, 4_000, 5_000, 4_500)

    def test_zero_length_stage_is_accepted(self):
        sample = make_sample("SMPL-ZERO", (300, 0, 1_000, 400, 300))
        self.assertEqual(sample.stage_deltas_ns()[LatencyStage.DECODER_PARSING], 0)

    def test_float_timestamp_raises(self):
        with self.assertRaises(LatencyError):
            LatencySample("SMPL-F", "AAPL", 1_000.0, 2_000, 3_000, 4_000, 5_000, 6_000)

    def test_bool_timestamp_raises(self):
        """bool is a subclass of int; True must not be accepted as the timestamp 1."""
        with self.assertRaises(LatencyError):
            LatencySample("SMPL-B", "AAPL", True, 2_000, 3_000, 4_000, 5_000, 6_000)

    def test_negative_timestamp_raises(self):
        with self.assertRaises(LatencyError):
            LatencySample("SMPL-N", "AAPL", -1, 2_000, 3_000, 4_000, 5_000, 6_000)

    def test_implausible_timestamp_raises(self):
        with self.assertRaises(LatencyError):
            LatencySample("SMPL-BIG", "AAPL", 1_000, 2_000, 3_000, 4_000, 5_000, 10 ** 20)

    def test_blank_identity_fields_raise(self):
        with self.assertRaises(LatencyError):
            make_sample("   ", FAST_STAGES_NS)
        with self.assertRaises(LatencyError):
            make_sample("SMPL-S", FAST_STAGES_NS, symbol="")

    def test_validate_is_idempotent_and_rechecks_mutation(self):
        sample = make_sample("SMPL-MUT", FAST_STAGES_NS)
        sample.validate()
        sample.egress_ns = sample.ingress_ns - 1
        with self.assertRaises(LatencyError):
            sample.validate()


class TestPercentileArithmetic(unittest.TestCase):
    """Expected values derived by hand from the rank rule, not from the code."""

    def setUp(self):
        self.one_to_hundred = [float(i) for i in range(1, 101)]

    def test_nearest_rank_returns_observed_values(self):
        pct = TickToTradeLatencyEngine.calculate_percentile
        self.assertEqual(pct(self.one_to_hundred, 50.0), 50.0)
        self.assertEqual(pct(self.one_to_hundred, 90.0), 90.0)
        self.assertEqual(pct(self.one_to_hundred, 99.0), 99.0)
        # P99.9 over 100 samples is the maximum wearing a percentile's name.
        self.assertEqual(pct(self.one_to_hundred, 99.9), 100.0)

    def test_linear_interpolation_preserved_as_an_option(self):
        vals = [float(i) for i in range(1, 11)]
        pct = TickToTradeLatencyEngine.calculate_percentile
        self.assertAlmostEqual(pct(vals, 50.0, PERCENTILE_LINEAR), 5.5)
        self.assertAlmostEqual(pct(vals, 90.0, PERCENTILE_LINEAR), 9.1)
        # Nearest rank never returns a value between two observations.
        self.assertEqual(pct(vals, 50.0, PERCENTILE_NEAREST_RANK), 5.0)

    def test_linear_can_report_an_unobserved_latency(self):
        bimodal = [10.0] * 500 + [900.0] * 500
        pct = TickToTradeLatencyEngine.calculate_percentile
        self.assertAlmostEqual(pct(bimodal, 50.0, PERCENTILE_LINEAR), 455.0)
        self.assertEqual(pct(bimodal, 50.0, PERCENTILE_NEAREST_RANK), 10.0)

    def test_empty_sequence_raises_rather_than_returning_zero(self):
        with self.assertRaises(LatencyError):
            TickToTradeLatencyEngine.calculate_percentile([], 50.0)

    def test_out_of_range_percentile_raises(self):
        with self.assertRaises(LatencyError):
            TickToTradeLatencyEngine.calculate_percentile([1.0], 100.5)

    def test_unknown_method_raises(self):
        with self.assertRaises(LatencyError):
            TickToTradeLatencyEngine.calculate_percentile([1.0], 50.0, "MEDIAN_ISH")

    def test_rank_and_resolution_boundaries(self):
        self.assertEqual(rank_for_percentile(1_000, 99.9), 999)
        self.assertTrue(is_percentile_resolvable(1_000, 99.9))
        self.assertFalse(is_percentile_resolvable(999, 99.9))
        self.assertEqual(min_samples_for_percentile(99.9), 1_000)
        self.assertEqual(min_samples_for_percentile(99.0), 100)

    def test_std_dev_matches_hand_computed_value(self):
        # Sample std dev (n-1) of [1, 2, 3, 4, 5]: variance = 10/4 = 2.5.
        self.assertAlmostEqual(
            TickToTradeLatencyEngine.calculate_std_dev([1.0, 2.0, 3.0, 4.0, 5.0], 3.0),
            2.5 ** 0.5,
        )
        self.assertEqual(TickToTradeLatencyEngine.calculate_std_dev([7.0], 7.0), 0.0)


class TestStageBreakdown(unittest.TestCase):
    def setUp(self):
        self.engine = TickToTradeLatencyEngine()
        for i in range(100):
            self.engine.record_sample(
                make_sample(f"SMPL-{i}", FAST_STAGES_NS, base_ns=BASE_NS + i * 10_000)
            )

    def test_all_five_stages_reported(self):
        summary = self.engine.evaluate_latency_distribution()
        self.assertEqual(set(summary.stage_breakdowns), set(STAGE_ORDER))

    def test_stage_shares_are_hand_derivable_and_sum_to_100(self):
        summary = self.engine.evaluate_latency_distribution()
        self.assertAlmostEqual(summary.t2t_avg_us, 2.5)
        expected = {
            LatencyStage.NIC_INGRESS: 12.0,          # 0.3 / 2.5
            LatencyStage.DECODER_PARSING: 20.0,      # 0.5 / 2.5
            LatencyStage.STRATEGY_EVALUATION: 40.0,  # 1.0 / 2.5
            LatencyStage.ORDER_SERIALIZATION: 16.0,  # 0.4 / 2.5
            LatencyStage.NIC_EGRESS: 12.0,           # 0.3 / 2.5
        }
        for stage, share in expected.items():
            self.assertAlmostEqual(summary.stage_breakdowns[stage].percentage_of_total, share)
        self.assertAlmostEqual(
            sum(b.percentage_of_total for b in summary.stage_breakdowns.values()), 100.0
        )

    def test_stage_means_sum_to_total_mean(self):
        summary = self.engine.evaluate_latency_distribution()
        self.assertAlmostEqual(
            sum(b.avg_us for b in summary.stage_breakdowns.values()), summary.t2t_avg_us
        )

    def test_noise_floor_flag_marks_unmeasurable_stages(self):
        summary = self.engine.evaluate_latency_distribution(
            SLAConfig(timestamp_uncertainty_us=0.5)
        )
        self.assertTrue(summary.stage_breakdowns[LatencyStage.NIC_INGRESS].below_noise_floor)
        self.assertTrue(summary.stage_breakdowns[LatencyStage.NIC_EGRESS].below_noise_floor)
        self.assertFalse(
            summary.stage_breakdowns[LatencyStage.STRATEGY_EVALUATION].below_noise_floor
        )

    def test_noise_floor_off_by_default(self):
        summary = self.engine.evaluate_latency_distribution(SLAConfig())
        self.assertFalse(any(b.below_noise_floor for b in summary.stage_breakdowns.values()))


class TestTailAttribution(unittest.TestCase):
    def setUp(self):
        self.engine = TickToTradeLatencyEngine()
        # 99 fast samples plus one whose strategy stage alone stalls to 20 us.
        for i in range(99):
            self.engine.record_sample(
                make_sample(f"FAST-{i}", FAST_STAGES_NS, base_ns=BASE_NS + i * 100_000)
            )
        self.engine.record_sample(
            make_sample("STALL", (300, 500, 20_000, 400, 300), base_ns=BASE_NS + 99 * 100_000)
        )

    def test_dominant_tail_stage_is_the_stalling_stage(self):
        summary = self.engine.evaluate_latency_distribution()
        tail = summary.tail_attribution
        self.assertIsNotNone(tail)
        self.assertEqual(tail.dominant_stage, LatencyStage.STRATEGY_EVALUATION)
        # Nearest rank for P99 over 100 samples is 99, so the tail holds 2 samples.
        self.assertEqual(tail.tail_sample_count, 2)
        self.assertEqual(tail.body_sample_count, 98)
        # Tail totals are 21.5 and 2.5 us; the body is a flat 2.5 us.
        self.assertAlmostEqual(tail.tail_mean_total_us, 12.0)
        self.assertAlmostEqual(tail.body_mean_total_us, 2.5)
        self.assertAlmostEqual(tail.total_excess_us, 9.5)

    def test_stage_excesses_sum_exactly_to_the_total_excess(self):
        """The property that makes this decomposition correct and summed P99s wrong."""
        tail = self.engine.evaluate_latency_distribution().tail_attribution
        self.assertAlmostEqual(
            sum(s.excess_us for s in tail.stages), tail.total_excess_us, places=9
        )

    def test_stalling_stage_owns_all_of_the_excess(self):
        tail = self.engine.evaluate_latency_distribution().tail_attribution
        shares = {s.stage: s.share_of_excess_pct for s in tail.stages}
        self.assertAlmostEqual(shares[LatencyStage.STRATEGY_EVALUATION], 100.0)
        self.assertAlmostEqual(shares[LatencyStage.NIC_INGRESS], 0.0)

    def test_summed_stage_p99s_badly_under_report_the_total_p99(self):
        """Documents why StageBreakdown percentiles must never be added up.

        Two stalls in *different* samples and *different* stages. Each stall is a
        1-in-100 event within its own stage, so no stage's own P99 resolves it,
        while both land in the top 2 of the totals. Summing the stage P99s
        approves a pipeline that is already 21.5 us at P99.
        """
        engine = TickToTradeLatencyEngine()
        for i in range(98):
            engine.record_sample(make_sample(f"F-{i}", FAST_STAGES_NS, base_ns=BASE_NS + i * 100_000))
        engine.record_sample(
            make_sample("STALL-STRATEGY", (300, 500, 20_000, 400, 300), base_ns=BASE_NS + 98 * 100_000)
        )
        engine.record_sample(
            make_sample("STALL-DECODE", (300, 20_000, 1_000, 400, 300), base_ns=BASE_NS + 99 * 100_000)
        )
        summary = engine.evaluate_latency_distribution()

        self.assertAlmostEqual(summary.t2t_p99_us, 21.5)
        summed = sum(b.p99_us for b in summary.stage_breakdowns.values())
        self.assertAlmostEqual(summed, 2.5)  # 0.3 + 0.5 + 1.0 + 0.4 + 0.3
        self.assertLess(summed, summary.t2t_p99_us)

    def test_tail_attribution_unavailable_on_a_single_sample(self):
        engine = TickToTradeLatencyEngine()
        engine.record_sample(make_sample("ONLY", FAST_STAGES_NS))
        summary = engine.evaluate_latency_distribution()
        self.assertIsNone(summary.tail_attribution)
        self.assertTrue(any("Tail attribution unavailable" in w for w in summary.resolution_warnings))

    def test_flat_distribution_still_splits_by_rank(self):
        """A repeated threshold value must not sweep every sample into the tail."""
        engine = TickToTradeLatencyEngine()
        for i in range(100):
            engine.record_sample(make_sample(f"FLAT-{i}", FAST_STAGES_NS, base_ns=BASE_NS + i * 10_000))
        tail = engine.evaluate_latency_distribution().tail_attribution
        self.assertIsNotNone(tail)
        self.assertEqual(tail.tail_sample_count, 2)
        self.assertAlmostEqual(tail.total_excess_us, 0.0)
        self.assertIsNone(tail.dominant_stage)


class TestSlaVerdict(unittest.TestCase):
    def _fill(self, engine, count, stages=FAST_STAGES_NS):
        for i in range(count):
            engine.record_sample(make_sample(f"S-{i}", stages, base_ns=BASE_NS + i * 100_000))

    def test_breach_is_reported_from_a_single_sample(self):
        engine = TickToTradeLatencyEngine()
        # 10 + 20 + 10 + 10 us stages plus 300 ns ingress: total 50.3 us.
        engine.record_sample(make_sample("SLOW", (300, 10_000, 20_000, 10_000, 10_000)))
        summary = engine.evaluate_latency_distribution(
            SLAConfig(max_p50_us=5.0, max_p99_us=15.0, max_p999_us=25.0, max_tail_us=25.0)
        )
        self.assertEqual(summary.sla_status, STATUS_BREACHED)
        self.assertTrue(any("P50 SLA Breach" in b for b in summary.sla_breaches))

    def test_no_breach_on_an_unresolvable_window_is_not_an_approval(self):
        engine = TickToTradeLatencyEngine()
        self._fill(engine, 100)  # P99 resolvable, P99.9 is not.
        summary = engine.evaluate_latency_distribution(SLAConfig())
        self.assertEqual(summary.sla_breaches, [])
        self.assertEqual(summary.sla_status, STATUS_INSUFFICIENT_SAMPLES)
        self.assertTrue(any("P99.9" in w for w in summary.resolution_warnings))

    def test_approval_requires_a_resolvable_window(self):
        engine = TickToTradeLatencyEngine()
        self._fill(engine, 1_000)
        summary = engine.evaluate_latency_distribution(SLAConfig())
        self.assertEqual(summary.resolution_warnings, [])
        self.assertEqual(summary.sla_status, STATUS_APPROVED)

    def test_breach_outranks_insufficient_samples(self):
        engine = TickToTradeLatencyEngine()
        self._fill(engine, 10, stages=(300, 500, 20_000, 400, 300))
        summary = engine.evaluate_latency_distribution(SLAConfig())
        self.assertEqual(summary.sla_status, STATUS_BREACHED)
        self.assertTrue(summary.resolution_warnings)

    def test_no_sla_config_reports_distribution_without_a_verdict(self):
        """An unaudited report must never read as an approved one."""
        engine = TickToTradeLatencyEngine()
        self._fill(engine, 10)
        summary = engine.evaluate_latency_distribution()
        self.assertEqual(summary.sla_breaches, [])
        self.assertEqual(summary.sla_status, STATUS_NOT_AUDITED)
        self.assertNotEqual(summary.sla_status, STATUS_APPROVED)
        self.assertTrue(summary.resolution_warnings)


class TestSlaConfigValidation(unittest.TestCase):
    def test_non_monotonic_budgets_raise(self):
        engine = TickToTradeLatencyEngine()
        engine.record_sample(make_sample("S", FAST_STAGES_NS))
        with self.assertRaises(LatencyError):
            engine.evaluate_latency_distribution(SLAConfig(max_p50_us=20.0, max_p99_us=15.0))

    def test_non_finite_budget_raises(self):
        engine = TickToTradeLatencyEngine()
        engine.record_sample(make_sample("S", FAST_STAGES_NS))
        with self.assertRaises(LatencyError):
            engine.evaluate_latency_distribution(SLAConfig(max_p99_us=float("nan")))

    def test_negative_budget_raises(self):
        with self.assertRaises(LatencyError):
            SLAConfig(max_p50_us=-1.0).validate()


class TestEngineLifecycle(unittest.TestCase):
    def test_empty_evaluation_raises(self):
        with self.assertRaises(LatencyError):
            TickToTradeLatencyEngine().evaluate_latency_distribution()

    def test_unknown_percentile_method_raises(self):
        with self.assertRaises(LatencyError):
            TickToTradeLatencyEngine(percentile_method="BEST_GUESS")

    def test_max_samples_raises_rather_than_evicting(self):
        engine = TickToTradeLatencyEngine(max_samples=2)
        engine.record_sample(make_sample("A", FAST_STAGES_NS))
        engine.record_sample(make_sample("B", FAST_STAGES_NS))
        with self.assertRaises(LatencyError):
            engine.record_sample(make_sample("C", FAST_STAGES_NS))
        self.assertEqual(len(engine.samples), 2)

    def test_invalid_max_samples_raises(self):
        with self.assertRaises(LatencyError):
            TickToTradeLatencyEngine(max_samples=0)

    def test_reset_clears_the_window(self):
        engine = TickToTradeLatencyEngine()
        engine.record_sample(make_sample("A", FAST_STAGES_NS))
        engine.reset()
        self.assertEqual(engine.samples, [])

    def test_percentile_method_is_recorded_in_the_summary(self):
        engine = TickToTradeLatencyEngine(percentile_method=PERCENTILE_LINEAR)
        engine.record_sample(make_sample("A", FAST_STAGES_NS))
        self.assertEqual(
            engine.evaluate_latency_distribution().percentile_method, PERCENTILE_LINEAR
        )


if __name__ == "__main__":
    unittest.main()
