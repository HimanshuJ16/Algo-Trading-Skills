"""
Unit tests for categorical-feature-encoding-for-instrument-identity.

Coverage:
1.  Point-in-time correctness on a hand-computed panel (prior, blend, cold start).
2.  Leakage regressions -- no row sees its own or any later label, and overlapping
    forward-looking labels are purged via ``label_horizon``.
3.  Alignment regressions -- caller index, row order and existing columns survive.
4.  Shrinkage identity: the symbol mean carries exactly 50% at n == smoothing_weight.
5.  Fit/transform parity, so a live row is encoded exactly as the backtest encoded it.
6.  Input validation and numerical edge cases.
"""
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from categorical_feature_encoding_for_instrument_identity import PointInTimeTargetEncoder

BASE = datetime(2025, 1, 1)


def day(offset: int) -> datetime:
    return BASE + timedelta(days=offset)


class TestPointInTimeCorrectness(unittest.TestCase):
    """Expected values below are derived by hand from the panel, not from the code."""

    def setUp(self):
        # Day 0: AAPL +1.0, TSLA -1.0
        # Day 1: AAPL +1.0, MSFT  0.0  <- MSFT is newly listed
        # Day 2: AAPL +1.0
        self.df = pd.DataFrame(
            {
                "timestamp": [day(0), day(0), day(1), day(1), day(2)],
                "symbol": ["AAPL", "TSLA", "AAPL", "MSFT", "AAPL"],
                "target": [1.0, -1.0, 1.0, 0.0, 1.0],
            }
        )
        self.encoder = PointInTimeTargetEncoder(smoothing_weight=1.0)

    def encode(self, **kwargs):
        return PointInTimeTargetEncoder(**kwargs).fit_transform(
            self.df, "timestamp", "symbol", "target"
        )["symbol_encoded"]

    def test_first_timestamp_falls_back_entirely_to_the_prior(self):
        # Nothing is realized before day 0, so both rows must be the cold-start prior
        # (default 0.0) and must not depend on their own day-0 targets of +1 and -1.
        encoded = self.encode(smoothing_weight=1.0)
        self.assertEqual(encoded.iloc[0], 0.0)
        self.assertEqual(encoded.iloc[1], 0.0)

    def test_blend_of_symbol_history_and_global_history(self):
        # Day 1 AAPL. Realized history is day 0: global sum 1 + (-1) = 0 over 2 obs,
        # so global_mean = 0.0. AAPL: sum 1.0, count 1.
        # encoded = (1.0 + 1.0 * 0.0) / (1 + 1.0) = 0.5
        self.assertAlmostEqual(self.encode(smoothing_weight=1.0).iloc[2], 0.5)

    def test_newly_listed_symbol_gets_the_global_mean(self):
        # Day 1 MSFT has no history of its own: encoded = (0 + w * global_mean) / w
        # = global_mean = 0.0. It must not be NaN and must not be an extreme value.
        self.assertAlmostEqual(self.encode(smoothing_weight=1.0).iloc[3], 0.0)

    def test_history_accumulates_across_days(self):
        # Day 2 AAPL. Realized history is days 0-1: global sum 1 - 1 + 1 + 0 = 1 over
        # 4 obs -> global_mean = 0.25. AAPL sum 2.0, count 2.
        # encoded = (2.0 + 1.0 * 0.25) / (2 + 1.0) = 2.25 / 3 = 0.75
        self.assertAlmostEqual(self.encode(smoothing_weight=1.0).iloc[4], 0.75)

    def test_shrinkage_gives_the_symbol_mean_half_the_weight_at_n_equals_weight(self):
        # standards.md claims a weight of m means the symbol mean carries 50% once it
        # has m observations. Verify against the independent midpoint formula.
        panel = pd.DataFrame(
            {
                "timestamp": [day(0), day(0), day(1), day(1), day(2)],
                "symbol": ["AAA", "BBB", "AAA", "BBB", "AAA"],
                "target": [4.0, 0.0, 4.0, 0.0, np.nan],
            }
        )
        encoded = PointInTimeTargetEncoder(smoothing_weight=2.0).fit_transform(
            panel, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        # Day 2 AAA: local mean 4.0 over exactly 2 observations, global mean 2.0.
        local_mean, global_mean = 4.0, 2.0
        self.assertAlmostEqual(encoded.iloc[4], 0.5 * local_mean + 0.5 * global_mean)

    def test_multiple_rows_for_one_symbol_on_one_timestamp_all_count(self):
        panel = pd.DataFrame(
            {
                "timestamp": [day(0), day(0), day(1)],
                "symbol": ["AAA", "AAA", "AAA"],
                "target": [1.0, 3.0, 0.0],
            }
        )
        encoded = PointInTimeTargetEncoder(smoothing_weight=2.0).fit_transform(
            panel, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        # Day 1: local sum 4.0 over 2 obs, global mean also 2.0.
        # encoded = (4.0 + 2.0 * 2.0) / (2 + 2.0) = 2.0
        self.assertAlmostEqual(encoded.iloc[2], 2.0)


class TestLeakageControls(unittest.TestCase):
    def test_row_never_contributes_to_its_own_encoding(self):
        # A single row cannot see itself: a huge target must not move its own encoding.
        panel = pd.DataFrame(
            {"timestamp": [day(0)], "symbol": ["AAA"], "target": [1_000.0]}
        )
        encoded = PointInTimeTargetEncoder(smoothing_weight=1.0).fit_transform(
            panel, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        self.assertEqual(encoded.iloc[0], 0.0)

    def test_later_rows_never_influence_earlier_ones(self):
        panel = pd.DataFrame(
            {
                "timestamp": [day(0), day(1)],
                "symbol": ["AAA", "AAA"],
                "target": [0.0, 500.0],
            }
        )
        extended = pd.concat(
            [panel, pd.DataFrame({"timestamp": [day(2)], "symbol": ["AAA"], "target": [-500.0]})],
            ignore_index=True,
        )
        short = PointInTimeTargetEncoder(2.0).fit_transform(panel, "timestamp", "symbol", "target")
        long = PointInTimeTargetEncoder(2.0).fit_transform(extended, "timestamp", "symbol", "target")
        self.assertEqual(
            short["symbol_encoded"].tolist(), long["symbol_encoded"].iloc[:2].tolist()
        )

    def test_label_horizon_purges_labels_not_yet_realized(self):
        # Minute bars carrying a 1-DAY forward-return label. Without a horizon the
        # encoder would hand row 1 the label of row 0, which is not observable until a
        # day later -- this is the regression the label_horizon parameter exists for.
        panel = pd.DataFrame(
            {
                "timestamp": [BASE + timedelta(minutes=i) for i in range(4)],
                "symbol": ["AAA"] * 4,
                "target": [5.0] * 4,
            }
        )
        leaky = PointInTimeTargetEncoder(1.0).fit_transform(
            panel, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        self.assertAlmostEqual(leaky.iloc[1], 5.0, msg="sanity: no horizon does leak here")

        purged = PointInTimeTargetEncoder(
            1.0, label_horizon=pd.Timedelta(days=1)
        ).fit_transform(panel, "timestamp", "symbol", "target")["symbol_encoded"]
        self.assertTrue(
            (purged == 0.0).all(),
            "no 1-day label is realized within a 4-minute window; all rows must be the prior",
        )

    def test_label_horizon_still_admits_a_fully_realized_label(self):
        # Daily bars with a 1-day label: the day-0 label is realized exactly at day 1,
        # so day 1 may use it. Purging must not be off by one period.
        panel = pd.DataFrame(
            {
                "timestamp": [day(0), day(1), day(2)],
                "symbol": ["AAA"] * 3,
                "target": [2.0, 2.0, 2.0],
            }
        )
        encoded = PointInTimeTargetEncoder(
            1.0, label_horizon=pd.Timedelta(days=1)
        ).fit_transform(panel, "timestamp", "symbol", "target")["symbol_encoded"]
        self.assertEqual(encoded.iloc[0], 0.0)
        self.assertAlmostEqual(encoded.iloc[1], 2.0)

    def test_integer_bar_index_horizon(self):
        panel = pd.DataFrame(
            {"bar": [0, 1, 2, 3], "symbol": ["AAA"] * 4, "target": [6.0] * 4}
        )
        encoded = PointInTimeTargetEncoder(1.0, label_horizon=2).fit_transform(
            panel, "bar", "symbol", "target"
        )["symbol_encoded"]
        # Bar 2 may use bars <= 0; bar 3 may use bars <= 1.
        self.assertEqual(encoded.iloc[1], 0.0)
        self.assertAlmostEqual(encoded.iloc[2], 6.0)


class TestFrameIntegrity(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame(
            {
                "timestamp": [day(0), day(0), day(1), day(1), day(2)],
                "symbol": ["AAPL", "TSLA", "AAPL", "MSFT", "AAPL"],
                "target": [1.0, -1.0, 1.0, 0.0, 1.0],
            }
        )

    def test_unsorted_input_keeps_caller_index_and_row_order(self):
        # Regression: an implementation that merges or sorts internally silently
        # reorders the output and resets the index, misaligning the new feature against
        # the caller's labels.
        shuffled = self.df.iloc[[4, 0, 2, 1, 3]].copy()
        shuffled.index = [100, 101, 102, 103, 104]
        out = PointInTimeTargetEncoder(1.0).fit_transform(
            shuffled, "timestamp", "symbol", "target"
        )
        self.assertEqual(list(out.index), [100, 101, 102, 103, 104])
        self.assertEqual(out["symbol"].tolist(), shuffled["symbol"].tolist())
        # Day-2 AAPL row, still first: (2.0 + 1.0 * 0.25) / 3 = 0.75
        self.assertAlmostEqual(out["symbol_encoded"].iloc[0], 0.75)

    def test_row_order_does_not_change_any_encoding(self):
        ordered = PointInTimeTargetEncoder(1.0).fit_transform(
            self.df, "timestamp", "symbol", "target"
        )
        shuffled = self.df.iloc[[3, 1, 4, 0, 2]]
        out = PointInTimeTargetEncoder(1.0).fit_transform(
            shuffled, "timestamp", "symbol", "target"
        )
        for position, source_row in enumerate([3, 1, 4, 0, 2]):
            self.assertAlmostEqual(
                out["symbol_encoded"].iloc[position],
                ordered["symbol_encoded"].iloc[source_row],
            )

    def test_caller_columns_are_never_overwritten(self):
        # Regression: temp columns named global_mean/local_mean used to clobber and then
        # drop a caller column of the same name.
        df = self.df.assign(global_mean=7.0, local_mean=9.0)
        out = PointInTimeTargetEncoder(1.0).fit_transform(df, "timestamp", "symbol", "target")
        self.assertEqual(out["global_mean"].tolist(), [7.0] * 5)
        self.assertEqual(out["local_mean"].tolist(), [9.0] * 5)
        self.assertEqual(
            list(out.columns), list(df.columns) + ["symbol_encoded"]
        )

    def test_existing_output_column_is_refused_not_overwritten(self):
        df = self.df.assign(symbol_encoded=1.0)
        with self.assertRaises(ValueError):
            PointInTimeTargetEncoder(1.0).fit_transform(df, "timestamp", "symbol", "target")

    def test_custom_output_column_name(self):
        out = PointInTimeTargetEncoder(1.0).fit_transform(
            self.df, "timestamp", "symbol", "target", encoded_col="sym_te"
        )
        self.assertIn("sym_te", out.columns)
        self.assertNotIn("symbol_encoded", out.columns)

    def test_input_frame_is_not_mutated(self):
        before = self.df.copy()
        PointInTimeTargetEncoder(1.0).fit_transform(self.df, "timestamp", "symbol", "target")
        pd.testing.assert_frame_equal(self.df, before)

    def test_duplicate_index_labels(self):
        # Panels pick these up through concatenation; index-aligned assignment of the
        # encoded column would be ambiguous.
        df = self.df.copy()
        df.index = [0, 0, 1, 1, 2]
        encoded = PointInTimeTargetEncoder(1.0).fit_transform(
            df, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        self.assertAlmostEqual(encoded.iloc[4], 0.75)

    def test_categorical_and_integer_symbol_columns(self):
        categorical = self.df.copy()
        categorical["symbol"] = pd.Categorical(
            categorical["symbol"], categories=["AAPL", "TSLA", "MSFT", "GOOG"]
        )
        encoded = PointInTimeTargetEncoder(1.0).fit_transform(
            categorical, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        # The unused GOOG category must not disturb anything.
        self.assertAlmostEqual(encoded.iloc[4], 0.75)

        integer_ids = self.df.assign(symbol=[1, 2, 1, 3, 1])
        encoded = PointInTimeTargetEncoder(1.0).fit_transform(
            integer_ids, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        self.assertAlmostEqual(encoded.iloc[4], 0.75)

    def test_timezone_aware_timestamps(self):
        df = self.df.copy()
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
        encoded = PointInTimeTargetEncoder(1.0).fit_transform(
            df, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        self.assertAlmostEqual(encoded.iloc[4], 0.75)


class TestLiveTransform(unittest.TestCase):
    def setUp(self):
        self.panel = pd.DataFrame(
            {
                "timestamp": [day(0), day(0), day(1), day(1), day(2)],
                "symbol": ["AAPL", "TSLA", "AAPL", "MSFT", "AAPL"],
                "target": [1.0, -1.0, 1.0, 0.0, 1.0],
            }
        )

    def test_transform_requires_fit(self):
        with self.assertRaises(RuntimeError):
            PointInTimeTargetEncoder(1.0).transform(self.panel, "timestamp", "symbol")

    def test_live_row_encoding_matches_the_backtest_encoding(self):
        # Train/live parity: encoding a day-2 row from a day-0/1 fit must equal what a
        # single fit_transform over the whole panel produced for that row. If these
        # diverge, the model sees different features live than it was trained on.
        history = self.panel.iloc[:4]
        live = self.panel.iloc[4:][["timestamp", "symbol"]]
        encoder = PointInTimeTargetEncoder(1.0).fit(history, "timestamp", "symbol", "target")
        live_value = encoder.transform(live, "timestamp", "symbol")["symbol_encoded"].iloc[0]
        backtest_value = PointInTimeTargetEncoder(1.0).fit_transform(
            self.panel, "timestamp", "symbol", "target"
        )["symbol_encoded"].iloc[4]
        self.assertAlmostEqual(live_value, backtest_value)

    def test_unseen_symbol_transforms_to_the_global_mean(self):
        encoder = PointInTimeTargetEncoder(1.0).fit(self.panel, "timestamp", "symbol", "target")
        out = encoder.transform(
            pd.DataFrame({"timestamp": [day(3)], "symbol": ["NVDA"]}), "timestamp", "symbol"
        )
        # Global history at day 3: sum 1 - 1 + 1 + 0 + 1 = 2 over 5 obs -> 0.4
        self.assertAlmostEqual(out["symbol_encoded"].iloc[0], 0.4)

    def test_fitted_symbols_are_reported(self):
        encoder = PointInTimeTargetEncoder(1.0).fit(self.panel, "timestamp", "symbol", "target")
        self.assertTrue(encoder.is_fitted)
        self.assertEqual(sorted(encoder.fitted_symbols), ["AAPL", "MSFT", "TSLA"])


class TestColdStartPrior(unittest.TestCase):
    def setUp(self):
        self.panel = pd.DataFrame(
            {"timestamp": [day(0), day(1)], "symbol": ["AAA", "AAA"], "target": [1.0, 1.0]}
        )

    def test_prior_defaults_to_zero(self):
        encoded = PointInTimeTargetEncoder(1.0).fit_transform(
            self.panel, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        self.assertEqual(encoded.iloc[0], 0.0)

    def test_prior_can_be_set_to_a_base_rate(self):
        # For a 0/1 label, a 0.0 prior asserts an event that never happens; the base
        # rate must be settable.
        encoded = PointInTimeTargetEncoder(1.0, cold_start_prior=0.3).fit_transform(
            self.panel, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        self.assertAlmostEqual(encoded.iloc[0], 0.3)

    def test_prior_can_be_nan_to_mark_absent_history(self):
        encoded = PointInTimeTargetEncoder(1.0, cold_start_prior=float("nan")).fit_transform(
            self.panel, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        self.assertTrue(np.isnan(encoded.iloc[0]))
        self.assertFalse(np.isnan(encoded.iloc[1]))


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.panel = pd.DataFrame(
            {"timestamp": [day(0), day(1)], "symbol": ["AAA", "AAA"], "target": [1.0, 2.0]}
        )

    def test_non_positive_smoothing_weight_is_rejected(self):
        # A weight of 0 made a cold-start symbol 0/0 = NaN, silently poisoning the
        # feature; a negative weight could produce inf.
        for weight in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                PointInTimeTargetEncoder(weight)

    def test_infinite_cold_start_prior_is_rejected(self):
        with self.assertRaises(ValueError):
            PointInTimeTargetEncoder(1.0, cold_start_prior=float("inf"))

    def test_missing_column_names_the_column_and_the_alternatives(self):
        with self.assertRaises(KeyError) as ctx:
            PointInTimeTargetEncoder(1.0).fit_transform(
                self.panel, "timestamp", "ticker", "target"
            )
        self.assertIn("ticker", str(ctx.exception))
        self.assertIn("symbol", str(ctx.exception))

    def test_empty_frame_is_rejected(self):
        with self.assertRaises(ValueError):
            PointInTimeTargetEncoder(1.0).fit_transform(
                self.panel.iloc[:0], "timestamp", "symbol", "target"
            )

    def test_missing_timestamp_is_rejected(self):
        panel = self.panel.copy()
        panel.loc[0, "timestamp"] = pd.NaT
        with self.assertRaises(ValueError):
            PointInTimeTargetEncoder(1.0).fit_transform(panel, "timestamp", "symbol", "target")

    def test_missing_symbol_is_rejected(self):
        panel = self.panel.copy()
        panel.loc[0, "symbol"] = None
        with self.assertRaises(ValueError):
            PointInTimeTargetEncoder(1.0).fit_transform(panel, "timestamp", "symbol", "target")

    def test_non_numeric_target_is_rejected(self):
        panel = self.panel.assign(target=["up", "down"])
        with self.assertRaises(ValueError):
            PointInTimeTargetEncoder(1.0).fit_transform(panel, "timestamp", "symbol", "target")

    def test_infinite_target_is_rejected(self):
        panel = self.panel.copy()
        panel.loc[0, "target"] = np.inf
        with self.assertRaises(ValueError):
            PointInTimeTargetEncoder(1.0).fit_transform(panel, "timestamp", "symbol", "target")

    def test_negative_label_horizon_is_rejected(self):
        with self.assertRaises(ValueError):
            PointInTimeTargetEncoder(
                1.0, label_horizon=pd.Timedelta(days=-1)
            ).fit_transform(self.panel, "timestamp", "symbol", "target")

    def test_horizon_incompatible_with_the_time_dtype_is_rejected(self):
        with self.assertRaises(TypeError):
            PointInTimeTargetEncoder(1.0, label_horizon=3).fit_transform(
                self.panel, "timestamp", "symbol", "target"
            )

    def test_non_dataframe_input_is_rejected(self):
        with self.assertRaises(TypeError):
            PointInTimeTargetEncoder(1.0).fit_transform(
                [{"timestamp": day(0)}], "timestamp", "symbol", "target"
            )


def brute_force_encode(panel, weight, horizon, prior):
    """
    Deliberately naive O(n^2) reference: for every row, rescan the whole panel and
    average by hand. Independent of the vectorized cumulative-sum path under test.
    """
    encoded = []
    for _, row in panel.iterrows():
        usable = []
        for _, other in panel.iterrows():
            if other["timestamp"] >= row["timestamp"]:
                continue
            if horizon is not None and other["timestamp"] > row["timestamp"] - horizon:
                continue
            if pd.isna(other["target"]):
                continue
            usable.append(other)
        global_targets = [o["target"] for o in usable]
        local_targets = [o["target"] for o in usable if o["symbol"] == row["symbol"]]
        global_mean = sum(global_targets) / len(global_targets) if global_targets else prior
        encoded.append(
            (sum(local_targets) + weight * global_mean) / (len(local_targets) + weight)
        )
    return encoded


class TestAgainstBruteForceReference(unittest.TestCase):
    def test_matches_naive_reference_on_a_random_panel(self):
        rng = np.random.default_rng(20250820)
        rows = []
        for offset in range(12):
            for symbol in ["AAA", "BBB", "CCC", "DDD"]:
                if rng.random() < 0.25:
                    continue  # ragged panel: symbols enter and leave
                target = float(rng.normal()) if rng.random() > 0.15 else np.nan
                rows.append({"timestamp": day(offset), "symbol": symbol, "target": target})
        panel = pd.DataFrame(rows)

        for horizon in (None, pd.Timedelta(days=1), pd.Timedelta(days=3)):
            with self.subTest(horizon=horizon):
                encoded = PointInTimeTargetEncoder(
                    smoothing_weight=5.0, label_horizon=horizon, cold_start_prior=0.02
                ).fit_transform(panel, "timestamp", "symbol", "target")["symbol_encoded"]
                expected = brute_force_encode(panel, 5.0, horizon, 0.02)
                np.testing.assert_allclose(encoded.to_numpy(), expected, rtol=1e-12)


class TestMissingTargets(unittest.TestCase):
    def test_nan_targets_are_excluded_from_statistics_but_rows_still_encode(self):
        panel = pd.DataFrame(
            {
                "timestamp": [day(0), day(0), day(1)],
                "symbol": ["AAA", "BBB", "AAA"],
                "target": [np.nan, 4.0, 0.0],
            }
        )
        encoded = PointInTimeTargetEncoder(1.0).fit_transform(
            panel, "timestamp", "symbol", "target"
        )["symbol_encoded"]
        self.assertFalse(encoded.isna().any(), "an unlabelled row still needs a feature")
        # Day 1 AAA: only BBB's 4.0 is realized -> global_mean 4.0, AAA has no counted
        # observation, so encoded = (0 + 1.0 * 4.0) / (0 + 1.0) = 4.0
        self.assertAlmostEqual(encoded.iloc[2], 4.0)


if __name__ == "__main__":
    unittest.main()
