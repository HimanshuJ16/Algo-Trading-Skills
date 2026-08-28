"""Tests for the strategy tick-to-trade latency budget decomposition engine.

Expected values are derived by hand from the inputs, never by re-running the
implementation's own arithmetic. Tests named ``test_regression_*`` each fail against a
specific v1.0.0 defect and pass against the fix; the defect is named in the docstring.
"""

import logging
import math
import unittest
from dataclasses import FrozenInstanceError

from strategy_latency_budget_decomposition import (
    AUDIT_TAIL_PERCENTILE,
    DEFAULT_STAGE_BUDGETS_US,
    MAX_PLAUSIBLE_STAGE_LATENCY_US,
    STATUS_BREACH,
    STATUS_HEALTHY,
    STATUS_INSUFFICIENT_SAMPLES,
    LatencyBudgetConfigError,
    LatencyBudgetError,
    LatencyDecompositionReport,
    LatencyPipelineStage,
    LatencyTraceError,
    StageLatencyMeasurement,
    StrategyLatencyBudgetDecompositionEngine,
    is_percentile_resolvable,
    min_samples_for_percentile,
    percentile_nearest_rank,
    rank_for_percentile,
)

S = LatencyPipelineStage

# Sums to 18.0 us against the default 25.0 us allocation. Per-stage headroom:
# ingress -0.5, decode -1.0, signal -3.0, risk -1.5, egress -1.0.
COMPLIANT_TRACE = {
    S.INGRESS_NETWORK: 1.5,
    S.MARKET_DATA_DECODE: 2.0,
    S.SIGNAL_COMPUTATION: 7.0,
    S.PRE_TRADE_RISK: 3.5,
    S.EGRESS_ORDER_ENCODE: 4.0,
}


def _quiet() -> None:
    """Silence the engine's audit logging for the duration of the suite."""
    logging.getLogger(
        "strategy_latency_budget_decomposition"
    ).setLevel(logging.CRITICAL)


class TestPercentileArithmetic(unittest.TestCase):
    """Nearest rank, verified against ranks derived by hand."""

    def test_nearest_rank_over_one_to_one_hundred(self):
        samples = [float(i) for i in range(1, 101)]
        # ceil(p/100 * 100) = p, so the p-th percentile is the p-th smallest value.
        self.assertEqual(percentile_nearest_rank(samples, 50.0), 50.0)
        self.assertEqual(percentile_nearest_rank(samples, 95.0), 95.0)
        self.assertEqual(percentile_nearest_rank(samples, 99.0), 99.0)

    def test_every_percentile_is_an_observed_value(self):
        # A bimodal series: interpolation would report a latency never observed.
        samples = sorted([1.0] * 50 + [9.0] * 50)
        self.assertEqual(percentile_nearest_rank(samples, 50.0), 1.0)
        self.assertEqual(percentile_nearest_rank(samples, 95.0), 9.0)

    def test_rank_is_clamped_into_range(self):
        self.assertEqual(rank_for_percentile(10, 0.0), 1)
        self.assertEqual(rank_for_percentile(10, 100.0), 10)

    def test_p99_resolvability_threshold_is_one_hundred(self):
        self.assertFalse(is_percentile_resolvable(99, 99.0))
        self.assertTrue(is_percentile_resolvable(100, 99.0))
        self.assertEqual(min_samples_for_percentile(99.0), 100)

    def test_invalid_percentile_inputs_raise(self):
        with self.assertRaises(LatencyTraceError):
            rank_for_percentile(0, 99.0)
        with self.assertRaises(LatencyTraceError):
            rank_for_percentile(10, 101.0)


class TestBudgetConfiguration(unittest.TestCase):

    def setUp(self):
        _quiet()

    def test_defaults_sum_to_twenty_five_microseconds(self):
        engine = StrategyLatencyBudgetDecompositionEngine()
        # 2 + 3 + 10 + 5 + 5 = 25
        self.assertEqual(engine.allocated_budget_us, 25.0)
        self.assertEqual(engine.total_budget_us, 25.0)
        self.assertEqual(engine.unallocated_budget_us, 0.0)

    def test_regression_partial_budget_map_raises(self):
        """v1.0.0 gave every unlisted stage an invented 10.0 us budget.

        A one-stage map produced a 2 + 10 + 10 + 10 + 10 = 42 us total budget where
        2 us was intended, quietly turning breaches into passes.
        """
        with self.assertRaises(LatencyBudgetConfigError) as ctx:
            StrategyLatencyBudgetDecompositionEngine({S.INGRESS_NETWORK: 2.0})
        for stage in ("MARKET_DATA_DECODE", "SIGNAL_COMPUTATION", "PRE_TRADE_RISK"):
            self.assertIn(stage, str(ctx.exception))

    def test_regression_default_budgets_cannot_be_mutated_through_an_engine(self):
        """v1.0.0 assigned the mutable class-level default dict by reference.

        Mutating one engine's ``sla_budgets`` changed the defaults for every engine
        constructed afterwards.
        """
        first = StrategyLatencyBudgetDecompositionEngine()
        first.sla_budgets[S.INGRESS_NETWORK] = 999.0
        second = StrategyLatencyBudgetDecompositionEngine()
        self.assertEqual(second.sla_budgets[S.INGRESS_NETWORK], 2.0)
        self.assertEqual(DEFAULT_STAGE_BUDGETS_US[S.INGRESS_NETWORK], 2.0)

    def test_shipped_defaults_are_read_only(self):
        with self.assertRaises(TypeError):
            DEFAULT_STAGE_BUDGETS_US[S.INGRESS_NETWORK] = 1.0  # type: ignore[index]

    def test_string_stage_keys_are_accepted_in_budgets(self):
        engine = StrategyLatencyBudgetDecompositionEngine(
            {stage.value: 4.0 for stage in S}
        )
        self.assertEqual(engine.allocated_budget_us, 20.0)

    def test_unknown_stage_key_raises(self):
        budgets = dict(DEFAULT_STAGE_BUDGETS_US)
        budgets["SIGNAL"] = 1.0
        with self.assertRaises(LatencyBudgetConfigError):
            StrategyLatencyBudgetDecompositionEngine(budgets)

    def test_non_positive_or_non_finite_budgets_raise(self):
        for bad in (0.0, -1.0, float("nan"), float("inf"), True, "5"):
            with self.subTest(bad=bad):
                budgets = dict(DEFAULT_STAGE_BUDGETS_US)
                budgets[S.INGRESS_NETWORK] = bad
                with self.assertRaises(LatencyBudgetConfigError):
                    StrategyLatencyBudgetDecompositionEngine(budgets)

    def test_non_positive_total_budget_raises(self):
        for bad in (0.0, -5.0, float("nan"), float("inf"), True, "25"):
            with self.subTest(bad=bad):
                with self.assertRaises(LatencyBudgetConfigError):
                    StrategyLatencyBudgetDecompositionEngine(total_budget_us=bad)

    def test_overcommitted_allocation_is_reported_not_rejected(self):
        # Defaults allocate 25 us against a 20 us end-to-end budget.
        engine = StrategyLatencyBudgetDecompositionEngine(total_budget_us=20.0)
        self.assertEqual(engine.unallocated_budget_us, -5.0)
        report = engine.decompose_tick_to_trade("OVERCOMMIT", COMPLIANT_TRACE)
        self.assertTrue(report.is_overcommitted)
        # 18.0 us total is inside the 20 us end-to-end budget even so.
        self.assertTrue(report.is_within_budget)

    def test_total_can_breach_with_every_stage_inside_its_share(self):
        # Allocation sums to 25 us; the real end-to-end budget is 15 us.
        engine = StrategyLatencyBudgetDecompositionEngine(total_budget_us=15.0)
        report = engine.decompose_tick_to_trade("SPLIT", COMPLIANT_TRACE)
        self.assertFalse(report.is_within_budget)
        self.assertEqual(report.breached_stages, [])
        self.assertEqual(report.budget_deficit_us, 3.0)  # 18.0 - 15.0

    def test_headroom_is_reported_when_the_allocation_underspends_the_total(self):
        engine = StrategyLatencyBudgetDecompositionEngine(total_budget_us=40.0)
        self.assertEqual(engine.unallocated_budget_us, 15.0)
        report = engine.decompose_tick_to_trade("HEADROOM", COMPLIANT_TRACE)
        self.assertFalse(report.is_overcommitted)
        self.assertEqual(report.unallocated_budget_us, 15.0)


class TestSingleTraceDecomposition(unittest.TestCase):

    def setUp(self):
        _quiet()
        self.engine = StrategyLatencyBudgetDecompositionEngine()

    def test_compliant_trace(self):
        report = self.engine.decompose_tick_to_trade("TRADE_001", COMPLIANT_TRACE)
        self.assertTrue(report.is_within_budget)
        self.assertEqual(report.total_tick_to_trade_latency_us, 18.0)
        self.assertEqual(report.total_sla_budget_us, 25.0)
        self.assertEqual(report.breached_stages, [])
        self.assertEqual(report.budget_deficit_us, 0.0)
        self.assertEqual(report.stage_reduction_required_fraction, {})
        self.assertEqual(len(report.stage_breakdown), 5)

    def test_regression_bottleneck_on_a_compliant_trace_is_the_least_headroom_stage(self):
        """v1.0.0 named the greatest *absolute* latency when nothing breached.

        That reports whichever stage was given the largest budget. On
        ``COMPLIANT_TRACE`` the per-stage headroom is ingress -0.5, decode -1.0,
        signal -3.0, risk -1.5, egress -1.0 us, so the stage nearest to blowing its
        share is INGRESS_NETWORK -- not SIGNAL_COMPUTATION, which has 3.0 us spare.
        """
        report = self.engine.decompose_tick_to_trade("TRADE_001", COMPLIANT_TRACE)
        self.assertEqual(report.primary_bottleneck_stage, S.INGRESS_NETWORK)

    def test_breach_names_the_stage_furthest_over_its_share(self):
        trace = dict(COMPLIANT_TRACE)
        trace[S.SIGNAL_COMPUTATION] = 25.0   # 15.0 us over its 10 us share
        trace[S.PRE_TRADE_RISK] = 9.0        # 4.0 us over its 5 us share
        report = self.engine.decompose_tick_to_trade("TRADE_002", trace)
        # 1.5 + 2.0 + 25.0 + 9.0 + 4.0 = 41.5
        self.assertEqual(report.total_tick_to_trade_latency_us, 41.5)
        self.assertFalse(report.is_within_budget)
        self.assertEqual(
            report.breached_stages, [S.SIGNAL_COMPUTATION, S.PRE_TRADE_RISK]
        )
        self.assertEqual(report.primary_bottleneck_stage, S.SIGNAL_COMPUTATION)
        self.assertEqual(report.budget_deficit_us, 16.5)  # 41.5 - 25.0

    def test_bottleneck_ties_break_towards_the_earliest_stage(self):
        # Ingress and decode are each exactly 1.0 us over their share.
        trace = {
            S.INGRESS_NETWORK: 3.0,
            S.MARKET_DATA_DECODE: 4.0,
            S.SIGNAL_COMPUTATION: 1.0,
            S.PRE_TRADE_RISK: 1.0,
            S.EGRESS_ORDER_ENCODE: 1.0,
        }
        report = self.engine.decompose_tick_to_trade("TIE", trace)
        self.assertEqual(report.primary_bottleneck_stage, S.INGRESS_NETWORK)

    def test_exactly_on_budget_is_not_a_breach(self):
        trace = dict(DEFAULT_STAGE_BUDGETS_US)  # each stage exactly on its share
        report = self.engine.decompose_tick_to_trade("EXACT", trace)
        self.assertTrue(report.is_within_budget)
        self.assertEqual(report.breached_stages, [])
        self.assertEqual(report.total_tick_to_trade_latency_us, 25.0)

    def test_budget_comparison_runs_on_unrounded_values(self):
        trace = dict(DEFAULT_STAGE_BUDGETS_US)
        trace[S.INGRESS_NETWORK] = 2.0 + 1e-6  # 25.000001 us total
        report = self.engine.decompose_tick_to_trade("SUBROUND", trace)
        self.assertFalse(report.is_within_budget)
        self.assertEqual(report.breached_stages, [S.INGRESS_NETWORK])
        # ...while the displayed total still rounds to the budget.
        self.assertEqual(report.total_tick_to_trade_latency_us, 25.0)

    def test_stage_shares_sum_to_one(self):
        report = self.engine.decompose_tick_to_trade("SHARE", COMPLIANT_TRACE)
        # 1.5/18, 2/18, 7/18, 3.5/18, 4/18
        self.assertAlmostEqual(report.stage_share_of_total[S.SIGNAL_COMPUTATION], 7 / 18)
        self.assertAlmostEqual(math.fsum(report.stage_share_of_total.values()), 1.0)

    def test_amdahl_bound_marks_stages_that_cannot_close_the_deficit(self):
        engine = StrategyLatencyBudgetDecompositionEngine(total_budget_us=10.0)
        report = engine.decompose_tick_to_trade("AMDAHL", COMPLIANT_TRACE)
        self.assertEqual(report.budget_deficit_us, 8.0)  # 18.0 - 10.0
        fractions = report.stage_reduction_required_fraction
        # Signal computation is 7.0 us: deleting it entirely removes only 7.0 of the
        # 8.0 us deficit, so it alone cannot bring the trace inside budget.
        self.assertAlmostEqual(fractions[S.SIGNAL_COMPUTATION], 8.0 / 7.0)
        self.assertGreater(fractions[S.SIGNAL_COMPUTATION], 1.0)
        # Nor can any other stage -- every one of them is smaller still.
        self.assertTrue(all(value > 1.0 for value in fractions.values()))

    def test_a_zero_latency_stage_is_accepted_and_reported_as_zero(self):
        trace = dict(COMPLIANT_TRACE)
        trace[S.INGRESS_NETWORK] = 0.0  # below the timer's resolution
        report = self.engine.decompose_tick_to_trade("SUBRES", trace)
        self.assertEqual(report.total_tick_to_trade_latency_us, 16.5)
        self.assertAlmostEqual(report.stage_share_of_total[S.INGRESS_NETWORK], 0.0)

    def test_all_zero_trace_is_accepted_and_carries_no_shares(self):
        # Every stage below the timer's resolution. Pathological but not invalid: the
        # finding is that the timer resolved nothing, and the report must not pretend
        # otherwise by inventing shares that sum to one.
        report = self.engine.decompose_tick_to_trade("ZERO", {stage: 0.0 for stage in S})
        self.assertEqual(report.total_tick_to_trade_latency_us, 0.0)
        self.assertTrue(report.is_within_budget)
        self.assertEqual(set(report.stage_share_of_total.values()), {0.0})

    def test_string_stage_keys_are_accepted_in_a_trace(self):
        report = self.engine.decompose_tick_to_trade(
            "STR", {stage.value: value for stage, value in COMPLIANT_TRACE.items()}
        )
        self.assertEqual(report.total_tick_to_trade_latency_us, 18.0)


class TestTraceRejection(unittest.TestCase):
    """Each rejected input produced a confidently wrong v1.0.0 report instead."""

    def setUp(self):
        _quiet()
        self.engine = StrategyLatencyBudgetDecompositionEngine()

    def test_regression_incomplete_trace_raises(self):
        """v1.0.0 defaulted absent stages to 0.0 us.

        A trace carrying one of five measurements reported a 9.0 us total against a
        25 us budget and ``is_within_budget=True`` -- a dropped instrumentation point
        became a passing audit.
        """
        with self.assertRaises(LatencyTraceError) as ctx:
            self.engine.decompose_tick_to_trade("PARTIAL", {S.SIGNAL_COMPUTATION: 9.0})
        self.assertIn("INGRESS_NETWORK", str(ctx.exception))

    def test_regression_nan_latency_raises(self):
        """v1.0.0 emitted a report with a NaN total and no breached stages.

        ``NaN - budget > 0`` is False, so nothing breached, while ``NaN <= budget`` is
        also False -- the two readings of the same report disagreed.
        """
        trace = dict(COMPLIANT_TRACE)
        trace[S.SIGNAL_COMPUTATION] = float("nan")
        with self.assertRaises(LatencyTraceError):
            self.engine.decompose_tick_to_trade("NAN", trace)

    def test_regression_negative_latency_raises(self):
        """v1.0.0 let a negative stage offset a real breach.

        A 40 us signal stage with a -30 us ingress stage summed to 10 us and passed a
        25 us budget.
        """
        trace = dict(COMPLIANT_TRACE)
        trace[S.SIGNAL_COMPUTATION] = 40.0
        trace[S.INGRESS_NETWORK] = -30.0
        with self.assertRaises(LatencyTraceError):
            self.engine.decompose_tick_to_trade("NEG", trace)

    def test_infinite_latency_raises(self):
        trace = dict(COMPLIANT_TRACE)
        trace[S.EGRESS_ORDER_ENCODE] = float("inf")
        with self.assertRaises(LatencyTraceError):
            self.engine.decompose_tick_to_trade("INF", trace)

    def test_boolean_latency_raises(self):
        trace = dict(COMPLIANT_TRACE)
        trace[S.INGRESS_NETWORK] = True  # a valid Real worth 1.0 us
        with self.assertRaises(LatencyTraceError):
            self.engine.decompose_tick_to_trade("BOOL", trace)

    def test_non_numeric_latency_raises(self):
        trace = dict(COMPLIANT_TRACE)
        trace[S.INGRESS_NETWORK] = "1.5"
        with self.assertRaises(LatencyTraceError):
            self.engine.decompose_tick_to_trade("STRVAL", trace)

    def test_implausibly_large_latency_raises(self):
        trace = dict(COMPLIANT_TRACE)
        trace[S.INGRESS_NETWORK] = MAX_PLAUSIBLE_STAGE_LATENCY_US * 10
        with self.assertRaises(LatencyTraceError):
            self.engine.decompose_tick_to_trade("UNITS", trace)

    def test_unknown_stage_key_in_a_trace_raises(self):
        trace = dict(COMPLIANT_TRACE)
        trace["SIGNAL"] = 1.0  # a typo, silently ignored by v1.0.0
        with self.assertRaises(LatencyBudgetConfigError):
            self.engine.decompose_tick_to_trade("TYPO", trace)

    def test_non_mapping_trace_raises(self):
        with self.assertRaises(LatencyTraceError):
            self.engine.decompose_tick_to_trade("LIST", [1.0, 2.0])  # type: ignore[arg-type]

    def test_every_rejection_is_a_value_error(self):
        # Callers written against the previous revision caught bare ValueError.
        self.assertTrue(issubclass(LatencyBudgetError, ValueError))
        self.assertTrue(issubclass(LatencyTraceError, LatencyBudgetError))
        self.assertTrue(issubclass(LatencyBudgetConfigError, LatencyBudgetError))


class TestBatchProfile(unittest.TestCase):

    def setUp(self):
        _quiet()
        self.engine = StrategyLatencyBudgetDecompositionEngine()
        # Clean trace sums to 11.0 us, comfortably inside the 25 us budget.
        self.base = {
            S.INGRESS_NETWORK: 1.0,
            S.MARKET_DATA_DECODE: 1.0,
            S.SIGNAL_COMPUTATION: 5.0,
            S.PRE_TRADE_RISK: 2.0,
            S.EGRESS_ORDER_ENCODE: 2.0,
        }

    def _report(self, trade_id, trace=None):
        return self.engine.decompose_tick_to_trade(trade_id, trace or self.base)

    def test_empty_batch_raises(self):
        with self.assertRaises(LatencyTraceError):
            self.engine.profile_batch([])

    def test_single_trace_batch_cannot_resolve_a_p99(self):
        profile = self.engine.profile_batch([self._report("ONE")])
        self.assertEqual(profile.sample_count, 1)
        self.assertEqual(profile.p99_total_us, 11.0)  # the only trace there is
        self.assertFalse(profile.is_p99_resolvable)
        self.assertEqual(profile.status, STATUS_INSUFFICIENT_SAMPLES)

    def test_percentiles_of_a_clean_batch(self):
        profile = self.engine.profile_batch([self._report(f"C{i}") for i in range(100)])
        self.assertEqual(profile.sample_count, 100)
        self.assertEqual(profile.p50_total_us, 11.0)
        self.assertEqual(profile.p99_total_us, 11.0)
        self.assertEqual(profile.status, STATUS_HEALTHY)
        self.assertEqual(profile.breach_count, 0)
        self.assertEqual(profile.breach_rate, 0.0)
        self.assertTrue(profile.is_p99_resolvable)

    def test_regression_ninety_nine_clean_traces_cannot_approve_a_p99(self):
        """A P99 over 99 traces is the observed maximum, not a 1-in-100 excursion."""
        profile = self.engine.profile_batch([self._report(f"C{i}") for i in range(99)])
        self.assertFalse(profile.is_p99_resolvable)
        self.assertEqual(profile.status, STATUS_INSUFFICIENT_SAMPLES)

    def test_a_breach_is_reported_at_any_sample_count(self):
        slow = {stage: value * 3 for stage, value in self.base.items()}  # 33.0 us
        reports = [self._report(f"C{i}") for i in range(9)] + [self._report("SLOW", slow)]
        profile = self.engine.profile_batch(reports)
        self.assertEqual(profile.status, STATUS_BREACH)
        self.assertFalse(profile.is_p99_resolvable)
        self.assertEqual(profile.breach_count, 1)

    def test_comonotonic_stall_gives_a_zero_gap(self):
        # 99 clean traces plus one where every stage stalls together.
        slow = {stage: value * 3 for stage, value in self.base.items()}
        reports = [self._report(f"C{i}") for i in range(99)] + [self._report("SLOW", slow)]
        profile = self.engine.profile_batch(reports)
        # Rank 99 of 100 in every series is still the clean value.
        self.assertEqual(profile.p99_total_us, 11.0)
        self.assertEqual(profile.sum_of_stage_p99_us, 11.0)
        self.assertEqual(profile.comonotonic_gap_us, 0.0)

    def test_independent_stalls_make_the_sum_of_stage_p99s_an_underestimate(self):
        """The core quantitative claim: stage P99s must not be added up.

        Five stages each stall by +8.0 us on a different 1% of 100 traces. Each stage's
        own series is then 99 clean values and one stall, so its nearest-rank P99 (rank
        99 of 100) is the *clean* value and the stage P99s sum to the clean total of
        11.0 us. The totals, however, contain five stalled traces at 19.0 us occupying
        ranks 96-100, so the measured total P99 is 19.0 us. Budgeting stage by stage at
        P99 would under-provision the end-to-end tail by 8.0 us.
        """
        stages = list(S)
        reports = []
        for i in range(100):
            trace = dict(self.base)
            if i >= 95:
                stalled = stages[i - 95]
                trace[stalled] = trace[stalled] + 8.0
            reports.append(self._report(f"I{i}", trace))
        profile = self.engine.profile_batch(reports)
        self.assertEqual(profile.p99_total_us, 19.0)
        self.assertEqual(profile.sum_of_stage_p99_us, 11.0)
        self.assertEqual(profile.comonotonic_gap_us, -8.0)

    def test_stage_p99_excess_and_batch_bottleneck(self):
        # Pre-trade risk sits at 6.0 us on every trace, 1.0 us over its 5 us share; no
        # other stage is over. Its P99 excess is therefore the largest.
        trace = dict(self.base)
        trace[S.PRE_TRADE_RISK] = 6.0
        profile = self.engine.profile_batch(
            [self._report(f"R{i}", trace) for i in range(100)]
        )
        self.assertEqual(profile.stage_p99_us[S.PRE_TRADE_RISK], 6.0)
        self.assertEqual(profile.stage_p99_excess_us[S.PRE_TRADE_RISK], 1.0)
        self.assertEqual(profile.stage_p99_excess_us[S.SIGNAL_COMPUTATION], -5.0)
        self.assertEqual(profile.primary_bottleneck_stage, S.PRE_TRADE_RISK)

    def test_breach_rate_counts_traces_over_the_end_to_end_budget(self):
        slow = {stage: value * 3 for stage, value in self.base.items()}  # 33.0 us
        reports = [self._report(f"C{i}") for i in range(90)]
        reports += [self._report(f"S{i}", slow) for i in range(10)]
        profile = self.engine.profile_batch(reports)
        self.assertEqual(profile.breach_count, 10)
        self.assertEqual(profile.breach_rate, 0.1)
        self.assertEqual(profile.max_total_us, 33.0)

    def test_reports_audited_under_a_different_allocation_are_rejected(self):
        other = StrategyLatencyBudgetDecompositionEngine(
            dict(DEFAULT_STAGE_BUDGETS_US, **{S.PRE_TRADE_RISK: 4.0})
        )
        with self.assertRaises(LatencyTraceError) as ctx:
            other.profile_batch([self._report("C0")])
        self.assertIn("PRE_TRADE_RISK", str(ctx.exception))

    def test_non_report_objects_are_rejected(self):
        with self.assertRaises(LatencyTraceError):
            self.engine.profile_batch([{"total": 11.0}])  # type: ignore[list-item]


class TestStageMeasurement(unittest.TestCase):

    def test_derived_properties(self):
        measurement = StageLatencyMeasurement(
            stage=S.PRE_TRADE_RISK, latency_us=4.0, sla_budget_us=5.0
        )
        self.assertEqual(measurement.excess_us, -1.0)
        self.assertEqual(measurement.budget_utilization, 0.8)
        self.assertFalse(measurement.is_breached)

    def test_exactly_on_its_share_is_not_breached(self):
        measurement = StageLatencyMeasurement(
            stage=S.PRE_TRADE_RISK, latency_us=5.0, sla_budget_us=5.0
        )
        self.assertEqual(measurement.excess_us, 0.0)
        self.assertFalse(measurement.is_breached)

    def test_measurements_are_immutable(self):
        measurement = StageLatencyMeasurement(
            stage=S.PRE_TRADE_RISK, latency_us=4.0, sla_budget_us=5.0
        )
        with self.assertRaises(FrozenInstanceError):
            measurement.latency_us = 99.0  # type: ignore[misc]


class TestModuleContract(unittest.TestCase):

    def test_audit_tail_percentile_is_p99(self):
        self.assertEqual(AUDIT_TAIL_PERCENTILE, 99.0)

    def test_report_type_is_exported_for_downstream_consumers(self):
        _quiet()
        engine = StrategyLatencyBudgetDecompositionEngine()
        report = engine.decompose_tick_to_trade("EXPORT", COMPLIANT_TRACE)
        self.assertIsInstance(report, LatencyDecompositionReport)
        self.assertIn("WITHIN_BUDGET", report.audit_notes)

    def test_p99_jitter_field_is_gone(self):
        """v1.0.0's ``p99_jitter_us`` was a cross-stage standard deviation.

        It described the dispersion between an ingress hop and a signal computation,
        each measured once, and could not describe variation over time. Jitter now
        comes from :meth:`profile_batch` over a batch of traces.
        """
        _quiet()
        engine = StrategyLatencyBudgetDecompositionEngine()
        report = engine.decompose_tick_to_trade("NOJITTER", COMPLIANT_TRACE)
        self.assertFalse(hasattr(report, "p99_jitter_us"))


if __name__ == "__main__":
    unittest.main()
