"""
Unit tests for feature-engineering-without-leakage.

Expected values are derived independently of the implementation:
  * Spearman correlation of a strictly increasing transform of a series with that
    series is exactly 1.0 (ranks are identical by construction).
  * Rank AUC is verified against a hand-counted concordant-pair fraction.
  * Lead correlations are checked against the closed form for a linear combination
    of independent standard normals: corr(a*Y_{t+1} + b*Y_{t+3}, Y_{t+k}) is
    a/sqrt(a^2+b^2) at k=1 and b/sqrt(a^2+b^2) at k=3.
  * Prefix invariance is asserted against pipelines whose causality is known by
    construction (rolling(...) vs rolling(..., center=True)).
"""
import logging
import unittest

import numpy as np
import pandas as pd

from feature_audit import (
    FeatureLeakageAuditor,
    LeakageType,
    _is_perfectly_separable,
    _rank_auc,
    verify_shift_direction,
)

# The auditor logs a warning whenever chronological order is only assumed, and an
# error when a calibration fails. Both are expected in this suite.
logging.getLogger("feature_audit").setLevel(logging.CRITICAL)


class TestAssociationScreen(unittest.TestCase):
    """Correlation / separation screening of features against the target."""

    def setUp(self):
        rng = np.random.default_rng(42)
        n = 400
        self.dates = pd.date_range("2026-01-01", periods=n, freq="D")
        close = 100.0 + np.cumsum(rng.standard_normal(n))
        self.returns = pd.Series(close).pct_change()

        # Target: return from T to T+1, realised only after bar T+1 closes.
        self.target = self.returns.shift(-1)
        self.df = pd.DataFrame(
            {
                "timestamp": self.dates,
                "symbol": ["ACME"] * n,
                "close": close,
                "target": self.target,
                "feature_lag1": self.returns.shift(1),
                "feature_lag5": self.returns.shift(5),
            }
        )
        self.auditor = FeatureLeakageAuditor()

    def test_lagged_features_produce_no_findings(self):
        """A strictly backward-looking feature set must not be flagged."""
        findings = self.auditor.audit_dataframe(
            self.df,
            target_col="target",
            feature_cols=["feature_lag1", "feature_lag5"],
            timestamp_col="timestamp",
        )
        self.assertEqual(findings, [])

    def test_default_column_selection_tolerates_non_numeric_columns(self):
        """
        Regression: the default feature selection used to raise
        'Cannot cast DatetimeArray to dtype float64' on any frame carrying a
        timestamp column -- i.e. on this skill's own documented data shape.
        """
        df = self.df.copy()
        df["feature_leaked"] = self.target.shift(-1)

        findings = self.auditor.audit_dataframe(
            df, target_col="target", timestamp_col="timestamp"
        )

        flagged = {f.feature_name for f in findings}
        self.assertIn("feature_leaked", flagged)
        self.assertNotIn("feature_lag1", flagged)
        self.assertNotIn("feature_lag5", flagged)

    def test_detects_future_leakage_at_the_argmax_lead(self):
        """
        Regression: the lead scan used to stop at the FIRST lead over the threshold
        while calling the result 'max_correlation_lead'.

        feature = 0.5*Y_{t+1} + 0.9*Y_{t+3} over iid standard normals gives a
        population correlation of 0.486 at lead 1 and 0.874 at lead 3, so with a
        threshold of 0.40 both cross and lead 3 is the true argmax.
        """
        rng = np.random.default_rng(2024)
        y = pd.Series(rng.standard_normal(600))
        df = pd.DataFrame({"target": y, "feature": 0.5 * y.shift(-1) + 0.9 * y.shift(-3)})

        findings = FeatureLeakageAuditor(correlation_threshold=0.40).audit_dataframe(
            df, target_col="target"
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.FUTURE_LOOKAHEAD)
        self.assertEqual(findings[0].max_correlation_lead, 3)
        self.assertAlmostEqual(findings[0].correlation_value, 0.874, delta=0.02)

    def test_detects_monotone_transform_of_target(self):
        """
        Regression: a Pearson-only screen misses a monotone copy of the target.
        cube(target) has Pearson ~0.77 (below the 0.99 same-bar threshold) but
        Spearman exactly 1.0, because cubing preserves ranks.
        """
        df = pd.DataFrame({"target": self.target, "cubed": self.target.pow(3)})

        findings = self.auditor.audit_dataframe(df, target_col="target")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.SAME_BAR_CONTAMINATION)
        self.assertEqual(findings[0].method, "spearman")
        self.assertEqual(findings[0].correlation_value, 1.0)
        # Confirm the old Pearson-only screen genuinely could not see this.
        self.assertLess(abs(df["cubed"].corr(df["target"])), 0.99)

    def test_detects_contamination_of_a_direction_target_by_perfect_separation(self):
        """
        Regression, and the skill's flagship failure mode: predicting the SIGN of the
        next return while a feature carries the return itself.

        Neither Pearson (~0.78) nor Spearman (~0.86) reaches the same-bar threshold,
        because a normal variate correlates with its own sign at only sqrt(2/pi).
        The label is nonetheless exactly recoverable from the feature by a single
        threshold, which the rank-AUC separation test detects (AUC = 1.0).
        """
        df = pd.DataFrame(
            {"target": np.sign(self.target), "leaked_return": 3.0 * self.target}
        )

        findings = self.auditor.audit_dataframe(df, target_col="target")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.SAME_BAR_CONTAMINATION)
        self.assertEqual(findings[0].method, "rank_auc")
        self.assertEqual(findings[0].correlation_value, 1.0)
        self.assertLess(abs(df["leaked_return"].corr(df["target"])), 0.99)
        self.assertLess(abs(df["leaked_return"].corr(df["target"], method="spearman")), 0.99)

    def test_rank_auc_matches_hand_counted_concordant_pairs(self):
        """
        feature [10,20,30,40] against target [0,1,0,1] has four (negative, positive)
        pairs: (10,20) (10,40) (30,40) are concordant, (30,20) is not -> AUC = 3/4.
        """
        auc = _rank_auc(pd.Series([10.0, 20.0, 30.0, 40.0]), pd.Series([0, 1, 0, 1]))
        self.assertEqual(auc, 0.75)

    def test_perfectly_ordered_binary_target_has_auc_one(self):
        auc = _rank_auc(pd.Series([1.0, 2.0, 3.0, 4.0]), pd.Series([0, 0, 1, 1]))
        self.assertEqual(auc, 1.0)

    def test_rank_auc_returns_none_for_non_binary_target(self):
        self.assertIsNone(_rank_auc(pd.Series([1.0, 2.0, 3.0]), pd.Series([0, 1, 2])))

    def test_detects_contamination_of_a_three_level_direction_target(self):
        """
        Regression: rank AUC is defined only for a binary target, so a separation test
        limited to it switches itself off on the most realistic form of the skill's
        flagship case -- sign(return) has THREE levels ({-1, 0, +1}) as soon as any bar
        closes unchanged, which real return series do routinely.
        """
        rng = np.random.default_rng(9)
        returns = pd.Series(rng.standard_normal(300)).round(0) / 10.0  # yields exact zeros
        direction = np.sign(returns)
        self.assertEqual(sorted(pd.unique(direction.dropna())), [-1.0, 0.0, 1.0])

        findings = self.auditor.audit_dataframe(
            pd.DataFrame({"target": direction, "leaked_return": 3.0 * returns}),
            target_col="target",
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.SAME_BAR_CONTAMINATION)
        self.assertEqual(findings[0].method, "class_separation")

    def test_three_level_target_with_an_independent_feature_is_clean(self):
        rng = np.random.default_rng(9)
        returns = pd.Series(rng.standard_normal(300)).round(0) / 10.0
        findings = self.auditor.audit_dataframe(
            pd.DataFrame(
                {"target": np.sign(returns), "noise": rng.standard_normal(300)}
            ),
            target_col="target",
        )
        self.assertEqual(findings, [])

    def test_perfect_separability_matches_hand_checked_intervals(self):
        """Classes on disjoint ordered intervals separate; overlapping ones do not."""
        feature = pd.Series([1.0, 2.0, 5.0, 6.0, 9.0, 10.0])
        self.assertTrue(_is_perfectly_separable(feature, pd.Series([0, 0, 1, 1, 2, 2])))
        self.assertFalse(_is_perfectly_separable(feature, pd.Series([0, 1, 0, 2, 1, 2])))


class TestChronologicalOrdering(unittest.TestCase):
    """Row order is the time axis; an unverifiable order must not yield a verdict."""

    def setUp(self):
        rng = np.random.default_rng(7)
        n = 300
        self.dates = pd.date_range("2026-03-01", periods=n, freq="D")
        target = pd.Series(rng.standard_normal(n))
        self.df = pd.DataFrame(
            {"timestamp": self.dates, "target": target, "leaked": target.shift(-1)}
        )
        self.auditor = FeatureLeakageAuditor()

    def test_sorted_frame_detects_the_leak(self):
        findings = self.auditor.audit_dataframe(
            self.df, target_col="target", timestamp_col="timestamp"
        )
        self.assertEqual([f.feature_name for f in findings], ["leaked"])
        self.assertEqual(findings[0].leakage_type, LeakageType.FUTURE_LOOKAHEAD)
        self.assertEqual(findings[0].max_correlation_lead, 1)

    def test_shuffled_frame_raises_instead_of_reporting_clean(self):
        """
        Regression: a shuffled frame used to audit CLEAN even though it contained a
        literal copy of the next period's target -- the worst possible failure for a
        leakage detector.
        """
        shuffled = self.df.sample(frac=1.0, random_state=1)

        with self.assertRaises(ValueError) as ctx:
            self.auditor.audit_dataframe(
                shuffled, target_col="target", timestamp_col="timestamp"
            )
        self.assertIn("not sorted", str(ctx.exception))

    def test_non_monotonic_index_raises_when_no_timestamp_column_given(self):
        shuffled = self.df.drop(columns=["timestamp"]).sample(frac=1.0, random_state=1)
        with self.assertRaises(ValueError):
            self.auditor.audit_dataframe(shuffled, target_col="target")

    def test_null_timestamps_raise(self):
        df = self.df.copy()
        df.loc[df.index[5], "timestamp"] = pd.NaT
        with self.assertRaises(ValueError):
            self.auditor.audit_dataframe(df, target_col="target", timestamp_col="timestamp")


class TestUndeterminedColumns(unittest.TestCase):
    """'Could not evaluate' must never be indistinguishable from 'clean'."""

    def setUp(self):
        rng = np.random.default_rng(11)
        self.n = 120
        self.target = pd.Series(rng.standard_normal(self.n))
        self.auditor = FeatureLeakageAuditor(min_observations=30)

    def test_sparse_column_is_reported_as_undetermined(self):
        sparse = pd.Series([np.nan] * self.n)
        sparse.iloc[:10] = 1.0
        sparse.iloc[10:20] = 2.0  # 20 non-null observations, below min_observations
        df = pd.DataFrame({"target": self.target, "sparse": sparse})

        findings = self.auditor.audit_dataframe(df, target_col="target")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.UNDETERMINED)
        self.assertEqual(findings[0].method, "insufficient_data")
        self.assertTrue(np.isnan(findings[0].correlation_value))

    def test_constant_column_is_reported_as_undetermined(self):
        df = pd.DataFrame({"target": self.target, "flat": pd.Series([1.0] * self.n)})

        findings = self.auditor.audit_dataframe(df, target_col="target")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.UNDETERMINED)
        self.assertEqual(findings[0].method, "constant_column")


class TestCausalityScreen(unittest.TestCase):
    """Prefix-invariance (truncation) test of a feature-construction function."""

    def setUp(self):
        rng = np.random.default_rng(11)
        n = 200
        self.raw = pd.DataFrame({"close": 100.0 + np.cumsum(rng.standard_normal(n))})
        self.auditor = FeatureLeakageAuditor()

    def test_causal_rolling_mean_is_prefix_invariant(self):
        findings = self.auditor.audit_feature_causality(
            self.raw,
            lambda d: pd.DataFrame({"ma": d["close"].rolling(5).mean()}, index=d.index),
        )
        self.assertEqual(findings, [])

    def test_expanding_window_is_prefix_invariant(self):
        findings = self.auditor.audit_feature_causality(
            self.raw,
            lambda d: pd.DataFrame({"hi": d["close"].expanding().max()}, index=d.index),
        )
        self.assertEqual(findings, [])

    def test_centered_rolling_window_is_flagged(self):
        findings = self.auditor.audit_feature_causality(
            self.raw,
            lambda d: pd.DataFrame(
                {"ma": d["close"].rolling(5, center=True).mean()}, index=d.index
            ),
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.UNSHIFTED_ROLLING)
        self.assertEqual(findings[0].method, "prefix_invariance")

    def test_negative_shift_is_flagged(self):
        findings = self.auditor.audit_feature_causality(
            self.raw,
            lambda d: pd.DataFrame({"next": d["close"].shift(-1)}, index=d.index),
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.UNSHIFTED_ROLLING)

    def test_whole_sample_normalisation_is_flagged(self):
        """
        A scaler fitted on the full sample leaks the test period's mean and standard
        deviation into every training row. Its correlation with the target is
        unremarkable, so only the structural screen can see it.
        """
        findings = self.auditor.audit_feature_causality(
            self.raw,
            lambda d: pd.DataFrame(
                {"z": (d["close"] - d["close"].mean()) / d["close"].std()}, index=d.index
            ),
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.UNSHIFTED_ROLLING)

    def test_backfill_over_sparse_gaps_is_flagged(self):
        findings = self.auditor.audit_feature_causality(
            self.raw,
            lambda d: pd.DataFrame(
                {"f": d["close"].mask(d.index % 7 == 0).bfill()}, index=d.index
            ),
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].leakage_type, LeakageType.UNSHIFTED_ROLLING)

    def test_forward_fill_is_prefix_invariant(self):
        findings = self.auditor.audit_feature_causality(
            self.raw,
            lambda d: pd.DataFrame(
                {"f": d["close"].mask(d.index % 7 == 0).ffill()}, index=d.index
            ),
        )
        self.assertEqual(findings, [])

    def test_series_returning_feature_fn_is_accepted(self):
        findings = self.auditor.audit_feature_causality(
            self.raw, lambda d: d["close"].pct_change().rename("ret")
        )
        self.assertEqual(findings, [])

    def test_index_altering_feature_fn_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.auditor.audit_feature_causality(
                self.raw,
                lambda d: pd.DataFrame({"f": d["close"].iloc[1:]}),
            )
        self.assertIn("preserve the input index", str(ctx.exception))

    def test_unsorted_raw_frame_raises(self):
        with self.assertRaises(ValueError):
            self.auditor.audit_feature_causality(
                self.raw.sample(frac=1.0, random_state=3),
                lambda d: pd.DataFrame({"ma": d["close"].rolling(5).mean()}, index=d.index),
            )

    def test_invalid_cut_fractions_raise(self):
        causal = lambda d: pd.DataFrame({"ma": d["close"].rolling(5).mean()}, index=d.index)
        with self.assertRaises(ValueError):
            self.auditor.audit_feature_causality(self.raw, causal, cut_fractions=())
        with self.assertRaises(ValueError):
            self.auditor.audit_feature_causality(self.raw, causal, cut_fractions=(1.0,))


class TestPointInTimeJoins(unittest.TestCase):
    """As-of merge and post-hoc timing verification."""

    def setUp(self):
        self.auditor = FeatureLeakageAuditor()
        self.trades = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 12:00"]),
                "trade_id": [1, 2],
            }
        )
        self.fundamentals = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-01-01 09:00", "2026-01-01 11:00"]),
                "earnings_pe": [15.0, 16.0],
            }
        )

    def test_asof_merge_attaches_the_latest_prior_record(self):
        merged = self.auditor.point_in_time_asof_merge(
            left_df=self.trades, right_df=self.fundamentals, on_timestamp_col="timestamp"
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged.loc[0, "earnings_pe"], 15.0)
        self.assertEqual(merged.loc[1, "earnings_pe"], 16.0)

    def test_exact_timestamp_match_is_excluded_by_default(self):
        """
        Regression: pandas' merge_asof defaults to allow_exact_matches=True, so a
        record stamped at exactly the decision timestamp was attached -- contradicting
        the documented 'publication timestamps strictly precede trade timestamps'.
        """
        single_trade = self.trades.iloc[:1]
        simultaneous = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-01-01 10:00"]),
                "earnings_pe": [99.0],
            }
        )

        strict = self.auditor.point_in_time_asof_merge(
            single_trade, simultaneous, "timestamp"
        )
        self.assertTrue(strict["earnings_pe"].isna().all())

        permissive = self.auditor.point_in_time_asof_merge(
            single_trade, simultaneous, "timestamp", allow_exact_matches=True
        )
        self.assertEqual(permissive["earnings_pe"].tolist(), [99.0])

    def test_asof_merge_rejects_missing_columns(self):
        with self.assertRaises(KeyError):
            self.auditor.point_in_time_asof_merge(
                self.trades, self.fundamentals, "not_a_column"
            )

    def test_verify_asof_timing_flags_publication_at_or_after_decision(self):
        joined = pd.DataFrame(
            {
                "decision": pd.to_datetime(
                    ["2026-01-01 10:00", "2026-01-01 11:00", "2026-01-01 12:00"]
                ),
                "published": pd.to_datetime(
                    ["2026-01-01 09:00", "2026-01-01 11:00", "2026-01-01 13:00"]
                ),
            }
        )

        strict = self.auditor.verify_asof_timing(joined, "decision", "published")
        self.assertEqual(len(strict), 1)
        self.assertEqual(strict[0].leakage_type, LeakageType.ASOF_TIMING_VIOLATION)
        self.assertIn("2 joined row(s)", strict[0].message)

        lenient = self.auditor.verify_asof_timing(
            joined, "decision", "published", allow_exact_matches=True
        )
        self.assertIn("1 joined row(s)", lenient[0].message)

    def test_verify_asof_timing_passes_a_clean_join(self):
        joined = pd.DataFrame(
            {
                "decision": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 11:00"]),
                "published": pd.to_datetime(["2026-01-01 09:00", "2026-01-01 09:30"]),
            }
        )
        self.assertEqual(self.auditor.verify_asof_timing(joined, "decision", "published"), [])

    def test_verify_asof_timing_rejects_mixed_timezone_awareness(self):
        """Comparing a tz-aware column with a naive one is itself a lookahead risk."""
        joined = pd.DataFrame(
            {
                "decision": pd.to_datetime(["2026-01-01 10:00"]).tz_localize("UTC"),
                "published": pd.to_datetime(["2026-01-01 09:00"]),
            }
        )
        with self.assertRaises(ValueError) as ctx:
            self.auditor.verify_asof_timing(joined, "decision", "published")
        self.assertIn("timezone", str(ctx.exception))

    def test_verify_asof_timing_treats_unmatched_rows_as_non_violations(self):
        joined = pd.DataFrame(
            {
                "decision": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 11:00"]),
                "published": pd.to_datetime(["2026-01-01 09:00", None]),
            }
        )
        self.assertEqual(self.auditor.verify_asof_timing(joined, "decision", "published"), [])


class TestLeakageCalibration(unittest.TestCase):
    """The calibration must be capable of failing."""

    def setUp(self):
        rng = np.random.default_rng(5)
        self.n = 200
        target = pd.Series(rng.standard_normal(self.n))
        self.df = pd.DataFrame({"target": target, "lag1": target.shift(1)})

    def test_calibration_reports_the_strength_actually_detected(self):
        auditor = FeatureLeakageAuditor()
        strength, findings = auditor.run_intentional_leakage_calibration(
            self.df, target_col="target"
        )
        self.assertEqual(strength, 1.0)
        self.assertTrue(
            any(f.leakage_type == LeakageType.FUTURE_LOOKAHEAD for f in findings)
        )

    def test_calibration_reports_zero_when_the_injected_leak_is_missed(self):
        """
        Regression: the returned correlation used to be corr(s, s), identically 1.0,
        so the calibration reported a perfect result even when the auditor detected
        nothing. Here min_observations is set so the injected feature cannot be
        screened; the correct report is 0.0, not 1.0.
        """
        auditor = FeatureLeakageAuditor(min_observations=self.n)

        strength, findings = auditor.run_intentional_leakage_calibration(
            self.df, target_col="target"
        )

        self.assertEqual(strength, 0.0)
        self.assertTrue(all(f.leakage_type == LeakageType.UNDETERMINED for f in findings))

    def test_calibration_refuses_to_overwrite_an_existing_column(self):
        df = self.df.copy()
        df["__leaked_feature__"] = 1.0
        with self.assertRaises(ValueError):
            FeatureLeakageAuditor().run_intentional_leakage_calibration(df, "target")


class TestShiftDirection(unittest.TestCase):

    def setUp(self):
        self.series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])

    def test_positive_shift_produces_a_lag(self):
        lagged = verify_shift_direction(self.series, shift_periods=1, expected_lag=True)
        self.assertTrue(np.isnan(lagged.iloc[0]))
        self.assertEqual(lagged.iloc[1], 1.0)

    def test_negative_shift_produces_a_lead_for_label_construction(self):
        led = verify_shift_direction(self.series, shift_periods=-1, expected_lag=False)
        self.assertEqual(led.iloc[0], 2.0)
        self.assertTrue(np.isnan(led.iloc[-1]))

    def test_negative_shift_rejected_when_a_lag_is_expected(self):
        with self.assertRaises(ValueError):
            verify_shift_direction(self.series, shift_periods=-1, expected_lag=True)

    def test_positive_shift_rejected_when_a_lead_is_expected(self):
        with self.assertRaises(ValueError):
            verify_shift_direction(self.series, shift_periods=1, expected_lag=False)

    def test_zero_shift_rejected(self):
        with self.assertRaises(ValueError):
            verify_shift_direction(self.series, shift_periods=0, expected_lag=True)


class TestArgumentValidation(unittest.TestCase):

    def setUp(self):
        rng = np.random.default_rng(1)
        target = pd.Series(rng.standard_normal(100))
        self.df = pd.DataFrame({"target": target, "lag1": target.shift(1)})

    def test_invalid_thresholds_rejected(self):
        for kwargs in (
            {"correlation_threshold": 0.0},
            {"correlation_threshold": 1.5},
            {"same_bar_threshold": -0.1},
            {"separation_threshold": 2.0},
            {"min_observations": 1},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    FeatureLeakageAuditor(**kwargs)

    def test_missing_target_column_raises(self):
        with self.assertRaises(KeyError):
            FeatureLeakageAuditor().audit_dataframe(self.df, target_col="absent")

    def test_explicitly_requested_missing_feature_column_raises(self):
        with self.assertRaises(KeyError):
            FeatureLeakageAuditor().audit_dataframe(
                self.df, target_col="target", feature_cols=["absent"]
            )

    def test_empty_frame_raises(self):
        with self.assertRaises(ValueError):
            FeatureLeakageAuditor().audit_dataframe(
                pd.DataFrame({"target": [], "f": []}), target_col="target"
            )

    def test_non_positive_max_lead_periods_raises(self):
        with self.assertRaises(ValueError):
            FeatureLeakageAuditor().audit_dataframe(
                self.df, target_col="target", max_lead_periods=0
            )

    def test_constant_target_raises(self):
        """
        Every correlation against a constant target is NaN, so an audit would report a
        leaked feature set as clean.
        """
        df = pd.DataFrame({"target": [1.0] * 100, "leaked": np.arange(100.0)})
        with self.assertRaises(ValueError) as ctx:
            FeatureLeakageAuditor().audit_dataframe(df, target_col="target")
        self.assertIn("constant", str(ctx.exception))

    def test_target_with_too_few_observations_raises(self):
        tiny = pd.DataFrame({"target": [1.0, 2.0, 3.0], "f": [1.0, 2.0, 3.0]})
        with self.assertRaises(ValueError):
            FeatureLeakageAuditor().audit_dataframe(tiny, target_col="target")


if __name__ == "__main__":
    unittest.main()
