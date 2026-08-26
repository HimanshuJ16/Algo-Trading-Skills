"""Behaviour tests for the ensemble weight decay engine.

Expected weights are derived independently of the implementation wherever a
number is asserted. The two-model softmax has a closed logistic form,
``w_A = e^(beta * (L_B - L_A)) / (1 + e^(beta * (L_B - L_A)))``, which is
reached without reproducing the module's max-shift normalisation, and the EWMA
and half-life values are hand-computable.

Tests marked REGRESSION fail against the pre-2.0 implementation.
"""
import logging
import math
import unittest

from ensemble_weight_decay import (
    EXPONENTIAL_LOSS,
    IC_SOFTMAX,
    ENSEMBLE_HALTED_ALL_DEMOTED,
    ENSEMBLE_REWEIGHTED_SUCCESS,
    STATUS_ACTIVE,
    STATUS_DEMOTED_BELOW_FLOOR,
    STATUS_DEMOTED_NEGATIVE_IC,
    STATUS_PENDING_WARMUP,
    EnsembleConfig,
    EnsembleWeightDecayEngine,
    EnsembleWeightError,
    ModelTelemetry,
    half_life_periods,
)


def _by_id(report, model_id):
    return next(s for s in report.model_statuses if s.model_id == model_id)


class TestBaselineReweighting(unittest.TestCase):
    """Core softmax weighting and floor demotion."""

    def setUp(self):
        self.engine = EnsembleWeightDecayEngine()

    def test_exponential_loss_softmax_reweighting_and_demotion(self):
        models = [
            ModelTelemetry("XGBoost_v1", recent_loss=0.1, recent_ic=0.08),
            ModelTelemetry("LightGBM_v1", recent_loss=0.5, recent_ic=0.04),
            ModelTelemetry("Ridge_v1", recent_loss=2.5, recent_ic=0.01),
        ]
        cfg = EnsembleConfig(decay_factor_lambda=0.90, temperature_beta=2.0,
                             min_weight_floor=0.05)
        report = self.engine.reweight_ensemble("ML_ALPHA_ENSEMBLE", cfg, models)

        self.assertEqual(report.status, ENSEMBLE_REWEIGHTED_SUCCESS)
        self.assertEqual(report.active_model_count, 2)
        self.assertEqual(report.demoted_model_count, 1)

        ridge = _by_id(report, "Ridge_v1")
        self.assertFalse(ridge.is_active)
        self.assertEqual(ridge.status, STATUS_DEMOTED_BELOW_FLOOR)
        self.assertEqual(ridge.final_normalized_weight, 0.0)

        # Independent derivation: with the third model demoted, the two survivors
        # renormalise to the logistic of beta * (L_B - L_A) = 2.0 * 0.4 = 0.8.
        odds = math.exp(0.8)
        expected_xgb = odds / (1.0 + odds)
        self.assertAlmostEqual(
            _by_id(report, "XGBoost_v1").final_normalized_weight, expected_xgb, places=6)
        self.assertAlmostEqual(
            _by_id(report, "LightGBM_v1").final_normalized_weight, 1.0 - expected_xgb, places=6)

    def test_raw_weights_are_reported_pre_demotion(self):
        """raw_weight is the softmax share over all models, before any breaker."""
        models = [
            ModelTelemetry("A", recent_loss=0.1, recent_ic=0.05),
            ModelTelemetry("B", recent_loss=0.5, recent_ic=0.05),
            ModelTelemetry("C", recent_loss=2.5, recent_ic=0.05),
        ]
        report = self.engine.reweight_ensemble("E", EnsembleConfig(), models)
        self.assertAlmostEqual(
            sum(s.raw_weight for s in report.model_statuses), 1.0, places=6)
        self.assertGreater(_by_id(report, "C").raw_weight, 0.0)
        self.assertEqual(_by_id(report, "C").final_normalized_weight, 0.0)

    def test_lower_loss_always_receives_more_weight(self):
        models = [ModelTelemetry(f"M{i}", recent_loss=0.1 * i, recent_ic=0.05)
                  for i in range(1, 6)]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(min_weight_floor=0.0), models)
        weights = [_by_id(report, f"M{i}").final_normalized_weight for i in range(1, 6)]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_higher_beta_concentrates_weight(self):
        models = [ModelTelemetry("A", 0.1, 0.05), ModelTelemetry("B", 0.5, 0.05)]
        flat = self.engine.reweight_ensemble(
            "E", EnsembleConfig(temperature_beta=0.5, min_weight_floor=0.0), models)
        sharp = self.engine.reweight_ensemble(
            "E", EnsembleConfig(temperature_beta=8.0, min_weight_floor=0.0), models)
        self.assertGreater(_by_id(sharp, "A").final_normalized_weight,
                           _by_id(flat, "A").final_normalized_weight)

    def test_identical_models_receive_equal_weight(self):
        models = [ModelTelemetry(f"M{i}", 0.3, 0.05) for i in range(4)]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(min_weight_floor=0.2), models)
        self.assertEqual(report.active_model_count, 4)
        for s in report.model_statuses:
            self.assertAlmostEqual(s.final_normalized_weight, 0.25, places=6)

    def test_deterministic_across_identical_calls(self):
        models = [ModelTelemetry("A", 0.1, 0.05), ModelTelemetry("B", 0.5, 0.05)]
        first = self.engine.reweight_ensemble("E", EnsembleConfig(), list(models))
        second = self.engine.reweight_ensemble("E", EnsembleConfig(), list(models))
        self.assertEqual(first, second)


class TestExponentialDecay(unittest.TestCase):
    """EWMA recursion, seeding, and half-life."""

    def setUp(self):
        self.engine = EnsembleWeightDecayEngine()

    def test_first_observation_seeds_with_current_reading(self):
        """With no prior state the recursion is lam*L + (1-lam)*L = L exactly,
        so lambda has no observable effect on the first call."""
        models = [ModelTelemetry("A", 0.4, 0.05), ModelTelemetry("B", 0.9, 0.05)]
        slow = self.engine.reweight_ensemble(
            "E", EnsembleConfig(decay_factor_lambda=0.99), models)
        fast = self.engine.reweight_ensemble(
            "E", EnsembleConfig(decay_factor_lambda=0.10), models)
        self.assertEqual(_by_id(slow, "A").decayed_metric, 0.4)
        self.assertEqual(_by_id(fast, "A").decayed_metric, 0.4)

    def test_ewma_recursion_hand_computed(self):
        # 0.9 * 0.5 + 0.1 * 0.1 = 0.45 + 0.01 = 0.46
        models = [
            ModelTelemetry("A", recent_loss=0.1, recent_ic=0.05, previous_decayed_loss=0.5),
            ModelTelemetry("B", recent_loss=0.3, recent_ic=0.05),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(decay_factor_lambda=0.9, min_weight_floor=0.0), models)
        self.assertAlmostEqual(_by_id(report, "A").decayed_metric, 0.46, places=9)

    def test_decayed_ic_maintained_under_loss_weighting(self):
        """The IC breaker needs a discounted IC even when weighting by loss."""
        # 0.9 * 0.10 + 0.1 * 0.02 = 0.09 + 0.002 = 0.092
        models = [
            ModelTelemetry("A", 0.2, recent_ic=0.02, previous_decayed_ic=0.10),
            ModelTelemetry("B", 0.3, recent_ic=0.05),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(decay_factor_lambda=0.9, weighting_method=EXPONENTIAL_LOSS,
                                min_weight_floor=0.0), models)
        self.assertAlmostEqual(_by_id(report, "A").decayed_ic, 0.092, places=9)

    def test_half_life_exact_values(self):
        self.assertAlmostEqual(half_life_periods(0.5), 1.0, places=12)
        self.assertAlmostEqual(half_life_periods(0.25), 0.5, places=12)
        self.assertAlmostEqual(half_life_periods(0.95), 13.513407, places=5)
        self.assertAlmostEqual(half_life_periods(0.94), 11.202306, places=5)

    def test_half_life_undefined_at_boundaries(self):
        for lam in (0.0, 1.0, -0.1, 1.5):
            with self.assertRaises(EnsembleWeightError):
                half_life_periods(lam)

    def test_report_carries_half_life(self):
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(decay_factor_lambda=0.95),
            [ModelTelemetry("A", 0.1, 0.05), ModelTelemetry("B", 0.5, 0.05)])
        self.assertAlmostEqual(report.decay_half_life_periods, 13.5134, places=4)

    def test_report_half_life_is_none_at_lambda_boundaries(self):
        for lam in (0.0, 1.0):
            report = self.engine.reweight_ensemble(
                "E", EnsembleConfig(decay_factor_lambda=lam),
                [ModelTelemetry("A", 0.1, 0.05), ModelTelemetry("B", 0.5, 0.05)])
            self.assertIsNone(report.decay_half_life_periods)


class TestNegativeICCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self.engine = EnsembleWeightDecayEngine()

    def test_negative_ic_demoted_under_ic_softmax(self):
        models = [
            ModelTelemetry("Model_Good", recent_loss=0.2, recent_ic=0.05),
            ModelTelemetry("Model_Bad", recent_loss=0.2, recent_ic=-0.03),
        ]
        report = self.engine.reweight_ensemble(
            "IC_ENSEMBLE", EnsembleConfig(weighting_method=IC_SOFTMAX), models)
        bad = _by_id(report, "Model_Bad")
        self.assertFalse(bad.is_active)
        self.assertEqual(bad.status, STATUS_DEMOTED_NEGATIVE_IC)

    def test_negative_ic_demoted_under_exponential_loss(self):
        """REGRESSION. The breaker previously ran only in IC_SOFTMAX mode, so an
        anti-predictive model with a competitive MSE kept the majority of the
        book under loss weighting -- contradicting the documented workflow."""
        models = [
            ModelTelemetry("Good", recent_loss=0.20, recent_ic=0.06),
            ModelTelemetry("AntiPredictive", recent_loss=0.19, recent_ic=-0.09),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(weighting_method=EXPONENTIAL_LOSS), models)
        anti = _by_id(report, "AntiPredictive")
        self.assertEqual(anti.status, STATUS_DEMOTED_NEGATIVE_IC)
        self.assertEqual(anti.final_normalized_weight, 0.0)
        self.assertEqual(_by_id(report, "Good").final_normalized_weight, 1.0)

    def test_breaker_reads_decayed_ic_not_single_period_ic(self):
        """REGRESSION. A model with a strong IC history survives one bad period;
        the breaker previously fired on the raw single-period reading."""
        # 0.9 * 0.06 + 0.1 * (-0.02) = 0.054 - 0.002 = 0.052 > 0 -> survives
        models = [
            ModelTelemetry("Established", 0.2, recent_ic=-0.02, previous_decayed_ic=0.06),
            ModelTelemetry("Peer", 0.2, recent_ic=0.05, previous_decayed_ic=0.05),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(decay_factor_lambda=0.9, weighting_method=EXPONENTIAL_LOSS), models)
        established = _by_id(report, "Established")
        self.assertAlmostEqual(established.decayed_ic, 0.052, places=9)
        self.assertEqual(established.status, STATUS_ACTIVE)

    def test_breaker_fires_on_decayed_ic_despite_positive_current_reading(self):
        """REGRESSION, other direction. A sustained negative IC history is not
        rescued by one good period."""
        # 0.9 * (-0.04) + 0.1 * 0.01 = -0.036 + 0.001 = -0.035 <= 0 -> demoted
        models = [
            ModelTelemetry("Decayed", 0.2, recent_ic=0.01, previous_decayed_ic=-0.04),
            ModelTelemetry("Peer", 0.2, recent_ic=0.05, previous_decayed_ic=0.05),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(decay_factor_lambda=0.9, weighting_method=EXPONENTIAL_LOSS), models)
        decayed = _by_id(report, "Decayed")
        self.assertAlmostEqual(decayed.decayed_ic, -0.035, places=9)
        self.assertEqual(decayed.status, STATUS_DEMOTED_NEGATIVE_IC)

    def test_exactly_zero_ic_is_demoted(self):
        """Boundary: the documented trigger is IC <= 0, not IC < 0."""
        models = [
            ModelTelemetry("Zero", 0.2, recent_ic=0.0),
            ModelTelemetry("Peer", 0.2, recent_ic=0.05),
        ]
        report = self.engine.reweight_ensemble("E", EnsembleConfig(), models)
        self.assertEqual(_by_id(report, "Zero").status, STATUS_DEMOTED_NEGATIVE_IC)

    def test_breaker_can_be_disabled_explicitly(self):
        models = [
            ModelTelemetry("Bad", 0.2, recent_ic=-0.03),
            ModelTelemetry("Peer", 0.2, recent_ic=0.05),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(demote_on_negative_ic=False), models)
        self.assertEqual(_by_id(report, "Bad").status, STATUS_ACTIVE)


class TestAllDemotedHalt(unittest.TestCase):
    def setUp(self):
        self.engine = EnsembleWeightDecayEngine()

    def test_all_demoted_halts_instead_of_equal_weight_fallback(self):
        """REGRESSION. The engine previously returned SUCCESS with demoted=0 and
        equal weights spread across the very models the breakers had rejected."""
        models = [
            ModelTelemetry("A", 0.2, recent_ic=-0.05),
            ModelTelemetry("B", 0.2, recent_ic=-0.09),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(weighting_method=IC_SOFTMAX), models)

        self.assertEqual(report.status, ENSEMBLE_HALTED_ALL_DEMOTED)
        self.assertEqual(report.active_model_count, 0)
        self.assertEqual(report.demoted_model_count, 2)
        for s in report.model_statuses:
            self.assertFalse(s.is_active)
            self.assertEqual(s.final_normalized_weight, 0.0)
            self.assertEqual(s.status, STATUS_DEMOTED_NEGATIVE_IC)

    def test_halt_is_logged_at_error_level_with_per_model_reasons(self):
        models = [ModelTelemetry("A", 0.2, -0.05), ModelTelemetry("B", 0.2, -0.09)]
        with self.assertLogs("ensemble_weight_decay", level=logging.ERROR) as captured:
            report = self.engine.reweight_ensemble("HALT_ME", EnsembleConfig(), models)
        joined = "\n".join(captured.output)
        self.assertIn("ENSEMBLE HALTED [HALT_ME]", joined)
        self.assertIn("A=DEMOTED_NEGATIVE_IC", joined)
        self.assertIn(ENSEMBLE_HALTED_ALL_DEMOTED, report.status)


class TestWarmUpGuard(unittest.TestCase):
    def setUp(self):
        self.engine = EnsembleWeightDecayEngine()

    def test_warmup_withholds_weight_without_counting_as_broken(self):
        models = [
            ModelTelemetry("Fresh", 0.05, 0.09, days_active=3),
            ModelTelemetry("Seasoned", 0.30, 0.05, days_active=250),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(min_days_active=30), models)
        fresh = _by_id(report, "Fresh")
        self.assertEqual(fresh.status, STATUS_PENDING_WARMUP)
        self.assertEqual(fresh.final_normalized_weight, 0.0)
        self.assertEqual(_by_id(report, "Seasoned").final_normalized_weight, 1.0)

    def test_warmup_default_is_a_no_op(self):
        models = [ModelTelemetry("A", 0.1, 0.05), ModelTelemetry("B", 0.5, 0.05)]
        report = self.engine.reweight_ensemble("E", EnsembleConfig(), models)
        self.assertEqual(report.active_model_count, 2)

    def test_warmup_is_counted_apart_from_demotion(self):
        """Insufficient history is not evidence of failure; an operator reading
        the audit trail must not see a new model counted as a broken one."""
        models = [
            ModelTelemetry("Fresh", 0.05, 0.09, days_active=3),
            ModelTelemetry("Inverted", 0.20, -0.04, days_active=250),
            ModelTelemetry("Seasoned", 0.30, 0.05, days_active=250),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(min_days_active=30), models)
        self.assertEqual(report.active_model_count, 1)
        self.assertEqual(report.demoted_model_count, 1)
        self.assertEqual(report.pending_warmup_model_count, 1)
        self.assertEqual(
            report.active_model_count
            + report.demoted_model_count
            + report.pending_warmup_model_count,
            len(models))

    def test_all_warming_up_halts_without_counting_as_demoted(self):
        models = [
            ModelTelemetry("A", 0.1, 0.05, days_active=1),
            ModelTelemetry("B", 0.5, 0.05, days_active=2),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(min_days_active=30), models)
        self.assertEqual(report.status, ENSEMBLE_HALTED_ALL_DEMOTED)
        self.assertEqual(report.demoted_model_count, 0)
        self.assertEqual(report.pending_warmup_model_count, 2)
        for s in report.model_statuses:
            self.assertEqual(s.final_normalized_weight, 0.0)

    def test_warmup_boundary_is_inclusive(self):
        models = [
            ModelTelemetry("Exactly", 0.1, 0.05, days_active=30),
            ModelTelemetry("Peer", 0.5, 0.05, days_active=250),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(min_days_active=30), models)
        self.assertEqual(_by_id(report, "Exactly").status, STATUS_ACTIVE)


class TestNumericalStability(unittest.TestCase):
    """REGRESSION. Unshifted math.exp crashed on both tails."""

    def setUp(self):
        self.engine = EnsembleWeightDecayEngine()

    def test_extreme_losses_do_not_underflow_to_zero_division(self):
        models = [
            ModelTelemetry("A", recent_loss=400.0, recent_ic=0.05),
            ModelTelemetry("B", recent_loss=500.0, recent_ic=0.05),
        ]
        report = self.engine.reweight_ensemble("E", EnsembleConfig(), models)
        for s in report.model_statuses:
            self.assertTrue(math.isfinite(s.raw_weight))
            self.assertTrue(math.isfinite(s.final_normalized_weight))
        self.assertEqual(_by_id(report, "A").final_normalized_weight, 1.0)

    def test_extreme_ic_does_not_overflow(self):
        models = [
            ModelTelemetry("A", 0.1, recent_ic=400.0),
            ModelTelemetry("B", 0.1, recent_ic=0.05),
        ]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(weighting_method=IC_SOFTMAX), models)
        for s in report.model_statuses:
            self.assertTrue(math.isfinite(s.raw_weight))
        self.assertEqual(_by_id(report, "A").final_normalized_weight, 1.0)

    def test_max_shift_leaves_ordinary_weights_unchanged(self):
        """The shift is an exact identity, so a well-scaled case is untouched."""
        models = [ModelTelemetry("A", 0.1, 0.05), ModelTelemetry("B", 0.5, 0.05)]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(min_weight_floor=0.0), models)
        odds = math.exp(2.0 * 0.4)
        self.assertAlmostEqual(
            _by_id(report, "A").final_normalized_weight, odds / (1.0 + odds), places=6)


class TestWeightInvariants(unittest.TestCase):
    def setUp(self):
        self.engine = EnsembleWeightDecayEngine()

    def test_active_weights_sum_to_exactly_one_under_exact_summation(self):
        """Rounding each weight independently previously left the reported
        vector summing to 0.999 -- an error 1000x the reporting precision.

        The invariant is exact under math.fsum. A naive left-to-right sum() may
        still differ by an ULP or two, which is IEEE-754 accumulation in the
        caller, not a residual in the allocation; it is bounded well below the
        reporting precision and asserted separately."""
        for count in (2, 3, 5, 7, 11, 23, 41, 200):
            models = [ModelTelemetry(f"M{i}", 0.30 + 0.001 * i, 0.05)
                      for i in range(count)]
            report = self.engine.reweight_ensemble(
                "E", EnsembleConfig(min_weight_floor=0.0), models)
            weights = [s.final_normalized_weight for s in report.model_statuses]
            exact = math.fsum(weights)
            self.assertEqual(exact, 1.0, f"{count} models: fsum was {exact!r}")
            self.assertLess(abs(sum(weights) - 1.0), 1e-12,
                            f"{count} models: naive sum was {sum(weights)!r}")

    def test_every_active_weight_clears_the_floor_after_renormalisation(self):
        """One demotion pass suffices: renormalising divides by a sum <= 1, so
        surviving weights only increase."""
        models = [ModelTelemetry(f"M{i}", 0.1 * i, 0.05) for i in range(1, 12)]
        cfg = EnsembleConfig(min_weight_floor=0.08, temperature_beta=3.0)
        report = self.engine.reweight_ensemble("E", cfg, models)
        actives = [s for s in report.model_statuses if s.is_active]
        self.assertTrue(actives)
        for s in actives:
            self.assertGreaterEqual(s.final_normalized_weight, cfg.min_weight_floor)

    def test_demoted_models_hold_exactly_zero_weight(self):
        models = [ModelTelemetry(f"M{i}", 0.1 * i, 0.05) for i in range(1, 12)]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(min_weight_floor=0.08, temperature_beta=3.0), models)
        for s in report.model_statuses:
            if not s.is_active:
                self.assertEqual(s.final_normalized_weight, 0.0)

    def test_counts_partition_the_ensemble(self):
        models = [ModelTelemetry(f"M{i}", 0.1 * i, 0.05) for i in range(1, 12)]
        report = self.engine.reweight_ensemble(
            "E", EnsembleConfig(min_weight_floor=0.08, temperature_beta=3.0), models)
        self.assertEqual(
            report.active_model_count
            + report.demoted_model_count
            + report.pending_warmup_model_count,
            len(models))
        self.assertEqual(report.pending_warmup_model_count, 0)


class TestTelemetryValidation(unittest.TestCase):
    def setUp(self):
        self.engine = EnsembleWeightDecayEngine()

    def _run(self, models, cfg=None):
        return self.engine.reweight_ensemble("E", cfg or EnsembleConfig(), models)

    def test_empty_telemetry_rejected(self):
        with self.assertRaises(EnsembleWeightError):
            self._run([])

    def test_empty_ensemble_id_rejected(self):
        with self.assertRaises(EnsembleWeightError):
            self.engine.reweight_ensemble("   ", EnsembleConfig(),
                                          [ModelTelemetry("A", 0.1, 0.05)])

    def test_non_finite_telemetry_rejected(self):
        """REGRESSION. NaN previously propagated to a NaN weight on an ACTIVE
        model, silently corrupting the allocation vector."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(EnsembleWeightError):
                    self._run([ModelTelemetry("A", bad, 0.05),
                               ModelTelemetry("B", 0.5, 0.05)])
                with self.assertRaises(EnsembleWeightError):
                    self._run([ModelTelemetry("A", 0.1, bad),
                               ModelTelemetry("B", 0.5, 0.05)])

    def test_non_finite_carried_state_rejected(self):
        with self.assertRaises(EnsembleWeightError):
            self._run([ModelTelemetry("A", 0.1, 0.05, previous_decayed_loss=float("nan")),
                       ModelTelemetry("B", 0.5, 0.05)])
        with self.assertRaises(EnsembleWeightError):
            self._run([ModelTelemetry("A", 0.1, 0.05, previous_decayed_ic=float("inf")),
                       ModelTelemetry("B", 0.5, 0.05)])

    def test_negative_loss_rejected(self):
        with self.assertRaises(EnsembleWeightError):
            self._run([ModelTelemetry("A", -0.1, 0.05), ModelTelemetry("B", 0.5, 0.05)])

    def test_non_numeric_telemetry_rejected(self):
        with self.assertRaises(EnsembleWeightError):
            self._run([ModelTelemetry("A", "0.1", 0.05), ModelTelemetry("B", 0.5, 0.05)])

    def test_duplicate_model_id_rejected(self):
        """REGRESSION. Weights are keyed by model_id; a duplicate previously made
        the first entry silently inherit the second's metric and be demoted."""
        with self.assertRaises(EnsembleWeightError):
            self._run([ModelTelemetry("DUP", 0.1, 0.05),
                       ModelTelemetry("DUP", 3.0, 0.05),
                       ModelTelemetry("X", 0.5, 0.05)])

    def test_empty_model_id_rejected(self):
        with self.assertRaises(EnsembleWeightError):
            self._run([ModelTelemetry("", 0.1, 0.05), ModelTelemetry("B", 0.5, 0.05)])

    def test_negative_days_active_rejected(self):
        with self.assertRaises(EnsembleWeightError):
            self._run([ModelTelemetry("A", 0.1, 0.05, days_active=-1),
                       ModelTelemetry("B", 0.5, 0.05)])

    def test_single_model_ensemble_is_allowed(self):
        report = self._run([ModelTelemetry("Solo", 0.1, 0.05)],
                           EnsembleConfig(min_weight_floor=0.0))
        self.assertEqual(report.active_model_count, 1)
        self.assertEqual(_by_id(report, "Solo").final_normalized_weight, 1.0)


class TestConfigValidation(unittest.TestCase):
    """REGRESSION. Every one of these was previously accepted in silence."""

    def test_lambda_outside_unit_interval_rejected(self):
        for lam in (-0.1, 1.5, 5.0):
            with self.subTest(lam=lam):
                with self.assertRaises(EnsembleWeightError):
                    EnsembleConfig(decay_factor_lambda=lam)

    def test_non_positive_beta_rejected(self):
        """A negative beta inverts the softmax and rewards the worst model."""
        for beta in (-3.0, 0.0, float("nan"), float("inf")):
            with self.subTest(beta=beta):
                with self.assertRaises(EnsembleWeightError):
                    EnsembleConfig(temperature_beta=beta)

    def test_floor_outside_unit_interval_rejected(self):
        for floor in (-0.01, 1.0, 1.5):
            with self.subTest(floor=floor):
                with self.assertRaises(EnsembleWeightError):
                    EnsembleConfig(min_weight_floor=floor)

    def test_unknown_weighting_method_rejected(self):
        """Previously a typo fell through to IC_SOFTMAX behaviour."""
        for method in ("EXPONENTIAL_LOOS", "exponential_loss", "", "SHARPE"):
            with self.subTest(method=method):
                with self.assertRaises(EnsembleWeightError):
                    EnsembleConfig(weighting_method=method)

    def test_min_days_active_below_one_rejected(self):
        with self.assertRaises(EnsembleWeightError):
            EnsembleConfig(min_days_active=0)

    def test_floor_at_or_above_equal_share_rejected(self):
        """A 0.05 floor over 30 models demotes every model even when all perform
        identically; the engine previously fell back to sub-floor equal weights
        that summed to 0.999."""
        engine = EnsembleWeightDecayEngine()
        models = [ModelTelemetry(f"M{i}", 0.3, 0.04) for i in range(30)]
        with self.assertRaises(EnsembleWeightError):
            engine.reweight_ensemble("E", EnsembleConfig(min_weight_floor=0.05), models)

    def test_floor_just_below_equal_share_is_accepted(self):
        engine = EnsembleWeightDecayEngine()
        models = [ModelTelemetry(f"M{i}", 0.3, 0.04) for i in range(30)]
        report = engine.reweight_ensemble(
            "E", EnsembleConfig(min_weight_floor=0.03), models)
        self.assertEqual(report.active_model_count, 30)
        self.assertEqual(
            math.fsum(s.final_normalized_weight for s in report.model_statuses), 1.0)

    def test_errors_are_value_errors_for_legacy_callers(self):
        with self.assertRaises(ValueError):
            EnsembleConfig(temperature_beta=-1.0)


if __name__ == "__main__":
    unittest.main()
