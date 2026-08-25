import logging
import math
import unittest
from itertools import permutations

from feature_drift_monitor import (
    FeatureImportanceDriftMonitorEngine,
    STATUS_ALERT,
    STATUS_NORMAL,
)

logging.disable(logging.CRITICAL)


def shortcut_spearman(base_ranks, live_ranks):
    """
    Textbook shortcut, valid ONLY for distinct integer ranks:
        rho = 1 - 6 * sum(d^2) / (M * (M^2 - 1))
    Used here as an independent reference for the untied case; the engine
    deliberately does not use it.
    """
    m = len(base_ranks)
    d_sq = sum((a - b) ** 2 for a, b in zip(base_ranks, live_ranks))
    return 1.0 - (6.0 * d_sq) / (m * (m ** 2 - 1))


class TestSpearmanCorrelation(unittest.TestCase):

    def setUp(self):
        self.engine = FeatureImportanceDriftMonitorEngine()

    def test_matches_shortcut_formula_for_every_untied_permutation(self):
        # With distinct integer ranks the Pearson-on-ranks definition must reproduce
        # the shortcut exactly. Exhaustive over all 120 permutations of 1..5.
        base = [1, 2, 3, 4, 5]
        for perm in permutations(base):
            self.assertAlmostEqual(
                self.engine.compute_spearman_rank_correlation(base, list(perm)),
                shortcut_spearman(base, list(perm)),
                places=12,
                msg=f"disagreement on permutation {perm}",
            )

    def test_perfect_and_reversed_orderings_are_exactly_plus_and_minus_one(self):
        base = [1, 2, 3, 4]
        self.assertEqual(self.engine.compute_spearman_rank_correlation(base, [1, 2, 3, 4]), 1.0)
        self.assertEqual(self.engine.compute_spearman_rank_correlation(base, [4, 3, 2, 1]), -1.0)

    def test_constant_rank_vector_raises_instead_of_reporting_perfect_stability(self):
        with self.assertRaises(ValueError):
            self.engine.compute_spearman_rank_correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])

    def test_mismatched_lengths_and_degenerate_sizes_raise(self):
        with self.assertRaises(ValueError):
            self.engine.compute_spearman_rank_correlation([1, 2, 3], [1, 2])
        with self.assertRaises(ValueError):
            self.engine.compute_spearman_rank_correlation([1], [1])


class TestFeatureImportanceDriftMonitorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FeatureImportanceDriftMonitorEngine(
            min_spearman_rank_threshold=0.70,
            max_degradation_drop_pct=0.80,
        )
        self.base_map = {
            "rsi_14": 0.40,
            "volatility_20d": 0.30,
            "trend_50d": 0.20,
            "sentiment_score": 0.10,
        }

    def test_stable_feature_importance_passes(self):
        # Ordering preserved (rsi #1, vol #2, trend #3, sentiment #4) -> rho = 1.0.
        live_map = {
            "rsi_14": 0.38,
            "volatility_20d": 0.32,
            "trend_50d": 0.18,
            "sentiment_score": 0.12,
        }
        report = self.engine.audit_feature_importance_drift(
            "XGB_ALPHA_V1", "2026-W30", self.base_map, live_map)

        self.assertEqual(report.spearman_rank_correlation, 1.0)
        self.assertFalse(report.is_retrain_triggered)
        self.assertEqual(report.status, STATUS_NORMAL)
        self.assertEqual(report.trigger_reasons, [])
        self.assertEqual(report.common_feature_count, 4)
        self.assertEqual(report.top_n_rank_churn, 0)
        self.assertEqual(report.feature_set_overlap_ratio, 1.0)

    def test_regime_shift_feature_drift_triggers_retrain(self):
        # Complete reversal: sentiment #1, rsi #4. Hand-derived with the untied
        # shortcut: d = (3, 1, -1, -3), sum(d^2) = 20, M = 4
        #   rho = 1 - 6*20 / (4 * 15) = 1 - 2 = -1.0
        live_map = {
            "rsi_14": 0.05,
            "volatility_20d": 0.20,
            "trend_50d": 0.30,
            "sentiment_score": 0.45,
        }
        report = self.engine.audit_feature_importance_drift(
            "XGB_ALPHA_V1", "2026-W30-CRASH", self.base_map, live_map)

        self.assertEqual(report.spearman_rank_correlation, -1.0)
        self.assertTrue(report.is_retrain_triggered)
        self.assertEqual(report.status, STATUS_ALERT)
        # rsi_14 holds 40% of baseline importance and 5% of live importance:
        # a share ratio of 0.125, i.e. an 87.5% drop, beyond the 80% trigger.
        self.assertIn("rsi_14", report.degraded_features)
        self.assertEqual(report.top_n_rank_churn, 1)

    def test_tie_corrected_rho_does_not_depend_on_feature_names(self):
        # Same importance structure under two namings. The live profile ties two
        # features at 0.25, and a positional (non-mid-rank) ranking resolves that tie
        # by insertion order, so the pre-fix implementation returned -0.5 for the
        # first naming and -1.0 for the second. Mid-ranks give one answer:
        #   base ranks (1, 2, 3) vs live mid-ranks (2.5, 2.5, 1)
        #   cov = -1.5, var_base = 2, var_live = 1.5 -> rho = -1.5 / sqrt(3)
        expected = -math.sqrt(3.0) / 2.0
        naming_a = self.engine.audit_feature_importance_drift(
            "m", "w",
            {"alpha": 0.6, "beta": 0.3, "gamma": 0.1},
            {"alpha": 0.25, "beta": 0.25, "gamma": 0.5},
        )
        naming_b = self.engine.audit_feature_importance_drift(
            "m", "w",
            {"zulu": 0.6, "beta": 0.3, "gamma": 0.1},
            {"zulu": 0.25, "beta": 0.25, "gamma": 0.5},
        )
        self.assertAlmostEqual(naming_a.spearman_rank_correlation, expected, places=12)
        self.assertAlmostEqual(naming_b.spearman_rank_correlation, expected, places=12)

    def test_tied_importances_receive_averaged_mid_ranks(self):
        report = self.engine.audit_feature_importance_drift(
            "m", "w",
            {"a": 0.6, "b": 0.3, "c": 0.1},
            {"a": 0.5, "b": 0.25, "c": 0.25},
        )
        live_ranks = {d.feature_name: d.live_rank for d in report.rank_details}
        self.assertEqual(live_ranks["a"], 1.0)
        self.assertEqual(live_ranks["b"], 2.5)
        self.assertEqual(live_ranks["c"], 2.5)

    def test_different_importance_scales_do_not_fake_degradation(self):
        # Gain-based baseline summing to 100 vs mean|SHAP| live summing to 0.01, with
        # identical proportions. Comparing raw magnitudes reports a ~99.99% drop on
        # every feature; comparing shares correctly reports no change at all.
        gain_baseline = {"rsi_14": 40.0, "volatility_20d": 30.0, "trend_50d": 20.0,
                         "sentiment_score": 10.0}
        shap_live = {"rsi_14": 0.004, "volatility_20d": 0.003, "trend_50d": 0.002,
                     "sentiment_score": 0.001}
        report = self.engine.audit_feature_importance_drift(
            "m", "w", gain_baseline, shap_live)

        self.assertEqual(report.spearman_rank_correlation, 1.0)
        self.assertEqual(report.degraded_features, [])
        self.assertEqual(report.status, STATUS_NORMAL)
        rsi = next(d for d in report.rank_details if d.feature_name == "rsi_14")
        self.assertAlmostEqual(rsi.baseline_share, 0.40, places=12)
        self.assertAlmostEqual(rsi.live_share, 0.40, places=12)
        self.assertAlmostEqual(rsi.importance_ratio_live_to_base, 1.0, places=12)

    def test_top_feature_missing_from_live_profile_triggers_alert(self):
        # The live profile no longer reports the feature carrying 70% of baseline
        # importance. Intersecting the two maps and correlating the remainder yields a
        # perfect rho over the survivors; absence must not read as stability.
        baseline = {"rsi_14": 0.70, "vol_20d": 0.20, "trend_50d": 0.07, "sent": 0.03}
        live = {"vol_20d": 0.20, "trend_50d": 0.07, "sent": 0.03}
        report = self.engine.audit_feature_importance_drift("m", "w", baseline, live)

        self.assertEqual(report.status, STATUS_ALERT)
        self.assertTrue(report.is_retrain_triggered)
        self.assertIn("rsi_14", report.degraded_features)
        self.assertEqual(report.baseline_only_features, ["rsi_14"])
        self.assertAlmostEqual(report.feature_set_overlap_ratio, 0.75, places=12)
        self.assertEqual(report.top_n_rank_churn, 1)

    def test_unexpected_new_live_feature_breaches_overlap_requirement(self):
        live = dict(self.base_map)
        live["macro_rate_surprise"] = 0.05
        report = self.engine.audit_feature_importance_drift("m", "w", self.base_map, live)

        self.assertEqual(report.live_only_features, ["macro_rate_surprise"])
        self.assertAlmostEqual(report.feature_set_overlap_ratio, 0.8, places=12)
        self.assertEqual(report.status, STATUS_ALERT)
        self.assertTrue(any("overlap" in r for r in report.trigger_reasons))

    def test_relaxed_overlap_requirement_tolerates_a_new_feature(self):
        engine = FeatureImportanceDriftMonitorEngine(min_feature_set_overlap_ratio=0.75)
        live = dict(self.base_map)
        live["macro_rate_surprise"] = 0.05
        report = engine.audit_feature_importance_drift("m", "w", self.base_map, live)

        self.assertEqual(report.status, STATUS_NORMAL)
        self.assertEqual(report.live_only_features, ["macro_rate_surprise"])

    def test_degradation_boundary_is_exclusive(self):
        # Rank criterion disabled so only the degradation check can fire.
        engine = FeatureImportanceDriftMonitorEngine(min_spearman_rank_threshold=-1.0)
        baseline = {"a": 50, "b": 30, "c": 20}          # a holds a 0.50 share

        exactly_80pct_drop = {"a": 10, "b": 50, "c": 40}  # a -> 0.10 share, ratio 0.20
        report = engine.audit_feature_importance_drift("m", "w", baseline, exactly_80pct_drop)
        self.assertEqual(report.degraded_features, [])
        self.assertEqual(report.status, STATUS_NORMAL)

        just_beyond = {"a": 9, "b": 51, "c": 40}          # a -> 0.09 share, ratio 0.18
        report = engine.audit_feature_importance_drift("m", "w", baseline, just_beyond)
        self.assertEqual(report.degraded_features, ["a"])
        self.assertEqual(report.status, STATUS_ALERT)

    def test_degradation_check_ignores_features_outside_the_baseline_top_n(self):
        engine = FeatureImportanceDriftMonitorEngine(
            min_spearman_rank_threshold=-1.0, top_n_monitored_features=2)
        baseline = {"a": 50, "b": 30, "c": 20}
        # c is rank 3, outside the monitored top 2, and loses ~95% of its share.
        live = {"a": 50, "b": 49, "c": 1}
        report = engine.audit_feature_importance_drift("m", "w", baseline, live)
        self.assertEqual(report.degraded_features, [])

    def test_zero_importance_features_are_ranked_without_arbitrary_ordering(self):
        # Explicit zeros for unused features: all zeros tie at the same mid-rank on
        # both sides, so they add no spurious rank churn.
        baseline = {"a": 0.5, "b": 0.3, "c": 0.2, "d": 0.0, "e": 0.0}
        live = {"a": 0.5, "b": 0.3, "c": 0.2, "d": 0.0, "e": 0.0}
        report = self.engine.audit_feature_importance_drift("m", "w", baseline, live)
        self.assertEqual(report.spearman_rank_correlation, 1.0)
        ranks = {d.feature_name: d.baseline_rank for d in report.rank_details}
        self.assertEqual(ranks["d"], 4.5)
        self.assertEqual(ranks["e"], 4.5)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = FeatureImportanceDriftMonitorEngine()
        self.base_map = {"a": 0.5, "b": 0.3, "c": 0.2}

    def test_non_finite_importance_is_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                self.engine.audit_feature_importance_drift(
                    "m", "w", self.base_map, {"a": bad, "b": 0.3, "c": 0.2})

    def test_negative_importance_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_feature_importance_drift(
                "m", "w", self.base_map, {"a": -0.1, "b": 0.3, "c": 0.2})

    def test_all_zero_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_feature_importance_drift(
                "m", "w", self.base_map, {"a": 0.0, "b": 0.0, "c": 0.0})

    def test_empty_and_non_numeric_profiles_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_feature_importance_drift("m", "w", self.base_map, {})
        with self.assertRaises(TypeError):
            self.engine.audit_feature_importance_drift(
                "m", "w", self.base_map, {"a": "0.5", "b": 0.3, "c": 0.2})
        with self.assertRaises(TypeError):
            self.engine.audit_feature_importance_drift("m", "w", self.base_map, [0.5, 0.3, 0.2])

    def test_insufficient_common_features_raises_rather_than_passing(self):
        with self.assertRaises(ValueError):
            self.engine.audit_feature_importance_drift(
                "m", "w", self.base_map, {"a": 0.5, "z": 0.5})

    def test_constructor_rejects_out_of_range_configuration(self):
        with self.assertRaises(ValueError):
            FeatureImportanceDriftMonitorEngine(min_spearman_rank_threshold=1.5)
        with self.assertRaises(ValueError):
            FeatureImportanceDriftMonitorEngine(max_degradation_drop_pct=1.0)
        with self.assertRaises(ValueError):
            FeatureImportanceDriftMonitorEngine(max_degradation_drop_pct=0.0)
        with self.assertRaises(ValueError):
            FeatureImportanceDriftMonitorEngine(top_n_monitored_features=0)
        with self.assertRaises(ValueError):
            FeatureImportanceDriftMonitorEngine(min_common_features=2)
        with self.assertRaises(ValueError):
            FeatureImportanceDriftMonitorEngine(min_feature_set_overlap_ratio=0.0)


if __name__ == '__main__':
    unittest.main()
