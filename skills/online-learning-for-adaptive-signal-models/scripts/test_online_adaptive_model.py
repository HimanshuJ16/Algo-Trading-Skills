"""Unit tests for online-learning-for-adaptive-signal-models.

Expected values are derived independently of the implementation wherever the
quantity is closed-form:

* the LMS step is hand-evaluated from ``w <- w + eta * e * x``;
* the NLMS unit step is checked against its *defining* property -- a posteriori
  error exactly zero -- rather than against the code's own arithmetic;
* RLS at ``lambda = 1`` is checked against the ordinary-least-squares solution
  of the 2x2 normal equations, computed here by Cramer's rule;
* the Page-Hinkley statistic is checked against a recursion evaluated by hand.

Tests are deterministic: no ``random``, no wall clock.
"""
import json
import math
import unittest

from online_adaptive_model import (
    DIVERGENT_STEP_RATIO,
    LMS,
    NLMS,
    RLS,
    LabelHorizonBuffer,
    OnlineAdaptiveSignalModel,
    OnlineLearningError,
    PageHinkleyDetector,
)


def _stationary_stream(count, w0=2.0, w1=-1.5, start=0):
    """Deterministic, persistently exciting 2-feature stream for y = w0*x0 + w1*x1."""
    for i in range(start, start + count):
        x0 = math.sin(i)
        x1 = math.cos(2 * i)
        yield [x0, x1], w0 * x0 + w1 * x1


def _ols_two_features(samples):
    """Least-squares solution of the 2x2 normal equations by Cramer's rule.

    Deliberately written from the textbook definition rather than reusing any
    part of the engine, so it is an independent expected value.
    """
    a = sum(x[0] * x[0] for x, _ in samples)
    b = sum(x[0] * x[1] for x, _ in samples)
    d = sum(x[1] * x[1] for x, _ in samples)
    p = sum(x[0] * y for x, y in samples)
    q = sum(x[1] * y for x, y in samples)
    determinant = a * d - b * b
    return [(p * d - b * q) / determinant, (a * q - p * b) / determinant]


class TestGradientUpdateRules(unittest.TestCase):
    def test_lms_single_step_matches_hand_derived_closed_form(self):
        # w0 = [0, 0], x = [2, -1], y = 3, eta = 0.1, no leakage.
        # pred = 0, e = 3, w = 0 + 0.1 * 3 * x = [0.6, -0.3].
        model = OnlineAdaptiveSignalModel(2, learning_rate=0.1, l2_penalty=0.0)
        result = model.update([2.0, -1.0], 3.0)

        self.assertAlmostEqual(model.weights[0], 0.6, places=12)
        self.assertAlmostEqual(model.weights[1], -0.3, places=12)
        self.assertEqual(result.predicted_y, 0.0)
        self.assertEqual(result.prediction_error, 3.0)
        self.assertEqual(result.update_rule, LMS)
        # step_ratio = eta * ||x||^2 = 0.1 * 5
        self.assertAlmostEqual(result.step_ratio, 0.5, places=12)

    def test_lms_leakage_term_matches_hand_derived_closed_form(self):
        # Second step from w = [0.6, -0.3] with l2 = 0.2, eta = 0.1:
        # retention = 1 - 0.1*0.2 = 0.98
        # x = [1, 0], y = 1  ->  pred = 0.6, e = 0.4
        # w0 = 0.98*0.6 + 0.1*0.4*1 = 0.588 + 0.04 = 0.628
        # w1 = 0.98*-0.3 + 0            = -0.294
        model = OnlineAdaptiveSignalModel(2, learning_rate=0.1, l2_penalty=0.2)
        model.weights = [0.6, -0.3]
        model.update([1.0, 0.0], 1.0)

        self.assertAlmostEqual(model.weights[0], 0.628, places=12)
        self.assertAlmostEqual(model.weights[1], -0.294, places=12)

    def test_nlms_unit_step_drives_the_a_posteriori_error_to_zero(self):
        # Defining property of NLMS: with mu = 1 and no leakage, one step makes
        # w'x equal y exactly, whatever the scale of x.
        for scale in (1e-3, 1.0, 1e3):
            with self.subTest(scale=scale):
                model = OnlineAdaptiveSignalModel(
                    2,
                    learning_rate=1.0,
                    l2_penalty=0.0,
                    update_rule=NLMS,
                    nlms_epsilon=1e-18,
                    max_weight_norm=1e12,
                )
                features = [3.0 * scale, 4.0 * scale]
                model.update(features, 1.0)
                self.assertAlmostEqual(model.predict(features), 1.0, places=9)

    def test_nlms_step_ratio_is_mu_regardless_of_feature_scale(self):
        # This is the property that makes the NLMS stability region independent
        # of the signal statistics, and the reason LMS needs a warning instead.
        for scale in (1e-2, 1.0, 1e2):
            with self.subTest(scale=scale):
                nlms = OnlineAdaptiveSignalModel(
                    2,
                    learning_rate=0.5,
                    l2_penalty=0.0,
                    update_rule=NLMS,
                    max_weight_norm=1e12,
                )
                result = nlms.update([1.0 * scale, 1.0 * scale], 1.0)
                # nlms_epsilon shades the ratio below mu when ||x||^2 approaches
                # it; at ||x||^2 = 2e-4 against eps = 1e-8 that is 5e-5 relative.
                self.assertAlmostEqual(result.step_ratio, 0.5, delta=1e-3)

        # The same feature scaling moves the LMS ratio by four orders of magnitude.
        ratios = []
        for scale in (1e-2, 1.0, 1e2):
            lms = OnlineAdaptiveSignalModel(
                2, learning_rate=0.5, l2_penalty=0.0, max_weight_norm=1e12
            )
            ratios.append(lms.update([1.0 * scale, 1.0 * scale], 1.0).step_ratio)
        self.assertLess(ratios[0], 1e-3)
        self.assertGreater(ratios[2], 1e3)

    def test_nlms_rejects_a_step_size_outside_its_stability_region(self):
        for bad_mu in (2.0, 2.5, 10.0):
            with self.subTest(mu=bad_mu):
                with self.assertRaises(OnlineLearningError):
                    OnlineAdaptiveSignalModel(2, learning_rate=bad_mu, update_rule=NLMS)
        # The boundary value below 2 is accepted.
        OnlineAdaptiveSignalModel(2, learning_rate=1.999, update_rule=NLMS)

    def test_unstable_lms_step_is_counted_and_reported_not_silently_applied(self):
        model = OnlineAdaptiveSignalModel(
            1, learning_rate=1.0, l2_penalty=0.0, max_weight_norm=5.0
        )
        result = model.update([10.0], 100.0)

        # eta * ||x||^2 = 1.0 * 100 = 100 >= 2: the step magnified the error.
        self.assertAlmostEqual(result.step_ratio, 100.0, places=12)
        self.assertGreaterEqual(result.step_ratio, DIVERGENT_STEP_RATIO)
        self.assertEqual(model.unstable_step_count, 1)
        self.assertTrue(result.weights_clipped)

    def test_rejects_a_leakage_factor_that_inverts_the_weights(self):
        # retention = 1 - eta*l2. At eta*l2 >= 1 the leakage term alone flips the
        # sign of every weight on every update, which is never what was meant.
        with self.assertRaises(OnlineLearningError):
            OnlineAdaptiveSignalModel(1, learning_rate=1.5, l2_penalty=0.8, update_rule=NLMS)
        with self.assertRaises(OnlineLearningError):
            OnlineAdaptiveSignalModel(1, learning_rate=10.0, l2_penalty=0.1)
        # Just below the boundary is accepted, and RLS ignores l2 entirely.
        OnlineAdaptiveSignalModel(1, learning_rate=10.0, l2_penalty=0.099)
        OnlineAdaptiveSignalModel(1, learning_rate=10.0, l2_penalty=0.5, update_rule=RLS)

    def test_converges_on_a_stationary_linear_relationship(self):
        model = OnlineAdaptiveSignalModel(
            2, learning_rate=0.5, l2_penalty=0.0, update_rule=NLMS
        )
        for features, target in _stationary_stream(400):
            model.update(features, target)

        self.assertAlmostEqual(model.weights[0], 2.0, delta=0.02)
        self.assertAlmostEqual(model.weights[1], -1.5, delta=0.02)

        report = model.audit_performance()
        self.assertTrue(report.sufficient_samples)
        self.assertTrue(report.is_converged)
        self.assertLess(report.final_mae, report.initial_mae)


class TestRecursiveLeastSquares(unittest.TestCase):
    SAMPLES = [
        ([1.0, 2.0], 5.0),
        ([2.0, -1.0], 0.5),
        ([-1.0, 3.0], 7.25),
        ([0.5, 0.5], 2.0),
        ([3.0, 1.0], 5.5),
        ([-2.0, 2.0], 5.0),
    ]

    def _fresh(self, **kwargs):
        params = dict(
            update_rule=RLS,
            forgetting_factor=1.0,
            rls_initial_covariance=1e8,
            rls_max_covariance_trace=1e12,
            l2_penalty=0.0,
            max_weight_norm=1e9,
        )
        params.update(kwargs)
        return OnlineAdaptiveSignalModel(2, **params)

    def test_unit_forgetting_reproduces_ordinary_least_squares(self):
        # The defining property of RLS: with lambda = 1 and a diffuse prior it
        # is the exact least-squares fit of everything seen so far.
        model = self._fresh()
        for features, target in self.SAMPLES:
            model.update(features, target)

        expected = _ols_two_features(self.SAMPLES)
        for got, want in zip(model.weights, expected):
            # Residual is the P_0 prior's weight, 1 / rls_initial_covariance = 1e-8.
            self.assertAlmostEqual(got, want, places=6)

    def test_forgetting_tracks_a_regime_shift_that_pooled_least_squares_cannot(self):
        # 300 samples of y = 2*x0 - 1.5*x1, then 300 of y = -1*x0 + 3*x1.
        adaptive = self._fresh(forgetting_factor=0.95, max_weight_norm=10.0)
        pooled = self._fresh(forgetting_factor=1.0, max_weight_norm=10.0)

        for features, target in _stationary_stream(300):
            adaptive.update(features, target)
            pooled.update(features, target)
        for features, target in _stationary_stream(300, w0=-1.0, w1=3.0, start=300):
            adaptive.update(features, target)
            pooled.update(features, target)

        # The post-shift truth is [-1.0, 3.0] by construction.
        self.assertAlmostEqual(adaptive.weights[0], -1.0, delta=0.01)
        self.assertAlmostEqual(adaptive.weights[1], 3.0, delta=0.01)

        pooled_distance = math.dist(pooled.weights, [-1.0, 3.0])
        self.assertGreater(pooled_distance, 0.5)

    def test_effective_memory_matches_one_over_one_minus_lambda(self):
        self.assertAlmostEqual(
            self._fresh(forgetting_factor=0.99).effective_memory_samples, 100.0, places=9
        )
        self.assertAlmostEqual(
            self._fresh(forgetting_factor=0.95).effective_memory_samples, 20.0, places=9
        )
        self.assertEqual(self._fresh(forgetting_factor=1.0).effective_memory_samples, math.inf)
        # Not defined for the gradient rules, and not faked.
        self.assertIsNone(OnlineAdaptiveSignalModel(2).effective_memory_samples)

    def test_covariance_windup_guard_bounds_the_trace_under_zero_excitation(self):
        # With x = 0 the gain is zero and P <- P / lambda every step, so the
        # trace grows geometrically: this is covariance windup in its purest form.
        limit = 1e6
        model = self._fresh(
            forgetting_factor=0.9,
            rls_initial_covariance=1e3,
            rls_max_covariance_trace=limit,
        )
        for _ in range(500):
            model.update([0.0, 0.0], 0.0)

        self.assertLessEqual(model.covariance_trace, limit)
        self.assertGreater(model.covariance_frozen_count, 0)
        # Unguarded, 500 steps at lambda = 0.9 would reach 2e3 * 0.9**-500.
        self.assertGreater(2e3 * 0.9 ** -500, limit)

    def test_rejects_a_trace_limit_below_the_initial_trace(self):
        # Otherwise the guard fires on sample 1 and RLS degenerates into a
        # fixed-gain filter that never adapts, silently.
        with self.assertRaises(OnlineLearningError):
            OnlineAdaptiveSignalModel(
                2,
                update_rule=RLS,
                rls_initial_covariance=1e4,
                rls_max_covariance_trace=1e4,
            )

    def test_l2_penalty_is_not_silently_applied_to_rls(self):
        with self.assertLogs("online_adaptive_model", level="WARNING") as captured:
            self._fresh(l2_penalty=0.001)
        self.assertIn("ignored by the rls update rule", "".join(captured.output))

    def test_covariance_stays_symmetric(self):
        model = self._fresh(forgetting_factor=0.97)
        for features, target in _stationary_stream(200):
            model.update(features, target)
        covariance = model.to_state()["covariance"]
        self.assertAlmostEqual(covariance[0][1], covariance[1][0], places=15)


class TestNonFiniteInputRejection(unittest.TestCase):
    """Regression tests. Before 2.0 a single NaN tick turned every weight into
    NaN permanently and silently: the norm projection never fired because
    ``nan > max_norm`` is ``False``, and every later prediction was NaN."""

    def _primed_model(self):
        model = OnlineAdaptiveSignalModel(2, learning_rate=0.05, l2_penalty=0.0)
        model.update([0.5, -0.2], 0.4)
        return model

    def test_nan_feature_is_rejected_and_the_weights_survive(self):
        model = self._primed_model()
        before = list(model.weights)
        with self.assertRaises(OnlineLearningError):
            model.update([float("nan"), 0.1], 0.3)
        self.assertEqual(model.weights, before)
        self.assertTrue(all(math.isfinite(w) for w in model.weights))
        self.assertTrue(math.isfinite(model.predict([0.5, -0.2])))

    def test_inf_feature_and_nan_target_are_rejected(self):
        for features, target in (
            ([float("inf"), 0.1], 0.3),
            ([-float("inf"), 0.1], 0.3),
            ([0.5, 0.1], float("nan")),
            ([0.5, 0.1], float("inf")),
        ):
            with self.subTest(features=features, target=target):
                model = self._primed_model()
                before = list(model.weights)
                with self.assertRaises(OnlineLearningError):
                    model.update(features, target)
                self.assertEqual(model.weights, before)

    def test_predict_rejects_non_finite_features(self):
        model = self._primed_model()
        with self.assertRaises(OnlineLearningError):
            model.predict([float("nan"), 0.0])

    def test_non_numeric_input_is_rejected(self):
        model = self._primed_model()
        for features in (["0.5", 0.1], [None, 0.1], [True, 0.1]):
            with self.subTest(features=features):
                with self.assertRaises(OnlineLearningError):
                    model.update(features, 0.3)

    def test_an_overflowing_step_raises_instead_of_installing_nan_weights(self):
        # 1e200 * 1e200 overflows to inf in float arithmetic without raising;
        # older this silently produced NaN weights.
        model = OnlineAdaptiveSignalModel(
            1, learning_rate=1.0, l2_penalty=0.0, max_weight_norm=1e300
        )
        before = list(model.weights)
        with self.assertRaises(OnlineLearningError):
            model.update([1e200], 1e200)
        self.assertEqual(model.weights, before)

    def test_wrong_feature_count_is_rejected(self):
        model = self._primed_model()
        for features in ([0.1], [0.1, 0.2, 0.3], []):
            with self.subTest(length=len(features)):
                with self.assertRaises(OnlineLearningError):
                    model.update(features, 0.3)


class TestWeightProjection(unittest.TestCase):
    def test_projection_lands_on_the_ball_and_preserves_direction(self):
        model = OnlineAdaptiveSignalModel(2, max_weight_norm=5.0)
        model.weights = [30.0, 40.0]  # norm 50, direction 3:4
        self.assertTrue(model._project_onto_norm_ball())

        self.assertAlmostEqual(model.weights_norm(), 5.0, places=12)
        self.assertAlmostEqual(model.weights[0], 3.0, places=12)
        self.assertAlmostEqual(model.weights[1], 4.0, places=12)

    def test_projection_is_inactive_inside_the_ball(self):
        model = OnlineAdaptiveSignalModel(2, max_weight_norm=5.0)
        model.weights = [3.0, 4.0]  # exactly on the boundary
        self.assertFalse(model._project_onto_norm_ball())
        self.assertEqual(model.weights, [3.0, 4.0])

    def test_projection_survives_weights_whose_squares_would_overflow(self):
        # sum(w*w) is inf for w = 1e200, so a naive norm cannot be compared to
        # max_norm and the projection would raise with the diverged weights
        # installed. The scaled norm projects them back onto the ball instead.
        model = OnlineAdaptiveSignalModel(2, max_weight_norm=10.0)
        model.weights = [1e200, 1e200]

        self.assertTrue(model._project_onto_norm_ball())
        self.assertAlmostEqual(model.weights_norm(), 10.0, places=9)
        self.assertAlmostEqual(model.weights[0], model.weights[1], places=9)

    def test_repeated_large_updates_stay_bounded(self):
        model = OnlineAdaptiveSignalModel(
            1, learning_rate=1.0, l2_penalty=0.0, max_weight_norm=5.0
        )
        for _ in range(10):
            model.update([10.0], 100.0)
        self.assertLessEqual(model.weights_norm(), 5.0 + 1e-9)
        self.assertEqual(model.clipped_update_count, 10)


class TestLookAheadRefusal(unittest.TestCase):
    def test_an_unrealised_label_is_refused(self):
        model = OnlineAdaptiveSignalModel(2)
        before = list(model.weights)
        with self.assertRaises(OnlineLearningError) as ctx:
            model.update([0.1, 0.2], 0.5, label_ready_time=100.0, now=99.999)
        self.assertIn("not realised yet", str(ctx.exception))
        self.assertEqual(model.weights, before)
        self.assertEqual(model.total_samples, 0)

    def test_a_label_realised_exactly_now_is_accepted(self):
        model = OnlineAdaptiveSignalModel(2, l2_penalty=0.0)
        model.update([0.1, 0.2], 0.5, label_ready_time=100.0, now=100.0)
        self.assertEqual(model.total_samples, 1)

    def test_supplying_only_one_horizon_argument_is_refused(self):
        model = OnlineAdaptiveSignalModel(2)
        with self.assertRaises(OnlineLearningError):
            model.update([0.1, 0.2], 0.5, label_ready_time=100.0)
        with self.assertRaises(OnlineLearningError):
            model.update([0.1, 0.2], 0.5, now=100.0)


class TestLabelHorizonBuffer(unittest.TestCase):
    def test_releases_only_due_samples_in_order(self):
        buffer = LabelHorizonBuffer()
        buffer.enqueue([1.0], feature_time=0.0, label_ready_time=300.0)
        buffer.enqueue([2.0], feature_time=60.0, label_ready_time=360.0)
        buffer.enqueue([3.0], feature_time=120.0, label_ready_time=420.0)

        self.assertEqual(buffer.pending_count, 3)
        self.assertEqual(buffer.oldest_label_ready_time, 300.0)
        self.assertEqual(buffer.release_due(299.0), [])

        due = buffer.release_due(360.0)  # boundary: 360 is due at now = 360
        self.assertEqual([s.features for s in due], [[1.0], [2.0]])
        self.assertEqual(buffer.pending_count, 1)

        self.assertEqual([s.features for s in buffer.release_due(1e9)], [[3.0]])
        self.assertEqual(buffer.pending_count, 0)

    def test_rejects_a_backdated_label(self):
        buffer = LabelHorizonBuffer()
        with self.assertRaises(OnlineLearningError):
            buffer.enqueue([1.0], feature_time=100.0, label_ready_time=99.0)

    def test_rejects_out_of_order_label_times(self):
        buffer = LabelHorizonBuffer()
        buffer.enqueue([1.0], feature_time=0.0, label_ready_time=300.0)
        with self.assertRaises(OnlineLearningError):
            buffer.enqueue([2.0], feature_time=10.0, label_ready_time=299.0)

    def test_refuses_to_grow_without_bound(self):
        buffer = LabelHorizonBuffer(max_pending=3)
        for i in range(3):
            buffer.enqueue([float(i)], feature_time=float(i), label_ready_time=100.0 + i)
        with self.assertRaises(OnlineLearningError) as ctx:
            buffer.enqueue([9.0], feature_time=9.0, label_ready_time=200.0)
        self.assertIn("full", str(ctx.exception))

    def test_drives_a_model_without_look_ahead(self):
        model = OnlineAdaptiveSignalModel(1, learning_rate=1.0, update_rule=NLMS, l2_penalty=0.0)
        buffer = LabelHorizonBuffer()
        realised = {}
        horizon = 300.0

        for step in range(5):
            feature_time = float(step * 60)
            buffer.enqueue([1.0], feature_time, feature_time + horizon)
            realised[feature_time + horizon] = 0.001 * (step + 1)

        applied = 0
        for now in (100.0, 400.0, 500.0):
            for sample in buffer.release_due(now):
                model.update(
                    sample.features,
                    realised[sample.label_ready_time],
                    label_ready_time=sample.label_ready_time,
                    now=now,
                )
                applied += 1

        # Labels fall due at 300, 360, 420, 480, 540. At now = 100 none are
        # realised; by 400 two are; by 500 four are, leaving 540 pending.
        self.assertEqual(applied, 4)
        self.assertEqual(buffer.pending_count, 1)
        self.assertEqual(model.total_samples, 4)


class TestPageHinkleyDetector(unittest.TestCase):
    def test_statistic_matches_a_hand_evaluated_recursion(self):
        # x = [1, 1, 1, 4], delta = 0, threshold = 1, min_samples = 1.
        # t=1: mean 1,     cum 0,    min 0,  PH 0
        # t=2: mean 1,     cum 0,    min 0,  PH 0
        # t=3: mean 1,     cum 0,    min 0,  PH 0
        # t=4: mean 1.75,  cum 2.25, min 0,  PH 2.25 > 1 -> drift
        detector = PageHinkleyDetector(delta=0.0, threshold=1.0, min_samples=1)
        statistics = [detector.observe(x) for x in (1.0, 1.0, 1.0, 4.0)]

        self.assertEqual([round(s.statistic, 12) for s in statistics[:3]], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(statistics[3].statistic, 2.25, places=12)
        self.assertEqual([s.drift_detected for s in statistics], [False, False, False, True])
        # Auto-reset on detection: a single change gives a single signal.
        self.assertEqual(detector.samples_seen, 0)

    def test_delta_absorbs_tolerated_error(self):
        # x = [1, 1], delta = 0.5: cum goes -0.5 then -1.0, and min tracks it,
        # so PH stays at 0 throughout.
        detector = PageHinkleyDetector(delta=0.5, threshold=0.1, min_samples=1)
        for _ in range(2):
            signal = detector.observe(1.0)
            self.assertAlmostEqual(signal.statistic, 0.0, places=12)
            self.assertFalse(signal.drift_detected)

    def test_a_stationary_error_stream_never_signals(self):
        detector = PageHinkleyDetector(delta=0.0005, threshold=0.05, min_samples=30)
        # Alternating around a constant mean: zero net drift.
        for i in range(1000):
            signal = detector.observe(0.001 + (0.0002 if i % 2 else -0.0002))
            self.assertFalse(signal.drift_detected)

    def test_a_step_increase_in_error_is_detected(self):
        detector = PageHinkleyDetector(delta=0.0005, threshold=0.05, min_samples=30)
        for _ in range(200):
            self.assertFalse(detector.observe(0.001).drift_detected)

        detected_at = None
        for i in range(200):
            if detector.observe(0.01).drift_detected:
                detected_at = i
                break
        self.assertIsNotNone(detected_at)

    def test_a_decrease_in_error_is_never_signalled(self):
        # One-sided test: an improving model must not raise a drift alarm.
        detector = PageHinkleyDetector(delta=0.0, threshold=0.01, min_samples=5)
        for _ in range(100):
            self.assertFalse(detector.observe(0.01).drift_detected)
        for _ in range(500):
            self.assertFalse(detector.observe(0.0001).drift_detected)

    def test_rejects_invalid_configuration(self):
        for kwargs in (
            {"delta": -0.1, "threshold": 1.0},
            {"delta": 0.0, "threshold": 0.0},
            {"delta": 0.0, "threshold": -1.0},
            {"delta": float("nan"), "threshold": 1.0},
            {"delta": 0.0, "threshold": 1.0, "min_samples": 0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(OnlineLearningError):
                    PageHinkleyDetector(**kwargs)

    def test_wired_into_the_model_it_reports_and_counts(self):
        detector = PageHinkleyDetector(delta=0.0005, threshold=0.05, min_samples=30)
        model = OnlineAdaptiveSignalModel(
            1, learning_rate=0.0001, l2_penalty=0.0, drift_detector=detector
        )
        signalled = False
        for i in range(600):
            target = 0.001 if i < 200 else 0.05
            if model.update([0.0], target).drift_detected:
                signalled = True
                break
        self.assertTrue(signalled)
        self.assertEqual(model.drift_detection_count, 1)

    def test_drift_resets_the_rls_covariance_when_configured(self):
        detector = PageHinkleyDetector(delta=0.0005, threshold=0.05, min_samples=30)
        model = OnlineAdaptiveSignalModel(
            2,
            update_rule=RLS,
            forgetting_factor=0.99,
            rls_initial_covariance=1e4,
            rls_max_covariance_trace=1e8,
            l2_penalty=0.0,
            drift_detector=detector,
            reset_covariance_on_drift=True,
        )
        for features, target in _stationary_stream(200):
            model.update(features, target)
        settled_trace = model.covariance_trace
        self.assertLess(settled_trace, 2e4)

        for _ in range(400):
            if model.update([1.0, 1.0], 5.0).drift_detected:
                break
        self.assertEqual(model.drift_detection_count, 1)
        self.assertGreater(model.covariance_trace, settled_trace)


class TestAuditReport(unittest.TestCase):
    def test_requires_two_disjoint_windows(self):
        model = OnlineAdaptiveSignalModel(1, baseline_window=10, recent_window=20)
        for _ in range(29):
            model.update([1.0], 0.5)
        report = model.audit_performance()
        self.assertFalse(report.sufficient_samples)
        self.assertFalse(report.is_converged)
        self.assertIn("Insufficient samples", report.message)

        model.update([1.0], 0.5)
        self.assertTrue(model.audit_performance().sufficient_samples)

    def test_weights_in_the_report_are_a_copy(self):
        # Regression: an earlier "not enough samples" branch returned the live
        # weight list, letting a caller mutate model state through the report.
        model = OnlineAdaptiveSignalModel(2, l2_penalty=0.0)
        report = model.audit_performance()
        report.weights[0] = 999.0
        self.assertEqual(model.weights, [0.0, 0.0])

        for features, target in _stationary_stream(200):
            model.update(features, target)
        full = model.audit_performance()
        full.weights[0] = 999.0
        self.assertNotEqual(model.weights[0], 999.0)

    def test_memory_is_bounded_by_the_configured_windows(self):
        # Regression: older the error history was an unbounded list, so a live
        # model leaked memory for as long as it ran.
        model = OnlineAdaptiveSignalModel(
            1, learning_rate=0.01, l2_penalty=0.0, baseline_window=50, recent_window=100
        )
        for i in range(20_000):
            model.update([1.0], 0.001 * (i % 7))

        self.assertEqual(len(model._baseline_errors), 50)
        self.assertEqual(len(model._recent_errors), 100)
        self.assertEqual(model.total_samples, 20_000)

        report = model.audit_performance()
        self.assertEqual(report.baseline_sample_count, 50)
        self.assertEqual(report.recent_sample_count, 100)

    def test_baseline_window_is_fixed_at_the_start_of_the_stream(self):
        # The baseline must keep meaning the same thing as the stream grows. The
        # older "first quarter of everything" moved with the sample count.
        model = OnlineAdaptiveSignalModel(
            1, learning_rate=1e-12, l2_penalty=0.0, baseline_window=5, recent_window=5
        )  # a negligible step keeps the weights ~frozen, so errors equal targets
        for value in [1.0] * 5 + [0.0] * 200:
            model.update([1.0], value)

        report = model.audit_performance()
        self.assertAlmostEqual(report.initial_mae, 1.0, places=6)
        self.assertAlmostEqual(report.final_mae, 0.0, places=6)
        self.assertAlmostEqual(report.mae_improvement_pct, 100.0, places=4)

    def test_zero_baseline_mae_reports_zero_change_not_a_fabricated_number(self):
        # Regression: older divided by a max(1e-4, mae) floor, turning a
        # perfect baseline into a fabricated five-figure "improvement".
        model = OnlineAdaptiveSignalModel(
            1, learning_rate=1e-12, l2_penalty=0.0, baseline_window=5, recent_window=5
        )
        for value in [0.0] * 5 + [1.0] * 5:
            model.update([1.0], value)

        report = model.audit_performance()
        self.assertEqual(report.initial_mae, 0.0)
        self.assertEqual(report.mae_improvement_pct, 0.0)
        self.assertFalse(report.is_converged)

    def test_report_carries_the_diagnostic_counters(self):
        model = OnlineAdaptiveSignalModel(
            1,
            learning_rate=1.0,
            l2_penalty=0.0,
            max_weight_norm=1.0,
            baseline_window=5,
            recent_window=5,
        )
        for _ in range(10):
            model.update([10.0], 100.0)
        report = model.audit_performance()

        self.assertEqual(report.update_rule, LMS)
        self.assertEqual(report.clipped_update_count, 10)
        self.assertEqual(report.unstable_step_count, 10)
        self.assertIsNone(report.effective_memory_samples)
        self.assertIsNone(report.covariance_trace)


class TestPersistence(unittest.TestCase):
    def test_round_trip_through_json_reproduces_the_model(self):
        for rule, kwargs in (
            (LMS, {}),
            (NLMS, {"learning_rate": 0.5}),
            (RLS, {"forgetting_factor": 0.98}),
        ):
            with self.subTest(rule=rule):
                model = OnlineAdaptiveSignalModel(
                    2,
                    l2_penalty=0.0,
                    update_rule=rule,
                    drift_detector=PageHinkleyDetector(0.001, 0.5),
                    **kwargs,
                )
                for features, target in _stationary_stream(120):
                    model.update(features, target)

                restored = OnlineAdaptiveSignalModel.from_state(
                    json.loads(json.dumps(model.to_state()))
                )
                self.assertEqual(restored.weights, model.weights)
                self.assertEqual(restored.total_samples, model.total_samples)
                self.assertEqual(restored.update_rule, model.update_rule)
                self.assertEqual(restored.covariance_trace, model.covariance_trace)

                probe = [0.31, -0.77]
                self.assertEqual(restored.predict(probe), model.predict(probe))

                # And it keeps learning identically from the restored state.
                for features, target in _stationary_stream(50, start=120):
                    model.update(features, target)
                    restored.update(features, target)
                self.assertEqual(restored.weights, model.weights)

    def test_rejects_a_mismatched_or_malformed_state(self):
        model = OnlineAdaptiveSignalModel(2, update_rule=RLS)
        state = model.to_state()

        with self.assertRaises(OnlineLearningError):
            OnlineAdaptiveSignalModel.from_state("not a dict")

        bad_version = dict(state, state_version=99)
        with self.assertRaises(OnlineLearningError):
            OnlineAdaptiveSignalModel.from_state(bad_version)

        bad_weights = dict(state, weights=[0.0])
        with self.assertRaises(OnlineLearningError):
            OnlineAdaptiveSignalModel.from_state(bad_weights)

        bad_covariance = dict(state, covariance=[[1.0], [1.0]])
        with self.assertRaises(OnlineLearningError):
            OnlineAdaptiveSignalModel.from_state(bad_covariance)

        nan_weights = dict(state, weights=[float("nan"), 0.0])
        with self.assertRaises(OnlineLearningError):
            OnlineAdaptiveSignalModel.from_state(nan_weights)

        missing = dict(state)
        del missing["total_samples"]
        with self.assertRaises(OnlineLearningError):
            OnlineAdaptiveSignalModel.from_state(missing)


class TestConfigurationValidation(unittest.TestCase):
    def test_rejects_invalid_constructor_arguments(self):
        cases = [
            {"num_features": 0},
            {"num_features": -1},
            {"num_features": 2.0},
            {"num_features": 2, "learning_rate": 0.0},
            {"num_features": 2, "learning_rate": -0.05},
            {"num_features": 2, "learning_rate": float("nan")},
            {"num_features": 2, "l2_penalty": -0.1},
            {"num_features": 2, "learning_rate": 1.0, "l2_penalty": 1.0},
            {"num_features": 2, "max_weight_norm": 0.0},
            {"num_features": 2, "max_weight_norm": -1.0},
            {"num_features": 2, "update_rule": "adam"},
            {"num_features": 2, "nlms_epsilon": 0.0},
            {"num_features": 2, "forgetting_factor": 0.0},
            {"num_features": 2, "forgetting_factor": 1.01},
            {"num_features": 2, "rls_initial_covariance": 0.0},
            {"num_features": 2, "baseline_window": 0},
            {"num_features": 2, "recent_window": -5},
            {"num_features": 2, "drift_detector": "page-hinkley"},
        ]
        for kwargs in cases:
            with self.subTest(**kwargs):
                with self.assertRaises(OnlineLearningError):
                    OnlineAdaptiveSignalModel(**kwargs)

    def test_defaults_construct_a_usable_model(self):
        model = OnlineAdaptiveSignalModel(3)
        self.assertEqual(model.weights, [0.0, 0.0, 0.0])
        self.assertEqual(model.update_rule, LMS)
        self.assertEqual(model.predict([1.0, 2.0, 3.0]), 0.0)


class TestDeterminismAndPurity(unittest.TestCase):
    def test_predict_does_not_mutate_the_model(self):
        model = OnlineAdaptiveSignalModel(2, l2_penalty=0.0)
        for features, target in _stationary_stream(20):
            model.update(features, target)
        before = list(model.weights)
        samples_before = model.total_samples

        for _ in range(10):
            model.predict([0.4, -0.9])

        self.assertEqual(model.weights, before)
        self.assertEqual(model.total_samples, samples_before)

    def test_two_identical_runs_produce_identical_weights(self):
        def run(rule):
            model = OnlineAdaptiveSignalModel(
                2, l2_penalty=0.0, update_rule=rule, forgetting_factor=0.97
            )
            for features, target in _stationary_stream(150):
                model.update(features, target)
            return model.weights

        for rule in (LMS, NLMS, RLS):
            with self.subTest(rule=rule):
                self.assertEqual(run(rule), run(rule))

    def test_the_caller_cannot_mutate_model_state_through_the_input_vector(self):
        model = OnlineAdaptiveSignalModel(2, l2_penalty=0.0)
        features = [1.0, 2.0]
        model.update(features, 1.0)
        features[0] = 99.0
        self.assertNotIn(99.0, model.weights)


if __name__ == "__main__":
    unittest.main()
