import logging
import math
import threading
import unittest

from portfolio_level_stop_loss_independent_of_strategy_stops import (
    NavValuationMode,
    PortfolioLevelStopLossIndependentOfStrategyStops,
    PortfolioLevelStopLossIndependentOfStrategyStopsConfig,
    PortfolioState,
    PortfolioStopReport,
    PortfolioStopStatus,
    StrategyPosition,
)


def setUpModule():
    # The engine logs every breach at CRITICAL by design; silence it so the suite output
    # shows test results rather than a wall of emergency notices.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def _engine(**cfg):
    return PortfolioLevelStopLossIndependentOfStrategyStops(
        PortfolioLevelStopLossIndependentOfStrategyStopsConfig(**cfg)
    )


class TestPortfolioLevelStopLossIndependentOfStrategyStops(unittest.TestCase):

    def test_execute_true_legacy(self):
        config = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(enabled=True)
        engine = PortfolioLevelStopLossIndependentOfStrategyStops(config)
        self.assertTrue(engine.execute())

    def test_execute_false_legacy(self):
        config = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(enabled=False)
        engine = PortfolioLevelStopLossIndependentOfStrategyStops(config)
        self.assertFalse(engine.execute())

    def test_healthy_portfolio_no_breach(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        positions = [
            StrategyPosition("Trend_1", "AAPL", 100, 150.0, 500.0),
            StrategyPosition("MeanRev_2", "MSFT", 50, 300.0, 200.0)
        ]
        # Cash $970,000 + Pos Val $30,000 = $1,000,000 NAV (SOD Equity $1,000,000) -> 0% DD
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                               current_cash=970000.0, open_positions=positions)

        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.status, "PORTFOLIO_NAV_HEALTHY")
        self.assertFalse(report.is_trading_locked)
        self.assertEqual(report.positions_to_flatten_count, 0)
        self.assertEqual(report.current_nav, 1000000.0)
        self.assertEqual(report.daily_drawdown_pct, 0.0)
        self.assertEqual(report.peak_drawdown_pct, 0.0)

    def test_daily_drawdown_breach_triggers_flattening(self):
        # Max daily drawdown 5%.
        # SOD Equity $1,000,000 -> Max acceptable NAV $950,000.
        # Current NAV $940,000 (Daily DD 6.0%) -> BREACH!
        config = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(max_daily_drawdown_pct=0.05)
        engine = PortfolioLevelStopLossIndependentOfStrategyStops(config)

        positions = [
            StrategyPosition("Trend_1", "AAPL", 100, 100.0, -10000.0),
            StrategyPosition("MeanRev_2", "TSLA", 200, 150.0, -50000.0)
        ]
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1050000.0,
                               current_cash=900000.0, open_positions=positions)

        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.status, "DAILY_DRAWDOWN_BREACH_FLATTEN")
        self.assertTrue(report.is_daily_breached)
        self.assertTrue(report.is_trading_locked)
        self.assertEqual(report.positions_to_flatten_count, 2)
        # Independently derived: (1,000,000 - 940,000) / 1,000,000 = 0.06
        self.assertAlmostEqual(report.daily_drawdown_pct, 0.06, places=9)
        # Peak DD against $1,050,000: (1,050,000 - 940,000) / 1,050,000 = 0.104761904...
        self.assertAlmostEqual(report.peak_drawdown_pct, 110000.0 / 1050000.0, places=6)
        self.assertTrue(report.is_peak_breached)

    def test_peak_breach_without_daily_breach(self):
        # NAV $1,070,000 is ABOVE start-of-day equity (no daily drawdown at all) but
        # 10.8333% below the $1,200,000 high-water mark -> peak breach only.
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        positions = [StrategyPosition("Carry_1", "GLD", 100, 700.0, -5000.0)]
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1200000.0,
                               current_cash=1000000.0, open_positions=positions)

        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.status, "PEAK_DRAWDOWN_BREACH_FLATTEN")
        self.assertFalse(report.is_daily_breached)
        self.assertTrue(report.is_peak_breached)
        self.assertEqual(report.daily_drawdown_pct, 0.0)
        self.assertAlmostEqual(report.peak_drawdown_pct, 130000.0 / 1200000.0, places=6)
        self.assertEqual(report.positions_to_flatten_count, 1)

    def test_exact_threshold_breaches_and_one_cent_below_does_not(self):
        # The documented rule is DD >= limit, so an exact 5.00% drawdown must breach.
        at_limit = _engine().evaluate_portfolio_stop(
            PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                           current_cash=950000.0)
        )
        self.assertEqual(at_limit.status, "DAILY_DRAWDOWN_BREACH_FLATTEN")
        self.assertAlmostEqual(at_limit.daily_drawdown_pct, 0.05, places=9)

        just_under = _engine().evaluate_portfolio_stop(
            PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                           current_cash=950001.0)
        )
        self.assertEqual(just_under.status, "PORTFOLIO_NAV_HEALTHY")
        self.assertFalse(just_under.is_trading_locked)

    def test_short_position_reduces_market_value(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        positions = [StrategyPosition("Pairs_1", "XOM", -100, 1000.0, 0.0)]
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                               current_cash=1100000.0, open_positions=positions)
        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.current_nav, 1000000.0)
        self.assertEqual(report.status, "PORTFOLIO_NAV_HEALTHY")


class TestFailClosedBehaviour(unittest.TestCase):
    """Regression tests: every case here previously returned PORTFOLIO_NAV_HEALTHY."""

    def test_nan_mark_halts_instead_of_reporting_healthy(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        positions = [StrategyPosition("Trend_1", "AAPL", 100, float("nan"), -1000.0)]
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                               current_cash=500000.0, open_positions=positions)

        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.status, "HALTED_INVALID_INPUT")
        self.assertTrue(report.is_trading_locked)
        # A halt on unevaluable data must not market-flatten the book.
        self.assertEqual(report.positions_to_flatten_count, 0)
        self.assertTrue(engine.is_trading_locked)

    def test_infinite_cash_halts(self):
        report = _engine().evaluate_portfolio_stop(
            PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                           current_cash=float("inf"))
        )
        self.assertEqual(report.status, "HALTED_INVALID_INPUT")
        self.assertTrue(report.is_trading_locked)

    def test_non_positive_start_of_day_equity_halts_rather_than_being_clamped(self):
        for bad_equity in (0.0, -1000.0, float("nan")):
            with self.subTest(bad_equity=bad_equity):
                report = _engine().evaluate_portfolio_stop(
                    PortfolioState(start_of_day_equity=bad_equity, peak_equity=1000000.0,
                                   current_cash=900000.0)
                )
                self.assertEqual(report.status, "HALTED_INVALID_INPUT")
                self.assertTrue(report.is_trading_locked)

    def test_non_positive_peak_equity_halts(self):
        report = _engine().evaluate_portfolio_stop(
            PortfolioState(start_of_day_equity=1000000.0, peak_equity=0.0,
                           current_cash=990000.0)
        )
        self.assertEqual(report.status, "HALTED_INVALID_INPUT")

    def test_nan_capital_flow_halts(self):
        report = _engine().evaluate_portfolio_stop(
            PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                           current_cash=990000.0,
                           capital_flow_since_sod=float("nan"))
        )
        self.assertEqual(report.status, "HALTED_INVALID_INPUT")

    def test_stale_marks_halt_when_gate_enabled(self):
        engine = _engine(max_price_staleness_s=5.0)
        positions = [StrategyPosition("Trend_1", "AAPL", 100, 150.0, 0.0, price_epoch_s=990.0)]
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                               current_cash=985000.0, open_positions=positions,
                               as_of_epoch_s=1000.0)  # mark is 10s old vs a 5s limit
        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.status, "HALTED_STALE_PRICES")
        self.assertTrue(report.is_trading_locked)
        self.assertEqual(report.positions_to_flatten_count, 0)

    def test_fresh_marks_pass_the_staleness_gate(self):
        engine = _engine(max_price_staleness_s=5.0)
        positions = [StrategyPosition("Trend_1", "AAPL", 100, 150.0, 0.0, price_epoch_s=998.0)]
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                               current_cash=985000.0, open_positions=positions,
                               as_of_epoch_s=1000.0)
        self.assertEqual(engine.evaluate_portfolio_stop(state).status, "PORTFOLIO_NAV_HEALTHY")

    def test_missing_timestamps_halt_when_gate_enabled(self):
        engine = _engine(max_price_staleness_s=5.0)
        positions = [StrategyPosition("Trend_1", "AAPL", 100, 150.0, 0.0)]
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                               current_cash=985000.0, open_positions=positions)
        self.assertEqual(engine.evaluate_portfolio_stop(state).status, "HALTED_STALE_PRICES")

    def test_staleness_gate_off_by_default(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        positions = [StrategyPosition("Trend_1", "AAPL", 100, 150.0, 0.0)]
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                               current_cash=985000.0, open_positions=positions)
        self.assertEqual(engine.evaluate_portfolio_stop(state).status, "PORTFOLIO_NAV_HEALTHY")


class TestConfigValidation(unittest.TestCase):

    def test_percentage_point_limits_are_rejected(self):
        # `5` meaning "5%" would read as 500% and silently disable the breaker.
        for bad in (5, 1.5, 0.0, -0.05, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    PortfolioLevelStopLossIndependentOfStrategyStopsConfig(
                        max_daily_drawdown_pct=bad)
                with self.assertRaises(ValueError):
                    PortfolioLevelStopLossIndependentOfStrategyStopsConfig(
                        max_peak_drawdown_pct=bad)

    def test_limit_of_one_is_accepted(self):
        cfg = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(
            max_daily_drawdown_pct=1.0, max_peak_drawdown_pct=1.0)
        self.assertEqual(cfg.max_daily_drawdown_pct, 1.0)

    def test_invalid_valuation_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            PortfolioLevelStopLossIndependentOfStrategyStopsConfig(
                nav_valuation_mode="CASH_PLUS_MAGIC")

    def test_valuation_mode_accepts_its_string_value(self):
        cfg = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(
            nav_valuation_mode="CASH_PLUS_UNREALIZED_PNL")
        self.assertIs(cfg.nav_valuation_mode, NavValuationMode.CASH_PLUS_UNREALIZED_PNL)

    def test_invalid_staleness_limit_is_rejected(self):
        for bad in (0.0, -1.0, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    PortfolioLevelStopLossIndependentOfStrategyStopsConfig(
                        max_price_staleness_s=bad)


class TestNavValuationMode(unittest.TestCase):
    """A margined book valued as cash + notional never breaches; valued correctly it does."""

    # $1,000,000 cash, one futures position: 100 contracts at $5,000 (=$500,000 notional)
    # sitting on $60,000 of unrealized loss. True account equity is $940,000 -> 6% daily DD.
    POSITIONS = [StrategyPosition("Futures_1", "ESZ5", 100, 5000.0, -60000.0)]
    STATE = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                           current_cash=1000000.0, open_positions=POSITIONS)

    def test_margined_book_breaches_under_unrealized_pnl_mode(self):
        engine = _engine(nav_valuation_mode=NavValuationMode.CASH_PLUS_UNREALIZED_PNL)
        report = engine.evaluate_portfolio_stop(self.STATE)
        self.assertEqual(report.current_nav, 940000.0)
        self.assertAlmostEqual(report.daily_drawdown_pct, 0.06, places=9)
        self.assertEqual(report.status, "DAILY_DRAWDOWN_BREACH_FLATTEN")

    def test_market_value_mode_would_treat_notional_as_equity(self):
        # Documents the hazard the mode flag exists to prevent: the same losing futures book
        # reports NAV $1,500,000 and the stop never fires.
        engine = _engine(nav_valuation_mode=NavValuationMode.CASH_PLUS_MARKET_VALUE)
        report = engine.evaluate_portfolio_stop(self.STATE)
        self.assertEqual(report.current_nav, 1500000.0)
        self.assertEqual(report.status, "PORTFOLIO_NAV_HEALTHY")


class TestCapitalFlows(unittest.TestCase):

    def test_settled_withdrawal_does_not_trip_the_stop(self):
        # $100,000 withdrawn from a $1,000,000 book that has made no P&L. NAV is $900,000,
        # which a naive check reads as a 10% drawdown and liquidates a healthy portfolio.
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                               current_cash=900000.0,
                               capital_flow_since_sod=-100000.0,
                               capital_flow_since_peak=-100000.0)
        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.status, "PORTFOLIO_NAV_HEALTHY")
        self.assertEqual(report.current_nav, 900000.0)
        self.assertEqual(report.nav_for_drawdown, 1000000.0)
        self.assertEqual(report.daily_drawdown_pct, 0.0)

    def test_deposit_does_not_mask_a_real_drawdown(self):
        # $200,000 deposited into a book that then lost $60,000. NAV $1,140,000 looks like a
        # gain; net of the deposit it is a 6% loss and must breach.
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                               current_cash=1140000.0,
                               capital_flow_since_sod=200000.0,
                               capital_flow_since_peak=200000.0)
        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.nav_for_drawdown, 940000.0)
        self.assertAlmostEqual(report.daily_drawdown_pct, 0.06, places=9)
        self.assertEqual(report.status, "DAILY_DRAWDOWN_BREACH_FLATTEN")


class TestLockoutLatch(unittest.TestCase):

    BREACH_STATE = PortfolioState(
        start_of_day_equity=1000000.0, peak_equity=1000000.0, current_cash=900000.0,
        open_positions=[StrategyPosition("Trend_1", "AAPL", 100, 100.0, -10000.0)])
    RECOVERED_STATE = PortfolioState(
        start_of_day_equity=1000000.0, peak_equity=1000000.0, current_cash=1000000.0)

    def test_lockout_survives_nav_recovery(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        first = engine.evaluate_portfolio_stop(self.BREACH_STATE)
        self.assertEqual(first.status, "DAILY_DRAWDOWN_BREACH_FLATTEN")
        self.assertFalse(first.is_latched)
        self.assertEqual(first.positions_to_flatten_count, 1)

        second = engine.evaluate_portfolio_stop(self.RECOVERED_STATE)
        self.assertEqual(second.status, "DAILY_DRAWDOWN_BREACH_FLATTEN")
        self.assertTrue(second.is_trading_locked)
        self.assertTrue(second.is_latched)
        self.assertFalse(second.is_daily_breached)   # this evaluation is clean...
        self.assertTrue(engine.is_trading_locked)    # ...but the lockout stands.

    def test_flatten_is_requested_once_not_on_every_poll(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        first = engine.evaluate_portfolio_stop(self.BREACH_STATE)
        second = engine.evaluate_portfolio_stop(self.BREACH_STATE)
        self.assertEqual(first.positions_to_flatten_count, 1)
        self.assertEqual(second.positions_to_flatten_count, 0)

    def test_concurrent_evaluations_latch_exactly_once(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        reports = []
        barrier = threading.Barrier(8)

        def run():
            barrier.wait()
            reports.append(engine.evaluate_portfolio_stop(self.BREACH_STATE))

        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        flatten_requests = [r for r in reports if r.positions_to_flatten_count > 0]
        self.assertEqual(len(flatten_requests), 1, "exactly one liquidation cascade expected")
        self.assertTrue(all(r.is_trading_locked for r in reports))

    def test_breach_after_a_fail_closed_halt_still_requests_the_flatten(self):
        # A feed hiccup halts first (no flatten, by design). When usable data returns and
        # shows a real breach, the liquidation must still be requested - the earlier halt
        # must not swallow it.
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        halt = engine.evaluate_portfolio_stop(PortfolioState(
            start_of_day_equity=1000000.0, peak_equity=1000000.0, current_cash=float("nan")))
        self.assertEqual(halt.status, "HALTED_INVALID_INPUT")
        self.assertEqual(halt.positions_to_flatten_count, 0)

        breach = engine.evaluate_portfolio_stop(self.BREACH_STATE)
        self.assertEqual(breach.status, "DAILY_DRAWDOWN_BREACH_FLATTEN")
        self.assertEqual(breach.positions_to_flatten_count, 1)
        self.assertFalse(breach.is_latched)
        self.assertEqual(engine.latched_status, "DAILY_DRAWDOWN_BREACH_FLATTEN")

    def test_halt_does_not_downgrade_an_existing_breach_latch(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        engine.evaluate_portfolio_stop(self.BREACH_STATE)
        engine.evaluate_portfolio_stop(PortfolioState(
            start_of_day_equity=1000000.0, peak_equity=1000000.0, current_cash=float("nan")))
        self.assertEqual(engine.latched_status, "DAILY_DRAWDOWN_BREACH_FLATTEN")

    def test_auto_flatten_disabled_still_locks(self):
        engine = _engine(auto_flatten_on_breach=False)
        report = engine.evaluate_portfolio_stop(self.BREACH_STATE)
        self.assertTrue(report.is_trading_locked)
        self.assertTrue(report.is_daily_breached)
        self.assertEqual(report.positions_to_flatten_count, 0)

    def test_disabled_engine_reports_disabled_and_never_locks(self):
        engine = _engine(enabled=False)
        report = engine.evaluate_portfolio_stop(self.BREACH_STATE)
        self.assertEqual(report.status, "ENGINE_DISABLED")
        self.assertFalse(report.is_trading_locked)
        self.assertFalse(engine.is_trading_locked)


class TestHumanReEnableGate(unittest.TestCase):

    BREACH_STATE = TestLockoutLatch.BREACH_STATE
    RECOVERED_STATE = TestLockoutLatch.RECOVERED_STATE

    def _locked_engine(self, **cfg):
        engine = _engine(**cfg)
        engine.evaluate_portfolio_stop(self.BREACH_STATE)
        self.assertTrue(engine.is_trading_locked)
        return engine

    def test_blank_identity_or_reason_is_refused_and_audited(self):
        engine = self._locked_engine()
        self.assertFalse(engine.human_re_enable("   ", "post-mortem complete"))
        self.assertFalse(engine.human_re_enable("risk.lead", ""))
        self.assertTrue(engine.is_trading_locked)
        self.assertEqual(len(engine.re_enable_log), 2)
        self.assertTrue(all(not e.granted and e.rejection_reason for e in engine.re_enable_log))

    def test_unlisted_operator_is_refused_when_roster_configured(self):
        engine = self._locked_engine(authorized_operators=("risk.lead",))
        self.assertFalse(engine.human_re_enable("junior.dev", "looks fine to me"))
        self.assertTrue(engine.is_trading_locked)
        self.assertTrue(engine.human_re_enable("risk.lead", "post-mortem complete"))
        self.assertFalse(engine.is_trading_locked)

    def test_granted_re_enable_is_audited_with_the_cleared_status(self):
        engine = self._locked_engine()
        self.assertTrue(engine.human_re_enable("risk.lead", "post-mortem complete"))
        entry = engine.re_enable_log[-1]
        self.assertTrue(entry.granted)
        self.assertEqual(entry.cleared_status, "DAILY_DRAWDOWN_BREACH_FLATTEN")
        self.assertIsNone(entry.rejection_reason)
        self.assertIsNone(engine.latched_status)

    def test_re_enable_when_not_locked_is_refused(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        self.assertFalse(engine.human_re_enable("risk.lead", "just in case"))
        self.assertEqual(len(engine.re_enable_log), 1)

    def test_re_enable_clears_the_latch_not_the_breach(self):
        # Resuming while the portfolio is still under water must re-latch immediately -
        # this is the peak-drawdown trap: the operator has to re-baseline peak_equity.
        engine = self._locked_engine()
        self.assertTrue(engine.human_re_enable("risk.lead", "believed transient"))
        report = engine.evaluate_portfolio_stop(self.BREACH_STATE)
        self.assertEqual(report.status, "DAILY_DRAWDOWN_BREACH_FLATTEN")
        self.assertTrue(engine.is_trading_locked)

    def test_re_enable_then_healthy_state_stays_unlocked(self):
        engine = self._locked_engine()
        self.assertTrue(engine.human_re_enable("risk.lead", "post-mortem complete"))
        report = engine.evaluate_portfolio_stop(self.RECOVERED_STATE)
        self.assertEqual(report.status, "PORTFOLIO_NAV_HEALTHY")
        self.assertFalse(report.is_trading_locked)


class TestReportContract(unittest.TestCase):

    def test_report_fields_are_finite_and_typed(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        report = engine.evaluate_portfolio_stop(
            PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0,
                           current_cash=980000.0))
        self.assertIsInstance(report, PortfolioStopReport)
        self.assertTrue(math.isfinite(report.current_nav))
        self.assertTrue(math.isfinite(report.daily_drawdown_pct))
        self.assertIsInstance(report.status, str)
        self.assertIsNotNone(report.evaluated_at)
        self.assertIn(report.status, {s.value for s in PortfolioStopStatus})


if __name__ == '__main__':
    unittest.main()
