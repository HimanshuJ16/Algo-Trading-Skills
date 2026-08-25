"""
Unit tests for factor-research-multiple-testing-correction.

The primary correctness anchor is Harvey, Liu and Zhu (2016), Table 4 -- a worked
ten-test example whose Bonferroni / Holm / BHY discovery counts are published. Those
expected values are independent of this implementation, so reproducing them tests the
procedures rather than restating their formulas.
"""
import logging
import math
import unittest

from factor_research_multiple_testing_correction import (
    CandidateFactorTest,
    FactorMultipleTestingCorrectionEngine,
    HLZ_RECOMMENDED_T_HURDLE,
    harmonic_sum,
    two_sided_p_value_from_t,
)

LOGGER_NAME = "factor_research_multiple_testing_correction"


def t_from_two_sided_p(p: float) -> float:
    """
    Inverts `two_sided_p_value_from_t` by bisection, so fixtures can carry
    t-statistics genuinely consistent with their p-values instead of arbitrary pairs.
    """
    lo, hi = 0.0, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if two_sided_p_value_from_t(mid) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# Harvey, Liu and Zhu (2016), Table 4, panel A: ten hypothetical tests in the paper's
# original ordering, with the published t-statistics. The paper quotes p-values in
# percent to two decimals; tests 7 and 8 are quoted as "0.00", which is a display
# rounding of ~5.1e-6 and ~9.3e-8, so their p-values are taken from the t-statistics.
# Ordering, and therefore every rank-based verdict, is identical either way.
HLZ_TABLE_4 = [
    ("T01", 1.99, 0.0466),
    ("T02", 2.63, 0.0085),
    ("T03", 2.21, 0.0271),
    ("T04", 3.43, 0.0005),
    ("T05", 2.17, 0.0300),
    ("T06", 2.64, 0.0084),
    ("T07", 4.56, two_sided_p_value_from_t(4.56)),
    ("T08", 5.34, two_sided_p_value_from_t(5.34)),
    ("T09", 2.75, 0.0060),
    ("T10", 2.49, 0.0128),
]


def hlz_table_4_factors():
    return [
        CandidateFactorTest(fid, f"HLZ_{fid}", raw_t_stat=t, raw_p_value=p, sample_size=600)
        for fid, t, p in HLZ_TABLE_4
    ]


class TestHelpers(unittest.TestCase):
    def test_harmonic_sum_matches_hlz_table_4_panel_d(self):
        # c(10) = 2.928968; the paper's rank-1 BHY threshold is
        # (1 * 5%) / (10 * c(10)) = 0.17%.
        self.assertAlmostEqual(harmonic_sum(10), 2.9289682539, places=9)
        self.assertAlmostEqual(0.05 / (10 * harmonic_sum(10)), 0.0017, places=4)

    def test_harmonic_sum_edge_and_invalid(self):
        self.assertEqual(harmonic_sum(1), 1.0)
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                harmonic_sum(bad)

    def test_two_sided_p_reproduces_hlz_published_hurdle_p_values(self):
        # HLZ map their hurdles to p-values using the normal approximation:
        # t = 3.00 -> 0.27%, t = 2.78 -> 0.54%, t = 3.39 -> 0.07%.
        self.assertAlmostEqual(two_sided_p_value_from_t(3.00), 0.0027, places=4)
        self.assertAlmostEqual(two_sided_p_value_from_t(2.78), 0.0054, places=4)
        self.assertAlmostEqual(two_sided_p_value_from_t(3.39), 0.0007, places=4)

    def test_two_sided_p_is_symmetric_and_bounded(self):
        self.assertAlmostEqual(two_sided_p_value_from_t(-2.5), two_sided_p_value_from_t(2.5))
        self.assertAlmostEqual(two_sided_p_value_from_t(0.0), 1.0)
        with self.assertRaises(ValueError):
            two_sided_p_value_from_t(float("nan"))

    def test_bisection_inverse_round_trips(self):
        for p in (0.5, 0.05, 0.0027, 1e-6):
            with self.subTest(p=p):
                self.assertAlmostEqual(
                    two_sided_p_value_from_t(t_from_two_sided_p(p)), p,
                    delta=max(1e-15, p * 1e-6))
        self.assertAlmostEqual(t_from_two_sided_p(math.erfc(3.0 / math.sqrt(2.0))), 3.0,
                               places=6)


class TestHarveyLiuZhuTable4(unittest.TestCase):
    """Reproduces the published discovery counts of HLZ Table 4, panels A-D."""

    def setUp(self):
        self.engine = FactorMultipleTestingCorrectionEngine(
            alpha_target=0.05, fdr_q_target=0.05, hlz_t_threshold=3.0)
        self.report = self.engine.audit_and_correct_factors(hlz_table_4_factors())

    def test_panel_a_single_tests_discover_all_ten(self):
        self.assertEqual(self.report.total_factors_tested, 10)
        self.assertEqual(self.report.raw_significant_count, 10)

    def test_panel_b_bonferroni_discovers_tests_4_7_and_8(self):
        self.assertEqual(self.report.bonferroni_significant_count, 3)
        discovered = {f.factor_id for f in self.report.audited_factors
                      if f.is_bonferroni_significant}
        self.assertEqual(discovered, {"T04", "T07", "T08"})

    def test_panel_c_holm_discovers_four(self):
        # Panel C: the first four ordered tests -- old order 8, 7, 4, 9.
        self.assertEqual(self.report.holm_significant_count, 4)
        discovered = {f.factor_id for f in self.report.audited_factors
                      if f.is_holm_significant}
        self.assertEqual(discovered, {"T08", "T07", "T04", "T09"})

    def test_panel_d_bhy_discovers_six(self):
        # Panel D: the first six ordered tests -- old order 8, 7, 4, 9, 6, 2.
        self.assertEqual(self.report.bhy_fdr_significant_count, 6)
        discovered = {f.factor_id for f in self.report.audited_factors
                      if f.is_bhy_fdr_significant}
        self.assertEqual(discovered, {"T08", "T07", "T04", "T09", "T06", "T02"})
        self.assertAlmostEqual(self.report.bhy_dependence_factor, 2.9289682539, places=9)

    def test_plain_bh_is_more_lenient_than_bhy_on_the_same_batch(self):
        # Every rank-b threshold b/M * q here exceeds its p-value, so BH accepts all
        # ten while BHY accepts six. That gap is the cost of the arbitrary-dependence
        # guarantee, and it is why BH alone is unsafe on a correlated factor zoo.
        self.assertEqual(self.report.bh_fdr_significant_count, 10)
        self.assertGreater(self.report.bh_fdr_significant_count,
                           self.report.bhy_fdr_significant_count)

    def test_hlz_t_hurdle_admits_only_the_three_largest_t_stats(self):
        # |t| >= 3.0 holds for T04 (3.43), T07 (4.56) and T08 (5.34).
        self.assertEqual(self.report.hlz_t3_significant_count, 3)
        admitted = {f.factor_id for f in self.report.audited_factors
                    if f.is_hlz_t3_significant}
        self.assertEqual(admitted, {"T04", "T07", "T08"})

    def test_procedure_ordering_holds(self):
        # Bonferroni <= Holm <= BH, and BHY <= BH, on any batch.
        r = self.report
        self.assertLessEqual(r.bonferroni_significant_count, r.holm_significant_count)
        self.assertLessEqual(r.holm_significant_count, r.bh_fdr_significant_count)
        self.assertLessEqual(r.bhy_fdr_significant_count, r.bh_fdr_significant_count)

    def test_every_flag_agrees_with_its_adjusted_p_value(self):
        for f in self.report.audited_factors:
            with self.subTest(factor=f.factor_id):
                self.assertEqual(f.is_bonferroni_significant,
                                 f.adjusted_p_value_bonferroni <= 0.05)
                self.assertEqual(f.is_holm_significant, f.adjusted_p_value_holm <= 0.05)
                self.assertEqual(f.is_bh_fdr_significant, f.adjusted_p_value_bh <= 0.05)
                self.assertEqual(f.is_bhy_fdr_significant, f.adjusted_p_value_bhy <= 0.05)

    def test_adjusted_p_values_are_monotone_in_rank(self):
        ordered = self.report.audited_factors
        self.assertEqual([f.p_value_rank for f in ordered], list(range(1, 11)))
        for attr in ("adjusted_p_value_bonferroni", "adjusted_p_value_holm",
                     "adjusted_p_value_bh", "adjusted_p_value_bhy"):
            values = [getattr(f, attr) for f in ordered]
            with self.subTest(attr=attr):
                self.assertEqual(values, sorted(values))


class TestTiedPValues(unittest.TestCase):
    """
    Identical evidence must produce identical verdicts. Rank-based procedures assign
    tied p-values different ranks, so a naive step-down can reject one member of a tie
    and not the other purely because of input order.
    """

    def _tied_batch(self):
        # p = 0.0125 exactly twice. Holm rank-2 threshold is 0.05/3 = 0.01667 and
        # rank-3 threshold is 0.05/2 = 0.025, so both tied members clear their own
        # threshold here; the risk is the monotonicity step, not the raw comparison.
        return [
            CandidateFactorTest("LEAD", "LEAD", raw_t_stat=t_from_two_sided_p(0.004),
                                raw_p_value=0.004, sample_size=400),
            CandidateFactorTest("TIE_A", "TIE_A", raw_t_stat=t_from_two_sided_p(0.0125),
                                raw_p_value=0.0125, sample_size=400),
            CandidateFactorTest("TIE_B", "TIE_B", raw_t_stat=t_from_two_sided_p(0.0125),
                                raw_p_value=0.0125, sample_size=400),
            CandidateFactorTest("TAIL", "TAIL", raw_t_stat=t_from_two_sided_p(0.4),
                                raw_p_value=0.4, sample_size=400),
        ]

    def test_tied_factors_share_every_adjusted_p_value_and_verdict(self):
        engine = FactorMultipleTestingCorrectionEngine()
        report = engine.audit_and_correct_factors(self._tied_batch())
        tied = [f for f in report.audited_factors if f.factor_id.startswith("TIE_")]
        self.assertEqual(len(tied), 2)
        for attr in ("adjusted_p_value_bonferroni", "adjusted_p_value_holm",
                     "adjusted_p_value_bh", "adjusted_p_value_bhy"):
            with self.subTest(attr=attr):
                self.assertEqual(len({getattr(f, attr) for f in tied}), 1)
        for attr in ("is_bonferroni_significant", "is_holm_significant",
                     "is_bh_fdr_significant", "is_bhy_fdr_significant"):
            with self.subTest(attr=attr):
                self.assertEqual(len({getattr(f, attr) for f in tied}), 1)

    def test_verdicts_do_not_depend_on_input_ordering(self):
        engine = FactorMultipleTestingCorrectionEngine()
        forward = engine.audit_and_correct_factors(self._tied_batch())
        reversed_batch = list(reversed(self._tied_batch()))
        backward = engine.audit_and_correct_factors(reversed_batch)
        self.assertEqual(
            sorted((f.factor_id, f.is_holm_significant) for f in forward.audited_factors),
            sorted((f.factor_id, f.is_holm_significant) for f in backward.audited_factors))


class TestBenjaminiHochbergAdjustedPValues(unittest.TestCase):
    """
    Regression cover for the monotonicity enforcement.

    Without the running minimum, adjusted_p_(i) = M/i * p_(i) is computed
    independently per rank. That is non-monotone whenever a later rank yields a
    smaller product, and it can report an adjusted p-value above the FDR target for a
    factor the step-up rule accepts.
    """

    def test_adjusted_p_value_never_exceeds_q_for_an_accepted_factor(self):
        # p = (0.010, 0.012), M = 2, q = 0.015.
        # Step-up: rank 2 threshold 2/2 * 0.015 = 0.015 >= 0.012, so k = 2 and BOTH
        # are accepted. Un-monotonised adjusted p at rank 1 is 2/1 * 0.010 = 0.020,
        # which exceeds q = 0.015 and contradicts that acceptance. The correct value
        # is min(0.020, 0.012) = 0.012.
        engine = FactorMultipleTestingCorrectionEngine(alpha_target=0.05, fdr_q_target=0.015)
        factors = [
            CandidateFactorTest("A", "A", raw_t_stat=t_from_two_sided_p(0.010),
                                raw_p_value=0.010, sample_size=500),
            CandidateFactorTest("B", "B", raw_t_stat=t_from_two_sided_p(0.012),
                                raw_p_value=0.012, sample_size=500),
        ]
        report = engine.audit_and_correct_factors(factors)

        self.assertEqual(report.bh_fdr_significant_count, 2)
        by_id = {f.factor_id: f for f in report.audited_factors}
        self.assertAlmostEqual(by_id["A"].adjusted_p_value_bh, 0.012, places=12)
        self.assertAlmostEqual(by_id["B"].adjusted_p_value_bh, 0.012, places=12)
        for f in report.audited_factors:
            self.assertTrue(f.is_bh_fdr_significant)
            self.assertLessEqual(f.adjusted_p_value_bh, 0.015)

    def test_small_adjusted_p_values_are_not_rounded_away(self):
        # A t-statistic of 6 implies p ~ 2e-9. With M = 3 the adjusted p-value is
        # ~6e-9 and must survive as a nonzero number rather than being rounded to 0.
        engine = FactorMultipleTestingCorrectionEngine()
        p_small = two_sided_p_value_from_t(6.0)
        factors = [
            CandidateFactorTest("A", "A", raw_t_stat=6.0, raw_p_value=p_small, sample_size=800),
            CandidateFactorTest("B", "B", raw_t_stat=1.0, raw_p_value=0.317, sample_size=800),
            CandidateFactorTest("C", "C", raw_t_stat=0.5, raw_p_value=0.617, sample_size=800),
        ]
        report = engine.audit_and_correct_factors(factors)
        best = report.audited_factors[0]
        self.assertGreater(best.adjusted_p_value_bh, 0.0)
        self.assertAlmostEqual(best.adjusted_p_value_bh, 3.0 * p_small, places=15)

    def test_single_factor_batch_leaves_p_value_unadjusted(self):
        engine = FactorMultipleTestingCorrectionEngine()
        report = engine.audit_and_correct_factors(
            [CandidateFactorTest("A", "A", raw_t_stat=t_from_two_sided_p(0.0124),
                                 raw_p_value=0.0124, sample_size=250)])
        only = report.audited_factors[0]
        self.assertEqual(report.total_factors_tested, 1)
        self.assertAlmostEqual(only.adjusted_p_value_bh, 0.0124, places=12)
        self.assertAlmostEqual(only.adjusted_p_value_bonferroni, 0.0124, places=12)
        self.assertAlmostEqual(only.adjusted_p_value_holm, 0.0124, places=12)
        # c(1) = 1, so BHY collapses to BH for a single test.
        self.assertAlmostEqual(only.adjusted_p_value_bhy, 0.0124, places=12)

    def test_exact_threshold_boundary_is_accepted(self):
        # p_(2) = 2/2 * 0.05 = 0.05 exactly: the step-up rule uses <=, so it accepts.
        engine = FactorMultipleTestingCorrectionEngine(fdr_q_target=0.05)
        factors = [
            CandidateFactorTest("A", "A", raw_t_stat=t_from_two_sided_p(0.0005),
                                raw_p_value=0.0005, sample_size=500),
            CandidateFactorTest("B", "B", raw_t_stat=t_from_two_sided_p(0.05),
                                raw_p_value=0.05, sample_size=500),
        ]
        report = engine.audit_and_correct_factors(factors)
        self.assertEqual(report.bh_fdr_significant_count, 2)


class TestTotalTestsConductedOverride(unittest.TestCase):
    """
    HLZ's core claim: the recorded factor count understates the number of tests run,
    so corrections computed on the reported set alone are too lenient.
    """

    def _winners(self):
        return [
            CandidateFactorTest("W1", "W1", raw_t_stat=t_from_two_sided_p(0.00097),
                                raw_p_value=0.00097, sample_size=700),
            CandidateFactorTest("W2", "W2", raw_t_stat=t_from_two_sided_p(0.00932),
                                raw_p_value=0.00932, sample_size=700),
            CandidateFactorTest("W3", "W3", raw_t_stat=t_from_two_sided_p(0.02781),
                                raw_p_value=0.02781, sample_size=700),
        ]

    def test_declaring_the_true_trial_count_tightens_the_correction(self):
        engine = FactorMultipleTestingCorrectionEngine()
        reported_only = engine.audit_and_correct_factors(self._winners())
        with_file_drawer = engine.audit_and_correct_factors(self._winners(),
                                                            total_tests_conducted=300)

        self.assertEqual(reported_only.total_factors_tested, 3)
        self.assertEqual(with_file_drawer.total_factors_tested, 300)
        self.assertEqual(with_file_drawer.factor_results_supplied, 3)
        self.assertLess(with_file_drawer.bh_fdr_significant_count,
                        reported_only.bh_fdr_significant_count)
        self.assertLessEqual(with_file_drawer.bhy_fdr_significant_count,
                             with_file_drawer.bh_fdr_significant_count)

    def test_raw_and_hlz_verdicts_are_unaffected_by_the_trial_count(self):
        # Neither depends on M -- only the multiplicity corrections do.
        engine = FactorMultipleTestingCorrectionEngine()
        a = engine.audit_and_correct_factors(self._winners())
        b = engine.audit_and_correct_factors(self._winners(), total_tests_conducted=300)
        self.assertEqual(a.raw_significant_count, b.raw_significant_count)
        self.assertEqual(a.hlz_t3_significant_count, b.hlz_t3_significant_count)

    def test_trial_count_below_supplied_results_is_rejected(self):
        engine = FactorMultipleTestingCorrectionEngine()
        with self.assertRaises(ValueError):
            engine.audit_and_correct_factors(self._winners(), total_tests_conducted=2)

    def test_trial_count_equal_to_supplied_results_matches_the_default(self):
        engine = FactorMultipleTestingCorrectionEngine()
        default = engine.audit_and_correct_factors(self._winners())
        explicit = engine.audit_and_correct_factors(self._winners(), total_tests_conducted=3)
        self.assertEqual(default.audit_notes, explicit.audit_notes)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = FactorMultipleTestingCorrectionEngine()

    def test_empty_batch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_and_correct_factors([])

    def test_nan_p_value_is_rejected_rather_than_silently_reordering_the_batch(self):
        factors = [
            CandidateFactorTest("A", "A", raw_t_stat=3.5, raw_p_value=0.0005, sample_size=500),
            CandidateFactorTest("B", "B", raw_t_stat=2.0, raw_p_value=float("nan"),
                                sample_size=500),
            CandidateFactorTest("C", "C", raw_t_stat=0.5, raw_p_value=0.617, sample_size=500),
        ]
        with self.assertRaises(ValueError):
            self.engine.audit_and_correct_factors(factors)

    def test_out_of_range_p_values_are_rejected(self):
        for bad_p in (-0.01, 1.5):
            with self.subTest(p=bad_p):
                with self.assertRaises(ValueError):
                    self.engine.audit_and_correct_factors(
                        [CandidateFactorTest("A", "A", raw_t_stat=1.0,
                                             raw_p_value=bad_p, sample_size=100)])

    def test_non_finite_t_stat_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_and_correct_factors(
                [CandidateFactorTest("A", "A", raw_t_stat=float("inf"),
                                     raw_p_value=0.01, sample_size=100)])

    def test_duplicate_factor_ids_are_rejected(self):
        factors = [
            CandidateFactorTest("A", "A", raw_t_stat=3.5, raw_p_value=0.0005, sample_size=500),
            CandidateFactorTest("A", "A copy", raw_t_stat=3.5, raw_p_value=0.0005,
                                sample_size=500),
        ]
        with self.assertRaises(ValueError):
            self.engine.audit_and_correct_factors(factors)

    def test_wrong_element_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.audit_and_correct_factors([{"factor_id": "A", "raw_p_value": 0.01}])

    def test_invalid_engine_configuration_is_rejected(self):
        for kwargs in ({"alpha_target": 0.0}, {"alpha_target": 1.0}, {"fdr_q_target": -0.1},
                       {"fdr_q_target": 1.0}, {"hlz_t_threshold": 0.0},
                       {"hlz_t_threshold": float("nan")}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    FactorMultipleTestingCorrectionEngine(**kwargs)

    def test_p_value_inconsistent_with_t_stat_is_warned_not_silently_accepted(self):
        # t = 2.0 implies a two-sided normal p of ~0.0455, and Student-t tails are
        # fatter still, so no t-test can produce 0.0001 at this t-statistic.
        factors = [CandidateFactorTest("A", "A", raw_t_stat=2.0, raw_p_value=0.0001,
                                       sample_size=500)]
        with self.assertLogs(LOGGER_NAME, level=logging.WARNING) as captured:
            self.engine.audit_and_correct_factors(factors)
        self.assertTrue(any("below the normal two-sided p-value" in m
                            for m in captured.output))

    def test_published_table_rounding_does_not_trigger_the_warning(self):
        # HLZ Table 4 quotes p-values to two decimal places in percent; the tolerance
        # must absorb that without flagging the paper's own numbers.
        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            self.engine.audit_and_correct_factors(hlz_table_4_factors())
        self.assertFalse(any(record.levelno >= logging.WARNING
                             for record in captured.records))


class TestReportContents(unittest.TestCase):
    def test_report_fields_are_internally_consistent(self):
        engine = FactorMultipleTestingCorrectionEngine()
        report = engine.audit_and_correct_factors(hlz_table_4_factors())
        self.assertEqual(len(report.audited_factors), report.factor_results_supplied)
        self.assertEqual(report.false_discoveries_filtered_count,
                         report.raw_significant_count - report.bh_fdr_significant_count)
        self.assertEqual(report.hlz_t_threshold, HLZ_RECOMMENDED_T_HURDLE)
        self.assertIn("MULTIPLE TESTING CORRECTION AUDIT", report.audit_notes)
        self.assertIn("not proven false", report.audit_notes)

    def test_audited_factors_are_returned_in_ascending_p_value_order(self):
        engine = FactorMultipleTestingCorrectionEngine()
        report = engine.audit_and_correct_factors(hlz_table_4_factors())
        p_values = [f.raw_p_value for f in report.audited_factors]
        self.assertEqual(p_values, sorted(p_values))

    def test_repeated_audits_are_deterministic(self):
        engine = FactorMultipleTestingCorrectionEngine()
        factors = hlz_table_4_factors()
        first = engine.audit_and_correct_factors(factors)
        second = engine.audit_and_correct_factors(factors)
        self.assertEqual(first.audit_notes, second.audit_notes)
        self.assertEqual(
            [(f.factor_id, f.is_bhy_fdr_significant) for f in first.audited_factors],
            [(f.factor_id, f.is_bhy_fdr_significant) for f in second.audited_factors])


class TestNoiseFactorZoo(unittest.TestCase):
    """
    The scenario the skill exists to prevent: screening a large batch of pure noise
    and treating the survivors of an uncorrected screen as discoveries.
    """

    def test_uncorrected_screening_of_pure_noise_yields_spurious_discoveries(self):
        # 100 tests whose p-values are the deterministic grid 0.005, 0.015, ..., 0.995
        # -- exactly the uniform spread expected when every null is true. Raw testing
        # accepts the five below 0.05; no correction accepts any of them.
        factors = [
            CandidateFactorTest(
                f"N{i:03d}", f"Noise_{i:03d}",
                raw_t_stat=t_from_two_sided_p((2 * i + 1) / 200.0),
                raw_p_value=(2 * i + 1) / 200.0,
                sample_size=1000)
            for i in range(100)
        ]

        engine = FactorMultipleTestingCorrectionEngine()
        report = engine.audit_and_correct_factors(factors)

        self.assertEqual(report.raw_significant_count, 5)
        self.assertEqual(report.bonferroni_significant_count, 0)
        self.assertEqual(report.holm_significant_count, 0)
        self.assertEqual(report.bh_fdr_significant_count, 0)
        self.assertEqual(report.bhy_fdr_significant_count, 0)
        self.assertEqual(report.hlz_t3_significant_count, 0)
        self.assertEqual(report.false_discoveries_filtered_count, 5)


if __name__ == "__main__":
    unittest.main()
