"""
Unit tests for the Nogueira feature-selection stability engine.

Expected values are derived independently of the implementation:

- closed-form rationals worked out by hand from Definition 4 of the source paper;
- Theorem 1 of the same paper, which gives the estimator a second, completely
  different computational route (average pairwise intersection between folds);
- Kuncheva's (2007) consistency index, which Theorem 5 says the estimator must
  reproduce exactly when every fold selects the same number of features.

The variance and confidence-interval fixtures were cross-validated against the
authors' own reference implementation (https://github.com/nogueirs/JMLR2018,
``python/stability``) over 400 randomised selection matrices, agreeing to within
1e-15 on the stability, its variance, the interval bounds and the test p-value.
"""
import itertools
import logging
import math
import unittest
from typing import List, Sequence, Set

from feature_stability_analyzer import (
    DEFAULT_CONFIDENCE_LEVEL,
    STATUS_DEGENERATE,
    STATUS_STABLE,
    STATUS_UNSTABLE,
    FeatureStabilityAnalyzerEngine,
    normal_cdf,
    normal_quantile,
)

logging.disable(logging.CRITICAL)


def phi_via_pairwise_intersections(
    candidate_features: Sequence[str], folds: Sequence[Set[str]]
) -> float:
    """
    Independent route to Phi via Theorem 1 of the source paper, which states that
    the average pairwise intersection over the M(M-1) ordered pairs of feature sets
    equals ``k_bar - sum_f s_f^2``. Rearranging gives ``sum_f s_f^2`` without ever
    computing a per-feature variance, so this shares no arithmetic with the
    implementation beyond the final ratio.
    """
    d = len(candidate_features)
    k_bar = sum(len(f) for f in folds) / len(folds)
    intersections = [len(a & b) for a, b in itertools.permutations(folds, 2)]
    sum_s_squared = k_bar - sum(intersections) / len(intersections)
    return 1.0 - (sum_s_squared / d) / ((k_bar / d) * (1.0 - k_bar / d))


def kuncheva_consistency_index(
    candidate_features: Sequence[str], folds: Sequence[Set[str]]
) -> float:
    """
    Kuncheva's (2007) consistency index, averaged over all ordered fold pairs.
    Defined only when every fold selects the same number k of features; Theorem 5
    of the source paper states the Nogueira estimator generalises it, so the two
    must agree exactly on constant-cardinality inputs.
    """
    d = len(candidate_features)
    k = len(folds[0])
    pairs = [
        (len(a & b) - (k * k) / d) / (k - (k * k) / d)
        for a, b in itertools.permutations(folds, 2)
    ]
    return sum(pairs) / len(pairs)


class TestNormalDistributionHelpers(unittest.TestCase):
    """The stdlib-only replacements for scipy.stats.norm."""

    def test_quantile_reproduces_published_critical_values(self):
        # Standard normal critical values, to 6 decimal places.
        self.assertAlmostEqual(normal_quantile(0.975), 1.959964, places=6)
        self.assertAlmostEqual(normal_quantile(0.95), 1.644854, places=6)
        self.assertAlmostEqual(normal_quantile(0.995), 2.575829, places=6)
        self.assertAlmostEqual(normal_quantile(0.5), 0.0, places=12)

    def test_cdf_matches_known_values(self):
        self.assertAlmostEqual(normal_cdf(0.0), 0.5, places=12)
        self.assertAlmostEqual(normal_cdf(1.959964), 0.975, places=6)
        self.assertAlmostEqual(normal_cdf(-1.644854), 0.05, places=6)

    def test_quantile_rejects_out_of_range_probability(self):
        for bad in (0.0, 1.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                normal_quantile(bad)


class TestNogueiraIndex(unittest.TestCase):

    def setUp(self):
        self.engine = FeatureStabilityAnalyzerEngine(
            min_nogueira_stability_threshold=0.70,
            min_inclusion_threshold=0.80,
        )
        self.candidates = [f"f{i}" for i in range(1, 11)]  # f1..f10

    def test_perfectly_stable_feature_selection(self):
        # All 5 folds select the exact same 3 features -> every p_f is 0 or 1, so
        # every sample variance is 0 and Phi attains its maximum of exactly 1.
        fold_selections = [{"f1", "f2", "f3"} for _ in range(5)]
        report = self.engine.audit_feature_stability(self.candidates, fold_selections)

        self.assertEqual(report.nogueira_stability_index_phi, 1.0)
        self.assertEqual(report.status, STATUS_STABLE)
        self.assertEqual(report.consensus_features_count, 3)
        self.assertEqual(report.pruned_unstable_features_count, 7)
        self.assertIn("f1", report.consensus_feature_names)
        self.assertFalse(report.is_degenerate)

    def test_erratic_selection_matches_hand_derived_phi(self):
        # M = 10 features, K = 5 folds. Selection counts: three features appear in
        # 2 folds (p = 0.4) and seven appear in 1 fold (p = 0.2).
        #   sum p(1-p)  = 3(0.24) + 7(0.16)        = 1.84
        #   numerator   = (5/4)(1.84)/10           = 0.23
        #   k_bar       = (3+3+2+2+3)/5            = 2.6
        #   denominator = (2.6/10)(1 - 2.6/10)     = 0.1924
        #   Phi         = 1 - 0.23/0.1924 = 1 - 575/481 = -94/481
        fold_selections = [
            {"f1", "f2", "f3"},
            {"f4", "f5", "f6"},
            {"f7", "f8"},
            {"f9", "f10"},
            {"f1", "f4", "f7"},
        ]
        report = self.engine.audit_feature_stability(self.candidates, fold_selections)

        self.assertAlmostEqual(report.nogueira_stability_index_phi, -94 / 481, places=12)
        self.assertEqual(report.status, STATUS_UNSTABLE)
        self.assertEqual(report.consensus_features_count, 0)
        self.assertEqual(report.pruned_unstable_features_count, 10)
        self.assertAlmostEqual(report.average_features_per_fold, 2.6, places=12)

    def test_phi_is_reported_at_full_precision(self):
        # Rounding Phi to 4 dp before comparing it to the threshold can flip a
        # borderline verdict, so the reported value must carry full precision.
        fold_selections = [
            {"f1", "f2", "f3"},
            {"f4", "f5", "f6"},
            {"f7", "f8"},
            {"f9", "f10"},
            {"f1", "f4", "f7"},
        ]
        phi, _, _ = self.engine.calculate_nogueira_index(self.candidates, fold_selections)
        self.assertNotEqual(phi, round(phi, 4))

    def test_phi_matches_pairwise_intersection_identity(self):
        """Theorem 1 gives an independent computational route to the same value."""
        fixtures: List[List[Set[str]]] = [
            [{"f1", "f2"}, {"f2", "f3"}, {"f1", "f3"}, {"f1", "f2"}, {"f2", "f4"}],
            [{"f1", "f2", "f3"}, {"f1", "f2"}, {"f1"}, {"f1", "f2", "f3", "f4"}],
            [{"f5"}, {"f6"}, {"f7"}, {"f8"}, {"f9"}, {"f10"}],
            [{"f1", "f2", "f3", "f4", "f5"}, {"f1", "f2", "f3", "f4", "f6"},
             {"f1", "f2", "f3", "f7", "f8"}],
        ]
        for folds in fixtures:
            with self.subTest(folds=folds):
                phi, _, _ = self.engine.calculate_nogueira_index(self.candidates, folds)
                self.assertAlmostEqual(
                    phi, phi_via_pairwise_intersections(self.candidates, folds), places=12
                )

    def test_phi_equals_kuncheva_index_for_constant_cardinality(self):
        """Theorem 5: the estimator generalises Kuncheva's consistency index."""
        fixtures: List[List[Set[str]]] = [
            [{"f1", "f2", "f3"}, {"f1", "f2", "f4"}, {"f1", "f5", "f6"},
             {"f2", "f3", "f7"}, {"f1", "f2", "f3"}],
            [{"f1", "f2"}, {"f3", "f4"}, {"f5", "f6"}, {"f7", "f8"}],
            [{"f1"}, {"f1"}, {"f2"}, {"f3"}, {"f1"}],
        ]
        for folds in fixtures:
            with self.subTest(folds=folds):
                phi, _, _ = self.engine.calculate_nogueira_index(self.candidates, folds)
                self.assertAlmostEqual(
                    phi, kuncheva_consistency_index(self.candidates, folds), places=12
                )

    def test_phi_respects_theoretical_bounds(self):
        # Appendix D of the source paper: Phi is bounded above by 1 and below by
        # -1/(K-1), which is -0.25 for 5 folds. Maximum disagreement is achieved by
        # partitioning disjoint features across folds.
        fold_selections = [{"f1", "f2"}, {"f3", "f4"}, {"f5", "f6"}, {"f7", "f8"},
                           {"f9", "f10"}]
        report = self.engine.audit_feature_stability(self.candidates, fold_selections)

        self.assertAlmostEqual(report.phi_theoretical_lower_bound, -0.25, places=12)
        self.assertGreaterEqual(
            report.nogueira_stability_index_phi, report.phi_theoretical_lower_bound - 1e-12
        )
        self.assertLessEqual(report.nogueira_stability_index_phi, 1.0)
        self.assertLess(report.nogueira_stability_index_phi, 0.0)


class TestDegenerateSelection(unittest.TestCase):
    """
    Phi is undefined when k_bar = 0 or k_bar = d (the denominator is zero). These
    are regression tests: reporting Phi = 1.0 / STABLE_FEATURE_SET here would pass
    a stability gate on a selector that had stopped discriminating entirely.
    """

    def setUp(self):
        self.engine = FeatureStabilityAnalyzerEngine()
        self.candidates = [f"f{i}" for i in range(1, 11)]

    def test_no_features_selected_in_any_fold_is_degenerate(self):
        report = self.engine.audit_feature_stability(self.candidates, [set() for _ in range(5)])

        self.assertEqual(report.status, STATUS_DEGENERATE)
        self.assertNotEqual(report.status, STATUS_STABLE)
        self.assertIsNone(report.nogueira_stability_index_phi)
        self.assertIsNone(report.stability_variance)
        self.assertIsNone(report.stability_ci_lower)
        self.assertTrue(report.is_degenerate)
        self.assertFalse(report.stability_significantly_above_threshold)
        self.assertEqual(report.consensus_features_count, 0)
        self.assertIn("no features were selected", report.audit_notes)

    def test_all_features_selected_in_every_fold_is_degenerate(self):
        folds = [set(self.candidates) for _ in range(5)]
        report = self.engine.audit_feature_stability(self.candidates, folds)

        self.assertEqual(report.status, STATUS_DEGENERATE)
        self.assertIsNone(report.nogueira_stability_index_phi)
        self.assertTrue(report.is_degenerate)
        # Every feature still reaches the inclusion threshold, which is exactly why
        # the stability verdict must not be reported as a pass.
        self.assertEqual(report.consensus_features_count, 10)

    def test_partial_selection_in_every_fold_is_not_degenerate(self):
        folds = [set(self.candidates) - {"f1"} for _ in range(5)]
        report = self.engine.audit_feature_stability(self.candidates, folds)

        self.assertFalse(report.is_degenerate)
        self.assertEqual(report.nogueira_stability_index_phi, 1.0)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = FeatureStabilityAnalyzerEngine()
        self.candidates = [f"f{i}" for i in range(1, 11)]

    def test_feature_outside_candidate_pool_raises(self):
        # An unselected-but-unknown feature inflates k_bar without contributing a
        # p_f, silently biasing Phi. It must be rejected, not absorbed.
        folds = [{"f1", "f2"}, {"f1", "unknown_feature"}, {"f1", "f2"}]
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_feature_stability(self.candidates, folds)
        self.assertIn("unknown_feature", str(ctx.exception))

    def test_duplicate_candidate_features_raise(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_feature_stability(
                ["f1", "f2", "f1"], [{"f1"}, {"f2"}, {"f1"}]
            )
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_empty_candidate_pool_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_feature_stability([], [set(), set()])

    def test_fewer_than_two_folds_raises(self):
        for folds in ([], [{"f1"}]):
            with self.assertRaises(ValueError):
                self.engine.audit_feature_stability(self.candidates, folds)

    def test_non_string_candidate_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_feature_stability(["f1", 2], [{"f1"}, {"f1"}])

    def test_string_passed_as_a_fold_raises(self):
        # "f1" iterates as characters, which would otherwise be read as one-letter
        # features and rejected downstream with a confusing message.
        with self.assertRaises(ValueError):
            self.engine.audit_feature_stability(self.candidates, ["f1", {"f1"}])

    def test_invalid_thresholds_raise(self):
        with self.assertRaises(ValueError):
            FeatureStabilityAnalyzerEngine(min_nogueira_stability_threshold=1.5)
        with self.assertRaises(ValueError):
            FeatureStabilityAnalyzerEngine(min_inclusion_threshold=0.0)
        with self.assertRaises(ValueError):
            FeatureStabilityAnalyzerEngine(min_inclusion_threshold=1.2)
        with self.assertRaises(ValueError):
            FeatureStabilityAnalyzerEngine(confidence_level=1.0)

    def test_repeated_entries_in_a_fold_do_not_inflate_k_bar(self):
        # Folds supplied as lists are deduplicated; a repeated name is one selection.
        as_lists = [["f1", "f2", "f2"], ["f1", "f2"], ["f1", "f2"]]
        as_sets = [{"f1", "f2"}, {"f1", "f2"}, {"f1", "f2"}]
        phi_lists, k_bar_lists, _ = self.engine.calculate_nogueira_index(
            self.candidates, as_lists
        )
        phi_sets, k_bar_sets, _ = self.engine.calculate_nogueira_index(
            self.candidates, as_sets
        )
        self.assertEqual(k_bar_lists, k_bar_sets)
        self.assertEqual(k_bar_lists, 2.0)
        self.assertEqual(phi_lists, phi_sets)


class TestConsensusExtraction(unittest.TestCase):

    def setUp(self):
        self.candidates = [f"f{i}" for i in range(1, 11)]

    def test_consensus_boundary_is_an_exact_fold_count(self):
        # At K = 5 and an 80% inclusion threshold, a feature needs 4 folds exactly.
        engine = FeatureStabilityAnalyzerEngine(min_inclusion_threshold=0.80)
        folds = [
            {"f1", "f2", "f3"},   # f1: 4/5 (consensus), f2: 3/5 (pruned)
            {"f1", "f2", "f3"},
            {"f1", "f2", "f3"},
            {"f1", "f4"},
            {"f4"},
        ]
        report = engine.audit_feature_stability(self.candidates, folds)

        self.assertEqual(report.min_folds_for_consensus, 4)
        self.assertIn("f1", report.consensus_feature_names)
        self.assertIn("f2", report.pruned_feature_names)
        self.assertIn("f3", report.pruned_feature_names)
        detail = {d.feature_name: d for d in report.inclusion_details}
        self.assertEqual(detail["f1"].selection_count_folds, 4)
        self.assertAlmostEqual(detail["f1"].inclusion_probability, 0.8, places=12)
        self.assertTrue(detail["f1"].is_consensus_feature)
        self.assertFalse(detail["f2"].is_consensus_feature)

    def test_required_fold_count_scales_with_k(self):
        # An 80% threshold is not reachable by 2 of 3 folds (66.7%), so at K = 3 it
        # silently means "selected in every fold". The report makes that visible.
        engine = FeatureStabilityAnalyzerEngine(min_inclusion_threshold=0.80)
        self.assertEqual(engine._min_folds_for_consensus(3), 3)
        self.assertEqual(engine._min_folds_for_consensus(5), 4)
        self.assertEqual(engine._min_folds_for_consensus(10), 8)
        self.assertEqual(engine._min_folds_for_consensus(20), 16)

    def test_fold_count_below_recommended_minimum_is_flagged(self):
        engine = FeatureStabilityAnalyzerEngine()
        folds = [{"f1", "f2"}, {"f1", "f3"}]
        report = engine.audit_feature_stability(self.candidates, folds)
        self.assertTrue(report.folds_below_recommended_minimum)

        five_folds = [{"f1", "f2"}, {"f1", "f3"}, {"f1", "f2"}, {"f1", "f3"}, {"f1", "f2"}]
        self.assertFalse(
            engine.audit_feature_stability(self.candidates, five_folds)
            .folds_below_recommended_minimum
        )


class TestUncertaintyQuantification(unittest.TestCase):
    """
    Theorem 7 (variance), Corollary 8 (confidence interval) and Section 4.2.4
    (one-sided test) of the source paper.

    The numeric fixture below was cross-validated against the authors' reference
    implementation, which returns stability 0.7536945812807881 and variance
    0.00764616002761418 for the same selection matrix.
    """

    def setUp(self):
        self.engine = FeatureStabilityAnalyzerEngine(
            min_nogueira_stability_threshold=0.70, min_inclusion_threshold=0.80
        )
        self.candidates = [f"f{i}" for i in range(10)]
        # Four features chosen in nearly every fold, with one dropout and two
        # one-off additions. Hand-derived Phi:
        #   p:  f1=f2=f4=1, f3=0.8, f0=f8=0.2, rest 0
        #   sum p(1-p) = 3 x 0.16 = 0.48;  numerator = (5/4)(0.48)/10 = 0.06
        #   k_bar = (4+4+4+4+5)/5 = 4.2;   denominator = 0.42 x 0.58 = 0.2436
        #   Phi = 1 - 0.06/0.2436 = 1 - 50/203 = 153/203 = 0.75369...
        self.folds = [
            {"f0", "f1", "f2", "f4"},
            {"f1", "f2", "f3", "f4"},
            {"f1", "f2", "f3", "f4"},
            {"f1", "f2", "f3", "f4"},
            {"f1", "f2", "f3", "f4", "f8"},
        ]

    def test_variance_and_interval_match_reference_values(self):
        stats = self.engine.compute_stability_statistics(self.candidates, self.folds)

        self.assertAlmostEqual(stats.nogueira_stability_index_phi, 153 / 203, places=12)
        self.assertAlmostEqual(stats.variance, 0.00764616002761418, places=12)
        self.assertEqual(stats.confidence_level, DEFAULT_CONFIDENCE_LEVEL)
        # Corollary 8: Phi +/- z_(1-alpha/2) * sqrt(v(Phi)).
        half_width = normal_quantile(0.975) * math.sqrt(stats.variance)
        self.assertAlmostEqual(
            stats.ci_lower, stats.nogueira_stability_index_phi - half_width, places=12
        )
        self.assertAlmostEqual(
            stats.ci_upper, stats.nogueira_stability_index_phi + half_width, places=12
        )
        self.assertAlmostEqual(stats.ci_lower, 0.582310775504054, places=9)

    def test_point_estimate_above_threshold_can_still_be_insignificant(self):
        # Phi = 0.7537 clears the 0.70 gate, but the 95% interval reaches down to
        # 0.58 and the one-sided test does not reject at alpha = 0.05. This is the
        # case the point-estimate-only gate cannot see.
        report = self.engine.audit_feature_stability(self.candidates, self.folds)

        self.assertEqual(report.status, STATUS_STABLE)
        self.assertFalse(report.stability_significantly_above_threshold)
        self.assertAlmostEqual(report.stability_test_p_value, 0.2695887925856073, places=9)
        self.assertLess(report.stability_ci_lower, 0.70)
        self.assertIn("NOT significant", report.audit_notes)

    def test_perfect_stability_has_zero_variance_and_is_significant(self):
        folds = [{"f1", "f2", "f3"} for _ in range(5)]
        report = self.engine.audit_feature_stability(self.candidates, folds)

        self.assertEqual(report.nogueira_stability_index_phi, 1.0)
        self.assertAlmostEqual(report.stability_variance, 0.0, places=12)
        self.assertTrue(report.stability_significantly_above_threshold)
        self.assertEqual(report.stability_test_p_value, 0.0)
        self.assertEqual(report.stability_ci_lower, 1.0)

    def test_zero_variance_below_threshold_is_not_significant(self):
        # Guards the division-by-zero branch in the other direction: a degenerate
        # variance must never be read as evidence of stability.
        p_value, significant = self.engine.test_stability_against_threshold(
            phi=0.50, variance=0.0
        )
        self.assertEqual(p_value, 1.0)
        self.assertFalse(significant)

    def test_p_value_falls_as_the_estimate_rises(self):
        variance = 0.0076
        p_low, _ = self.engine.test_stability_against_threshold(0.72, variance)
        p_high, sig_high = self.engine.test_stability_against_threshold(0.95, variance)
        self.assertLess(p_high, p_low)
        self.assertTrue(sig_high)
        self.assertAlmostEqual(
            p_low, 1.0 - normal_cdf((0.72 - 0.70) / math.sqrt(variance)), places=12
        )


if __name__ == "__main__":
    unittest.main()
