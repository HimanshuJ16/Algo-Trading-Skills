import logging
import unittest

from granularity_advisor import (
    DEFAULT_SESSION_MINUTES_PER_DAY,
    DEFAULT_TRADING_DAYS_PER_YEAR,
    STATUS_APPROVED,
    STATUS_COMPUTE_OVERHEAD,
    STATUS_IN_BAR_PATH_AMBIGUITY,
    STATUS_INSUFFICIENT_RESOLUTION,
    STATUS_OHLC_SEQUENCE_BIAS,
    BacktestGranularityAdvisorEngine,
    BacktestGranularityReport,
    BacktestStrategyProfile,
)


def make_profile(**overrides):
    """A US-equity intraday scalper profile; override one field per test."""
    base = dict(
        strategy_name="Intraday_Momentum_01",
        holding_period="INTRADAY_MINUTES",
        trade_frequency_per_day=25.0,
        has_intraday_stop_loss=True,
        universe_size=500,
        history_years=5.0,
        selected_data_granularity="INTRADAY_1MIN",
    )
    base.update(overrides)
    return BacktestStrategyProfile(**base)


class TestDataFootprintArithmetic(unittest.TestCase):
    """
    Expected values are derived by hand from the published session length, not by
    re-running the module's own formula.

    NYSE Core Trading Session = 09:30-16:00 ET = 390 minutes (nyse.com/markets/hours-calendars).
    252 trading days/year x 5 years = 1,260 days. Universe = 500 symbols.
    """

    def setUp(self):
        self.engine = BacktestGranularityAdvisorEngine()

    def test_one_minute_footprint(self):
        # 1,260 days x 390 bars x 500 symbols = 245,700,000 records.
        # 245,700,000 x 40 B = 9,828,000,000 B; / 2**30 = 9.153 GiB.
        points, gib = self.engine.estimate_data_footprint("INTRADAY_1MIN", 500, 5.0)
        self.assertEqual(points, 245_700_000)
        self.assertAlmostEqual(gib, 9.153, places=3)

    def test_five_minute_footprint(self):
        # 390 / 5 = 78 bars/day -> 1,260 x 78 x 500 = 49,140,000 records x 40 B = 1.831 GiB.
        points, gib = self.engine.estimate_data_footprint("INTRADAY_5MIN", 500, 5.0)
        self.assertEqual(points, 49_140_000)
        self.assertAlmostEqual(gib, 1.831, places=3)

    def test_daily_footprint(self):
        # 1,260 x 1 x 500 = 630,000 records x 48 B = 30,240,000 B = 0.028 GiB.
        points, gib = self.engine.estimate_data_footprint("DAILY_EOD", 500, 5.0)
        self.assertEqual(points, 630_000)
        self.assertAlmostEqual(gib, 0.028, places=3)

    def test_tick_footprint(self):
        # 1,260 x 100,000 x 500 = 63,000,000,000 records x 32 B = 1,877.546 GiB (~1.8 TiB).
        points, gib = self.engine.estimate_data_footprint("TICK_L2", 500, 5.0)
        self.assertEqual(points, 63_000_000_000)
        self.assertAlmostEqual(gib, 1877.546, places=3)

    def test_continuous_venue_calendar_is_not_a_us_equity_one(self):
        # A crypto venue: 1,440 minutes/day, 365 days/year, one symbol, 5 years.
        # 365 x 5 x 1,440 x 1 = 2,628,000 one-minute bars.
        points, _ = self.engine.estimate_data_footprint(
            "INTRADAY_1MIN", 1, 5.0,
            session_minutes_per_day=1440, trading_days_per_year=365)
        self.assertEqual(points, 2_628_000)
        # The US-equity defaults would understate this by more than 5x.
        us_points, _ = self.engine.estimate_data_footprint("INTRADAY_1MIN", 1, 5.0)
        # 252 x 5 x 390 x 1 = 491,400 bars on the US equity default calendar.
        self.assertEqual(us_points, 491_400)
        self.assertGreater(points / us_points, 5.0)

    def test_partial_trailing_bar_is_counted(self):
        # A 62-minute session yields ceil(62/5) = 13 five-minute bars, not 12.
        self.assertEqual(
            self.engine.records_per_symbol_per_day("INTRADAY_5MIN", session_minutes_per_day=62),
            13,
        )

    def test_compression_ratio_divides_the_estimate(self):
        _, uncompressed = self.engine.estimate_data_footprint("INTRADAY_1MIN", 500, 5.0)
        _, compressed = self.engine.estimate_data_footprint(
            "INTRADAY_1MIN", 500, 5.0, compression_ratio=4.0)
        self.assertAlmostEqual(uncompressed, 9.153, places=3)
        self.assertAlmostEqual(compressed, 2.288, places=3)

    def test_measured_record_width_overrides_the_default(self):
        engine = BacktestGranularityAdvisorEngine(bytes_per_record={"DAILY_EOD": 96})
        _, doubled = engine.estimate_data_footprint("DAILY_EOD", 500, 5.0)
        _, default = self.engine.estimate_data_footprint("DAILY_EOD", 500, 5.0)
        self.assertAlmostEqual(doubled, round(default * 2, 3), places=3)

    def test_defaults_match_the_documented_us_equity_session(self):
        self.assertEqual(DEFAULT_SESSION_MINUTES_PER_DAY, 390)
        self.assertEqual(DEFAULT_TRADING_DAYS_PER_YEAR, 252)


class TestFootprintInputValidation(unittest.TestCase):
    """Invalid sizing inputs must raise, not silently produce a plausible number."""

    def setUp(self):
        self.engine = BacktestGranularityAdvisorEngine()

    def test_unknown_granularity_raises_instead_of_defaulting_to_daily(self):
        # Regression: an unrecognized resolution previously fell through to the
        # DAILY_EOD branch and was sized as one row per symbol per day.
        with self.assertRaises(ValueError):
            self.engine.estimate_data_footprint("INTRADAY_1_MIN", 500, 5.0)

    def test_granularity_is_case_and_whitespace_insensitive(self):
        self.assertEqual(
            self.engine.estimate_data_footprint("  daily_eod ", 500, 5.0),
            self.engine.estimate_data_footprint("DAILY_EOD", 500, 5.0),
        )

    def test_non_positive_universe_raises(self):
        for bad in (0, -500):
            with self.subTest(universe_size=bad), self.assertRaises(ValueError):
                self.engine.estimate_data_footprint("DAILY_EOD", bad, 5.0)

    def test_non_positive_or_non_finite_history_raises(self):
        for bad in (0.0, -5.0, float("nan"), float("inf")):
            with self.subTest(history_years=bad), self.assertRaises(ValueError):
                self.engine.estimate_data_footprint("DAILY_EOD", 500, bad)

    def test_impossible_session_length_raises(self):
        with self.assertRaises(ValueError):
            self.engine.estimate_data_footprint(
                "INTRADAY_1MIN", 500, 5.0, session_minutes_per_day=1441)

    def test_compression_ratio_below_one_raises(self):
        with self.assertRaises(ValueError):
            self.engine.estimate_data_footprint(
                "DAILY_EOD", 500, 5.0, compression_ratio=0.5)


class TestInBarPathAmbiguity(unittest.TestCase):
    """
    The ambiguity is a property of bars, not of daily bars. Only tick/L2 data carries
    the path that decides whether the stop or the target was reached first.
    """

    def setUp(self):
        self.engine = BacktestGranularityAdvisorEngine()

    def test_daily_bars_with_intraday_stop_is_the_severe_case(self):
        report = self.engine.advise_backtest_granularity(
            make_profile(strategy_name="Flawed_EOD_Scalper",
                         trade_frequency_per_day=15.0,
                         selected_data_granularity="DAILY_EOD"))
        self.assertEqual(report.status, STATUS_OHLC_SEQUENCE_BIAS)
        self.assertTrue(report.has_ohlc_sequence_bias)
        self.assertEqual(report.recommended_granularity, "INTRADAY_1MIN")

    def test_minute_bars_do_not_clear_the_ambiguity(self):
        # Regression: a 1-minute + intraday-stop config with no declared tie-break was
        # previously reported GRANULARITY_APPROVED, implying the bias had been removed.
        report = self.engine.advise_backtest_granularity(make_profile())
        self.assertEqual(report.status, STATUS_IN_BAR_PATH_AMBIGUITY)
        self.assertTrue(report.has_ohlc_sequence_bias)
        self.assertEqual(report.intrabar_fill_assumption, "UNSPECIFIED")

    def test_optimistic_tie_break_is_flagged(self):
        report = self.engine.advise_backtest_granularity(
            make_profile(intrabar_fill_assumption="OPTIMISTIC"))
        self.assertEqual(report.status, STATUS_IN_BAR_PATH_AMBIGUITY)

    def test_pessimistic_tie_break_is_approved_but_still_flagged_as_biased(self):
        report = self.engine.advise_backtest_granularity(
            make_profile(intrabar_fill_assumption="pessimistic"))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.has_ohlc_sequence_bias)
        self.assertIn("conservative bound", report.audit_notes)

    def test_tick_data_resolves_the_ordering(self):
        report = self.engine.advise_backtest_granularity(
            make_profile(trade_frequency_per_day=60.0,
                         selected_data_granularity="TICK_L2"))
        self.assertFalse(report.has_ohlc_sequence_bias)
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_no_stop_loss_means_no_ambiguity_flag(self):
        report = self.engine.advise_backtest_granularity(
            make_profile(has_intraday_stop_loss=False))
        self.assertFalse(report.has_ohlc_sequence_bias)
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_bias_warning_is_logged(self):
        with self.assertLogs("granularity_advisor", level=logging.WARNING) as captured:
            self.engine.advise_backtest_granularity(
                make_profile(selected_data_granularity="DAILY_EOD"))
        self.assertTrue(any("OHLC SEQUENCE BIAS" in line for line in captured.output))


class TestResolutionMatching(unittest.TestCase):

    def setUp(self):
        self.engine = BacktestGranularityAdvisorEngine()

    def test_minutes_holding_period_on_daily_bars_is_under_resolved(self):
        # Regression: a minutes-long holding period simulated on daily bars was
        # previously approved whenever no stop-loss was declared, even though entry
        # and exit fall inside a single bar.
        report = self.engine.advise_backtest_granularity(
            make_profile(has_intraday_stop_loss=False,
                         selected_data_granularity="DAILY_EOD"))
        self.assertEqual(report.status, STATUS_INSUFFICIENT_RESOLUTION)
        self.assertEqual(report.recommended_granularity, "INTRADAY_1MIN")

    def test_swing_strategy_on_tick_data_is_flagged_as_overhead(self):
        # Regression: only POSITIONAL_MONTHS was previously checked, so a swing
        # strategy on ~1.8 TiB of tick data came back approved.
        report = self.engine.advise_backtest_granularity(
            make_profile(holding_period="SWING_DAYS",
                         trade_frequency_per_day=0.5,
                         has_intraday_stop_loss=False,
                         selected_data_granularity="TICK_L2"))
        self.assertEqual(report.status, STATUS_COMPUTE_OVERHEAD)
        self.assertEqual(report.recommended_granularity, "DAILY_EOD")
        self.assertEqual(report.data_volume_ratio_vs_recommended, 100_000.0)

    def test_positional_strategy_on_tick_data_is_flagged_as_overhead(self):
        report = self.engine.advise_backtest_granularity(
            make_profile(holding_period="POSITIONAL_MONTHS",
                         trade_frequency_per_day=0.1,
                         has_intraday_stop_loss=False,
                         selected_data_granularity="TICK_L2"))
        self.assertEqual(report.status, STATUS_COMPUTE_OVERHEAD)

    def test_portfolio_trade_count_does_not_override_holding_period(self):
        # Regression: 25 trades/day forced INTRADAY_1MIN even on a months-long hold,
        # because frequency was tested before the holding period.
        report = self.engine.advise_backtest_granularity(
            make_profile(holding_period="POSITIONAL_MONTHS",
                         trade_frequency_per_day=25.0,
                         has_intraday_stop_loss=False,
                         selected_data_granularity="DAILY_EOD"))
        self.assertEqual(report.recommended_granularity, "DAILY_EOD")
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(any("per-instrument rate" in w for w in report.profile_warnings))

    def test_positional_strategy_with_intraday_stop_needs_intraday_data(self):
        report = self.engine.advise_backtest_granularity(
            make_profile(holding_period="POSITIONAL_MONTHS",
                         trade_frequency_per_day=0.1,
                         has_intraday_stop_loss=True,
                         selected_data_granularity="DAILY_EOD"))
        self.assertEqual(report.recommended_granularity, "INTRADAY_5MIN")
        self.assertEqual(report.status, STATUS_OHLC_SEQUENCE_BIAS)

    def test_high_frequency_intraday_strategy_is_pushed_to_tick(self):
        report = self.engine.advise_backtest_granularity(
            make_profile(trade_frequency_per_day=60.0,
                         selected_data_granularity="TICK_L2"))
        self.assertEqual(report.recommended_granularity, "TICK_L2")

    def test_report_carries_both_footprints_and_their_ratio(self):
        report = self.engine.advise_backtest_granularity(
            make_profile(intrabar_fill_assumption="PESSIMISTIC"))
        self.assertIsInstance(report, BacktestGranularityReport)
        self.assertEqual(report.selected_granularity, "INTRADAY_1MIN")
        self.assertEqual(report.estimated_data_points_count, 245_700_000)
        self.assertAlmostEqual(report.estimated_storage_size_gb, 9.153, places=3)
        self.assertEqual(report.recommended_data_points_count, 245_700_000)
        self.assertEqual(report.data_volume_ratio_vs_recommended, 1.0)


class TestProfileValidation(unittest.TestCase):
    """An unusable profile must raise rather than return a confident approval."""

    def setUp(self):
        self.engine = BacktestGranularityAdvisorEngine()

    def test_unknown_granularity_no_longer_approves_a_biased_config(self):
        # Regression, and the worst of the old failure modes: a misspelled resolution
        # skipped the DAILY_EOD equality test, so a stop-loss strategy on daily data
        # was reported GRANULARITY_APPROVED with a daily-sized footprint.
        with self.assertRaises(ValueError):
            self.engine.advise_backtest_granularity(
                make_profile(selected_data_granularity="DAILY"))

    def test_unknown_holding_period_raises(self):
        with self.assertRaises(ValueError):
            self.engine.advise_backtest_granularity(make_profile(holding_period="SCALP"))

    def test_unknown_fill_assumption_raises(self):
        with self.assertRaises(ValueError):
            self.engine.advise_backtest_granularity(
                make_profile(intrabar_fill_assumption="STOP_FIRST"))

    def test_negative_trade_frequency_raises(self):
        with self.assertRaises(ValueError):
            self.engine.advise_backtest_granularity(make_profile(trade_frequency_per_day=-1.0))

    def test_empty_strategy_name_raises(self):
        with self.assertRaises(ValueError):
            self.engine.advise_backtest_granularity(make_profile(strategy_name="   "))

    def test_negative_universe_raises(self):
        with self.assertRaises(ValueError):
            self.engine.advise_backtest_granularity(make_profile(universe_size=-500))

    def test_non_bool_stop_loss_flag_raises(self):
        with self.assertRaises(TypeError):
            self.engine.advise_backtest_granularity(make_profile(has_intraday_stop_loss="yes"))


if __name__ == '__main__':
    unittest.main()
