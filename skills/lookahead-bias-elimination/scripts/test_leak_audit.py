"""
Unit tests for lookahead-bias-elimination.

The expected values here are derived independently of the implementation:
same-bar contamination is asserted against hand-placed fill prices, the centred
window fingerprint against a window whose arithmetic is checked by hand, and the
alignment tests against explicitly enumerated row positions.

Regression coverage for defects the previous implementation had:
- warm-up and row_index computed from the index *label*, which collapsed to 0 on
  a datetime-indexed frame (the standard OHLCV shape) and flagged every signal;
- a missing column silently producing an empty (i.e. "clean") finding list;
- an absolute 1e-6 fill tolerance that mis-classifies at crypto and FX price scales;
- a positional shift that ran across instrument boundaries in a stacked panel;
- `run_leak_calibration` documented as confirming detector sensitivity while
  measuring nothing.
"""
import logging
import unittest

import numpy as np
import pandas as pd

from leak_audit import (
    FRAME_LEVEL_ROW_INDEX,
    LookaheadBiasAuditor,
    LookaheadViolationType,
    audit_feature_timestamps,
    check_feature_timestamps,
    inject_forward_leak,
)


def _types(findings):
    return [finding.violation_type for finding in findings]


def _of_type(findings, violation_type):
    return [finding for finding in findings if finding.violation_type == violation_type]


class TestSameBarFillDetection(unittest.TestCase):
    def setUp(self):
        self.auditor = LookaheadBiasAuditor(warmup_periods=5)
        n = 20
        # Bars are spaced so that no bar's Open, High, Low or Close coincides with
        # any other bar's -- otherwise a correctly aligned next-bar-Open fill can
        # equal the previous bar's High by arithmetic accident. See
        # test_coincident_next_open_is_a_documented_false_positive.
        self.df = pd.DataFrame(
            {
                "open": [100.0 + 2 * i for i in range(n)],
                "high": [100.7 + 2 * i for i in range(n)],
                "low": [99.6 + 2 * i for i in range(n)],
                "close": [100.3 + 2 * i for i in range(n)],
                "signal": [0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                # Flawed backtest: filled at same-bar Close.
                "fill_price": [100.3 + 2 * i for i in range(n)],
            }
        )

    def test_detects_same_bar_fill_and_warmup_violations(self):
        findings = self.auditor.audit_backtest_timing(self.df)
        types = _types(findings)
        self.assertIn(LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION, types)
        self.assertIn(LookaheadViolationType.INDICATOR_UNWARMED_EXECUTION, types)

        contaminated = _of_type(findings, LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION)
        self.assertEqual([f.row_index for f in contaminated], [2, 5])
        # Only row 2 (< warmup 5) is unwarmed; row 5 is not.
        unwarmed = _of_type(findings, LookaheadViolationType.INDICATOR_UNWARMED_EXECUTION)
        self.assertEqual([f.row_index for f in unwarmed], [2])

    def test_correctly_aligned_next_bar_open_fill_is_clean(self):
        clean = self.df.copy()
        clean["fill_price"] = clean["open"].shift(-1)
        findings = self.auditor.audit_backtest_timing(clean)
        self.assertEqual(
            _of_type(findings, LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION), []
        )

    def test_coincident_next_open_is_a_documented_false_positive(self):
        """
        The screen compares prices, not provenance. A gapless bar whose Open
        equals the previous Close -- routine in continuous and synthetic series --
        makes a correctly aligned fill indistinguishable from a same-bar one, and
        is reported. Asserted here so the limitation stays visible rather than
        being discovered as a surprise on real data.
        """
        n = 8
        gapless = pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0],
                "high": [100.5 + i for i in range(n)],
                "low": [99.5 + i for i in range(n)],
                # Every Close equals the following bar's Open: no overnight gap.
                "close": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0],
                "signal": [0, 0, 1, 0, 0, 0, 0, 0],
            }
        )
        gapless["fill_price"] = gapless["open"].shift(-1)  # correctly aligned
        findings = LookaheadBiasAuditor(warmup_periods=0).audit_backtest_timing(gapless)
        contaminated = _of_type(findings, LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION)
        self.assertEqual([f.row_index for f in contaminated], [2])
        self.assertIn("close", contaminated[0].details)

    def test_detects_fill_at_same_bar_high_and_low(self):
        # Documented behaviour that the previous implementation never had: it
        # compared the fill against Close only.
        for column in ("high", "low"):
            with self.subTest(column=column):
                df = self.df.copy()
                df["fill_price"] = df[column]
                findings = self.auditor.audit_backtest_timing(df)
                contaminated = _of_type(
                    findings, LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION
                )
                self.assertEqual([f.row_index for f in contaminated], [2, 5])
                self.assertIn(column, contaminated[0].details)

    def test_absent_reference_column_is_reported_not_silently_skipped(self):
        df = self.df.drop(columns=["high", "low"])
        findings = df.pipe(self.auditor.audit_backtest_timing)
        undetermined = _of_type(findings, LookaheadViolationType.UNDETERMINED)
        self.assertEqual(len(undetermined), 1)
        self.assertEqual(undetermined[0].row_index, FRAME_LEVEL_ROW_INDEX)
        self.assertIn("high", undetermined[0].details)
        self.assertIn("low", undetermined[0].details)

    def test_missing_required_column_raises_instead_of_reporting_clean(self):
        for column in ("signal", "fill_price"):
            with self.subTest(column=column):
                with self.assertRaises(ValueError):
                    self.auditor.audit_backtest_timing(self.df.drop(columns=[column]))
        with self.assertRaises(ValueError):
            self.auditor.audit_backtest_timing(self.df.drop(columns=["close", "high", "low"]))

    def test_nan_fill_price_is_reported_not_passed(self):
        df = self.df.copy()
        df.loc[2, "fill_price"] = np.nan
        findings = self.auditor.audit_backtest_timing(df)
        undetermined = _of_type(findings, LookaheadViolationType.UNDETERMINED)
        self.assertEqual(len(undetermined), 1)
        self.assertIn("1 active signal(s)", undetermined[0].details)

    def test_nan_reference_price_at_a_signal_bar_is_reported(self):
        """
        Without this, an active signal whose Close/High/Low are all NaN passes the
        same-bar comparison for want of anything to compare against, and the caller
        reads that silence as a clean bar.
        """
        df = self.df.drop(columns=["high", "low"])
        df.loc[2, "close"] = np.nan
        findings = self.auditor.audit_backtest_timing(df)
        details = [f.details for f in _of_type(findings, LookaheadViolationType.UNDETERMINED)]
        self.assertTrue(any("for want of a comparison" in text for text in details))

    def test_nan_signal_is_reported_not_treated_as_active(self):
        df = self.df.copy()
        df.loc[7, "signal"] = np.nan
        findings = self.auditor.audit_backtest_timing(df)
        undetermined = _of_type(findings, LookaheadViolationType.UNDETERMINED)
        self.assertEqual(len(undetermined), 1)
        self.assertIn("NaN 'signal'", undetermined[0].details)
        contaminated = _of_type(findings, LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION)
        self.assertEqual([f.row_index for f in contaminated], [2, 5])


class TestPositionalAddressing(unittest.TestCase):
    """
    Regression: the previous implementation derived both the warm-up comparison
    and `row_index` from the index label, so a DatetimeIndex -- the ordinary shape
    of an OHLCV backtest frame -- collapsed every row to position 0.
    """

    def setUp(self):
        self.n = 12
        self.base = pd.DataFrame(
            {
                "open": [100.0 + i for i in range(self.n)],
                "close": [100.5 + i for i in range(self.n)],
                "signal": [0] * self.n,
                "fill_price": [100.0 + i for i in range(self.n)],
            }
        )
        # One active signal at position 9, filled at that bar's close.
        self.base.loc[9, "signal"] = 1
        self.base.loc[9, "fill_price"] = self.base.loc[9, "close"]

    def _assert_single_contamination_at_position_9(self, df, **kwargs):
        findings = LookaheadBiasAuditor(warmup_periods=5).audit_backtest_timing(df, **kwargs)
        contaminated = _of_type(findings, LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION)
        self.assertEqual([f.row_index for f in contaminated], [9])
        # Position 9 is past the 5-bar warm-up, so nothing may be flagged unwarmed.
        self.assertEqual(_of_type(findings, LookaheadViolationType.INDICATOR_UNWARMED_EXECUTION), [])
        return contaminated[0]

    def test_datetime_index(self):
        df = self.base.copy()
        df.index = pd.date_range("2024-01-02", periods=self.n, freq="D")
        finding = self._assert_single_contamination_at_position_9(df)
        self.assertIn("2024-01-11", finding.timestamp)

    def test_non_zero_based_integer_index(self):
        df = self.base.copy()
        df.index = range(500, 500 + self.n)
        self._assert_single_contamination_at_position_9(df)

    def test_multiindex_frame_is_addressable(self):
        # A (symbol, date) MultiIndex is an ordinary backtest shape, and
        # `Index.astype(str)` raises on one.
        df = self.base.copy()
        df.index = pd.MultiIndex.from_product(
            [["AAA"], pd.date_range("2024-01-02", periods=self.n, freq="D")],
            names=["symbol", "date"],
        )
        self._assert_single_contamination_at_position_9(df)

    def test_timestamp_column_is_used_for_the_finding_label(self):
        df = self.base.copy()
        df["ts"] = pd.date_range("2024-03-01", periods=self.n, freq="D")
        finding = self._assert_single_contamination_at_position_9(df, timestamp_col="ts")
        self.assertIn("2024-03-10", finding.timestamp)


class TestPriceScaleInvariance(unittest.TestCase):
    """
    Regression: an absolute 1e-6 tolerance is wrong at both ends of the price
    range -- it misses nothing on equities but silently mis-classifies elsewhere.
    """

    def _frame(self, close, fill):
        n = 10
        return pd.DataFrame(
            {
                "close": [close] * n,
                "open": [close] * n,
                "signal": [0, 0, 0, 0, 0, 1, 0, 0, 0, 0],
                "fill_price": [fill] * n,
            }
        )

    def test_fx_scale_distinct_prices_are_not_flagged(self):
        # Two genuinely different FX quotes one pip apart: 1e-6 absolute would
        # call them equal and report a violation that does not exist.
        df = self._frame(close=0.000_120, fill=0.000_121)
        findings = LookaheadBiasAuditor(warmup_periods=0).audit_backtest_timing(df)
        self.assertEqual(_of_type(findings, LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION), [])

    def test_crypto_scale_identical_prices_are_flagged(self):
        df = self._frame(close=95_000.25, fill=95_000.25)
        findings = LookaheadBiasAuditor(warmup_periods=0).audit_backtest_timing(df)
        self.assertEqual(
            [f.row_index for f in _of_type(findings, LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION)],
            [5],
        )


class TestIndicatorWarmup(unittest.TestCase):
    def test_nan_indicator_at_signal_bar_is_flagged(self):
        n = 30
        df = pd.DataFrame(
            {
                "open": np.arange(100.0, 100.0 + n),
                "close": np.arange(100.5, 100.5 + n),
                "signal": [0] * n,
            }
        )
        # pandas documents min_periods as defaulting to the window size for an
        # integer window, so rolling(10) is NaN for positions 0..8.
        df["sma_10"] = df["close"].rolling(10).mean()
        df.loc[3, "signal"] = 1
        df.loc[20, "signal"] = -1
        df["fill_price"] = df["open"].shift(-1)

        findings = LookaheadBiasAuditor(warmup_periods=0).audit_backtest_timing(
            df, indicator_cols=["sma_10"]
        )
        unwarmed = _of_type(findings, LookaheadViolationType.INDICATOR_UNWARMED_EXECUTION)
        self.assertEqual([f.row_index for f in unwarmed], [3])
        self.assertIn("sma_10", unwarmed[0].details)

    def test_named_indicator_column_must_exist(self):
        df = pd.DataFrame({"close": [1.0] * 5, "signal": [0] * 5, "fill_price": [1.0] * 5})
        with self.assertRaises(ValueError):
            LookaheadBiasAuditor().audit_backtest_timing(df, indicator_cols=["missing"])


class TestSignalAlignment(unittest.TestCase):
    def setUp(self):
        self.n = 10
        self.df = pd.DataFrame(
            {
                "open": [100.0 + i for i in range(self.n)],
                "close": [100.5 + i for i in range(self.n)],
                "signal": [0, 0, 1, 0, 0, -1, 0, 0, 0, 0],
            }
        )

    def test_shifts_signal_to_next_bar_open(self):
        aligned = LookaheadBiasAuditor.align_signal_execution(self.df)
        self.assertEqual(aligned.loc[2, "executed_signal"], 0)
        self.assertEqual(aligned.loc[3, "executed_signal"], 1)
        self.assertEqual(aligned.loc[6, "executed_signal"], -1)
        self.assertEqual(aligned.loc[3, "fill_price"], self.df.loc[3, "open"])

    def test_fill_price_is_written_only_on_execution_bars(self):
        aligned = LookaheadBiasAuditor.align_signal_execution(self.df)
        priced = aligned.index[aligned["fill_price"].notna()].tolist()
        self.assertEqual(priced, [3, 6])

    def test_aligned_output_audits_clean(self):
        aligned = LookaheadBiasAuditor.align_signal_execution(self.df)
        findings = LookaheadBiasAuditor(warmup_periods=0).audit_backtest_timing(
            aligned, signal_col="executed_signal"
        )
        self.assertEqual(_of_type(findings, LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION), [])

    def test_execution_lag_greater_than_one(self):
        aligned = LookaheadBiasAuditor.align_signal_execution(self.df, execution_lag=3)
        self.assertEqual(aligned.loc[5, "executed_signal"], 1)
        self.assertEqual(aligned.loc[5, "fill_price"], self.df.loc[5, "open"])

    def test_zero_or_negative_lag_is_rejected(self):
        for lag in (0, -1):
            with self.subTest(lag=lag):
                with self.assertRaises(ValueError):
                    LookaheadBiasAuditor.align_signal_execution(self.df, execution_lag=lag)

    def test_panel_shift_does_not_cross_instrument_boundaries(self):
        """
        Regression: an ungrouped positional shift carries the last signal of one
        symbol onto the first bar of the next.
        """
        panel = pd.DataFrame(
            {
                "symbol": ["AAA"] * 4 + ["BBB"] * 4,
                "ts": list(pd.date_range("2024-01-01", periods=4, freq="D")) * 2,
                "open": [10.0, 11.0, 12.0, 13.0, 50.0, 51.0, 52.0, 53.0],
                "signal": [0, 0, 0, 1, 0, 0, 0, 0],
            }
        )
        aligned = LookaheadBiasAuditor.align_signal_execution(
            panel, symbol_col="symbol", timestamp_col="ts"
        )
        # AAA's signal on its last bar has nowhere to execute; BBB must stay flat.
        self.assertEqual(aligned["executed_signal"].tolist(), [0, 0, 0, 0, 0, 0, 0, 0])
        self.assertTrue(aligned["fill_price"].isna().all())

        ungrouped = LookaheadBiasAuditor.align_signal_execution(panel, timestamp_col=None)
        self.assertEqual(ungrouped.loc[4, "executed_signal"], 1)  # the defect, unguarded

    def test_unsorted_frame_is_rejected(self):
        df = self.df.copy()
        df["ts"] = pd.date_range("2024-01-01", periods=self.n, freq="D")
        shuffled = df.iloc[[5, 1, 3, 0, 2, 4, 6, 7, 8, 9]].reset_index(drop=True)
        with self.assertRaises(ValueError):
            LookaheadBiasAuditor.align_signal_execution(shuffled, timestamp_col="ts")

    def test_signal_dtype_is_preserved(self):
        boolean = self.df.copy()
        boolean["signal"] = [False, False, True, False, False, True, False, False, False, False]
        aligned = LookaheadBiasAuditor.align_signal_execution(boolean)
        self.assertTrue(pd.api.types.is_bool_dtype(aligned["executed_signal"]))
        self.assertTrue(bool(aligned.loc[3, "executed_signal"]))

        integer = self.df.copy()
        integer["signal"] = integer["signal"].astype("int64")
        aligned_int = LookaheadBiasAuditor.align_signal_execution(integer)
        self.assertTrue(pd.api.types.is_integer_dtype(aligned_int["executed_signal"]))

    def test_overwriting_an_existing_fill_price_warns(self):
        df = self.df.copy()
        df["fill_price"] = df["close"]
        with self.assertLogs("leak_audit", level=logging.WARNING) as captured:
            LookaheadBiasAuditor.align_signal_execution(df)
        self.assertTrue(any("overwriting" in line for line in captured.output))


class TestCalibration(unittest.TestCase):
    def setUp(self):
        self.n = 20
        self.df = pd.DataFrame(
            {
                "open": [100.0 + i for i in range(self.n)],
                "close": [100.5 + i for i in range(self.n)],
                "signal": [0] * self.n,
            }
        )
        self.df.loc[[6, 11, 15], "signal"] = 1
        self.df["fill_price"] = self.df["open"].shift(-1)
        self.auditor = LookaheadBiasAuditor(warmup_periods=0)

    def test_timing_calibration_detects_every_injected_violation(self):
        self.assertEqual(self.auditor.run_timing_calibration(self.df), 1.0)

    def test_timing_calibration_reports_partial_blindness(self):
        blind = self.df.copy()
        blind.loc[11, "close"] = np.nan  # nothing to compare the injected fill against
        with self.assertLogs("leak_audit", level=logging.WARNING) as captured:
            ratio = self.auditor.run_timing_calibration(blind)
        self.assertAlmostEqual(ratio, 2 / 3)
        self.assertTrue(any("not trustworthy" in line for line in captured.output))

    def test_timing_calibration_refuses_a_frame_with_no_signals(self):
        flat = self.df.copy()
        flat["signal"] = 0
        with self.assertRaises(ValueError):
            self.auditor.run_timing_calibration(flat)

    def test_leak_injection_shifts_target_one_bar_back(self):
        leaked = self.auditor.run_leak_calibration(self.df, target_col="close")
        self.assertIn("leaked_feature", leaked.columns)
        self.assertEqual(leaked.loc[0, "leaked_feature"], self.df.loc[1, "close"])
        self.assertTrue(pd.isna(leaked.loc[self.n - 1, "leaked_feature"]))

    def test_leak_injection_requires_the_target_column(self):
        with self.assertRaises(ValueError):
            self.auditor.run_leak_calibration(self.df, target_col="absent")


class TestIndicatorCausalityFingerprint(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(20240301)
        self.df = pd.DataFrame({"close": 100 + rng.normal(size=200).cumsum()})
        self.auditor = LookaheadBiasAuditor()

    def test_centered_window_is_detected(self):
        df = self.df.copy()
        df["sma"] = df["close"].rolling(9, center=True).mean()
        findings = self.auditor.audit_indicator_causality(df, "close", "sma")
        self.assertEqual(_types(findings), [LookaheadViolationType.CENTERED_ROLLING_WINDOW])
        self.assertIn("center=True", findings[0].details)
        self.assertEqual(findings[0].row_index, FRAME_LEVEL_ROW_INDEX)

    def test_trailing_window_is_not_reported(self):
        df = self.df.copy()
        df["sma"] = df["close"].rolling(9).mean()
        self.assertEqual(self.auditor.audit_indicator_causality(df, "close", "sma"), [])

    def test_rolling_max_centered_window_is_detected(self):
        df = self.df.copy()
        df["donchian_high"] = df["close"].rolling(11, center=True).max()
        findings = self.auditor.audit_indicator_causality(df, "close", "donchian_high")
        self.assertEqual(_types(findings), [LookaheadViolationType.CENTERED_ROLLING_WINDOW])

    def test_negative_shift_is_detected(self):
        df = self.df.copy()
        df["next_close"] = df["close"].shift(-2)
        findings = self.auditor.audit_indicator_causality(df, "close", "next_close")
        self.assertEqual(_types(findings), [LookaheadViolationType.TIMESTAMP_CAUSALITY_BREACH])
        self.assertIn("shift(-2)", findings[0].details)

    def test_centered_window_expected_value_is_independently_derived(self):
        # Hand-checked: for a centred window of 5, position 10 averages close[8:13].
        df = self.df.copy()
        df["sma"] = df["close"].rolling(5, center=True).mean()
        expected = df["close"].iloc[8:13].mean()
        self.assertAlmostEqual(df["sma"].iloc[10], expected)
        self.assertEqual(
            _types(self.auditor.audit_indicator_causality(df, "close", "sma")),
            [LookaheadViolationType.CENTERED_ROLLING_WINDOW],
        )

    def test_unrecognised_construction_returns_no_findings(self):
        # Documented limitation: only two signatures are recognised, so silence
        # here is absence of evidence, not evidence of causality.
        df = self.df.copy()
        df["ewma"] = df["close"].ewm(span=10).mean()
        self.assertEqual(self.auditor.audit_indicator_causality(df, "close", "ewma"), [])

    def test_degenerate_series_is_not_reported_as_a_forward_leak(self):
        # A constant price equals every one of its own shifts, so an unguarded lead
        # search reports a leak that does not exist.
        constant = pd.DataFrame({"close": [5.0] * 100})
        constant["sma"] = constant["close"].rolling(5, center=True).mean()
        self.assertEqual(self.auditor.audit_indicator_causality(constant, "close", "sma"), [])

    def test_verbatim_copy_of_the_price_is_not_a_forward_leak(self):
        df = self.df.copy()
        df["copy"] = df["close"]
        self.assertEqual(self.auditor.audit_indicator_causality(df, "close", "copy"), [])

    def test_invalid_arguments_are_rejected(self):
        df = self.df.copy()
        df["sma"] = df["close"].rolling(5).mean()
        with self.assertRaises(ValueError):
            self.auditor.audit_indicator_causality(df, "close", "sma", max_window=1)
        with self.assertRaises(ValueError):
            self.auditor.audit_indicator_causality(df, "close", "sma", max_lead=0)
        with self.assertRaises(ValueError):
            self.auditor.audit_indicator_causality(df, "close", "absent")


class TestFeatureTimestamps(unittest.TestCase):
    def test_feature_available_after_the_decision_is_a_breach(self):
        findings = audit_feature_timestamps({"feat1": 100, "feat2": 200}, decision_ts=150)
        self.assertEqual(_types(findings), [LookaheadViolationType.TIMESTAMP_CAUSALITY_BREACH])
        self.assertIn("feat2", findings[0].details)
        self.assertEqual(check_feature_timestamps({"feat1": 100, "feat2": 200}, 150), ["feat2"])

    def test_exactly_simultaneous_feature_is_a_breach_by_default(self):
        self.assertEqual(check_feature_timestamps({"bar_close": 150}, 150), ["bar_close"])
        self.assertEqual(
            check_feature_timestamps({"bar_close": 150}, 150, allow_exact_matches=True), []
        )

    def test_datetime_timestamps(self):
        decision = pd.Timestamp("2024-05-01 09:30")
        features = {
            "prev_close": pd.Timestamp("2024-04-30 16:00"),
            "filing": pd.Timestamp("2024-05-01 16:05"),
        }
        self.assertEqual(check_feature_timestamps(features, decision), ["filing"])

    def test_incomparable_timestamps_raise_a_clear_error(self):
        aware = pd.Timestamp("2024-05-01 09:30", tz="UTC")
        with self.assertRaises(ValueError) as ctx:
            check_feature_timestamps({"feat": aware}, pd.Timestamp("2024-05-01 09:30"))
        self.assertIn("not comparable", str(ctx.exception))

    def test_empty_mapping_is_clean(self):
        self.assertEqual(check_feature_timestamps({}, 150), [])


class TestBackwardCompatibility(unittest.TestCase):
    def test_inject_forward_leak_alias(self):
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]})
        leaked = inject_forward_leak(df, "close")
        self.assertEqual(leaked.loc[0, "leaked_feature"], 2.0)
        self.assertTrue(pd.isna(leaked.loc[3, "leaked_feature"]))

    def test_auditor_rejects_invalid_warmup(self):
        for value in (-1, 2.5, "10"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    LookaheadBiasAuditor(warmup_periods=value)


if __name__ == "__main__":
    unittest.main()
