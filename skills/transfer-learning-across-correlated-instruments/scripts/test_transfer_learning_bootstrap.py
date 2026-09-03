"""
Unit tests for transfer-learning-across-correlated-instruments.

Expected values are derived independently of the implementation:

  * Ordinary least squares on a noiseless linear design must recover the generating
    coefficients exactly (up to floating point), so the expected standardized weight
    is ``true_coefficient * feature_std`` computed from the data, not from the fit.
  * The L2-SP solution is hand-solved in the scalar case, where profiling out the
    intercept leaves ``w = (Sxy + lambda * w_src) / (Sxx + lambda)`` with ``Sxx`` and
    ``Sxy`` the target-window moments of the source-scaled feature.
  * Standardized mean differences and Fisher-z bounds are recomputed with
    ``statistics`` and ``math``, not by calling the functions under test twice.
  * Campbell-Thompson out-of-sample R-squared is arithmetic on hand-picked residuals.

Several tests are regressions against specific defects and fail against the previous
gradient-descent implementation; each says so in its docstring.
"""
import math
import statistics
import unittest

from transfer_learning_bootstrap import (
    Dataset,
    FinancialTransferLearningEngine,
    MLOpsError,
    TransferConfig,
)


def _lcg(seed):
    """
    Deterministic uniform generator, so fixtures do not depend on the version-specific
    internals of ``random``. Numerical Recipes LCG constants.
    """
    state = seed

    def nxt():
        nonlocal state
        state = (1664525 * state + 1013904223) % (2 ** 32)
        return state / (2 ** 32)

    return nxt


def _normalish(rand, n):
    """Sum of 12 uniforms minus 6: mean 0, variance 1, bounded. Good enough fixtures."""
    return [sum(rand() for _ in range(12)) - 6.0 for _ in range(n)]


class TestDiagnostics(unittest.TestCase):
    def setUp(self):
        self.engine = FinancialTransferLearningEngine()

    def test_correlation_of_perfect_linear_relation_is_one(self):
        r = self.engine.calculate_correlation([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0])
        self.assertAlmostEqual(r, 1.0, places=12)

    def test_correlation_of_constant_series_fails_closed(self):
        """A constant series leaves the coefficient undefined. Every gate threshold is
        a lower bound, so 0.0 is the fail-closed answer, not an error."""
        self.assertEqual(
            self.engine.calculate_correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]), 0.0
        )

    def test_correlation_rejects_short_or_unequal_series(self):
        with self.assertRaises(MLOpsError):
            self.engine.calculate_correlation([1.0], [1.0])
        with self.assertRaises(MLOpsError):
            self.engine.calculate_correlation([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_fisher_z_lower_bound_matches_hand_computation(self):
        r, n = 0.8, 28
        expected = math.tanh(math.atanh(r) - 1.959963984540054 / math.sqrt(n - 3))
        self.assertAlmostEqual(
            self.engine.correlation_ci95_lower(r, n), expected, places=12
        )
        # The point estimate always exceeds its own lower bound.
        self.assertLess(self.engine.correlation_ci95_lower(r, n), r)

    def test_fisher_z_lower_bound_undefined_cases_return_none(self):
        self.assertIsNone(self.engine.correlation_ci95_lower(0.8, 3))
        self.assertIsNone(self.engine.correlation_ci95_lower(1.0, 50))

    def test_fisher_z_bound_widens_as_overlap_shrinks(self):
        """The reason min_correlation_overlap exists: r = 0.8 on 5 bars establishes
        far less than r = 0.8 on 200."""
        self.assertLess(
            self.engine.correlation_ci95_lower(0.8, 5),
            self.engine.correlation_ci95_lower(0.8, 200),
        )

    def test_covariate_shift_matches_hand_computed_smd(self):
        src = [[1.0, 10.0], [2.0, 10.0], [3.0, 20.0]]
        tgt = [[4.0, 10.0], [5.0, 10.0]]

        col0 = [1.0, 2.0, 3.0]
        col1 = [10.0, 10.0, 20.0]
        expected = [
            abs(statistics.mean(col0) - 4.5) / statistics.stdev(col0),
            abs(statistics.mean(col1) - 10.0) / statistics.stdev(col1),
        ]

        shifts = self.engine.calculate_covariate_shift(src, tgt)
        self.assertEqual(len(shifts), 2)
        for got, want in zip(shifts, expected):
            self.assertAlmostEqual(got, want, places=12)
        self.assertAlmostEqual(
            self.engine.mean_covariate_shift(src, tgt), sum(expected) / 2, places=12
        )

    def test_covariate_shift_is_blind_to_dispersion(self):
        """Documented limitation: an SMD compares means only. Identical means with
        wildly different spreads score zero, which a Wasserstein distance would not."""
        src = [[-1.0], [0.0], [1.0]]
        tgt = [[-100.0], [0.0], [100.0]]
        self.assertAlmostEqual(self.engine.calculate_covariate_shift(src, tgt)[0], 0.0)

    def test_covariate_shift_rejects_feature_space_mismatch(self):
        with self.assertRaises(MLOpsError):
            self.engine.calculate_covariate_shift([[1.0, 2.0], [3.0, 4.0]], [[1.0], [2.0]])

    def test_covariate_shift_rejects_empty_and_constant_source(self):
        with self.assertRaises(MLOpsError):
            self.engine.calculate_covariate_shift([], [[1.0]])
        with self.assertRaises(MLOpsError):
            self.engine.calculate_covariate_shift([[5.0], [5.0], [5.0]], [[1.0], [2.0]])

    def test_covariate_shift_rejects_ragged_matrices(self):
        """The public entry point must not leak an IndexError when a caller hands it a
        malformed matrix directly rather than through evaluate_transfer_performance."""
        with self.assertRaises(MLOpsError):
            self.engine.calculate_covariate_shift(
                [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], [[1.0, 2.0], [3.0]]
            )
        with self.assertRaises(MLOpsError):
            self.engine.calculate_covariate_shift(
                [[1.0, 2.0], [3.0], [5.0, 6.0]], [[1.0, 2.0], [3.0, 4.0]]
            )


class TestFitting(unittest.TestCase):
    def setUp(self):
        self.engine = FinancialTransferLearningEngine()
        rand = _lcg(11)
        self.d = 3
        self.n = 200
        self.coefficients = [2.0, -1.0, 0.5]
        cols = [_normalish(rand, self.n) for _ in range(self.d)]
        self.features = [[cols[j][i] for j in range(self.d)] for i in range(self.n)]
        self.targets = [
            sum(self.coefficients[j] * self.features[i][j] for j in range(self.d)) + 3.0
            for i in range(self.n)
        ]

    def test_source_fit_recovers_generating_coefficients_exactly(self):
        """Regression: the previous fixed-step gradient-descent loop stopped after 300
        passes with roughly 5% coefficient bias and an in-sample R-squared of 0.993 on
        this noiseless design. A closed-form solve is exact."""
        params = self.engine.fit_source_model(
            Dataset("SRC", self.features, self.targets)
        )
        for j in range(self.d):
            expected = self.coefficients[j] * params.feature_stds[j]
            self.assertAlmostEqual(params.weights[j], expected, places=9)
        self.assertAlmostEqual(
            self.engine.calculate_r2(
                self.targets, self.engine.predict(params, self.features)
            ),
            1.0,
            places=12,
        )

    def test_source_fit_requires_a_residual_degree_of_freedom(self):
        rows = [[1.0, 2.0], [3.0, 1.0], [5.0, 9.0]]
        with self.assertRaises(MLOpsError):
            self.engine.fit_source_model(Dataset("SRC", rows, [1.0, 2.0, 3.0]))

    def test_collinear_features_raise_instead_of_returning_garbage(self):
        rand = _lcg(3)
        base = _normalish(rand, 40)
        rows = [[base[i], 2.0 * base[i] + 1.0] for i in range(40)]
        with self.assertRaises(MLOpsError) as ctx:
            self.engine.fit_source_model(Dataset("SRC", rows, base))
        self.assertIn("collinear", str(ctx.exception))

    def test_l2sp_matches_hand_solved_scalar_case(self):
        """Scalar closed form: w = (Sxy + lambda*w_src) / (Sxx + lambda), on the
        source-scaled, target-centred feature."""
        src_rows = [[float(i)] for i in range(20)]
        src_y = [3.0 * i + 1.0 for i in range(20)]
        src_params = self.engine.fit_source_model(Dataset("SRC", src_rows, src_y))

        tgt_rows = [[2.0], [5.0], [9.0], [11.0], [14.0]]
        tgt_y = [1.0, -2.0, 4.0, 0.5, 7.0]
        lam = 0.35

        scaled = [
            (row[0] - src_params.feature_means[0]) / src_params.feature_stds[0]
            for row in tgt_rows
        ]
        z_bar = statistics.mean(scaled)
        y_bar = statistics.mean(tgt_y)
        n = len(scaled)
        s_xx = sum((z - z_bar) ** 2 for z in scaled) / n
        s_xy = sum((scaled[i] - z_bar) * (tgt_y[i] - y_bar) for i in range(n)) / n
        expected_w = (s_xy + lam * src_params.weights[0]) / (s_xx + lam)
        expected_b = y_bar - z_bar * expected_w

        config = TransferConfig("SRC", "TGT", l2_penalty=lam)
        tuned = self.engine.fine_tune_target_model(
            src_params, Dataset("TGT", tgt_rows, tgt_y), config
        )
        self.assertAlmostEqual(tuned.weights[0], expected_w, places=10)
        self.assertAlmostEqual(tuned.bias, expected_b, places=10)

    def test_l2sp_shrinkage_is_invariant_to_target_sample_size(self):
        """Regression for the penalty-scaling defect. The previous implementation
        applied the L2-SP gradient once per sample against an N-averaged data
        gradient, giving an effective penalty of N*lambda: departure from the source
        prior collapsed from 2.40 at N=5 to 0.19 at N=500 for one fixed lambda.
        Solved correctly, shrinkage depends on lambda alone."""
        src_params = self.engine.fit_source_model(
            Dataset("SRC", self.features, self.targets)
        )
        config = TransferConfig("SRC", "TGT", l2_penalty=0.1)

        drifts = []
        for n in (25, 100, 400, 1600):
            rand = _lcg(97 + n)
            cols = [_normalish(rand, n) for _ in range(self.d)]
            rows = [[cols[j][i] for j in range(self.d)] for i in range(n)]
            y = [sum(-4.0 * rows[i][j] for j in range(self.d)) for i in range(n)]
            tuned = self.engine.fine_tune_target_model(
                src_params, Dataset("TGT", rows, y), config
            )
            drifts.append(
                math.sqrt(
                    sum(
                        (tuned.weights[j] - src_params.weights[j]) ** 2
                        for j in range(self.d)
                    )
                )
            )

        # Every drift within 15% of the mean: no systematic trend in N. The old
        # implementation spanned better than an order of magnitude over this range.
        mean_drift = sum(drifts) / len(drifts)
        self.assertGreater(mean_drift, 0.0)
        for drift in drifts:
            self.assertLess(abs(drift - mean_drift) / mean_drift, 0.15, msg=str(drifts))

    def test_l2sp_limits_are_source_weights_and_target_ols(self):
        src_params = self.engine.fit_source_model(
            Dataset("SRC", self.features, self.targets)
        )
        rand = _lcg(5)
        cols = [_normalish(rand, 60) for _ in range(self.d)]
        rows = [[cols[j][i] for j in range(self.d)] for i in range(60)]
        y = [sum(-4.0 * rows[i][j] for j in range(self.d)) for i in range(60)]
        target = Dataset("TGT", rows, y)

        pinned = self.engine.fine_tune_target_model(
            src_params, target, TransferConfig("SRC", "TGT", l2_penalty=1e9)
        )
        for j in range(self.d):
            self.assertAlmostEqual(pinned.weights[j], src_params.weights[j], places=6)

        free = self.engine.fine_tune_target_model(
            src_params, target, TransferConfig("SRC", "TGT", l2_penalty=0.0)
        )
        self.assertAlmostEqual(
            self.engine.calculate_r2(y, self.engine.predict(free, rows)), 1.0, places=9
        )

    def test_fine_tune_rejects_feature_space_mismatch(self):
        """Regression: a target with fewer columns than the source model previously
        succeeded silently, returning the source's trailing weights unchanged and in
        force against features that do not exist."""
        src_params = self.engine.fit_source_model(
            Dataset("SRC", self.features, self.targets)
        )
        narrow = Dataset("TGT", [[1.0], [2.0], [3.0], [4.0]], [1.0, 2.0, 3.0, 4.0])
        with self.assertRaises(MLOpsError):
            self.engine.fine_tune_target_model(
                src_params, narrow, TransferConfig("SRC", "TGT")
            )

    def test_fine_tune_rejects_unidentified_cold_start_without_penalty(self):
        src_params = self.engine.fit_source_model(
            Dataset("SRC", self.features, self.targets)
        )
        tiny = Dataset("TGT", [[1.0, 2.0, 3.0], [2.0, 1.0, 5.0]], [1.0, 2.0])
        with self.assertRaises(MLOpsError):
            self.engine.fine_tune_target_model(
                src_params, tiny, TransferConfig("SRC", "TGT", l2_penalty=0.0)
            )
        # A positive penalty is exactly what makes the same fit well posed.
        tuned = self.engine.fine_tune_target_model(
            src_params, tiny, TransferConfig("SRC", "TGT", l2_penalty=0.5)
        )
        self.assertEqual(len(tuned.weights), self.d)

    def test_fine_tune_rejects_negative_penalty(self):
        src_params = self.engine.fit_source_model(
            Dataset("SRC", self.features, self.targets)
        )
        with self.assertRaises(MLOpsError):
            self.engine.fine_tune_target_model(
                src_params,
                Dataset("TGT", self.features, self.targets),
                TransferConfig("SRC", "TGT", l2_penalty=-0.1),
            )

    def test_fine_tuned_model_inherits_the_source_scaler(self):
        """Re-standardizing the target with its own statistics is what breaks feature
        space alignment; the scaler must travel with the weights untouched."""
        src_params = self.engine.fit_source_model(
            Dataset("SRC", self.features, self.targets)
        )
        tuned = self.engine.fine_tune_target_model(
            src_params,
            Dataset("TGT", self.features[:50], self.targets[:50]),
            TransferConfig("SRC", "TGT"),
        )
        self.assertEqual(tuned.feature_means, src_params.feature_means)
        self.assertEqual(tuned.feature_stds, src_params.feature_stds)

    def test_predict_rejects_empty_and_mismatched_input(self):
        params = self.engine.fit_source_model(
            Dataset("SRC", self.features, self.targets)
        )
        with self.assertRaises(MLOpsError):
            self.engine.predict(params, [])
        with self.assertRaises(MLOpsError):
            self.engine.predict(params, [[1.0]])


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.engine = FinancialTransferLearningEngine()

    def test_oos_r2_matches_hand_computation(self):
        y_true = [1.0, 2.0, 3.0]
        y_pred = [1.5, 2.5, 2.5]
        expected = 1.0 - (0.25 + 0.25 + 0.25) / (1.0 + 4.0 + 9.0)
        self.assertAlmostEqual(
            self.engine.calculate_oos_r2(y_true, y_pred, 0.0), expected, places=12
        )

    def test_oos_r2_is_negative_when_the_model_loses_to_the_benchmark(self):
        """Out of sample this is a real, reachable outcome, not an error state. The
        deployment gate depends on being able to see it."""
        score = self.engine.calculate_oos_r2([1.0, -1.0, 1.0], [9.0, 9.0, 9.0], 0.0)
        self.assertLess(score, 0.0)

    def test_oos_r2_benchmark_differs_from_in_sample_r2(self):
        """calculate_r2 benchmarks against the evaluation window's own mean, which is
        a statistic of the period being scored. calculate_oos_r2 takes the fit
        window's mean instead, and the two disagree whenever the means differ."""
        y_true = [10.0, 12.0, 14.0]
        y_pred = [11.0, 12.0, 13.0]
        self.assertNotAlmostEqual(
            self.engine.calculate_r2(y_true, y_pred),
            self.engine.calculate_oos_r2(y_true, y_pred, 0.0),
        )

    def test_degenerate_evaluation_window_raises_rather_than_scoring_zero(self):
        with self.assertRaises(MLOpsError):
            self.engine.calculate_oos_r2([5.0, 5.0], [4.0, 6.0], 5.0)

    def test_r2_rejects_empty_and_mismatched_series(self):
        with self.assertRaises(MLOpsError):
            self.engine.calculate_r2([], [])
        with self.assertRaises(MLOpsError):
            self.engine.calculate_oos_r2([1.0, 2.0], [1.0], 0.0)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.engine = FinancialTransferLearningEngine()

    def test_non_finite_inputs_are_refused_not_propagated(self):
        """NaN previously flowed through predict into R-squared, where `nan > x` is
        False and a corrupt feed read as a quiet rejection."""
        with self.assertRaises(MLOpsError):
            self.engine.fit_source_model(
                Dataset("S", [[1.0], [float("nan")], [3.0], [4.0]], [1.0, 2.0, 3.0, 4.0])
            )
        with self.assertRaises(MLOpsError):
            self.engine.fit_source_model(
                Dataset("S", [[1.0], [2.0], [3.0], [4.0]], [1.0, float("inf"), 3.0, 4.0])
            )

    def test_ragged_and_mislabelled_matrices_are_refused(self):
        with self.assertRaises(MLOpsError):
            self.engine.fit_source_model(
                Dataset("S", [[1.0, 2.0], [3.0], [4.0, 5.0], [6.0, 7.0]], [1.0] * 4)
            )
        with self.assertRaises(MLOpsError):
            self.engine.fit_source_model(Dataset("S", [[1.0], [2.0], [3.0]], [1.0, 2.0]))
        with self.assertRaises(MLOpsError):
            self.engine.fit_source_model(
                Dataset("S", [[1.0], [2.0], [3.0], [4.0]], [1.0] * 4, ["a", "b"])
            )

    def test_empty_dataset_is_refused(self):
        with self.assertRaises(MLOpsError):
            self.engine.fit_source_model(Dataset("S", [], []))

    def test_overflowing_feature_magnitudes_raise_instead_of_solving_to_nan(self):
        """Every input here is finite, but their squares are not. Left alone this
        surfaces as a raw OverflowError, or as an inf that solves to a NaN model --
        and a NaN model scores NaN, which reads as a quiet rejection downstream."""
        rows = [[1e180], [2e180], [3e180], [4e180], [5e180]]
        with self.assertRaises(MLOpsError):
            self.engine.fit_source_model(Dataset("S", rows, [1.0, 2.0, 3.0, 4.0, 5.0]))
        with self.assertRaises(MLOpsError):
            self.engine.calculate_covariate_shift(rows, [[1e180], [2e180]])


_FIXTURE_BETA = [1.5, -0.8, 0.6, -0.4, 0.9]


def _build_transfer_fixture(
    n_target=45,
    n_source=400,
    n_features=5,
    seed=42,
    target_noise=1.2,
    target_stride=1,
    flip_tail=False,
):
    """
    A genuine cold-start pair: a liquid source with a long, clean history and a sparse,
    noisy target, both loading on the same latent factors.

    Each instrument observes the factors through its own idiosyncratic noise, so their
    feature distributions differ slightly (a non-zero standardized mean difference) and
    their targets co-move without being identical. The target is deliberately short and
    noisy enough that a target-only fit overfits and the source prior is worth having --
    a fixture where the target alone already suffices tests nothing about transfer.

    ``target_stride`` spaces the target's bars out over the source's timestamp grid,
    which is what makes timestamp alignment observable. ``flip_tail`` reverses the sign
    of the relationship over the target's final third, so a model fit on the earlier
    window must fail out of sample.
    """
    rand = _lcg(seed)
    beta = _FIXTURE_BETA[:n_features]
    factors = [_normalish(rand, n_source) for _ in range(n_features)]
    src_idio = [_normalish(rand, n_source) for _ in range(n_features)]
    tgt_idio = [_normalish(rand, n_source) for _ in range(n_features)]
    src_eps = _normalish(rand, n_source)
    tgt_eps = _normalish(rand, n_source)

    src_ts = list(range(n_source))
    src_features = [
        [factors[j][i] + 0.25 * src_idio[j][i] for j in range(n_features)]
        for i in range(n_source)
    ]
    src_targets = [
        sum(beta[j] * src_features[i][j] for j in range(n_features)) + 0.3 * src_eps[i]
        for i in range(n_source)
    ]

    tgt_ts, tgt_features, tgt_targets = [], [], []
    flip_from = int(n_target * 2 / 3)
    for k in range(n_target):
        i = k * target_stride
        row = [factors[j][i] + 0.25 * tgt_idio[j][i] for j in range(n_features)]
        sign = -1.0 if (flip_tail and k >= flip_from) else 1.0
        tgt_ts.append(src_ts[i])
        tgt_features.append(row)
        tgt_targets.append(
            sign * sum(beta[j] * row[j] for j in range(n_features))
            + target_noise * tgt_eps[i]
        )

    names = [f"f{j}" for j in range(n_features)]
    source = Dataset("SPY", src_features, src_targets, names, src_ts)
    target = Dataset("NEW_ETF", tgt_features, tgt_targets, names, tgt_ts)
    return source, target


class TestEvaluationGate(unittest.TestCase):
    def setUp(self):
        self.engine = FinancialTransferLearningEngine()

    def test_approves_a_genuine_transfer(self):
        """End-to-end happy path on a real cold start: the transferred model must beat
        both the fit-window historical mean and the target-only baseline out of
        sample. The margin over the baseline is small, which is what an honest cold
        start looks like."""
        source, target = _build_transfer_fixture()
        config = TransferConfig("SPY", "NEW_ETF", min_correlation_overlap=30)
        result = self.engine.evaluate_transfer_performance(source, target, config)

        self.assertTrue(result.is_transfer_recommended, msg=result.rejection_reasons)
        self.assertEqual(result.rejection_reasons, [])
        self.assertGreater(result.transfer_model_r2, 0.0)
        self.assertIsNotNone(result.direct_target_r2)
        self.assertGreater(result.transfer_model_r2, result.direct_target_r2)
        self.assertGreater(result.transfer_gain_r2, 0.0)
        self.assertGreaterEqual(result.correlation, config.min_correlation)
        self.assertEqual(result.n_target_fit + result.n_target_test, len(target.targets))
        self.assertTrue(any("Chronological split" in line for line in result.audit_trail))
        self.assertTrue(any("APPROVED" in line for line in result.audit_trail))

    def test_evaluation_never_fits_on_the_held_out_window(self):
        """Regression for the in-sample evaluation defect. The target's relationship
        reverses over its final third; a model fitted only on the earlier window must
        lose out of sample and the gate must reject. Scoring on the fitted rows -- what
        the previous implementation did while labelling the result 'OOS R-squared' --
        reports a positive fit here instead."""
        source, target = _build_transfer_fixture(flip_tail=True)
        config = TransferConfig("SPY", "NEW_ETF", min_correlation_overlap=30)
        result = self.engine.evaluate_transfer_performance(source, target, config)

        self.assertLess(result.transfer_model_r2, 0.0)
        self.assertFalse(result.is_transfer_recommended)
        self.assertTrue(
            any("loses to the fit-window historical mean" in r for r in result.rejection_reasons),
            msg=result.rejection_reasons,
        )

    def test_rejects_a_model_that_loses_to_the_historical_mean(self):
        """Beating a catastrophic target-only baseline is not sufficient: out of
        sample both scores can be negative, and deploying the less-bad of two models
        that each lose to the fit-window mean is still deploying a losing model."""
        source, target = _build_transfer_fixture(flip_tail=True)
        result = self.engine.evaluate_transfer_performance(
            source, target, TransferConfig("SPY", "NEW_ETF", min_correlation_overlap=30)
        )
        if result.direct_target_r2 is not None and result.transfer_gain_r2 is not None:
            # The gain may well be positive here; the absolute-performance gate is what
            # must still reject.
            self.assertLess(result.transfer_model_r2, 0.0)
        self.assertFalse(result.is_transfer_recommended)

    def test_correlation_is_computed_on_timestamp_aligned_bars(self):
        """Regression for the prefix-slice defect. With the target on every third bar
        of the source's grid, correlating the source's first N rows against the target
        compares unrelated periods. The reported overlap must be the true timestamp
        intersection and the correlation must match it."""
        source, target = _build_transfer_fixture(n_target=40, n_source=120, target_stride=3)
        config = TransferConfig("SPY", "NEW_ETF", min_correlation_overlap=5)
        result = self.engine.evaluate_transfer_performance(source, target, config)

        n_fit = result.n_target_fit
        fit_ts = set(target.timestamps[:n_fit])
        src_index = {ts: i for i, ts in enumerate(source.timestamps[: result.n_source_fit])}
        shared = sorted(fit_ts & set(src_index))

        self.assertEqual(result.correlation_overlap, len(shared))
        expected = self.engine.calculate_correlation(
            [source.targets[src_index[ts]] for ts in shared],
            [target.targets[target.timestamps.index(ts)] for ts in shared],
        )
        self.assertAlmostEqual(result.correlation, expected, places=12)

    def test_source_is_truncated_to_bars_before_the_held_out_window(self):
        """Source and target co-move by premise, so source rows drawn from the target's
        evaluation period leak that period into the pre-trained weights."""
        source, target = _build_transfer_fixture()
        result = self.engine.evaluate_transfer_performance(
            source, target, TransferConfig("SPY", "NEW_ETF", min_correlation_overlap=30)
        )
        test_start = target.timestamps[result.n_target_fit]
        expected = sum(1 for ts in source.timestamps if ts < test_start)

        self.assertEqual(result.n_source_fit, expected)
        self.assertLess(result.n_source_fit, len(source.timestamps))
        self.assertTrue(any("Dropped" in line for line in result.audit_trail))

    def test_rejects_when_overlap_is_too_short_to_establish_correlation(self):
        source, target = _build_transfer_fixture(n_target=40, n_source=120, target_stride=3)
        result = self.engine.evaluate_transfer_performance(
            source, target, TransferConfig("SPY", "NEW_ETF", min_correlation_overlap=1000)
        )
        self.assertFalse(result.is_transfer_recommended)
        self.assertTrue(
            any("min_correlation_overlap" in r for r in result.rejection_reasons)
        )

    def test_rejects_on_mean_domain_shift(self):
        source, target = _build_transfer_fixture()
        measured = self.engine.mean_covariate_shift(
            source.features[: len(target.features)], target.features
        )
        self.assertGreater(measured, 0.0)

        result = self.engine.evaluate_transfer_performance(
            source,
            target,
            TransferConfig(
                "SPY",
                "NEW_ETF",
                min_correlation_overlap=30,
                max_domain_shift=measured / 10.0,
            ),
        )
        self.assertFalse(result.is_transfer_recommended)
        self.assertTrue(any("max_domain_shift" in r for r in result.rejection_reasons))

    def test_worst_feature_shift_gate_catches_what_the_mean_hides(self):
        """One badly shifted feature among many is averaged away by the mean SMD. The
        optional per-feature ceiling is what catches it."""
        source, target = _build_transfer_fixture()
        # Displace a single column far enough that it dominates the worst-feature
        # statistic while the mean across all columns stays modest.
        shifted = Dataset(
            target.symbol,
            [[row[0] + 40.0] + row[1:] for row in target.features],
            target.targets,
            target.feature_names,
            target.timestamps,
        )
        lenient = self.engine.evaluate_transfer_performance(
            source,
            shifted,
            TransferConfig(
                "SPY", "NEW_ETF", min_correlation_overlap=30, max_domain_shift=1e9
            ),
        )
        self.assertEqual(lenient.worst_shift_feature, "f0")
        self.assertGreater(lenient.max_feature_shift, lenient.domain_shift_score)
        self.assertFalse(
            any("max_feature_domain_shift" in r for r in lenient.rejection_reasons)
        )

        strict = self.engine.evaluate_transfer_performance(
            source,
            shifted,
            TransferConfig(
                "SPY",
                "NEW_ETF",
                min_correlation_overlap=30,
                max_domain_shift=1e9,
                max_feature_domain_shift=1.0,
            ),
        )
        self.assertFalse(strict.is_transfer_recommended)
        self.assertTrue(
            any("max_feature_domain_shift" in r for r in strict.rejection_reasons)
        )

    def test_evaluation_requires_timestamps(self):
        source, target = _build_transfer_fixture()
        undated = Dataset(target.symbol, target.features, target.targets, target.feature_names)
        with self.assertRaises(MLOpsError) as ctx:
            self.engine.evaluate_transfer_performance(
                source, undated, TransferConfig("SPY", "NEW_ETF")
            )
        self.assertIn("timestamps", str(ctx.exception))

    def test_evaluation_rejects_duplicate_or_out_of_order_timestamps(self):
        source, target = _build_transfer_fixture()
        duplicated = list(target.timestamps)
        duplicated[5] = duplicated[4]
        with self.assertRaises(MLOpsError):
            self.engine.evaluate_transfer_performance(
                Dataset(target.symbol, target.features, target.targets, [], duplicated),
                target,
                TransferConfig("SPY", "NEW_ETF"),
            )

        reversed_ts = list(target.timestamps)
        reversed_ts[7], reversed_ts[8] = reversed_ts[8], reversed_ts[7]
        with self.assertRaises(MLOpsError):
            self.engine.evaluate_transfer_performance(
                source,
                Dataset(target.symbol, target.features, target.targets, [], reversed_ts),
                TransferConfig("SPY", "NEW_ETF"),
            )

    def test_evaluation_refuses_a_too_small_held_out_window(self):
        source, target = _build_transfer_fixture(n_target=12)
        with self.assertRaises(MLOpsError) as ctx:
            self.engine.evaluate_transfer_performance(
                source,
                target,
                TransferConfig(
                    "SPY", "NEW_ETF", test_fraction=0.1, min_test_samples=5
                ),
            )
        self.assertIn("min_test_samples", str(ctx.exception))

    def test_evaluation_refuses_mismatched_feature_spaces(self):
        source, target = _build_transfer_fixture()
        narrow = Dataset(
            target.symbol,
            [[row[0]] for row in target.features],
            target.targets,
            ["f0"],
            target.timestamps,
        )
        with self.assertRaises(MLOpsError):
            self.engine.evaluate_transfer_performance(
                source, narrow, TransferConfig("SPY", "NEW_ETF")
            )

    def test_evaluation_refuses_invalid_test_fraction(self):
        source, target = _build_transfer_fixture()
        for bad in (0.0, 1.0, -0.2, 1.5):
            with self.assertRaises(MLOpsError):
                self.engine.evaluate_transfer_performance(
                    source, target, TransferConfig("SPY", "NEW_ETF", test_fraction=bad)
                )

    def test_evaluation_is_deterministic(self):
        source, target = _build_transfer_fixture()
        config = TransferConfig("SPY", "NEW_ETF", min_correlation_overlap=30)
        first = self.engine.evaluate_transfer_performance(source, target, config)
        second = self.engine.evaluate_transfer_performance(source, target, config)
        self.assertEqual(first.transfer_model_r2, second.transfer_model_r2)
        self.assertEqual(first.correlation, second.correlation)
        self.assertEqual(first.audit_trail, second.audit_trail)


if __name__ == "__main__":
    unittest.main()
