import math
import random
import unittest

from scipy import stats

from model_ab_tester import (
    ABTestConfig,
    ABTestReport,
    ExperimentStatus,
    ModelABTesterEngine,
    ModelExecutionResult,
    RecommendedAction,
    TestMode,
)


def _normal_two_tailed_p(t_statistic: float) -> float:
    """The v1 normal-CDF approximation, kept only so regression tests can
    assert the current implementation no longer agrees with it."""
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t_statistic) / math.sqrt(2.0))))


class TestModelABTesterEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ModelABTesterEngine()
        self.config = ABTestConfig(
            experiment_id="EXP_001",
            champion_model_id="CHAMPION_V1",
            challenger_model_id="CHALLENGER_V2",
            traffic_split_ratio=0.80,
            test_mode="LIVE_SPLIT",
            min_sample_size=30,
        )

    @staticmethod
    def _results(model_id, returns_bps):
        return [
            ModelExecutionResult(model_id, float(i), "AAPL", float(v), 5.0)
            for i, v in enumerate(returns_bps)
        ]

    # ------------------------------------------------------------------
    # Welch's t-test: numerical correctness
    # ------------------------------------------------------------------

    def test_welch_statistics_match_hand_computed_values(self):
        """
        Champion [1,2,3,4,5]: mean 3.0, s^2 = 2.5, n = 5.
        Challenger [4,5,6,7,9]: mean 6.2, s^2 = 3.7, n = 5.

        SE   = sqrt(2.5/5 + 3.7/5) = sqrt(1.24)
        t    = (6.2 - 3.0) / sqrt(1.24)              = 2.8736848...
        df   = 1.24^2 / (0.5^2/4 + 0.74^2/4)         = 7.7111334...

        All three derived by hand from the NIST 1.3.5.3 definitions, not by
        re-running the implementation's own arithmetic.
        """
        cfg = ABTestConfig("EXP_HAND", "CHAMP", "CHAL", min_sample_size=5)
        report = self.engine.evaluate_ab_test_results(
            cfg,
            self._results("CHAMP", [1, 2, 3, 4, 5]),
            self._results("CHAL", [4, 5, 6, 7, 9]),
        )

        self.assertEqual(report.status, ExperimentStatus.AB_TEST_COMPLETED)
        self.assertAlmostEqual(report.champion_mean_return_bps, 3.0, places=12)
        self.assertAlmostEqual(report.challenger_mean_return_bps, 6.2, places=12)
        self.assertAlmostEqual(report.mean_difference_bps, 3.2, places=12)
        self.assertAlmostEqual(report.welch_t_statistic, 3.2 / math.sqrt(1.24), places=12)
        self.assertAlmostEqual(report.welch_t_statistic, 2.8736848324, places=9)
        self.assertAlmostEqual(report.degrees_of_freedom, 7.7111334002, places=9)
        self.assertAlmostEqual(report.p_value, 0.0215240042, places=9)

    def test_statistics_match_scipy_welch_reference(self):
        """Cross-check t, df and p against an independent implementation
        (scipy.stats.ttest_ind with equal_var=False) over unequal sample sizes
        and unequal variances -- the regime Welch's test exists for."""
        rng = random.Random(20260826)
        cfg = ABTestConfig("EXP_REF", "CHAMP", "CHAL", min_sample_size=5)

        for _ in range(50):
            n_a, n_b = rng.randint(5, 120), rng.randint(5, 120)
            sd_a, sd_b = rng.uniform(0.5, 40.0), rng.uniform(0.5, 40.0)
            champ = [rng.gauss(1.0, sd_a) for _ in range(n_a)]
            chal = [rng.gauss(1.4, sd_b) for _ in range(n_b)]

            report = self.engine.evaluate_ab_test_results(
                cfg, self._results("CHAMP", champ), self._results("CHAL", chal)
            )
            reference = stats.ttest_ind(chal, champ, equal_var=False)

            self.assertAlmostEqual(report.welch_t_statistic, reference.statistic, places=10)
            self.assertAlmostEqual(report.degrees_of_freedom, reference.df, places=10)
            self.assertAlmostEqual(report.p_value, reference.pvalue, places=12)

    def test_p_value_uses_t_distribution_not_normal_approximation(self):
        """
        Regression. v1 drew the p-value from the normal CDF, which understates
        p at every finite df and so over-promotes.

        This fixture (n = 30 per arm, identical spread, challenger shifted by
        +1.048 bps) sits in the disagreement band: t = 1.9616 on df = 58 gives
        an exact Welch p of 0.05462 -- not significant -- while the normal
        approximation gives 0.04981 and v1 recommended PROMOTE on it.
        """
        base = [float((i % 7) - 3) for i in range(30)]
        report = self.engine.evaluate_ab_test_results(
            self.config,
            self._results("CHAMPION_V1", base),
            self._results("CHALLENGER_V2", [v + 1.048 for v in base]),
        )

        self.assertAlmostEqual(report.welch_t_statistic, 1.961568, places=5)
        self.assertAlmostEqual(report.degrees_of_freedom, 58.0, places=9)
        self.assertAlmostEqual(report.p_value, 0.054619, places=5)

        # The exact test declines to promote; the v1 approximation would have.
        self.assertFalse(report.is_statistically_significant)
        self.assertEqual(
            report.recommended_action,
            RecommendedAction.CONTINUE_EXPERIMENT_NO_SIGNIFICANT_DIFFERENCE,
        )
        self.assertLess(_normal_two_tailed_p(report.welch_t_statistic), 0.05)

    def test_p_value_is_not_rounded_before_the_significance_comparison(self):
        """v1 rounded p to 4dp and compared the rounded value against alpha,
        so a p just under the threshold could be rounded up and rejected."""
        rng = random.Random(4242)
        cfg = ABTestConfig("EXP_ROUND", "CHAMP", "CHAL", min_sample_size=30)
        champ = [rng.gauss(0.0, 5.0) for _ in range(60)]
        chal = [rng.gauss(2.0, 5.0) for _ in range(60)]
        report = self.engine.evaluate_ab_test_results(
            cfg, self._results("CHAMP", champ), self._results("CHAL", chal)
        )
        self.assertEqual(report.p_value, report.p_value)  # not NaN
        self.assertNotEqual(report.p_value, round(report.p_value, 4))
        self.assertEqual(
            report.is_statistically_significant,
            report.p_value < cfg.significance_level_alpha,
        )

    # ------------------------------------------------------------------
    # Decision logic
    # ------------------------------------------------------------------

    def test_promote_challenger_statistical_significance(self):
        """50 champion samples around 2.0 bps vs 50 challenger around 8.0 bps."""
        champ = [2.0 + (i % 3) * 0.1 for i in range(50)]
        chal = [8.0 + (i % 3) * 0.1 for i in range(50)]

        report = self.engine.evaluate_ab_test_results(
            self.config,
            self._results("CHAMPION_V1", champ),
            self._results("CHALLENGER_V2", chal),
        )

        self.assertEqual(report.status, ExperimentStatus.AB_TEST_COMPLETED)
        self.assertTrue(report.is_statistically_significant)
        self.assertEqual(
            report.recommended_action, RecommendedAction.PROMOTE_CHALLENGER_TO_CHAMPION
        )
        self.assertGreater(report.welch_t_statistic, 10.0)
        self.assertLess(report.p_value, 0.01)
        self.assertAlmostEqual(report.mean_difference_bps, 6.0, places=9)

    def test_reject_underperforming_challenger(self):
        """Champion around +5 bps, challenger around -2 bps, with genuine
        dispersion in both arms so the verdict rests on a real t-statistic."""
        rng = random.Random(31337)
        champ = [rng.gauss(5.0, 1.5) for _ in range(50)]
        chal = [rng.gauss(-2.0, 2.5) for _ in range(50)]

        report = self.engine.evaluate_ab_test_results(
            self.config,
            self._results("CHAMPION_V1", champ),
            self._results("CHALLENGER_V2", chal),
        )

        self.assertEqual(report.recommended_action, RecommendedAction.REJECT_CHALLENGER)
        self.assertTrue(report.is_statistically_significant)
        self.assertLess(report.mean_difference_bps, 0.0)
        self.assertLess(report.welch_t_statistic, 0.0)

    def test_insufficient_samples_leaves_statistics_unmeasured(self):
        """v1 reported p_value = 1.0 and t = 0.0 for a test it never ran, which
        is indistinguishable on a dashboard from a genuine null result."""
        report = self.engine.evaluate_ab_test_results(
            self.config,
            self._results("CHAMPION_V1", [1.0] * 10),
            self._results("CHALLENGER_V2", [2.0] * 10),
        )

        self.assertEqual(
            report.recommended_action,
            RecommendedAction.CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES,
        )
        self.assertEqual(report.status, ExperimentStatus.AB_TEST_INSUFFICIENT_SAMPLES)
        self.assertIsNone(report.p_value)
        self.assertIsNone(report.welch_t_statistic)
        self.assertIsNone(report.degrees_of_freedom)
        self.assertIsNone(report.champion_mean_return_bps)
        self.assertFalse(report.is_statistically_significant)

    # ------------------------------------------------------------------
    # Degenerate and corrupt samples
    # ------------------------------------------------------------------

    def test_zero_variance_samples_are_not_declared_significant(self):
        """
        Regression. v1 floored both sample variances at 1e-6, so two constant
        samples produced t = -35000 and p = 0.0 -- certainty manufactured from
        data containing no information about sampling variability.
        """
        report = self.engine.evaluate_ab_test_results(
            self.config,
            self._results("CHAMPION_V1", [5.0] * 50),
            self._results("CHALLENGER_V2", [-2.0] * 50),
        )

        self.assertEqual(
            report.recommended_action, RecommendedAction.ABORT_EXPERIMENT_INVALID_DATA
        )
        self.assertEqual(report.status, ExperimentStatus.AB_TEST_INVALID_DATA)
        self.assertFalse(report.is_statistically_significant)
        self.assertIsNone(report.p_value)
        self.assertIsNone(report.welch_t_statistic)
        self.assertIn("zero variance", report.audit_notes)

    def test_single_zero_variance_arm_is_still_testable(self):
        """One constant arm keeps Welch's statistic well defined (df = n-1 of
        the other arm), so it must not be swept into the invalid-data branch."""
        rng = random.Random(9)
        report = self.engine.evaluate_ab_test_results(
            self.config,
            self._results("CHAMPION_V1", [1.0] * 40),
            self._results("CHALLENGER_V2", [rng.gauss(6.0, 2.0) for _ in range(40)]),
        )
        self.assertEqual(report.status, ExperimentStatus.AB_TEST_COMPLETED)
        self.assertAlmostEqual(report.degrees_of_freedom, 39.0, places=9)
        self.assertEqual(
            report.recommended_action, RecommendedAction.PROMOTE_CHALLENGER_TO_CHAMPION
        )

    def test_non_finite_returns_abort_the_experiment(self):
        """
        Regression. Every comparison against NaN is False, so in v1 a poisoned
        sample yielded t = 0.0, p = 1.0 and a clean-looking inconclusive verdict.
        """
        for bad_value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad_value):
                champ = [1.0] * 50
                champ[7] = bad_value
                report = self.engine.evaluate_ab_test_results(
                    self.config,
                    self._results("CHAMPION_V1", champ),
                    self._results("CHALLENGER_V2", [2.0 + (i % 5) for i in range(50)]),
                )
                self.assertEqual(
                    report.recommended_action,
                    RecommendedAction.ABORT_EXPERIMENT_INVALID_DATA,
                )
                self.assertIsNone(report.p_value)
                self.assertIn("champion sample[7]", report.audit_notes)

    def test_overflowing_variance_aborts_instead_of_raising(self):
        """Returns finite element-wise can still overflow their own variance.
        That is a mis-parsed feed, and must surface as invalid data rather than
        an OverflowError escaping the evaluator."""
        for scale in (1e200, 1e307):
            with self.subTest(scale=scale):
                champ = [scale * (1 + 0.001 * (i % 7)) for i in range(40)]
                chal = [1.5 * scale * (1 + 0.001 * (i % 7)) for i in range(40)]
                report = self.engine.evaluate_ab_test_results(
                    self.config,
                    self._results("CHAMPION_V1", champ),
                    self._results("CHALLENGER_V2", chal),
                )
                self.assertEqual(
                    report.recommended_action,
                    RecommendedAction.ABORT_EXPERIMENT_INVALID_DATA,
                )
                self.assertIsNone(report.p_value)

    def test_degrees_of_freedom_survive_extreme_variance_ratios(self):
        """Welch's nu collapses toward n-1 of the dominant arm. The scale-free
        formulation must reach that limit without overflowing on the way."""
        report = self.engine.evaluate_ab_test_results(
            self.config,
            self._results("CHAMPION_V1", [(i % 2) * 1e-8 for i in range(40)]),
            self._results("CHALLENGER_V2", [(i % 2) * 1e8 for i in range(40)]),
        )
        self.assertEqual(report.status, ExperimentStatus.AB_TEST_COMPLETED)
        self.assertAlmostEqual(report.degrees_of_freedom, 39.0, places=6)

    def test_empty_samples_report_insufficient_not_crash(self):
        report = self.engine.evaluate_ab_test_results(self.config, [], [])
        self.assertEqual(
            report.recommended_action,
            RecommendedAction.CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES,
        )
        self.assertEqual(report.champion_sample_count, 0)

    def test_config_is_frozen_after_validation(self):
        """A pre-registered experiment whose parameters can be edited mid-flight
        is not pre-registered -- and a mutable test_mode could be walked past
        the validation it just passed, routing live orders to the challenger."""
        import dataclasses

        for attribute, value in (
            ("test_mode", "SHADOW"),
            ("min_sample_size", 5),
            ("significance_level_alpha", 0.5),
            ("traffic_split_ratio", 0.0),
        ):
            with self.subTest(attribute=attribute):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(self.config, attribute, value)

    def test_swapped_result_lists_are_detected(self):
        """Passing the arms in the wrong order inverts every recommendation,
        so provenance is checked rather than trusted."""
        champ = self._results("CHAMPION_V1", [1.0 + (i % 4) for i in range(50)])
        chal = self._results("CHALLENGER_V2", [8.0 + (i % 4) for i in range(50)])

        report = self.engine.evaluate_ab_test_results(self.config, chal, champ)

        self.assertEqual(
            report.recommended_action, RecommendedAction.ABORT_EXPERIMENT_INVALID_DATA
        )
        self.assertIn("swapped", report.audit_notes)

    # ------------------------------------------------------------------
    # Configuration validation
    # ------------------------------------------------------------------

    def test_unrecognised_test_mode_is_rejected(self):
        """Regression. v1 compared test_mode against the literal 'SHADOW', so
        'shadow' fell through to the live-split branch and routed real orders
        to the unvalidated challenger."""
        for bad_mode in ("shadow", "Shadow", "SHADOW_MODE", "live", "", None):
            with self.subTest(mode=bad_mode):
                with self.assertRaises(ValueError):
                    ABTestConfig("E", "CHAMP", "CHAL", test_mode=bad_mode)

    def test_invalid_numeric_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            ABTestConfig("E", "CHAMP", "CHAL", traffic_split_ratio=1.7)
        with self.assertRaises(ValueError):
            ABTestConfig("E", "CHAMP", "CHAL", traffic_split_ratio=-0.5)
        with self.assertRaises(ValueError):
            ABTestConfig("E", "CHAMP", "CHAL", traffic_split_ratio=float("nan"))
        with self.assertRaises(ValueError):
            ABTestConfig("E", "CHAMP", "CHAL", significance_level_alpha=0.0)
        with self.assertRaises(ValueError):
            ABTestConfig("E", "CHAMP", "CHAL", significance_level_alpha=1.0)

    def test_min_sample_size_below_two_is_rejected(self):
        """Regression. v1 accepted min_sample_size = 1 and then raised
        ZeroDivisionError inside the Welch-Satterthwaite denominator."""
        for bad_n in (1, 0, -5):
            with self.subTest(n=bad_n):
                with self.assertRaises(ValueError):
                    ABTestConfig("E", "CHAMP", "CHAL", min_sample_size=bad_n)

    def test_identical_model_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            ABTestConfig("E", "SAME_MODEL", "SAME_MODEL")

    def test_blank_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            ABTestConfig("", "CHAMP", "CHAL")
        with self.assertRaises(ValueError):
            ABTestConfig("E", "   ", "CHAL")

    # ------------------------------------------------------------------
    # Traffic routing
    # ------------------------------------------------------------------

    def test_deterministic_traffic_routing(self):
        self.assertEqual(
            self.engine.route_request(self.config, "AAPL"),
            self.engine.route_request(self.config, "AAPL"),
        )

        shadow_cfg = ABTestConfig("EXP_002", "CHAMPION_V1", "CHALLENGER_V2", test_mode="SHADOW")
        for key in ("MSFT", "AAPL", "TSLA", "NVDA"):
            self.assertEqual(self.engine.route_request(shadow_cfg, key), "CHAMPION_V1")

    def test_traffic_split_ratio_is_honoured_in_aggregate(self):
        """A deterministic hash is only useful if it is also near-uniform."""
        keys = [f"ACC_{i:06d}" for i in range(20000)]
        champion_share = sum(
            self.engine.route_request(self.config, k) == "CHAMPION_V1" for k in keys
        ) / len(keys)
        self.assertAlmostEqual(champion_share, 0.80, delta=0.01)

    def test_boundary_split_ratios(self):
        all_challenger = ABTestConfig("E0", "CHAMP", "CHAL", traffic_split_ratio=0.0)
        all_champion = ABTestConfig("E1", "CHAMP", "CHAL", traffic_split_ratio=1.0)
        for key in (f"SYM_{i}" for i in range(500)):
            self.assertEqual(self.engine.route_request(all_challenger, key), "CHAL")
            self.assertEqual(self.engine.route_request(all_champion, key), "CHAMP")

    def test_routing_is_independent_across_experiments(self):
        """Regression. v1 hashed the request key alone, so every concurrent
        experiment bucketed every key identically and the allocations were
        perfectly correlated instead of independent."""
        cfg_a = ABTestConfig("EXP_A", "CHAMP", "CHAL", traffic_split_ratio=0.5)
        cfg_b = ABTestConfig("EXP_B", "CHAMP", "CHAL", traffic_split_ratio=0.5)
        keys = [f"SYM_{i:05d}" for i in range(4000)]
        agreement = sum(
            self.engine.route_request(cfg_a, k) == self.engine.route_request(cfg_b, k)
            for k in keys
        ) / len(keys)
        # Independent 50/50 allocations agree ~50% of the time, not 100%.
        self.assertAlmostEqual(agreement, 0.50, delta=0.03)

    def test_route_request_rejects_empty_key(self):
        for bad_key in ("", None, 12345):
            with self.subTest(key=bad_key):
                with self.assertRaises(ValueError):
                    self.engine.route_request(self.config, bad_key)

    def test_shadow_challenger_id(self):
        shadow_cfg = ABTestConfig("EXP_S", "CHAMPION_V1", "CHALLENGER_V2", test_mode="SHADOW")
        self.assertEqual(self.engine.shadow_challenger_id(shadow_cfg), "CHALLENGER_V2")
        self.assertIsNone(self.engine.shadow_challenger_id(self.config))

    # ------------------------------------------------------------------
    # API compatibility
    # ------------------------------------------------------------------

    def test_enum_members_compare_equal_to_v1_string_literals(self):
        self.assertEqual(TestMode.SHADOW, "SHADOW")
        self.assertEqual(self.config.test_mode, "LIVE_SPLIT")
        report = self.engine.evaluate_ab_test_results(
            self.config,
            self._results("CHAMPION_V1", [2.0 + (i % 3) * 0.1 for i in range(50)]),
            self._results("CHALLENGER_V2", [8.0 + (i % 3) * 0.1 for i in range(50)]),
        )
        self.assertIsInstance(report, ABTestReport)
        self.assertEqual(report.recommended_action, "PROMOTE_CHALLENGER_TO_CHAMPION")
        self.assertEqual(report.status, "AB_TEST_COMPLETED")


if __name__ == '__main__':
    unittest.main()
