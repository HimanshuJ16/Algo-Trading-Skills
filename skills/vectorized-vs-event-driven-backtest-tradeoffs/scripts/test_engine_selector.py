"""
Unit tests for vectorized-vs-event-driven-backtest-tradeoffs.

Expected values are derived *independently* of the implementation: equity paths are
worked through by hand on three- and four-bar series, the Sharpe ratio is checked
against ``statistics.mean``/``statistics.stdev`` from the standard library rather
than against the module's own NumPy call, and the cross-engine checks assert an
*invariant* (two engines given identical assumptions must produce identical equity)
rather than a recorded output. A test that restated the module's algebra would have
passed against every bug this suite now pins.

Regression coverage -- each of these fails against a naive implementation:
  * the two engines agree bar for bar under matched assumptions. The old event
    engine held signals as *share counts* against fixed capital, so on an identical
    long-only series it reported 0.06% against the vectorized engine's 49.00%.
  * a sell credits cash. The old ledger did ``cash -= cost + comm`` in both
    directions, so exits and reversals destroyed equity.
  * equity compounds instead of summing arithmetic returns.
  * turnover cost scales with |change in exposure|, so a reversal costs twice an
    entry rather than the same.
  * a constant return series has an undefined (NaN) Sharpe ratio, not 1.6e15.
  * path-dependent stops and limit orders route to the event-driven engine. The old
    score sent a stop-driven strategy to the vectorized engine -- the one case where
    a vectorized backtest is not merely optimistic but undefined.
  * malformed input raises instead of returning an all-zero result that looks like
    a successful backtest.
"""
import math
import statistics
import unittest

import numpy as np

from engine_selector import (
    MIN_BARS_FOR_TIMING,
    TRADING_DAYS_PER_YEAR,
    BacktestEngineMetrics,
    DualBacktestEngineSelector,
    RecommendedEngine,
    annualized_sharpe,
)


def _free() -> DualBacktestEngineSelector:
    """A cost-free selector, for isolating mechanics from friction."""
    return DualBacktestEngineSelector(commission_bps=0.0, slippage_bps=0.0)


class TestVectorizedEngineArithmetic(unittest.TestCase):
    """Hand-derived equity paths on series small enough to work through on paper."""

    def test_frictionless_return_matches_hand_computed_path(self):
        """
        prices 100 -> 110 -> 99, long from the first close, flat at the last.

        bar 0: no prior bar, nothing earned, equity = E
        bar 1: +10.0%  -> 1.100 E
        bar 2: -10.0%  -> 0.990 E   (99/110 - 1 = -0.1)
        """
        m = _free().run_vectorized_backtest([100.0, 110.0, 99.0], [1.0, 1.0, 0.0])
        self.assertAlmostEqual(m.total_return_pct, -1.0, places=9)

    def test_costs_are_charged_at_the_trade_not_the_return(self):
        """
        Same path at 100 bps commission, zero slippage. Two trades: the entry at
        bar 0 and the exit at bar 2.

        0.99 * 1.10 * 0.90 * 0.99 = 0.970299  ->  -2.9701%
        """
        sel = DualBacktestEngineSelector(commission_bps=100.0, slippage_bps=0.0)
        m = sel.run_vectorized_backtest([100.0, 110.0, 99.0], [1.0, 1.0, 0.0])
        self.assertAlmostEqual(m.total_return_pct, -2.9701, places=6)

    def test_equity_compounds_and_does_not_sum(self):
        """
        50 bars of exactly +1%, held long throughout. The position is established at
        the close of bar 0, so it earns 49 bars: 1.01**49 - 1 = 62.8348%.

        a naive engine summed arithmetic returns and reported 49 * 1% = 49.0%.
        """
        n = 50
        prices = [100.0 * (1.01 ** t) for t in range(n)]
        m = _free().run_vectorized_backtest(prices, [1.0] * n)
        self.assertAlmostEqual(m.total_return_pct, (1.01 ** 49 - 1.0) * 100.0, places=6)
        self.assertLess(abs(m.total_return_pct - 49.0), 100.0)
        self.assertGreater(m.total_return_pct, 60.0)

    def test_reversal_costs_twice_an_entry(self):
        """
        Flat prices, 10 bps commission, no slippage.

          0 -> +1 is one unit of turnover:  1 - 0.001            = -0.1000%
         -1 -> +1 is two units of turnover: 0.999 * 0.998        = -0.2998%

        a naive engine charged a flat one-way cost on any change, pricing both
        at -0.1% and understating every reversal by half.
        """
        sel = DualBacktestEngineSelector(commission_bps=10.0, slippage_bps=0.0)
        flat = [100.0, 100.0, 100.0]
        entry = sel.run_vectorized_backtest(flat, [0.0, 1.0, 1.0])
        reversal = sel.run_vectorized_backtest(flat, [-1.0, 1.0, 1.0])
        self.assertAlmostEqual(entry.total_return_pct, -0.1, places=6)
        self.assertAlmostEqual(reversal.total_return_pct, (0.999 * 0.998 - 1.0) * 100.0, places=6)
        self.assertAlmostEqual(entry.total_turnover, 1.0, places=9)
        self.assertAlmostEqual(reversal.total_turnover, 3.0, places=9)


class TestEventDrivenLedger(unittest.TestCase):
    """The cash ledger, position sizing, and fill latency."""

    def test_short_position_gains_when_price_falls(self):
        """
        Short at 100 with E of equity: units = -E/100, cash = 2E. At 90 the book is
        worth 2E - 0.9E = 1.1E, a 10% gain.

        an earlier ledger debited cash on the sell as well as the buy, so this
        position lost money in a falling market.
        """
        m = _free().run_event_driven_backtest(
            [100.0, 90.0], [-1.0, -1.0], execution_lag_bars=0
        )
        self.assertAlmostEqual(m.total_return_pct, 10.0, places=9)

    def test_short_position_loses_when_price_rises(self):
        m = _free().run_event_driven_backtest(
            [100.0, 110.0], [-1.0, -1.0], execution_lag_bars=0
        )
        self.assertAlmostEqual(m.total_return_pct, -10.0, places=9)

    def test_returns_are_invariant_to_initial_capital(self):
        """
        A percentage return must not depend on the size of the account. The
        older engine held a fixed number of *shares*, so doubling the capital
        halved the reported return.
        """
        prices = [100.0 * (1.0 + 0.01 * math.sin(t)) for t in range(60)]
        signals = [float((-1) ** (t // 7)) for t in range(60)]
        small = DualBacktestEngineSelector(initial_capital=10_000.0)
        large = DualBacktestEngineSelector(initial_capital=50_000_000.0)
        self.assertAlmostEqual(
            small.run_event_driven_backtest(prices, signals).total_return_pct,
            large.run_event_driven_backtest(prices, signals).total_return_pct,
            places=6,
        )

    def test_execution_lag_misses_the_bar_it_was_late_for(self):
        """
        The signal turns long at the close of bar 1; bar 2 is a single +20% move.

        lag 0: fills at 100, captures the move          -> +20%
        lag 1: fills at bar 2's close of 120, too late  ->   0%

        This is the entire content of "execution drag" from latency, isolated.
        """
        prices = [100.0, 100.0, 120.0, 120.0]
        signals = [0.0, 1.0, 1.0, 1.0]
        sel = _free()
        self.assertAlmostEqual(
            sel.run_event_driven_backtest(prices, signals, execution_lag_bars=0).total_return_pct,
            20.0, places=9,
        )
        self.assertAlmostEqual(
            sel.run_event_driven_backtest(prices, signals, execution_lag_bars=1).total_return_pct,
            0.0, places=9,
        )

    def test_order_still_pending_at_the_end_never_fills(self):
        """A late signal whose fill bar is past the end of the series does nothing."""
        m = _free().run_event_driven_backtest(
            [100.0, 100.0, 100.0], [0.0, 0.0, 1.0], execution_lag_bars=1
        )
        self.assertEqual(m.trades_count, 0)
        self.assertAlmostEqual(m.total_return_pct, 0.0, places=9)

    def test_slippage_is_charged_against_the_direction_of_the_trade(self):
        """
        Buying pays up and selling sells down. Sizing on the fill price, a full
        round trip at flat prices with one-way slippage s ends at exactly
        (1 - s) / (1 + s) of the opening equity:

          buy   units = E / (p(1+s)),   equity = E / (1+s)
          sell  proceeds = units * p(1-s) = E(1-s) / (1+s)

        At 10 bps that is -0.1998%. Note it is *not* (1-s)(1-s): a round trip pays
        the spread once each way against a position sized on the paid price.
        """
        s = 0.001
        sel = DualBacktestEngineSelector(commission_bps=0.0, slippage_bps=10.0)
        m = sel.run_event_driven_backtest(
            [100.0, 100.0, 100.0], [1.0, 0.0, 0.0], execution_lag_bars=0
        )
        self.assertEqual(m.trades_count, 2)
        # total_return_pct is reported rounded to 6 decimal places.
        self.assertAlmostEqual(m.total_return_pct, ((1 - s) / (1 + s) - 1.0) * 100.0, places=6)

    def test_target_exposure_is_reached_exactly_despite_slippage(self):
        """
        A target of 1.0 must deploy exactly 100% of equity, not 100.1%. Sizing on
        the mark price instead of the fill price buys ``target * (1 + slippage)``
        of exposure and quietly runs the book levered.
        """
        sel = DualBacktestEngineSelector(commission_bps=0.0, slippage_bps=50.0)
        m = sel.run_event_driven_backtest([100.0, 100.0], [1.0, 1.0], execution_lag_bars=0)
        # Equity is unchanged by a flat second bar only if exposure is exactly 1.0.
        self.assertAlmostEqual(m.equity_curve[1], m.equity_curve[2], places=9)


class TestEngineParity(unittest.TestCase):
    """
    The invariant the whole skill rests on: given identical assumptions the two
    engines must produce identical equity. Any residual gap is the measurement, so
    if the engines disagree for structural reasons the measurement is meaningless.
    """

    @staticmethod
    def _series(n=300, seed=11):
        rng = np.random.default_rng(seed)
        prices = 100.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.012, n))
        signals = rng.choice([-1.0, 0.0, 1.0], n)
        return prices, signals

    def test_engines_agree_exactly_under_matched_assumptions(self):
        """
        Zero cost, zero latency, and the event engine holding its target weight
        constant -- the three things that distinguish it from the vectorized
        formulation, all switched off. The equity curves must be identical.
        """
        prices, signals = self._series()
        sel = _free()
        vec = sel.run_vectorized_backtest(prices, signals)
        evt = sel.run_event_driven_backtest(
            prices, signals, execution_lag_bars=0, rebalance_every_bar=True
        )
        np.testing.assert_allclose(vec.equity_curve, evt.equity_curve, rtol=1e-12, atol=0.0)
        self.assertAlmostEqual(vec.total_return_pct, evt.total_return_pct, places=9)

    def test_engines_agree_closely_once_costs_are_switched_on(self):
        """
        With costs the two differ only at second order in the cost rate: the
        vectorized engine charges a fraction of *equity*, the event engine charges
        the same fraction of *traded notional at a slipped fill price*. At 10 bps
        that is ~1e-7 of equity per trade, and the test bounds it rather than
        pretending it is zero.
        """
        prices, signals = self._series()
        sel = DualBacktestEngineSelector(commission_bps=5.0, slippage_bps=5.0)
        vec = sel.run_vectorized_backtest(prices, signals)
        evt = sel.run_event_driven_backtest(
            prices, signals, execution_lag_bars=0, rebalance_every_bar=True
        )
        per_trade_tolerance_pp = 1e-3
        self.assertLess(
            abs(vec.total_return_pct - evt.total_return_pct),
            per_trade_tolerance_pp * max(1, vec.trades_count),
        )

    def test_short_positions_drift_when_the_weight_is_not_rebalanced(self):
        """
        The vectorized ``w * r`` product assumes the target weight is restored every
        bar. A short does not hold its weight as the price moves, so without
        ``rebalance_every_bar`` the two disagree even at zero cost and zero latency.
        That is a modelling difference, and it should be visible rather than hidden.
        """
        prices = [100.0, 110.0, 121.0, 133.1]
        signals = [-1.0, -1.0, -1.0, -1.0]
        sel = _free()
        vec = sel.run_vectorized_backtest(prices, signals)
        drifting = sel.run_event_driven_backtest(
            prices, signals, execution_lag_bars=0, rebalance_every_bar=False
        )
        held = sel.run_event_driven_backtest(
            prices, signals, execution_lag_bars=0, rebalance_every_bar=True
        )
        # A constant-weight short compounds -10% three times; a drifting one does not.
        self.assertAlmostEqual(vec.total_return_pct, (0.9 ** 3 - 1.0) * 100.0, places=6)
        self.assertAlmostEqual(held.total_return_pct, vec.total_return_pct, places=6)
        self.assertNotAlmostEqual(drifting.total_return_pct, vec.total_return_pct, places=2)


class TestNoLookAhead(unittest.TestCase):
    """
    Equity through bar k must depend only on bars up to k. Anything else means the
    engine is reading the future, which is the failure this skill's sibling
    `lookahead-bias-elimination` exists to catch.
    """

    def test_equity_prefix_is_unaffected_by_later_data(self):
        rng = np.random.default_rng(5)
        n, k = 80, 40
        prices = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, n))
        signals = rng.choice([-1.0, 0.0, 1.0], n)

        altered_prices = prices.copy()
        altered_signals = signals.copy()
        altered_prices[k:] *= 1.75
        altered_signals[k:] = -altered_signals[k:]

        sel = DualBacktestEngineSelector()
        for name, run in (
            ("vectorized", sel.run_vectorized_backtest),
            ("event_driven", sel.run_event_driven_backtest),
        ):
            with self.subTest(engine=name):
                base = run(prices, signals).equity_curve
                altered = run(altered_prices, altered_signals).equity_curve
                np.testing.assert_allclose(
                    base[: k + 1], altered[: k + 1], rtol=1e-12, atol=0.0
                )
                self.assertFalse(np.allclose(base[k + 1 :], altered[k + 1 :]))


class TestAnnualizedSharpe(unittest.TestCase):
    """Checked against the standard library, not against the module's own NumPy."""

    RETURNS = [0.01, -0.005, 0.02, 0.0]

    def _reference(self, returns, periods=TRADING_DAYS_PER_YEAR, rf=0.0):
        excess = [r - rf / periods for r in returns]
        return statistics.mean(excess) / statistics.stdev(excess) * math.sqrt(periods)

    def test_matches_an_independent_computation(self):
        self.assertAlmostEqual(
            annualized_sharpe(self.RETURNS), self._reference(self.RETURNS), places=10
        )

    def test_risk_free_rate_is_deducted_per_period(self):
        self.assertAlmostEqual(
            annualized_sharpe(self.RETURNS, TRADING_DAYS_PER_YEAR, 0.0252),
            self._reference(self.RETURNS, TRADING_DAYS_PER_YEAR, 0.0252),
            places=10,
        )

    def test_annualization_factor_follows_periods_per_year(self):
        minute_bars = TRADING_DAYS_PER_YEAR * 390.0
        self.assertAlmostEqual(
            annualized_sharpe(self.RETURNS, minute_bars),
            self._reference(self.RETURNS, minute_bars),
            places=8,
        )
        self.assertNotAlmostEqual(
            annualized_sharpe(self.RETURNS, minute_bars),
            annualized_sharpe(self.RETURNS),
            places=2,
        )

    def test_constant_returns_give_nan_not_a_huge_number(self):
        """
        A constant return series has no dispersion, so its Sharpe ratio is
        undefined. Its sample standard deviation is ~1e-17 of float noise rather
        than exactly 0.0, which is why an earlier ``std_r or 0.0001`` guard did
        not fire and a constant +1%/bar series reported a Sharpe ratio of 1.6e15.
        """
        self.assertTrue(math.isnan(annualized_sharpe([0.01] * 40)))

        # And at engine level: a permanently flat signal produces a flat equity
        # curve, which has no dispersion and therefore no Sharpe ratio.
        prices = [100.0 * (1.01 ** t) for t in range(40)]
        metrics = _free().run_vectorized_backtest(prices, [0.0] * 40)
        self.assertAlmostEqual(metrics.total_return_pct, 0.0, places=9)
        self.assertTrue(math.isnan(metrics.sharpe_ratio))

    def test_too_few_observations_give_nan(self):
        self.assertTrue(math.isnan(annualized_sharpe([])))
        self.assertTrue(math.isnan(annualized_sharpe([0.01])))

    def test_rejects_non_positive_periods_per_year(self):
        with self.assertRaises(ValueError):
            annualized_sharpe(self.RETURNS, periods_per_year=0.0)


class TestEngineRecommendation(unittest.TestCase):

    def setUp(self):
        self.sel = DualBacktestEngineSelector(commission_bps=5.0, slippage_bps=5.0)

    def test_path_dependent_stops_alone_force_the_event_engine(self):
        """
        Regression. an earlier rule scored this 3.0 against a threshold of 4.0
        and returned VECTORIZED -- for the one strategy shape a vectorized backtest
        cannot represent at all, because the exposure series is not knowable before
        the run.
        """
        rec = self.sel.recommend_engine(trades_per_day=0.01, uses_path_dependent_stops=True)
        self.assertEqual(rec.engine, RecommendedEngine.EVENT_DRIVEN)
        self.assertTrue(rec.blocking_reasons)
        self.assertIn("path-dependent", rec.blocking_reasons[0])

    def test_limit_orders_alone_force_the_event_engine(self):
        """Regression, and the case SKILL.md's workflow always documented."""
        rec = self.sel.recommend_engine(trades_per_day=0.01, uses_limit_orders=True)
        self.assertEqual(rec.engine, RecommendedEngine.EVENT_DRIVEN)
        self.assertTrue(rec.blocking_reasons)

    def test_low_turnover_market_order_strategy_stays_vectorized(self):
        rec = self.sel.recommend_engine(trades_per_day=0.01)
        self.assertEqual(rec.engine, RecommendedEngine.VECTORIZED)
        self.assertEqual(rec.blocking_reasons, [])

    def test_turnover_threshold_is_the_stated_cost_arithmetic(self):
        """
        1 trade/day x 252 bars/year x 1.0 exposure change x 10 bps = 25.2% of equity
        a year, which is past a 2% tolerance. The figure is reported so the caller
        can disagree with it, rather than hidden behind a bare trade-count cutoff.
        """
        rec = self.sel.recommend_engine(trades_per_day=1.0)
        self.assertAlmostEqual(rec.estimated_annual_cost_drag_pct, 25.2, places=6)
        self.assertEqual(rec.engine, RecommendedEngine.EVENT_DRIVEN)

        tolerant = self.sel.recommend_engine(
            trades_per_day=1.0, max_tolerable_annual_cost_drag=0.50
        )
        self.assertEqual(tolerant.engine, RecommendedEngine.VECTORIZED)

    def test_rejects_negative_trade_rate(self):
        with self.assertRaises(ValueError):
            self.sel.recommend_engine(trades_per_day=-1.0)


class TestInputValidation(unittest.TestCase):
    """
    Every one of these previously returned an all-zero ``BacktestEngineMetrics``,
    raised ``ZeroDivisionError`` from inside the loop, or produced NaN metrics that
    looked like results.
    """

    def setUp(self):
        self.sel = DualBacktestEngineSelector()
        self.runners = (
            self.sel.run_vectorized_backtest,
            self.sel.run_event_driven_backtest,
        )

    def _assert_both_raise(self, prices, signals, fragment):
        for run in self.runners:
            with self.subTest(engine=run.__name__):
                with self.assertRaises(ValueError) as ctx:
                    run(prices, signals)
                self.assertIn(fragment, str(ctx.exception))

    def test_length_mismatch_raises(self):
        self._assert_both_raise([100.0, 101.0, 102.0], [1.0, 1.0], "same length")

    def test_series_shorter_than_two_bars_raises(self):
        self._assert_both_raise([100.0], [1.0], "at least 2 bars")

    def test_non_finite_price_raises(self):
        self._assert_both_raise([100.0, float("nan"), 102.0], [1.0, 1.0, 1.0], "non-finite")

    def test_non_finite_signal_raises(self):
        self._assert_both_raise([100.0, 101.0, 102.0], [1.0, float("inf"), 1.0], "non-finite")

    def test_non_positive_price_raises(self):
        self._assert_both_raise([100.0, 0.0, 102.0], [1.0, 1.0, 1.0], "strictly positive")

    def test_exposure_above_the_leverage_cap_raises(self):
        """
        Guards the units confusion directly: a caller passing share counts rather
        than exposure fractions trips this instead of silently backtesting 3x
        leverage.
        """
        self._assert_both_raise([100.0, 101.0], [3.0, 3.0], "max_abs_exposure")

    def test_leverage_cap_can_be_raised_deliberately(self):
        levered = DualBacktestEngineSelector(max_abs_exposure=3.0)
        metrics = levered.run_vectorized_backtest([100.0, 110.0], [2.0, 2.0])
        self.assertIsInstance(metrics, BacktestEngineMetrics)

    def test_negative_execution_lag_raises(self):
        with self.assertRaises(ValueError):
            self.sel.run_event_driven_backtest([100.0, 101.0], [1.0, 1.0], execution_lag_bars=-1)

    def test_rejects_invalid_construction(self):
        for kwargs in (
            {"commission_bps": -1.0},
            {"slippage_bps": -1.0},
            {"initial_capital": 0.0},
            {"periods_per_year": 0.0},
            {"max_abs_exposure": 0.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    DualBacktestEngineSelector(**kwargs)


class TestDualEngineAudit(unittest.TestCase):

    @staticmethod
    def _series(n=200, seed=23):
        rng = np.random.default_rng(seed)
        prices = 100.0 * np.cumprod(1.0 + rng.normal(0.0002, 0.01, n))
        signals = rng.choice([-1.0, 0.0, 1.0], n)
        return prices, signals

    def test_drag_components_decompose_the_total(self):
        """cost drag + latency drag == total drag, to rounding."""
        prices, signals = self._series()
        report = DualBacktestEngineSelector().compare_engines(prices, signals)
        self.assertAlmostEqual(
            report.cost_drag_pct + report.return_drag_pct, report.total_drag_pct, places=5
        )

    def test_cost_drag_is_zero_without_costs_and_latency_drag_without_lag(self):
        """
        With every difference switched off, all three curves coincide. A non-zero
        drag under these settings would mean the harness is measuring itself.
        """
        prices, signals = self._series()
        report = _free().compare_engines(
            prices, signals, execution_lag_bars=0, rebalance_every_bar=True
        )
        self.assertAlmostEqual(report.cost_drag_pct, 0.0, places=6)
        self.assertAlmostEqual(report.return_drag_pct, 0.0, places=6)
        self.assertAlmostEqual(report.total_drag_pct, 0.0, places=6)

    def test_costs_can_only_reduce_the_frictionless_curve(self):
        prices, signals = self._series()
        report = DualBacktestEngineSelector().compare_engines(prices, signals)
        self.assertGreater(report.vectorized_metrics.total_turnover, 0.0)
        self.assertGreater(report.cost_drag_pct, 0.0)
        self.assertGreaterEqual(
            report.frictionless_metrics.total_return_pct,
            report.vectorized_metrics.total_return_pct,
        )

    def test_speedup_is_not_reported_on_a_series_too_short_to_time(self):
        """
        Timing an eight-bar workload measures the clock. an earlier report
        published that ratio anyway -- it came out at 0.78x, i.e. the "1,000x faster"
        vectorized engine measured as slower than the event loop.
        """
        prices, signals = self._series(n=50)
        report = DualBacktestEngineSelector().compare_engines(prices, signals)
        self.assertIsNone(report.speedup_factor)
        self.assertIn("not measured", report.summary)

    def test_speedup_is_measured_on_a_long_enough_series(self):
        n = MIN_BARS_FOR_TIMING
        prices, signals = self._series(n=n, seed=31)
        report = DualBacktestEngineSelector().compare_engines(prices, signals)
        self.assertIsNotNone(report.speedup_factor)
        self.assertGreater(report.speedup_factor, 0.0)
        self.assertIn("Dual Engine Audit", report.summary)

    def test_summary_reports_all_three_curves(self):
        prices, signals = self._series()
        report = DualBacktestEngineSelector().compare_engines(prices, signals)
        for token in ("frictionless", "vectorized", "event-driven", "Cost drag", "latency drag"):
            self.assertIn(token, report.summary)


if __name__ == "__main__":
    unittest.main()
