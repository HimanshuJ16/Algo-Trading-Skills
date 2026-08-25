"""Unit tests for explainable-boosting-machines-for-regulated-signals."""
import itertools
import logging
import math
import unittest

from explainable_boosting_pricer import (
    ExplainableBoostingPricerEngine,
    MonotonicDirection,
    MonotonicScope,
    ScoreScale,
    ShapeFunctionError,
    REASON_INTERACTION_SHADOWS_GLOBAL,
    REASON_NON_FINITE_SHAPE,
    REASON_SHAPE_NOT_MONOTONE,
    STATUS_FAIL_IDENTITY,
    STATUS_FAIL_MONOTONICITY,
    STATUS_PASS,
    logit_score_to_probability,
)

# Several cases deliberately trigger a failed audit, which the engine logs at WARNING.
logging.getLogger("explainable_boosting_pricer").setLevel(logging.CRITICAL)


def rsi_shape(rsi: float) -> float:
    """f_rsi(rsi) = (rsi - 50) / 100 — strictly increasing."""
    return (rsi - 50.0) / 100.0


def vol_shape(vol: float) -> float:
    """f_vol(vol) = -vol — strictly decreasing."""
    return -1.0 * vol


def rsi_vol_interaction(rsi: float, vol: float) -> float:
    """f_rsi,vol(rsi, vol) = 0.5 * (rsi / 100) * vol."""
    return 0.5 * (rsi / 100.0) * vol


class TestAdditiveComposition(unittest.TestCase):
    """The score is beta0 plus every term contribution, on the link scale."""

    def setUp(self):
        self.engine = ExplainableBoostingPricerEngine(
            model_id="EBM_SIGNAL_ALPHA",
            base_intercept_beta0=0.50,
            score_scale=ScoreScale.LOGIT,
            shape_table_version="2026-01-15",
        )
        self.engine.register_single_feature_shape("rsi_14", rsi_shape)
        self.engine.register_single_feature_shape("volatility", vol_shape)
        self.engine.register_interaction_shape("rsi_14", "volatility", rsi_vol_interaction)

    def test_exact_additive_score_matches_hand_computation(self):
        # Derived by hand, independently of the implementation:
        #   f_rsi(70)          = (70 - 50) / 100      = +0.20
        #   f_vol(0.10)        = -0.10                = -0.10
        #   f_rsi,vol(70,0.10) = 0.5 * 0.70 * 0.10    = +0.035
        #   score              = 0.50 + 0.20 - 0.10 + 0.035 = 0.635
        report = self.engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0, "volatility": 0.10})

        self.assertAlmostEqual(report.total_predicted_score, 0.635, places=12)
        self.assertTrue(report.is_exact_additive_identity_valid)
        self.assertEqual(report.status, STATUS_PASS)
        self.assertEqual(len(report.single_feature_contributions), 2)
        self.assertEqual(len(report.interaction_contributions), 1)

    def test_report_reconciles_to_its_own_recorded_components(self):
        report = self.engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0, "volatility": 0.10})
        recomputed = math.fsum(
            [report.base_intercept_beta0]
            + [c.contribution_score for c in report.single_feature_contributions]
            + [c.contribution_score for c in report.interaction_contributions]
        )
        self.assertAlmostEqual(recomputed, report.total_predicted_score, places=12)
        self.assertLessEqual(report.additive_identity_residual, 1e-12)

    def test_individual_contributions_are_recorded_unrounded(self):
        # 1/3 is not representable at 4 decimal places; the previous engine rounded
        # every contribution to 4dp before summing, so the certified score was not the
        # model's score.
        engine = ExplainableBoostingPricerEngine("EBM_PRECISION", 0.0)
        engine.register_single_feature_shape("x", lambda x: x / 3.0)
        report = engine.evaluate_ebm_signal("AAPL", {"x": 1.0})
        self.assertAlmostEqual(report.single_feature_contributions[0].contribution_score, 1.0 / 3.0, places=15)
        self.assertAlmostEqual(report.total_predicted_score, 1.0 / 3.0, places=15)

    def test_audit_report_carries_scale_version_and_fingerprint(self):
        report = self.engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0, "volatility": 0.10})
        self.assertEqual(report.score_scale, ScoreScale.LOGIT)
        self.assertEqual(report.shape_table_version, "2026-01-15")
        self.assertEqual(report.term_fingerprint, self.engine.term_fingerprint())
        self.assertIn("NOT a probability", report.audit_notes)

    def test_interaction_only_feature_is_required_and_contributes(self):
        engine = ExplainableBoostingPricerEngine("EBM_PAIR_ONLY", 0.0)
        engine.register_interaction_shape("a", "b", lambda x, y: x * y)
        self.assertEqual(engine.required_feature_names(), ("a", "b"))
        report = engine.evaluate_ebm_signal("AAPL", {"a": 2.0, "b": 3.0})
        self.assertAlmostEqual(report.total_predicted_score, 6.0, places=12)


class TestFeatureCoverage(unittest.TestCase):
    """
    A registered term with no supplied value, or a supplied value matching no term,
    used to be silently dropped: the engine scored a different model than the one
    registered and still reported PASS.
    """

    def setUp(self):
        self.engine = ExplainableBoostingPricerEngine("EBM_COVERAGE", 0.50)
        self.engine.register_single_feature_shape("rsi_14", rsi_shape)
        self.engine.register_single_feature_shape("volatility", vol_shape)

    def test_missing_registered_feature_raises_rather_than_scoring_a_subset(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0})
        self.assertIn("volatility", str(ctx.exception))

    def test_unknown_feature_name_raises_rather_than_being_ignored(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_ebm_signal(
                "AAPL", {"rsi_14": 70.0, "volatility": 0.1, "rsi14_typo": 999.0}
            )
        self.assertIn("rsi14_typo", str(ctx.exception))

    def test_non_finite_feature_value_raises(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.engine.evaluate_ebm_signal("AAPL", {"rsi_14": bad, "volatility": 0.1})

    def test_non_numeric_feature_value_raises(self):
        with self.assertRaises(TypeError):
            self.engine.evaluate_ebm_signal("AAPL", {"rsi_14": "70", "volatility": 0.1})

    def test_model_with_no_terms_raises(self):
        with self.assertRaises(ValueError):
            ExplainableBoostingPricerEngine("EBM_EMPTY", 0.0).evaluate_ebm_signal("AAPL", {})

    def test_blank_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_ebm_signal("", {"rsi_14": 70.0, "volatility": 0.1})


class TestMonotonicityAudit(unittest.TestCase):
    """
    The audit flag and the status were previously hard-coded to pass. Each of these
    cases fails against that behaviour and passes against a real audit.
    """

    def test_monotone_shape_passes(self):
        engine = ExplainableBoostingPricerEngine("EBM_MONO_OK", 0.0)
        engine.register_single_feature_shape(
            "rsi_14", rsi_shape,
            monotonic=MonotonicDirection.INCREASING,
            audit_grid=[0.0, 25.0, 50.0, 75.0, 100.0],
        )
        report = engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0})
        self.assertTrue(report.is_monotonicity_audit_passed)
        self.assertEqual(report.status, STATUS_PASS)
        self.assertEqual(report.monotonicity_violations, ())

    def test_non_monotone_shape_is_caught(self):
        # A V-shaped curve: falls to x=50 then rises. Declared increasing, so the
        # 0 -> 50 leg violates the constraint.
        engine = ExplainableBoostingPricerEngine("EBM_MONO_BAD", 0.0)
        engine.register_single_feature_shape(
            "rsi_14", lambda r: abs(r - 50.0) / 100.0,
            monotonic=MonotonicDirection.INCREASING,
            audit_grid=[0.0, 25.0, 50.0, 75.0, 100.0],
        )
        report = engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0})

        self.assertFalse(report.is_monotonicity_audit_passed)
        self.assertEqual(report.status, STATUS_FAIL_MONOTONICITY)
        reasons = {v.reason for v in report.monotonicity_violations}
        self.assertEqual(reasons, {REASON_SHAPE_NOT_MONOTONE})
        # f goes 0.50 -> 0.25 -> 0.00 -> 0.25 -> 0.50: two decreasing steps.
        self.assertEqual(len(report.monotonicity_violations), 2)
        self.assertIn("rsi_14", report.audit_notes)

    def test_decreasing_constraint_is_enforced_in_its_own_direction(self):
        engine = ExplainableBoostingPricerEngine("EBM_MONO_DEC", 0.0)
        engine.register_single_feature_shape(
            "volatility", vol_shape,
            monotonic=MonotonicDirection.DECREASING,
            audit_grid=[0.0, 0.1, 0.2, 0.5],
        )
        self.assertEqual(engine.evaluate_ebm_signal("AAPL", {"volatility": 0.1}).status, STATUS_PASS)

        rising = ExplainableBoostingPricerEngine("EBM_MONO_DEC_BAD", 0.0)
        rising.register_single_feature_shape(
            "volatility", lambda v: v,
            monotonic=MonotonicDirection.DECREASING,
            audit_grid=[0.0, 0.1, 0.2, 0.5],
        )
        self.assertEqual(
            rising.evaluate_ebm_signal("AAPL", {"volatility": 0.1}).status,
            STATUS_FAIL_MONOTONICITY,
        )

    def test_flat_shape_is_monotone_in_both_directions(self):
        for direction in (MonotonicDirection.INCREASING, MonotonicDirection.DECREASING):
            with self.subTest(direction=direction):
                engine = ExplainableBoostingPricerEngine("EBM_FLAT", 0.0)
                engine.register_single_feature_shape(
                    "x", lambda x: 0.25, monotonic=direction, audit_grid=[0.0, 1.0, 2.0],
                )
                self.assertEqual(engine.evaluate_ebm_signal("AAPL", {"x": 1.0}).status, STATUS_PASS)

    def test_constraint_is_only_checked_on_the_supplied_grid(self):
        # Monotone on [0, 50] but not beyond. A grid stopping at 50 certifies only
        # [0, 50] -- the audit must not claim more than it checked.
        shape = lambda x: x / 100.0 if x <= 50.0 else (100.0 - x) / 100.0
        narrow = ExplainableBoostingPricerEngine("EBM_GRID_NARROW", 0.0)
        narrow.register_single_feature_shape(
            "x", shape, monotonic=MonotonicDirection.INCREASING, audit_grid=[0.0, 25.0, 50.0],
        )
        self.assertEqual(narrow.evaluate_ebm_signal("AAPL", {"x": 10.0}).status, STATUS_PASS)

        wide = ExplainableBoostingPricerEngine("EBM_GRID_WIDE", 0.0)
        wide.register_single_feature_shape(
            "x", shape, monotonic=MonotonicDirection.INCREASING, audit_grid=[0.0, 25.0, 50.0, 100.0],
        )
        self.assertEqual(wide.evaluate_ebm_signal("AAPL", {"x": 10.0}).status, STATUS_FAIL_MONOTONICITY)

    def test_non_finite_shape_on_the_grid_is_a_violation_not_a_pass(self):
        engine = ExplainableBoostingPricerEngine("EBM_MONO_NAN", 0.0)
        engine.register_single_feature_shape(
            "x", lambda x: float("nan") if x > 1.0 else x,
            monotonic=MonotonicDirection.INCREASING, audit_grid=[0.0, 1.0, 2.0],
        )
        violations, _ = engine.audit_monotonicity()
        self.assertEqual([v.reason for v in violations], [REASON_NON_FINITE_SHAPE])


class TestGlobalMonotonicityVsInteractions(unittest.TestCase):
    """
    A monotone univariate term does not make the model monotone in that feature when
    an interaction on it is also present -- the interaction can move the score the
    other way. InterpretML documents the same limit for its own ``monotonize``.
    """

    @staticmethod
    def _engine():
        engine = ExplainableBoostingPricerEngine("EBM_SHADOW", 0.0)
        engine.register_single_feature_shape("volatility", vol_shape)
        return engine

    def test_global_scope_is_not_certified_when_an_interaction_contains_the_feature(self):
        engine = self._engine()
        engine.register_single_feature_shape(
            "rsi_14", rsi_shape,
            monotonic=MonotonicDirection.INCREASING,
            audit_grid=[0.0, 50.0, 100.0],
            scope=MonotonicScope.GLOBAL,
        )
        engine.register_interaction_shape("rsi_14", "volatility", rsi_vol_interaction)

        report = engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0, "volatility": 0.10})
        self.assertEqual(report.status, STATUS_FAIL_MONOTONICITY)
        self.assertEqual(
            [v.reason for v in report.monotonicity_violations],
            [REASON_INTERACTION_SHADOWS_GLOBAL],
        )

    def test_term_scope_passes_but_records_the_limitation(self):
        engine = self._engine()
        engine.register_single_feature_shape(
            "rsi_14", rsi_shape,
            monotonic=MonotonicDirection.INCREASING,
            audit_grid=[0.0, 50.0, 100.0],
            scope=MonotonicScope.TERM,
        )
        engine.register_interaction_shape("rsi_14", "volatility", rsi_vol_interaction)

        report = engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0, "volatility": 0.10})
        self.assertEqual(report.status, STATUS_PASS)
        self.assertEqual(len(report.monotonicity_audit_limitations), 1)
        self.assertIn("not certified monotone", report.monotonicity_audit_limitations[0])

    def test_term_scope_still_fails_on_a_genuinely_non_monotone_shape(self):
        engine = self._engine()
        engine.register_single_feature_shape(
            "rsi_14", lambda r: abs(r - 50.0) / 100.0,
            monotonic=MonotonicDirection.INCREASING,
            audit_grid=[0.0, 50.0, 100.0],
            scope=MonotonicScope.TERM,
        )
        engine.register_interaction_shape("rsi_14", "volatility", rsi_vol_interaction)
        report = engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0, "volatility": 0.10})
        self.assertEqual(report.status, STATUS_FAIL_MONOTONICITY)


class TestRegistrationSafety(unittest.TestCase):

    def test_interaction_pair_is_unordered_and_cannot_be_double_registered(self):
        engine = ExplainableBoostingPricerEngine("EBM_PAIR", 0.0)
        engine.register_interaction_shape("a", "b", lambda x, y: x * y)
        with self.assertRaises(ValueError):
            engine.register_interaction_shape("b", "a", lambda x, y: x * y)

        # Registered once, contributing once: 2 * 3 = 6, not 12.
        report = engine.evaluate_ebm_signal("AAPL", {"a": 2.0, "b": 3.0})
        self.assertEqual(len(report.interaction_contributions), 1)
        self.assertAlmostEqual(report.total_predicted_score, 6.0, places=12)

    def test_interaction_with_itself_raises(self):
        engine = ExplainableBoostingPricerEngine("EBM_SELF", 0.0)
        with self.assertRaises(ValueError):
            engine.register_interaction_shape("a", "a", lambda x, y: x * y)

    def test_re_registering_a_term_requires_an_explicit_replace(self):
        engine = ExplainableBoostingPricerEngine("EBM_REPLACE", 0.0)
        engine.register_single_feature_shape("x", lambda x: x)
        with self.assertRaises(ValueError):
            engine.register_single_feature_shape("x", lambda x: 2 * x)

        engine.register_single_feature_shape("x", lambda x: 2 * x, replace=True)
        self.assertAlmostEqual(
            engine.evaluate_ebm_signal("AAPL", {"x": 3.0}).total_predicted_score, 6.0, places=12
        )

    def test_replacing_a_term_clears_its_previous_constraint(self):
        engine = ExplainableBoostingPricerEngine("EBM_REPLACE_CONSTRAINT", 0.0)
        engine.register_single_feature_shape(
            "x", lambda x: x, monotonic=MonotonicDirection.INCREASING, audit_grid=[0.0, 1.0],
        )
        engine.register_single_feature_shape("x", lambda x: -x, replace=True)
        self.assertEqual(engine.monotonicity_constraints, {})
        self.assertEqual(engine.evaluate_ebm_signal("AAPL", {"x": 1.0}).status, STATUS_PASS)

    def test_intercept_must_be_supplied_and_finite(self):
        with self.assertRaises(TypeError):
            ExplainableBoostingPricerEngine("EBM_NO_INTERCEPT")  # noqa: E501 - intentional misuse
        with self.assertRaises(ValueError):
            ExplainableBoostingPricerEngine("EBM_NAN_INTERCEPT", float("nan"))

    def test_empty_model_id_raises(self):
        with self.assertRaises(ValueError):
            ExplainableBoostingPricerEngine("", 0.0)

    def test_non_callable_shape_raises(self):
        engine = ExplainableBoostingPricerEngine("EBM_NOT_CALLABLE", 0.0)
        with self.assertRaises(TypeError):
            engine.register_single_feature_shape("x", 1.0)
        with self.assertRaises(TypeError):
            engine.register_interaction_shape("a", "b", 1.0)


class TestAuditGridValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ExplainableBoostingPricerEngine("EBM_GRID", 0.0)

    def _register(self, **kwargs):
        self.engine.register_single_feature_shape(
            "x", lambda x: x, monotonic=MonotonicDirection.INCREASING, replace=True, **kwargs
        )

    def test_constraint_without_a_grid_raises(self):
        with self.assertRaises(ValueError):
            self._register()

    def test_grid_needs_at_least_two_points(self):
        with self.assertRaises(ValueError):
            self._register(audit_grid=[1.0])

    def test_grid_must_be_strictly_ascending(self):
        for grid in ([2.0, 1.0], [1.0, 1.0], [0.0, 2.0, 1.0]):
            with self.subTest(grid=grid):
                with self.assertRaises(ValueError):
                    self._register(audit_grid=grid)

    def test_grid_must_be_finite(self):
        with self.assertRaises(ValueError):
            self._register(audit_grid=[0.0, float("inf")])

    def test_grid_without_a_direction_raises(self):
        with self.assertRaises(ValueError):
            self.engine.register_single_feature_shape("y", lambda x: x, audit_grid=[0.0, 1.0])


class TestShapeFunctionFailures(unittest.TestCase):

    def test_raising_shape_function_is_reported_with_the_offending_term(self):
        engine = ExplainableBoostingPricerEngine("EBM_RAISE", 0.0)
        engine.register_single_feature_shape("x", lambda v: 1.0 / v)
        with self.assertRaises(ShapeFunctionError) as ctx:
            engine.evaluate_ebm_signal("AAPL", {"x": 0.0})
        self.assertIn("f_x", str(ctx.exception))
        self.assertIn("ZeroDivisionError", str(ctx.exception))

    def test_raising_interaction_is_reported_with_the_offending_pair(self):
        engine = ExplainableBoostingPricerEngine("EBM_RAISE_PAIR", 0.0)
        engine.register_interaction_shape("a", "b", lambda x, y: x / y)
        with self.assertRaises(ShapeFunctionError) as ctx:
            engine.evaluate_ebm_signal("AAPL", {"a": 1.0, "b": 0.0})
        self.assertIn("f_a,b", str(ctx.exception))

    def test_non_finite_shape_output_fails_the_audit_instead_of_passing_silently(self):
        engine = ExplainableBoostingPricerEngine("EBM_NAN_SHAPE", 0.50)
        engine.register_single_feature_shape("x", lambda v: float("nan"))
        report = engine.evaluate_ebm_signal("AAPL", {"x": 1.0})

        self.assertEqual(report.status, STATUS_FAIL_IDENTITY)
        self.assertFalse(report.is_exact_additive_identity_valid)
        self.assertTrue(math.isinf(report.additive_identity_residual))
        self.assertTrue(math.isnan(report.total_predicted_score))

    def test_non_deterministic_shape_function_fails_the_identity_check(self):
        # The identity check re-evaluates every term from the report's own recorded
        # inputs. A stateful shape function cannot reproduce its own audit record.
        counter = itertools.count()
        engine = ExplainableBoostingPricerEngine("EBM_STATEFUL", 0.0)
        engine.register_single_feature_shape("x", lambda v: float(next(counter)))

        report = engine.evaluate_ebm_signal("AAPL", {"x": 1.0})
        self.assertFalse(report.is_exact_additive_identity_valid)
        self.assertEqual(report.status, STATUS_FAIL_IDENTITY)
        self.assertGreater(report.additive_identity_residual, 0.0)


class TestAuditCacheInvalidation(unittest.TestCase):
    """
    The monotonicity audit is cached per model configuration because it is re-run on
    every score. A cache that outlives a model change would certify the old model.
    """

    def test_adding_an_interaction_after_a_passing_score_invalidates_the_cache(self):
        engine = ExplainableBoostingPricerEngine("EBM_CACHE_PAIR", 0.0)
        engine.register_single_feature_shape(
            "rsi_14", rsi_shape,
            monotonic=MonotonicDirection.INCREASING, audit_grid=[0.0, 50.0, 100.0],
        )
        engine.register_single_feature_shape("volatility", vol_shape)
        first = engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0, "volatility": 0.1})
        self.assertEqual(first.status, STATUS_PASS)

        engine.register_interaction_shape("rsi_14", "volatility", rsi_vol_interaction)
        second = engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0, "volatility": 0.1})
        self.assertEqual(second.status, STATUS_FAIL_MONOTONICITY)
        self.assertEqual(
            [v.reason for v in second.monotonicity_violations],
            [REASON_INTERACTION_SHADOWS_GLOBAL],
        )
        self.assertNotEqual(first.term_fingerprint, second.term_fingerprint)

    def test_replacing_a_shape_after_a_passing_score_invalidates_the_cache(self):
        engine = ExplainableBoostingPricerEngine("EBM_CACHE_SHAPE", 0.0)
        engine.register_single_feature_shape(
            "x", lambda v: v, monotonic=MonotonicDirection.INCREASING, audit_grid=[0.0, 1.0, 2.0],
        )
        self.assertEqual(engine.evaluate_ebm_signal("AAPL", {"x": 1.0}).status, STATUS_PASS)

        engine.register_single_feature_shape(
            "x", lambda v: -v, monotonic=MonotonicDirection.INCREASING,
            audit_grid=[0.0, 1.0, 2.0], replace=True,
        )
        self.assertEqual(
            engine.evaluate_ebm_signal("AAPL", {"x": 1.0}).status, STATUS_FAIL_MONOTONICITY
        )

    def test_cached_result_cannot_be_mutated_by_the_caller(self):
        engine = ExplainableBoostingPricerEngine("EBM_CACHE_COPY", 0.0)
        engine.register_single_feature_shape(
            "x", lambda v: -v, monotonic=MonotonicDirection.INCREASING, audit_grid=[0.0, 1.0],
        )
        violations, limitations = engine.audit_monotonicity()
        violations.clear()
        limitations.append("injected")

        again, again_limits = engine.audit_monotonicity()
        self.assertEqual(len(again), 1)
        self.assertEqual(again_limits, [])

    def test_forced_recompute_agrees_with_the_cache_for_a_pure_model(self):
        engine = ExplainableBoostingPricerEngine("EBM_CACHE_FORCE", 0.0)
        engine.register_single_feature_shape(
            "x", lambda v: -v, monotonic=MonotonicDirection.INCREASING, audit_grid=[0.0, 1.0, 2.0],
        )
        cached, _ = engine.audit_monotonicity()
        fresh, _ = engine.audit_monotonicity(use_cache=False)
        self.assertEqual([v.reason for v in cached], [v.reason for v in fresh])
        self.assertEqual([v.delta for v in cached], [v.delta for v in fresh])


class TestTermFingerprint(unittest.TestCase):

    @staticmethod
    def _engine(version="v1", beta0=0.0):
        engine = ExplainableBoostingPricerEngine("EBM_FP", beta0, shape_table_version=version)
        engine.register_single_feature_shape("rsi_14", rsi_shape)
        return engine

    def test_identical_structure_fingerprints_identically(self):
        self.assertEqual(self._engine().term_fingerprint(), self._engine().term_fingerprint())

    def test_fingerprint_changes_with_structure_version_intercept_and_constraints(self):
        base = self._engine().term_fingerprint()
        self.assertNotEqual(base, self._engine(version="v2").term_fingerprint())
        self.assertNotEqual(base, self._engine(beta0=0.25).term_fingerprint())

        added_term = self._engine()
        added_term.register_single_feature_shape("volatility", vol_shape)
        self.assertNotEqual(base, added_term.term_fingerprint())

        constrained = self._engine()
        constrained.register_single_feature_shape(
            "rsi_14", rsi_shape, monotonic=MonotonicDirection.INCREASING,
            audit_grid=[0.0, 100.0], replace=True,
        )
        self.assertNotEqual(base, constrained.term_fingerprint())

    def test_fingerprint_does_not_change_when_only_the_shape_values_change(self):
        # Documented limitation: the fingerprint covers structure, not the lookup
        # tables. Binding a recalibration to an audit record needs shape_table_version.
        recalibrated = ExplainableBoostingPricerEngine("EBM_FP", 0.0, shape_table_version="v1")
        recalibrated.register_single_feature_shape("rsi_14", lambda r: 10.0 * rsi_shape(r))
        self.assertEqual(self._engine().term_fingerprint(), recalibrated.term_fingerprint())


class TestLogitConversion(unittest.TestCase):
    """Expected values derived from the definition of the logistic function, not
    from the implementation: sigmoid(ln k) = k / (1 + k)."""

    def test_known_points(self):
        self.assertAlmostEqual(logit_score_to_probability(0.0), 0.5, places=15)
        self.assertAlmostEqual(logit_score_to_probability(math.log(3.0)), 0.75, places=15)
        self.assertAlmostEqual(logit_score_to_probability(-math.log(3.0)), 0.25, places=15)
        self.assertAlmostEqual(logit_score_to_probability(math.log(1.0 / 9.0)), 0.10, places=15)

    def test_symmetry(self):
        for score in (0.5, 1.0, 2.5, 7.5):
            with self.subTest(score=score):
                self.assertAlmostEqual(
                    logit_score_to_probability(score) + logit_score_to_probability(-score),
                    1.0, places=15,
                )

    def test_large_magnitudes_do_not_overflow(self):
        self.assertAlmostEqual(logit_score_to_probability(1000.0), 1.0, places=12)
        self.assertAlmostEqual(logit_score_to_probability(-1000.0), 0.0, places=12)

    def test_non_finite_and_non_numeric_raise(self):
        with self.assertRaises(ValueError):
            logit_score_to_probability(float("nan"))
        with self.assertRaises(TypeError):
            logit_score_to_probability("0.5")


if __name__ == "__main__":
    unittest.main()
