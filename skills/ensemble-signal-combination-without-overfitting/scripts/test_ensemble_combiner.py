"""
Unit tests for ensemble-signal-combination-without-overfitting skill.

Tests:
1. Causal (look-ahead-free) Z-score normalization, clipping, and warm-up.
2. Non-negative least squares solver against independently derived solutions.
3. Method differentiation (EQUAL_WEIGHT / INVERSE_VARIANCE / SHRUNK_NNLS).
4. 1/N shrinkage, non-negativity, unit sum, and max-weight-cap enforcement.
5. Input validation: non-finite values, empty/mismatched series, missing target,
   insufficient observations, duplicate model names, invalid configuration.
"""
import math
import unittest

from ensemble_combiner import (
    EnsembleError,
    EnsembleMethod,
    EnsembleSignalCombiner,
    SignalStream,
)


def _build_dataset(n: int = 40):
    """
    Deterministic fixture: `strong` tracks the target, `weak` is a noisy version
    of it, and `useless` is an alternating series uncorrelated with the target.
    """
    target = [math.sin(i / 3.0) for i in range(n)]
    strong = [t * 10.0 for t in target]
    weak = [t * 10.0 + (1.5 if i % 2 == 0 else -1.5) for i, t in enumerate(target)]
    useless = [(1.0 if i % 4 in (0, 1) else -1.0) * 0.05 for i in range(n)]
    streams = [
        SignalStream("StrongModel", strong),
        SignalStream("WeakModel", weak),
        SignalStream("UselessModel", useless),
    ]
    return streams, target


class TestZScoreNormalization(unittest.TestCase):

    def test_causal_expanding_zscore_matches_hand_computed_values(self):
        # Expanding window. t=0: warm-up -> 0.0.
        # t=1: window [10, 20], mean 15, sample stdev sqrt(50) -> z = 5/7.0711.
        # t=2: window [10, 20, 30], mean 20, sample stdev 10 -> z = 10/10 = 1.0.
        norm = EnsembleSignalCombiner.normalize_zscore([10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertEqual(len(norm), 5)
        self.assertEqual(norm[0], 0.0)
        self.assertAlmostEqual(norm[1], 5.0 / math.sqrt(50.0), places=9)
        self.assertAlmostEqual(norm[2], 1.0, places=9)

    def test_zscore_is_look_ahead_free(self):
        # Regression guard: the previous implementation used full-sample mean and
        # stdev, so appending a future observation changed every historical value.
        history = [1.0, 3.0, 2.0, 8.0, 5.0]
        future = history + [900.0, -400.0]
        prefix = EnsembleSignalCombiner.normalize_zscore(history)
        extended = EnsembleSignalCombiner.normalize_zscore(future)
        self.assertEqual(prefix, extended[: len(history)])

    def test_rolling_window_only_uses_last_lookback_observations(self):
        # With lookback=2, t=3 sees [30, 40]: mean 35, sample stdev sqrt(50).
        norm = EnsembleSignalCombiner.normalize_zscore(
            [10.0, 20.0, 30.0, 40.0], lookback=2
        )
        self.assertAlmostEqual(norm[3], 5.0 / math.sqrt(50.0), places=9)

    def test_outliers_are_clipped_to_bound(self):
        series = [1.0] * 20 + [1.0e6]
        norm = EnsembleSignalCombiner.normalize_zscore(series, clip=3.0)
        self.assertAlmostEqual(norm[-1], 3.0, places=9)
        self.assertTrue(all(-3.0 <= z <= 3.0 for z in norm))

    def test_constant_window_emits_zero_not_division_blowup(self):
        norm = EnsembleSignalCombiner.normalize_zscore([5.0, 5.0, 5.0, 5.0])
        self.assertEqual(norm, [0.0, 0.0, 0.0, 0.0])

    def test_non_finite_signal_raises_ensemble_error(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(EnsembleError):
                EnsembleSignalCombiner.normalize_zscore([1.0, bad, 3.0])

    def test_empty_series_returns_empty(self):
        self.assertEqual(EnsembleSignalCombiner.normalize_zscore([]), [])


class TestNNLSSolver(unittest.TestCase):

    def test_recovers_known_solution_for_orthogonal_design(self):
        # Z1 and Z2 are orthogonal with Z_i . Z_i = 4, so the least squares
        # solution is w_i = (Z_i . y) / 4, computed by hand below.
        z1 = [1.0, -1.0, 1.0, -1.0]
        z2 = [1.0, 1.0, -1.0, -1.0]
        y = [3.0 * a + 1.0 * b for a, b in zip(z1, z2)]  # -> [4, -2, 2, -4]
        w = EnsembleSignalCombiner._solve_nnls([z1, z2], y, ridge=0.0)
        self.assertAlmostEqual(w[0], 3.0, places=8)
        self.assertAlmostEqual(w[1], 1.0, places=8)

    def test_clips_negative_coefficient_to_zero(self):
        # Unconstrained OLS would return (-3, 1); the non-negativity constraint
        # must floor the first coefficient at exactly zero (orthogonal design
        # means the second coefficient is unaffected).
        z1 = [1.0, -1.0, 1.0, -1.0]
        z2 = [1.0, 1.0, -1.0, -1.0]
        y = [-3.0 * a + 1.0 * b for a, b in zip(z1, z2)]
        w = EnsembleSignalCombiner._solve_nnls([z1, z2], y, ridge=0.0)
        self.assertEqual(w[0], 0.0)
        self.assertAlmostEqual(w[1], 1.0, places=8)

    def test_degenerate_all_zero_regressor_gets_zero_weight(self):
        z1 = [0.0, 0.0, 0.0, 0.0]
        z2 = [1.0, 1.0, -1.0, -1.0]
        y = [2.0 * b for b in z2]
        w = EnsembleSignalCombiner._solve_nnls([z1, z2], y, ridge=0.0)
        self.assertEqual(w[0], 0.0)
        self.assertAlmostEqual(w[1], 2.0, places=8)

    def test_ridge_default_barely_perturbs_a_well_conditioned_fit(self):
        z1 = [1.0, -1.0, 1.0, -1.0]
        z2 = [1.0, 1.0, -1.0, -1.0]
        y = [3.0 * a + 1.0 * b for a, b in zip(z1, z2)]
        w = EnsembleSignalCombiner._solve_nnls([z1, z2], y)
        self.assertAlmostEqual(w[0], 3.0, places=4)
        self.assertAlmostEqual(w[1], 1.0, places=4)

    def test_perfectly_collinear_models_still_produce_finite_weights(self):
        # Duplicated sub-models make the Gram matrix singular. Without the ridge
        # term, coordinate descent creeps and the split between the duplicates
        # is arbitrary; the damped fit must still terminate with finite,
        # non-negative weights that reconstruct the target.
        base = [1.0, -1.0, 2.0, -2.0, 0.5, -0.5, 1.5, -1.5]
        y = [2.0 * v for v in base]
        w = EnsembleSignalCombiner._solve_nnls([list(base), list(base)], y)
        self.assertTrue(all(math.isfinite(v) and v >= 0.0 for v in w))
        self.assertAlmostEqual(w[0] + w[1], 2.0, places=4)


class TestWeighting(unittest.TestCase):

    def setUp(self):
        self.streams, self.target = _build_dataset()

    def test_methods_produce_materially_different_weights(self):
        # Regression guard: weights used to be derived from the variance of the
        # standardized signals, which is 1.0 by construction, so every method
        # collapsed to 1/N and the `method` argument was a no-op.
        weights = {}
        for method in EnsembleMethod:
            combiner = EnsembleSignalCombiner(
                method=method, shrinkage_lambda=0.50, max_weight_cap=1.0
            )
            weights[method] = combiner.combine_signals(
                self.streams, target_returns=self.target
            ).weights

        self.assertAlmostEqual(weights[EnsembleMethod.EQUAL_WEIGHT]["StrongModel"], 1 / 3, places=9)
        for method in (EnsembleMethod.INVERSE_VARIANCE, EnsembleMethod.SHRUNK_NNLS):
            with self.subTest(method=method):
                w = weights[method]
                self.assertGreater(w["StrongModel"], 1 / 3)
                self.assertLess(w["UselessModel"], 1 / 3)
                self.assertGreater(w["StrongModel"], w["UselessModel"] + 0.05)

    def test_weights_non_negative_and_sum_to_one(self):
        for method in EnsembleMethod:
            with self.subTest(method=method):
                combiner = EnsembleSignalCombiner(method=method)
                res = combiner.combine_signals(self.streams, target_returns=self.target)
                self.assertTrue(all(w >= 0.0 for w in res.weights.values()))
                self.assertAlmostEqual(sum(res.weights.values()), 1.0, places=9)

    def test_shrinkage_pulls_weights_toward_equal_allocation(self):
        unshrunk = EnsembleSignalCombiner(
            method=EnsembleMethod.SHRUNK_NNLS, shrinkage_lambda=0.0, max_weight_cap=1.0
        ).combine_signals(self.streams, target_returns=self.target).weights
        shrunk = EnsembleSignalCombiner(
            method=EnsembleMethod.SHRUNK_NNLS, shrinkage_lambda=0.50, max_weight_cap=1.0
        ).combine_signals(self.streams, target_returns=self.target).weights
        full = EnsembleSignalCombiner(
            method=EnsembleMethod.SHRUNK_NNLS, shrinkage_lambda=1.0, max_weight_cap=1.0
        ).combine_signals(self.streams, target_returns=self.target).weights

        equal = 1.0 / 3.0
        for name in unshrunk:
            with self.subTest(model=name):
                self.assertLess(
                    abs(shrunk[name] - equal), abs(unshrunk[name] - equal) + 1e-12
                )
                self.assertAlmostEqual(full[name], equal, places=9)

    def test_max_weight_cap_is_enforced(self):
        # Regression guard: max_weight_cap was accepted but never applied.
        streams = list(self.streams) + [SignalStream("FourthModel", [0.0] * 20 + [1.0] * 20)]
        combiner = EnsembleSignalCombiner(
            method=EnsembleMethod.SHRUNK_NNLS, shrinkage_lambda=0.0, max_weight_cap=0.40
        )
        res = combiner.combine_signals(streams, target_returns=self.target)
        self.assertEqual(len(res.weights), 4)
        self.assertLessEqual(max(res.weights.values()), 0.40 + 1e-9)
        self.assertAlmostEqual(sum(res.weights.values()), 1.0, places=9)

    def test_cap_below_one_over_n_is_raised_to_equal_weight(self):
        # A 0.40 cap is infeasible for two models; it must be relaxed to 1/N
        # rather than producing a vector that cannot sum to 1.
        two = self.streams[:2]
        combiner = EnsembleSignalCombiner(
            method=EnsembleMethod.SHRUNK_NNLS, shrinkage_lambda=0.0, max_weight_cap=0.40
        )
        res = combiner.combine_signals(two, target_returns=self.target)
        for w in res.weights.values():
            self.assertAlmostEqual(w, 0.50, places=9)

    def test_equal_weight_requires_no_target(self):
        res = EnsembleSignalCombiner(method=EnsembleMethod.EQUAL_WEIGHT).combine_signals(
            self.streams
        )
        self.assertEqual(set(res.weights.values()), {1 / 3})

    def test_ensemble_signal_is_bounded_by_clip(self):
        res = EnsembleSignalCombiner(method=EnsembleMethod.SHRUNK_NNLS).combine_signals(
            self.streams, target_returns=self.target
        )
        self.assertEqual(len(res.ensemble_signals), len(self.target))
        self.assertTrue(all(-3.0 <= s <= 3.0 for s in res.ensemble_signals))


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.streams, self.target = _build_dataset()
        self.combiner = EnsembleSignalCombiner(
            method=EnsembleMethod.SHRUNK_NNLS, shrinkage_lambda=0.50
        )

    def test_mismatched_length_raises_error(self):
        s1 = SignalStream("M1", [1.0, 2.0, 3.0])
        s2 = SignalStream("M2", [1.0, 2.0])
        with self.assertRaises(EnsembleError):
            self.combiner.combine_signals([s1, s2], target_returns=[0.1, 0.2, 0.3])

    def test_missing_target_raises_for_fitted_methods(self):
        for method in (EnsembleMethod.INVERSE_VARIANCE, EnsembleMethod.SHRUNK_NNLS):
            with self.subTest(method=method):
                combiner = EnsembleSignalCombiner(method=method)
                with self.assertRaises(EnsembleError):
                    combiner.combine_signals(self.streams)

    def test_target_length_mismatch_raises(self):
        with self.assertRaises(EnsembleError):
            self.combiner.combine_signals(self.streams, target_returns=self.target[:-1])

    def test_insufficient_observations_raise(self):
        short = [SignalStream(f"M{i}", [1.0, 2.0, 3.0]) for i in range(3)]
        with self.assertRaises(EnsembleError):
            self.combiner.combine_signals(short, target_returns=[0.1, 0.2, 0.3])

    def test_empty_streams_raise(self):
        with self.assertRaises(EnsembleError):
            self.combiner.combine_signals([])

    def test_zero_length_signals_raise(self):
        with self.assertRaises(EnsembleError):
            self.combiner.combine_signals(
                [SignalStream("A", []), SignalStream("B", [])], target_returns=[]
            )

    def test_non_finite_signal_raises_ensemble_error(self):
        streams = [
            SignalStream("A", [1.0, float("nan"), 3.0, 4.0]),
            SignalStream("B", [1.0, 2.0, 3.0, 4.0]),
        ]
        with self.assertRaises(EnsembleError):
            self.combiner.combine_signals(streams, target_returns=[0.1, 0.2, 0.3, 0.4])

    def test_non_finite_target_raises_ensemble_error(self):
        with self.assertRaises(EnsembleError):
            self.combiner.combine_signals(
                self.streams,
                target_returns=[float("inf")] + self.target[1:],
            )

    def test_duplicate_model_names_raise(self):
        dupes = [
            SignalStream("Same", self.streams[0].signals),
            SignalStream("Same", self.streams[1].signals),
            SignalStream("Other", self.streams[2].signals),
        ]
        with self.assertRaises(EnsembleError):
            self.combiner.combine_signals(dupes, target_returns=self.target)

    def test_out_of_range_shrinkage_lambda_rejected(self):
        # A negative lambda would extrapolate away from 1/N and can produce
        # negative weights, silently breaking the core non-negativity guarantee.
        for bad in (-0.1, 1.1):
            with self.subTest(shrinkage_lambda=bad):
                with self.assertRaises(EnsembleError):
                    EnsembleSignalCombiner(shrinkage_lambda=bad)

    def test_out_of_range_max_weight_cap_rejected(self):
        for bad in (0.0, -0.2, 1.5):
            with self.subTest(max_weight_cap=bad):
                with self.assertRaises(EnsembleError):
                    EnsembleSignalCombiner(max_weight_cap=bad)

    def test_invalid_lookback_and_min_periods_rejected(self):
        with self.assertRaises(EnsembleError):
            EnsembleSignalCombiner(lookback=1)
        with self.assertRaises(EnsembleError):
            EnsembleSignalCombiner(min_periods=1)
        with self.assertRaises(EnsembleError):
            EnsembleSignalCombiner(clip=0.0)


if __name__ == "__main__":
    unittest.main()
