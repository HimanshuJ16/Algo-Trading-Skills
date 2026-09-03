"""
Unit tests for walk-forward-validation-setup skill.

Expected Sharpe / drawdown / win-rate values are derived by hand from the definitions in
the module docstring, not by re-running the implementation's own arithmetic.

Tests:
1. Split geometry: hand-computed fold bounds, embargo contiguity, non-overlapping test windows.
2. Expanding vs rolling training-window behaviour.
3. Input validation, including the test_size=0 non-termination regression.
4. Embargo sized against feature lookback and label horizon.
5. Out-of-sample metrics computed from realised returns, and None when unavailable.
6. Test-label hiding, prediction-length and chronological-ordering guards.
7. Cross-fold aggregation.
8. Backward compatibility of walk_forward_splits, including mode-typo rejection.
"""
import logging
import unittest

import numpy as np
import pandas as pd

from walk_forward import (
    FoldMetrics,
    SplitMode,
    WalkForwardError,
    WalkForwardSplitter,
    walk_forward_splits,
)

# Silences the deliberate "embargo not verified" / "too few folds" warnings the helper emits.
logging.getLogger("walk_forward").setLevel(logging.CRITICAL)


def _frame(n_rows: int) -> pd.DataFrame:
    """Deterministic frame: target alternates 0,1 so accuracy is exactly computable."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2020-01-01", periods=n_rows, freq="D"),
            "feature": np.arange(n_rows, dtype=float),
            "target": np.arange(n_rows) % 2,
        }
    )


class TestSplitGeometry(unittest.TestCase):
    def setUp(self):
        self.n_rows = 1000
        self.train_size = 300
        self.test_size = 100
        self.embargo = 20

    def test_hand_computed_expanding_bounds(self):
        # train 10, embargo 2, test 4 over 24 rows yields exactly three folds:
        #   fold 0: train [0,10) embargo [10,12) test [12,16)
        #   fold 1: train [0,14) embargo [14,16) test [16,20)
        #   fold 2: train [0,18) embargo [18,20) test [20,24)
        splits = WalkForwardSplitter.generate_splits(
            n_rows=24, train_size=10, test_size=4, embargo_size=2, mode=SplitMode.EXPANDING
        )
        self.assertEqual(len(splits), 3)
        self.assertEqual(
            [(s.train_indices, s.embargo_indices, s.test_indices) for s in splits],
            [
                ((0, 10), (10, 12), (12, 16)),
                ((0, 14), (14, 16), (16, 20)),
                ((0, 18), (18, 20), (20, 24)),
            ],
        )

    def test_hand_computed_rolling_bounds(self):
        # Same geometry, fixed 10-row training window sliding forward by test_size.
        splits = WalkForwardSplitter.generate_splits(
            n_rows=24, train_size=10, test_size=4, embargo_size=2, mode=SplitMode.ROLLING
        )
        self.assertEqual(
            [(s.train_indices, s.embargo_indices, s.test_indices) for s in splits],
            [
                ((0, 10), (10, 12), (12, 16)),
                ((4, 14), (14, 16), (16, 20)),
                ((8, 18), (18, 20), (20, 24)),
            ],
        )

    def test_purged_embargo_gap(self):
        splits = WalkForwardSplitter.generate_splits(
            n_rows=self.n_rows,
            train_size=self.train_size,
            test_size=self.test_size,
            embargo_size=self.embargo,
            mode=SplitMode.EXPANDING,
        )

        self.assertGreater(len(splits), 0)

        for split in splits:
            _, tr_end = split.train_indices
            emb_start, emb_end = split.embargo_indices
            te_start, te_end = split.test_indices

            self.assertEqual(emb_start, tr_end)
            self.assertEqual(emb_end - emb_start, self.embargo)
            self.assertEqual(te_start, emb_end)
            self.assertEqual(te_end - te_start, self.test_size)

    def test_training_window_never_reaches_its_own_test_window(self):
        """The leakage invariant: train_end + embargo == test_start, in both modes."""
        for mode in (SplitMode.EXPANDING, SplitMode.ROLLING):
            for split in WalkForwardSplitter.generate_splits(
                self.n_rows, self.train_size, self.test_size, self.embargo, mode=mode
            ):
                self.assertLessEqual(
                    split.train_indices[1] + self.embargo, split.test_indices[0], msg=str(mode)
                )

    def test_test_windows_are_contiguous_and_non_overlapping(self):
        for mode in (SplitMode.EXPANDING, SplitMode.ROLLING):
            splits = WalkForwardSplitter.generate_splits(
                self.n_rows, self.train_size, self.test_size, self.embargo, mode=mode
            )
            for prev, nxt in zip(splits, splits[1:]):
                self.assertEqual(prev.test_indices[1], nxt.test_indices[0], msg=str(mode))

    def test_rolling_vs_expanding_mode(self):
        exp_splits = WalkForwardSplitter.generate_splits(
            self.n_rows, self.train_size, self.test_size, self.embargo, mode=SplitMode.EXPANDING
        )
        roll_splits = WalkForwardSplitter.generate_splits(
            self.n_rows, self.train_size, self.test_size, self.embargo, mode=SplitMode.ROLLING
        )

        # Expanding: anchored at row 0, training window grows by test_size each fold.
        self.assertTrue(all(s.train_indices[0] == 0 for s in exp_splits))
        self.assertEqual(
            [s.train_indices[1] - s.train_indices[0] for s in exp_splits[:3]],
            [self.train_size, self.train_size + self.test_size, self.train_size + 2 * self.test_size],
        )

        # Rolling: fixed length, start advances by test_size each fold.
        self.assertTrue(
            all(s.train_indices[1] - s.train_indices[0] == self.train_size for s in roll_splits)
        )
        self.assertEqual(
            [s.train_indices[0] for s in roll_splits[:3]], [0, self.test_size, 2 * self.test_size]
        )

    def test_dataset_too_short_yields_no_folds(self):
        self.assertEqual(WalkForwardSplitter.min_required_rows(10, 4, 2), 16)
        self.assertEqual(
            WalkForwardSplitter.generate_splits(
                n_rows=15, train_size=10, test_size=4, embargo_size=2
            ),
            [],
        )
        # Exactly the minimum produces exactly one fold.
        self.assertEqual(
            len(
                WalkForwardSplitter.generate_splits(
                    n_rows=16, train_size=10, test_size=4, embargo_size=2
                )
            ),
            1,
        )


class TestSplitValidation(unittest.TestCase):
    def test_zero_test_size_is_rejected_rather_than_looping_forever(self):
        """Regression: the generator advances by test_size, so 0 never terminated."""
        for mode in (SplitMode.EXPANDING, SplitMode.ROLLING):
            with self.assertRaises(WalkForwardError):
                WalkForwardSplitter.generate_splits(1000, 300, 0, 20, mode=mode)

    def test_negative_and_non_integer_bounds_are_rejected(self):
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(1000, 0, 100, 20)
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(1000, 300, -100, 20)
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(1000, 300, 100, -1)
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(1000, 300.5, 100, 20)
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(1000, True, 100, 20)
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(-1, 300, 100, 20)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(1000, 300, 100, 20, mode="EXPANDING")

    def test_embargo_must_cover_feature_lookback_and_label_horizon(self):
        # Gap must be at least max(L, H); each bound is checked independently.
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(
                1000, 300, 100, embargo_size=10, max_feature_lookback=20
            )
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(1000, 300, 100, embargo_size=10, label_horizon=20)
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.generate_splits(
                1000, 300, 100, embargo_size=15, max_feature_lookback=5, label_horizon=20
            )
        # Exactly max(L, H) is sufficient.
        self.assertGreater(
            len(
                WalkForwardSplitter.generate_splits(
                    1000, 300, 100, embargo_size=20, max_feature_lookback=20, label_horizon=5
                )
            ),
            0,
        )

    def test_unverified_embargo_warns_but_proceeds(self):
        with self.assertLogs("walk_forward", level="WARNING") as captured:
            splits = WalkForwardSplitter.generate_splits(1000, 300, 100, embargo_size=20)
        self.assertGreater(len(splits), 0)
        self.assertTrue(any("has not been checked" in line for line in captured.output))

    def test_constructor_rejects_invalid_annualization(self):
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter(periods_per_year=0)
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter(periods_per_year=252.5)
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter(risk_free_rate=float("nan"))
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter(mode="EXPANDING")


class TestFoldMetrics(unittest.TestCase):
    """Three folds over 24 rows: test windows [12,16), [16,20), [20,24)."""

    def setUp(self):
        self.df = _frame(24)
        self.splitter = WalkForwardSplitter(
            mode=SplitMode.EXPANDING, periods_per_year=4, risk_free_rate=0.0
        )
        self.kwargs = dict(train_size=10, test_size=4, embargo_size=2, target_col="target")

    @staticmethod
    def _predict_all_ones(train_df, test_df):
        return np.ones(len(test_df), dtype=int)

    def test_accuracy_is_hand_computable(self):
        # Every 4-row test window holds two 1s under an alternating target, so predicting
        # all ones scores exactly 0.5 on each fold.
        metrics = self.splitter.evaluate_walk_forward(
            self.df, fit_predict_fn=self._predict_all_ones, **self.kwargs
        )
        self.assertEqual(len(metrics), 3)
        self.assertEqual([m.accuracy for m in metrics], [0.5, 0.5, 0.5])
        self.assertEqual([m.sample_count for m in metrics], [4, 4, 4])
        self.assertEqual([m.fold_index for m in metrics], [0, 1, 2])

    def test_risk_metrics_are_none_without_returns(self):
        """Regression: these were previously hard-coded to 1.5 / 0.05 / accuracy."""
        metrics = self.splitter.evaluate_walk_forward(
            self.df, fit_predict_fn=self._predict_all_ones, **self.kwargs
        )
        for m in metrics:
            self.assertIsNone(m.sharpe_ratio)
            self.assertIsNone(m.max_drawdown_pct)
            self.assertIsNone(m.win_rate)

    def test_sharpe_drawdown_and_win_rate_from_returns(self):
        # Fold returns, periods_per_year = 4, rf = 0. Worked by hand:
        #  fold 0 [.02,.04,.06,.08]: mean .05 -> ann .20; sd(ddof=1) .0258199 -> vol .0516398;
        #         Sharpe .20/.0516398 = sqrt(15) = 3.8730; equity monotonically up -> dd 0.0;
        #         4 of 4 periods positive -> win rate 1.0.
        #  fold 1 [-.10,.05,0,0]:    mean -.0125 -> ann -.05; sd .0629153 -> vol .1258306;
        #         Sharpe -.05/.1258306 = -0.3974; equity 1,.9,.945,.945,.945 -> dd -0.10;
        #         1 of 4 periods positive -> win rate 0.25.
        #  fold 2 [.01,.01,.01,.01]: constant, so a Sharpe does not exist -> None;
        #         equity monotonically up -> dd 0.0; win rate 1.0.
        fold_returns = [
            [0.02, 0.04, 0.06, 0.08],
            [-0.10, 0.05, 0.0, 0.0],
            [0.01, 0.01, 0.01, 0.01],
        ]
        calls = []

        def returns_fn(test_df, predictions):
            calls.append(len(test_df))
            return fold_returns[len(calls) - 1]

        metrics = self.splitter.evaluate_walk_forward(
            self.df,
            fit_predict_fn=self._predict_all_ones,
            returns_fn=returns_fn,
            **self.kwargs,
        )

        self.assertEqual([m.sharpe_ratio for m in metrics], [3.873, -0.3974, None])
        self.assertEqual([m.max_drawdown_pct for m in metrics], [0.0, -0.1, 0.0])
        self.assertEqual([m.win_rate for m in metrics], [1.0, 0.25, 1.0])

    def test_win_rate_is_not_classification_accuracy(self):
        """Regression: win_rate used to be a copy of accuracy."""
        metrics = self.splitter.evaluate_walk_forward(
            self.df,
            fit_predict_fn=self._predict_all_ones,
            returns_fn=lambda test_df, preds: [0.01] * len(test_df),
            **self.kwargs,
        )
        self.assertEqual(metrics[0].accuracy, 0.5)
        self.assertEqual(metrics[0].win_rate, 1.0)

    def test_risk_free_rate_lowers_sharpe_by_the_expected_amount(self):
        # Same fold 0 returns, rf = 0.10: (0.20 - 0.10) / 0.0516398 = 1.9365.
        splitter = WalkForwardSplitter(
            mode=SplitMode.EXPANDING, periods_per_year=4, risk_free_rate=0.10
        )
        metrics = splitter.evaluate_walk_forward(
            self.df,
            fit_predict_fn=self._predict_all_ones,
            returns_fn=lambda test_df, preds: [0.02, 0.04, 0.06, 0.08],
            **self.kwargs,
        )
        self.assertEqual(metrics[0].sharpe_ratio, 1.9365)

    def test_drawdown_from_the_first_test_period_is_captured(self):
        # A decline on the very first period is measured from starting capital, not from
        # that period's own close, so a single -20% period is a -20% drawdown.
        metrics = self.splitter.evaluate_walk_forward(
            self.df,
            fit_predict_fn=self._predict_all_ones,
            returns_fn=lambda test_df, preds: [-0.20, 0.0, 0.0, 0.0],
            **self.kwargs,
        )
        self.assertEqual(metrics[0].max_drawdown_pct, -0.2)

    def test_period_labels_use_timestamps_when_supplied(self):
        metrics = self.splitter.evaluate_walk_forward(
            self.df,
            fit_predict_fn=self._predict_all_ones,
            timestamp_col="timestamp",
            **self.kwargs,
        )
        # Fold 0 tests rows [12,16) -> 2020-01-13 .. 2020-01-16.
        self.assertIn("2020-01-13", metrics[0].test_period)
        self.assertIn("2020-01-16", metrics[0].test_period)


class TestEvaluationGuards(unittest.TestCase):
    def setUp(self):
        self.df = _frame(24)
        self.splitter = WalkForwardSplitter(mode=SplitMode.EXPANDING, periods_per_year=4)
        self.kwargs = dict(train_size=10, test_size=4, embargo_size=2, target_col="target")

    def test_test_labels_are_hidden_from_the_model(self):
        """Regression: the test frame used to carry the labels it was being scored on."""
        seen = {}

        def fit_predict(train_df, test_df):
            seen["train_has_target"] = "target" in train_df.columns
            seen["test_has_target"] = "target" in test_df.columns
            return np.ones(len(test_df), dtype=int)

        self.splitter.evaluate_walk_forward(self.df, fit_predict_fn=fit_predict, **self.kwargs)
        self.assertTrue(seen["train_has_target"])
        self.assertFalse(seen["test_has_target"])

    def test_hide_test_labels_can_be_disabled_explicitly(self):
        seen = {}

        def fit_predict(train_df, test_df):
            seen["test_has_target"] = "target" in test_df.columns
            return np.ones(len(test_df), dtype=int)

        self.splitter.evaluate_walk_forward(
            self.df, fit_predict_fn=fit_predict, hide_test_labels=False, **self.kwargs
        )
        self.assertTrue(seen["test_has_target"])

    def test_prediction_length_mismatch_is_rejected(self):
        """A short array would broadcast and produce a plausible-looking accuracy."""
        with self.assertRaises(WalkForwardError):
            self.splitter.evaluate_walk_forward(
                self.df, fit_predict_fn=lambda tr, te: np.ones(1, dtype=int), **self.kwargs
            )
        with self.assertRaises(WalkForwardError):
            self.splitter.evaluate_walk_forward(
                self.df,
                fit_predict_fn=lambda tr, te: np.ones((len(te), 1), dtype=int),
                **self.kwargs,
            )

    def test_unsorted_timestamps_are_rejected(self):
        shuffled = self.df.iloc[::-1].reset_index(drop=True)
        with self.assertRaises(WalkForwardError):
            self.splitter.evaluate_walk_forward(
                shuffled,
                fit_predict_fn=lambda tr, te: np.ones(len(te), dtype=int),
                timestamp_col="timestamp",
                **self.kwargs,
            )

    def test_repeated_timestamps_are_accepted(self):
        ties = self.df.copy()
        ties.loc[5, "timestamp"] = ties.loc[4, "timestamp"]
        metrics = self.splitter.evaluate_walk_forward(
            ties,
            fit_predict_fn=lambda tr, te: np.ones(len(te), dtype=int),
            timestamp_col="timestamp",
            **self.kwargs,
        )
        self.assertEqual(len(metrics), 3)

    def test_missing_columns_are_rejected(self):
        with self.assertRaises(WalkForwardError):
            self.splitter.evaluate_walk_forward(
                self.df,
                fit_predict_fn=lambda tr, te: np.ones(len(te), dtype=int),
                train_size=10,
                test_size=4,
                embargo_size=2,
                target_col="missing",
            )
        with self.assertRaises(WalkForwardError):
            self.splitter.evaluate_walk_forward(
                self.df,
                fit_predict_fn=lambda tr, te: np.ones(len(te), dtype=int),
                timestamp_col="missing",
                **self.kwargs,
            )

    def test_non_finite_and_wrong_length_returns_are_rejected(self):
        for bad in ([np.nan, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [-1.5, 0.0, 0.0, 0.0]):
            with self.assertRaises(WalkForwardError):
                self.splitter.evaluate_walk_forward(
                    self.df,
                    fit_predict_fn=lambda tr, te: np.ones(len(te), dtype=int),
                    returns_fn=lambda te, preds, v=bad: v,
                    **self.kwargs,
                )


class TestAggregation(unittest.TestCase):
    def _folds(self, accuracies, sharpes, drawdowns):
        return [
            FoldMetrics(
                fold_index=i,
                train_period="",
                test_period="",
                sample_count=4,
                accuracy=a,
                sharpe_ratio=s,
                max_drawdown_pct=d,
                win_rate=None,
            )
            for i, (a, s, d) in enumerate(zip(accuracies, sharpes, drawdowns))
        ]

    def test_aggregate_reports_dispersion_and_worst_fold(self):
        # accuracies 0.40 / 0.50 / 0.60: mean 0.50; sample sd sqrt(0.02/2) = 0.1.
        folds = self._folds([0.40, 0.50, 0.60], [3.873, -0.3974, None], [0.0, -0.1, 0.0])
        agg = WalkForwardSplitter.aggregate_folds(folds)

        self.assertEqual(agg.fold_count, 3)
        self.assertAlmostEqual(agg.mean_accuracy, 0.5)
        self.assertAlmostEqual(agg.std_accuracy, 0.1)
        self.assertEqual(agg.min_accuracy, 0.40)
        self.assertEqual(agg.max_accuracy, 0.60)
        self.assertEqual(agg.folds_with_returns, 3)
        # Folds without a defined Sharpe are excluded from the Sharpe statistics only.
        self.assertAlmostEqual(agg.mean_sharpe, (3.873 - 0.3974) / 2)
        self.assertAlmostEqual(agg.min_sharpe, -0.3974)
        self.assertAlmostEqual(agg.worst_max_drawdown_pct, -0.1)

    def test_aggregate_without_returns_reports_no_risk_statistics(self):
        agg = WalkForwardSplitter.aggregate_folds(
            self._folds([0.4, 0.5, 0.6], [None] * 3, [None] * 3)
        )
        self.assertIsNone(agg.mean_sharpe)
        self.assertIsNone(agg.min_sharpe)
        self.assertIsNone(agg.worst_max_drawdown_pct)
        self.assertEqual(agg.folds_with_returns, 0)

    def test_single_fold_reports_zero_dispersion_and_warns(self):
        with self.assertLogs("walk_forward", level="WARNING") as captured:
            agg = WalkForwardSplitter.aggregate_folds(self._folds([0.5], [1.0], [-0.05]))
        self.assertEqual(agg.std_accuracy, 0.0)
        self.assertTrue(any("3-5 distinct time windows" in line for line in captured.output))

    def test_empty_aggregate_is_rejected(self):
        with self.assertRaises(WalkForwardError):
            WalkForwardSplitter.aggregate_folds([])


class TestBackwardCompatibility(unittest.TestCase):
    def test_dict_shape_and_bounds(self):
        splits = walk_forward_splits(24, 10, 4, 2, "expanding")
        self.assertEqual(len(splits), 3)
        self.assertEqual(splits[0]["train"], (0, 10))
        self.assertEqual(splits[0]["embargo"], (10, 12))
        self.assertEqual(splits[0]["test"], (12, 16))

    def test_mode_string_is_case_insensitive(self):
        self.assertEqual(
            walk_forward_splits(24, 10, 4, 2, "ROLLING")[1]["train"],
            walk_forward_splits(24, 10, 4, 2, "rolling")[1]["train"],
        )
        self.assertEqual(walk_forward_splits(24, 10, 4, 2, "rolling")[1]["train"], (4, 14))

    def test_mode_typo_is_rejected_rather_than_silently_rolling(self):
        """Regression: any string but "expanding" used to fall through to ROLLING."""
        with self.assertRaises(WalkForwardError):
            walk_forward_splits(24, 10, 4, 2, "expandng")


if __name__ == "__main__":
    unittest.main()
