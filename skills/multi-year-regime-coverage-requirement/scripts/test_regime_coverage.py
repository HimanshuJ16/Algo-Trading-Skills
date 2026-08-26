"""
Unit tests for multi-year-regime-coverage-requirement skill.

Expected values are derived independently of the implementation: drawdowns and
compounded returns are computed by hand in the docstrings below, and the Sharpe
check uses ``statistics.fmean`` / ``statistics.pstdev`` rather than the module's
own helpers.

Several tests are explicit regressions -- each is annotated with the behavior of
the previous implementation that it would have caught.
"""
import logging
import math
import statistics
import unittest

from regime_coverage import (
    CLASSIFIABLE_REGIMES,
    MarketRegime,
    MarketRegimeCoverageEngine,
    _contiguous_runs,
    _max_drawdown,
    _population_stdev,
)

# Keep test output clean without globally disabling logging, which would break the
# assertLogs assertions below.
logging.getLogger("regime_coverage").addHandler(logging.NullHandler())
logging.getLogger("regime_coverage").propagate = False


def build_four_regime_prices():
    """
    540 daily bars that classify into all four regimes under the defaults.

    Layout (verified in test_fixture_layout_is_stable):
        bars   0- 19  UNCLASSIFIED (warm-up)
        bars  20- 64  LOW_VOLATILITY_RANGE  (episode 1)
        bars  65-193  BULL_TREND
        bars 194-305  LOW_VOLATILITY_RANGE  (episode 2)
        bars 306-423  BEAR_MARKET
        bars 424-539  HIGH_VOLATILITY_CRASH
    """
    prices = []
    p = 100.0
    for _ in range(60):                       # flat
        prices.append(p)
    for _ in range(120):                      # +0.5%/bar
        p *= 1.005
        prices.append(p)
    for _ in range(120):                      # flat
        prices.append(p)
    for _ in range(120):                      # -0.5%/bar
        p *= 0.995
        prices.append(p)
    for i in range(120):                      # alternating +5% / -4.76%
        p *= 1.05 if i % 2 == 0 else 1.0 / 1.05
        prices.append(p)
    return prices


class TestHelpers(unittest.TestCase):
    """Pure helpers, checked against values computed by hand."""

    def test_max_drawdown_hand_derived(self):
        # Two -10% bars: equity 0.90 then 0.81, peak 1.00 -> 19% decline.
        self.assertAlmostEqual(_max_drawdown([-0.1, -0.1]), 0.19, places=9)
        # +50% (peak 1.50), -50% (0.75) -> (1.50-0.75)/1.50 = 50%; the later
        # recovery to 1.125 is a 25% decline from the same peak, so 50% stands.
        self.assertAlmostEqual(_max_drawdown([0.5, -0.5, 0.5]), 0.5, places=9)
        # A monotonically rising curve never leaves its peak.
        self.assertEqual(_max_drawdown([0.01, 0.01, 0.01]), 0.0)
        self.assertEqual(_max_drawdown([]), 0.0)

    def test_population_stdev_hand_derived(self):
        # sqrt(((1.5^2 + 0.5^2 + 0.5^2 + 1.5^2) / 4)) = sqrt(1.25)
        self.assertAlmostEqual(_population_stdev([1, 2, 3, 4], 2.5), math.sqrt(1.25), places=12)
        self.assertEqual(_population_stdev([7.0, 7.0, 7.0], 7.0), 0.0)

    def test_contiguous_runs(self):
        self.assertEqual(_contiguous_runs([0, 1, 2, 5, 6, 9]), [[0, 1, 2], [5, 6], [9]])
        self.assertEqual(_contiguous_runs([]), [])


class TestFixture(unittest.TestCase):

    def test_fixture_layout_is_stable(self):
        """The other tests rely on this exact segmentation; pin it down."""
        engine = MarketRegimeCoverageEngine(min_required_years=1.0, min_required_regimes=3)
        regimes = engine.classify_regimes(build_four_regime_prices())

        self.assertEqual(len(regimes), 540)
        self.assertTrue(all(r is MarketRegime.UNCLASSIFIED for r in regimes[:20]))
        self.assertTrue(all(r is MarketRegime.LOW_VOLATILITY_RANGE for r in regimes[20:65]))
        self.assertTrue(all(r is MarketRegime.BULL_TREND for r in regimes[65:194]))
        self.assertTrue(all(r is MarketRegime.LOW_VOLATILITY_RANGE for r in regimes[194:306]))
        self.assertTrue(all(r is MarketRegime.BEAR_MARKET for r in regimes[306:424]))
        self.assertTrue(all(r is MarketRegime.HIGH_VOLATILITY_CRASH for r in regimes[424:]))


class TestClassification(unittest.TestCase):

    def setUp(self):
        self.engine = MarketRegimeCoverageEngine(min_required_years=1.0, min_required_regimes=3)

    def test_warmup_bars_are_unclassified_not_low_volatility(self):
        """
        Regression: warm-up bars were unconditionally labelled
        LOW_VOLATILITY_RANGE, fabricating a regime that inflated the covered
        count. A monotonic bull series must observe exactly one regime.
        """
        prices = [100.0 * (1.005 ** i) for i in range(300)]
        report = self.engine.audit_coverage(prices, [0.0] * 300)

        self.assertEqual(report.unclassified_bars, 20)
        self.assertEqual(report.regimes_observed, [MarketRegime.BULL_TREND])
        self.assertNotIn(MarketRegime.LOW_VOLATILITY_RANGE.value, report.regime_metrics)
        self.assertNotIn(MarketRegime.UNCLASSIFIED, report.unique_regimes_covered)

    def test_series_shorter_than_window_is_entirely_unclassified(self):
        prices = [100.0 + i for i in range(10)]
        with self.assertLogs("regime_coverage", level=logging.WARNING):
            regimes = self.engine.classify_regimes(prices)
        self.assertTrue(all(r is MarketRegime.UNCLASSIFIED for r in regimes))

    def test_configured_window_size_is_used_by_audit(self):
        """
        Regression: ``window_size`` was a ``classify_regimes`` argument that
        ``audit_coverage`` never passed, so a configured window was ignored and
        the hard-coded 20 was always used.
        """
        engine = MarketRegimeCoverageEngine(
            min_required_years=1.0, min_required_regimes=1, window_size=50
        )
        prices = [100.0 * (1.005 ** i) for i in range(300)]
        self.assertEqual(engine.audit_coverage(prices, [0.0] * 300).unclassified_bars, 50)

    def test_classification_thresholds_are_configurable(self):
        """A 20-bar +10.5% move is a trend at the 3% default, a range at 20%."""
        prices = [100.0 * (1.005 ** i) for i in range(300)]
        strict = MarketRegimeCoverageEngine(
            min_required_years=1.0, min_required_regimes=1, trend_threshold_pct=0.20
        )
        self.assertEqual(
            strict.audit_coverage(prices, [0.0] * 300).regimes_observed,
            [MarketRegime.LOW_VOLATILITY_RANGE],
        )


class TestCoverageGates(unittest.TestCase):

    def test_thin_regime_is_observed_but_does_not_count_as_coverage(self):
        """
        Regression: a regime counted as "covered" on a single bar, so a
        one-regime backtest with two incidental stray bars satisfied the
        >=3-regime rule.
        """
        engine = MarketRegimeCoverageEngine(
            min_required_years=1.0, min_required_regimes=3, min_bars_per_regime=50
        )
        prices = build_four_regime_prices()
        report = engine.audit_coverage(prices, [0.0] * len(prices))

        # BULL(129), RANGE(157) and BEAR(118) clear 50 bars; CRASH has 116 too,
        # so raise the bar until one regime falls below it.
        engine_strict = MarketRegimeCoverageEngine(
            min_required_years=1.0, min_required_regimes=4, min_bars_per_regime=125
        )
        strict = engine_strict.audit_coverage(prices, [0.0] * len(prices))

        self.assertEqual(len(report.unique_regimes_covered), 4)
        self.assertIn(MarketRegime.BEAR_MARKET, strict.regimes_observed)
        self.assertNotIn(MarketRegime.BEAR_MARKET, strict.unique_regimes_covered)
        self.assertFalse(strict.regime_metrics["BEAR_MARKET"].counts_toward_coverage)
        self.assertFalse(strict.is_coverage_sufficient)
        self.assertIn("were not counted", strict.message)

    def test_duration_gate_is_not_rounded_before_comparison(self):
        """
        Regression: ``total_years`` was rounded to 2 dp before the comparison, so
        755 daily bars (2.9960 yr) rounded to 3.00 and passed a 3-year gate that
        requires 756 bars.
        """
        engine = MarketRegimeCoverageEngine(min_required_years=3.0, min_required_regimes=1)
        just_short = [100.0 * (1.005 ** i) for i in range(755)]
        exact = [100.0 * (1.005 ** i) for i in range(756)]

        short_report = engine.audit_coverage(just_short, [0.0] * 755)
        exact_report = engine.audit_coverage(exact, [0.0] * 756)

        self.assertAlmostEqual(short_report.total_years, 755 / 252.0, places=9)
        self.assertFalse(short_report.is_coverage_sufficient)
        self.assertIn("Insufficient duration", short_report.message)
        self.assertEqual(exact_report.total_years, 3.0)
        self.assertTrue(exact_report.is_coverage_sufficient)

    def test_bars_per_year_governs_duration_and_annualization(self):
        """
        Regression: duration was hard-coded to bars/252, so one year of 1-minute
        bars (98,280 bars) audited as 390 years and passed any duration gate.
        """
        bars = 98_280
        prices = [100.0 * (1.000_01 ** i) for i in range(bars)]
        engine = MarketRegimeCoverageEngine(
            min_required_years=3.0, min_required_regimes=1, bars_per_year=bars
        )
        report = engine.audit_coverage(prices, [0.0] * bars)

        self.assertEqual(report.bars_per_year, bars)
        self.assertAlmostEqual(report.total_years, 1.0, places=9)
        self.assertFalse(report.is_coverage_sufficient)


class TestDeAveragedMetrics(unittest.TestCase):

    def setUp(self):
        self.prices = build_four_regime_prices()
        self.engine = MarketRegimeCoverageEngine(
            min_required_years=1.0,
            min_required_regimes=3,
            max_allowed_regime_drawdown_pct=25.0,
        )

    def test_within_episode_drawdown_replaces_concatenated_drawdown(self):
        """
        LOW_VOLATILITY_RANGE has two separate episodes. Put two -10% bars at the
        start of each.

        Within an episode: 1.00 -> 0.90 -> 0.81, a 19.00% decline. That is what
        the account experienced, and it is below the 25% limit.

        Concatenated across both episodes (the old metric): 0.9^4 = 0.6561, a
        34.39% decline that never occurred -- and which the old code vetoed on.
        The compounded total return is likewise -34.39%, where the old code
        reported the arithmetic sum of -40.00%.
        """
        regimes = self.engine.classify_regimes(self.prices)
        runs = _contiguous_runs(
            [i for i, r in enumerate(regimes) if r is MarketRegime.LOW_VOLATILITY_RANGE]
        )
        self.assertEqual(len(runs), 2)

        returns = [0.0] * len(self.prices)
        for run in runs:
            returns[run[0]] = -0.1
            returns[run[1]] = -0.1

        report = self.engine.audit_coverage(self.prices, returns)
        metrics = report.regime_metrics["LOW_VOLATILITY_RANGE"]

        self.assertEqual(metrics.episode_count, 2)
        self.assertAlmostEqual(metrics.max_drawdown_pct, 19.0, places=2)
        self.assertAlmostEqual(metrics.concatenated_drawdown_pct, 34.39, places=2)
        self.assertAlmostEqual(metrics.total_return_pct, -34.39, places=2)
        self.assertEqual(report.vetoed_regimes, [])
        self.assertTrue(report.is_promotable)

    def test_sharpe_matches_independent_computation(self):
        """Cross-checked against ``statistics.fmean`` / ``statistics.pstdev``."""
        returns = [0.004 if i % 3 else -0.006 for i in range(len(self.prices))]
        report = self.engine.audit_coverage(self.prices, returns)

        regimes = self.engine.classify_regimes(self.prices)
        bull = [returns[i] for i, r in enumerate(regimes) if r is MarketRegime.BULL_TREND]
        expected = (
            statistics.fmean(bull) / statistics.pstdev(bull, mu=statistics.fmean(bull))
        ) * math.sqrt(252)

        self.assertAlmostEqual(report.regime_metrics["BULL_TREND"].sharpe_ratio, round(expected, 2), places=2)

    def test_constant_returns_give_no_sharpe_ratio(self):
        """
        Regression: a constant return series produced a standard deviation of
        ~1e-19 from floating-point error, which slipped past the ``or 0.0001``
        guard and yielded a reported Sharpe ratio of 2.4e+16.
        """
        returns = [0.001] * len(self.prices)
        with self.assertLogs("regime_coverage", level=logging.WARNING):
            report = self.engine.audit_coverage(self.prices, returns)

        for metrics in report.regime_metrics.values():
            self.assertIsNone(metrics.sharpe_ratio)
            self.assertEqual(metrics.win_rate_pct, 100.0)
            self.assertEqual(metrics.max_drawdown_pct, 0.0)

    def test_sharpe_suppressed_below_min_bars(self):
        engine = MarketRegimeCoverageEngine(
            min_required_years=1.0, min_required_regimes=1, min_bars_per_regime=200
        )
        returns = [0.004 if i % 3 else -0.006 for i in range(len(self.prices))]
        report = engine.audit_coverage(self.prices, returns)

        # No regime in the fixture reaches 200 bars, so no Sharpe is reportable,
        # but drawdowns are still measured -- a path fact, not an estimate.
        for metrics in report.regime_metrics.values():
            self.assertIsNone(metrics.sharpe_ratio)
            self.assertGreater(metrics.max_drawdown_pct, 0.0)


class TestPromotionDecision(unittest.TestCase):

    def setUp(self):
        self.prices = build_four_regime_prices()
        self.engine = MarketRegimeCoverageEngine(
            min_required_years=1.0,
            min_required_regimes=3,
            max_allowed_regime_drawdown_pct=25.0,
        )

    def test_coverage_failure_does_not_claim_a_drawdown_veto(self):
        """
        Regression: any failure appended "REGIME VETO: Exceeded max drawdown
        threshold in one or more regimes", so a backtest that failed purely on
        coverage was reported as having breached a drawdown limit it never
        approached. Here the strategy never loses a single bar.
        """
        prices = [100.0 * (1.005 ** i) for i in range(300)]
        report = self.engine.audit_coverage(prices, [0.001] * 300)

        self.assertFalse(report.is_promotable)
        self.assertFalse(report.is_coverage_sufficient)
        self.assertEqual(report.vetoed_regimes, [])
        self.assertNotIn("REGIME VETO", report.message)
        self.assertIn("Insufficient regimes", report.message)
        self.assertEqual(report.regime_metrics["BULL_TREND"].max_drawdown_pct, 0.0)

    def test_drawdown_veto_fires_with_coverage_otherwise_satisfied(self):
        """
        Non-vacuous veto test: coverage passes, so the veto is the only reason
        promotion is refused. Six consecutive -5% bars inside BULL_TREND compound
        to 0.95^6 = 0.735092, a 26.49% decline, above the 25% limit.
        """
        regimes = self.engine.classify_regimes(self.prices)
        bull = _contiguous_runs(
            [i for i, r in enumerate(regimes) if r is MarketRegime.BULL_TREND]
        )[0]

        returns = [0.0] * len(self.prices)
        for i in bull[:6]:
            returns[i] = -0.05

        with self.assertLogs("regime_coverage", level=logging.WARNING):
            report = self.engine.audit_coverage(self.prices, returns)

        self.assertTrue(report.is_coverage_sufficient)
        self.assertFalse(report.is_promotable)
        self.assertEqual(report.vetoed_regimes, [MarketRegime.BULL_TREND])
        self.assertAlmostEqual(report.regime_metrics["BULL_TREND"].max_drawdown_pct, 26.49, places=2)
        self.assertIn("REGIME VETO", report.message)
        self.assertNotIn("Insufficient", report.message)

    def test_veto_boundary_is_evaluated_before_rounding(self):
        """
        Regression: the veto compared the drawdown *already rounded to 2 dp*, so
        a 25.0049% decline presented as 25.00% and slipped under a 25% limit.

        A single -25.0049% bar gives equity 0.749951 from a peak of 1.0, i.e. a
        drawdown of exactly 25.0049% -- reported as 25.0%, but still a breach.
        Exactly 25.00% is at the limit, not over it, and must not veto.
        """
        regimes = self.engine.classify_regimes(self.prices)
        bull = _contiguous_runs(
            [i for i, r in enumerate(regimes) if r is MarketRegime.BULL_TREND]
        )[0]

        over = [0.0] * len(self.prices)
        over[bull[0]] = -0.250049
        at_limit = [0.0] * len(self.prices)
        at_limit[bull[0]] = -0.25

        over_report = self.engine.audit_coverage(self.prices, over)
        limit_report = self.engine.audit_coverage(self.prices, at_limit)

        self.assertEqual(over_report.regime_metrics["BULL_TREND"].max_drawdown_pct, 25.0)
        self.assertEqual(over_report.vetoed_regimes, [MarketRegime.BULL_TREND])

        self.assertEqual(limit_report.regime_metrics["BULL_TREND"].max_drawdown_pct, 25.0)
        self.assertEqual(limit_report.vetoed_regimes, [])
        self.assertTrue(limit_report.is_promotable)

    def test_clean_multi_regime_backtest_is_promotable(self):
        returns = [0.004 if i % 3 else -0.006 for i in range(len(self.prices))]
        report = self.engine.audit_coverage(self.prices, returns)

        self.assertTrue(report.is_coverage_sufficient)
        self.assertTrue(report.is_promotable)
        self.assertEqual(report.vetoed_regimes, [])
        self.assertEqual(len(report.unique_regimes_covered), 4)
        self.assertEqual(report.bars_analyzed, 540)
        self.assertIn("promotable", report.message)

    def test_thin_regime_still_vetoes_on_drawdown(self):
        """A catastrophic loss is a path fact; a small sample does not excuse it."""
        engine = MarketRegimeCoverageEngine(
            min_required_years=1.0,
            min_required_regimes=1,
            min_bars_per_regime=125,
            max_allowed_regime_drawdown_pct=25.0,
        )
        regimes = engine.classify_regimes(self.prices)
        bear = _contiguous_runs(
            [i for i, r in enumerate(regimes) if r is MarketRegime.BEAR_MARKET]
        )[0]

        returns = [0.0] * len(self.prices)
        for i in bear[:6]:
            returns[i] = -0.05

        report = engine.audit_coverage(self.prices, returns)

        self.assertFalse(report.regime_metrics["BEAR_MARKET"].counts_toward_coverage)
        self.assertEqual(report.vetoed_regimes, [MarketRegime.BEAR_MARKET])
        self.assertFalse(report.is_promotable)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MarketRegimeCoverageEngine(min_required_years=1.0, min_required_regimes=3)
        self.prices = [100.0 + i for i in range(300)]

    def test_non_finite_return_is_rejected(self):
        """
        Regression: a NaN return made the equity curve NaN, after which every
        ``dd > max_dd`` comparison was False. ``max_drawdown_pct`` stayed 0.0 and
        no veto fired -- corrupt data produced an automatic pass.
        """
        for bad in (float("nan"), float("inf"), float("-inf")):
            returns = [0.001] * 300
            returns[150] = bad
            with self.assertRaises(ValueError) as ctx:
                self.engine.audit_coverage(self.prices, returns)
            self.assertIn("not finite", str(ctx.exception))

    def test_total_loss_return_is_rejected(self):
        returns = [0.001] * 300
        returns[42] = -1.0
        with self.assertRaises(ValueError):
            self.engine.audit_coverage(self.prices, returns)

    def test_non_positive_price_is_rejected(self):
        """Regression: a zero price raised an unhandled ZeroDivisionError."""
        prices = [100.0] * 30 + [0.0] + [100.0] * 30
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_coverage(prices, [0.0] * 61)
        self.assertIn("strictly positive", str(ctx.exception))

    def test_non_finite_price_is_rejected(self):
        prices = [100.0] * 30 + [float("nan")] + [100.0] * 30
        with self.assertRaises(ValueError):
            self.engine.audit_coverage(prices, [0.0] * 61)

    def test_length_mismatch_is_rejected(self):
        """
        Regression: mismatched lengths were silently truncated to the shorter
        series, so the routine n-vs-n-1 return-series off-by-one misaligned every
        regime label without warning.
        """
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_coverage(self.prices, [0.001] * 299)
        self.assertIn("align one-to-one", str(ctx.exception))

    def test_too_short_series_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_coverage([100.0], [0.0])

    def test_constructor_rejects_invalid_parameters(self):
        bad_kwargs = [
            {"min_required_years": 0.0},
            {"min_required_years": float("nan")},
            {"min_required_regimes": 0},
            {"min_required_regimes": len(CLASSIFIABLE_REGIMES) + 1},
            {"max_allowed_regime_drawdown_pct": 0.0},
            {"max_allowed_regime_drawdown_pct": 100.1},
            {"bars_per_year": 0},
            {"min_bars_per_regime": 0},
            {"window_size": 1},
            {"high_vol_annualized_threshold": 0.0},
            {"trend_threshold_pct": -0.01},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    MarketRegimeCoverageEngine(**kwargs)


if __name__ == "__main__":
    unittest.main()
