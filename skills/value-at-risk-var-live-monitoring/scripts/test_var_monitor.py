"""
Unit tests for the value-at-risk-var-live-monitoring skill.

Expected values are derived independently of ``var_monitor`` -- by hand from a
constructed distribution, or from ``statistics``/``NormalDist`` primitives assembled
here -- so a test cannot pass merely by re-executing the implementation's own
arithmetic.

Coverage:
1.  Estimator correctness against hand-computed VaR / CVaR on a designed sample.
2.  Historical quantile convention: k = ceil(n(1-c)), VaR = k-th worst loss.
3.  CVaR >= VaR by construction, and CVaR strictly greater on a fat tail.
4.  Parametric VaR against an independently computed z * sigma - mu.
5.  Breach detection, breach attribution, and the risk-reducing override.
6.  Regression tests for the 2.0.0 defect fixes (see each test's docstring).
"""
import logging
import statistics
import unittest
from statistics import NormalDist

from var_monitor import (
    LiveRiskStatus,
    LiveValueAtRiskMonitor,
    VaRMetrics,
    VaRMonitorError,
)


def flat_book(returns, symbol="X", n_shares=1000.0, price=100.0, nav=100000.0):
    """A single 100%-of-NAV long, so portfolio returns equal the asset returns."""
    return (
        {symbol: n_shares},
        {symbol: price},
        {symbol: list(returns)},
        nav,
    )


class TestEstimatorCorrectness(unittest.TestCase):
    """Values here are computed by hand from the constructed sample."""

    def test_historical_var_and_cvar_hand_computed(self):
        # 100 observations: the 4 worst are -0.10, -0.08, -0.06, -0.04, the rest
        # are +0.001. At c = 0.99, k = ceil(100 * 0.01) = 1, so:
        #   historical VaR = -(1st worst) = 0.10
        #   CVaR           = -mean of the 1 worst = 0.10
        returns = [-0.10, -0.08, -0.06, -0.04] + [0.001] * 96
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        m = monitor.compute_var_metrics(*flat_book(returns))

        self.assertEqual(m.observations_used, 100)
        self.assertEqual(m.tail_observations_used, 1)
        self.assertAlmostEqual(m.historical_var_pct, 0.10, places=12)
        self.assertAlmostEqual(m.cvar_pct, 0.10, places=12)
        # USD figures are the fractions times NAV.
        self.assertAlmostEqual(m.historical_var_usd, 0.10 * 100000.0, places=6)

    def test_historical_var_and_cvar_at_95_percent(self):
        # Same 100 observations at c = 0.95 -> k = ceil(100 * 0.05) = 5.
        #   VaR  = -(5th worst) = -(+0.001) -> clamped to 0.0? No: the 5 worst are
        #          -0.10, -0.08, -0.06, -0.04, +0.001, so the 5th worst is +0.001
        #          and VaR clamps to 0.0.
        #   CVaR = -mean(-0.10, -0.08, -0.06, -0.04, 0.001) = -(-0.279/5) = 0.0558
        returns = [-0.10, -0.08, -0.06, -0.04] + [0.001] * 96
        monitor = LiveValueAtRiskMonitor(
            confidence_level=0.95, var_limit_pct=0.05, min_observations=20)
        m = monitor.compute_var_metrics(*flat_book(returns))

        self.assertEqual(m.tail_observations_used, 5)
        self.assertAlmostEqual(m.historical_var_pct, 0.0, places=12)
        expected_cvar = -((-0.10 - 0.08 - 0.06 - 0.04 + 0.001) / 5.0)
        self.assertAlmostEqual(m.cvar_pct, expected_cvar, places=12)
        self.assertAlmostEqual(m.cvar_pct, 0.0558, places=12)

    def test_tail_count_is_ceil_not_floor_at_round_sample_sizes(self):
        # n * (1 - c) exactly integral is where floor and ceil disagree. At
        # n = 250, c = 0.99 the tail is 2.5 -> k = 3. At n = 200, c = 0.95 the
        # tail is exactly 10.0 -> k = 10 (a bare ceil on binary floats gives 11).
        self.assertEqual(LiveValueAtRiskMonitor._tail_count(250, 0.99), 3)
        self.assertEqual(LiveValueAtRiskMonitor._tail_count(200, 0.95), 10)
        self.assertEqual(LiveValueAtRiskMonitor._tail_count(100, 0.99), 1)
        self.assertEqual(LiveValueAtRiskMonitor._tail_count(500, 0.99), 5)
        # Clamped into [1, n].
        self.assertEqual(LiveValueAtRiskMonitor._tail_count(10, 0.99), 1)

    def test_kth_worst_selection_is_the_kth_worst(self):
        # 250 strictly increasing observations -> k = 3, so historical VaR must be
        # the 3rd smallest value, independently identified via sorted().
        returns = [(-125 + i) / 1000.0 for i in range(250)]
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.99)
        m = monitor.compute_var_metrics(*flat_book(returns))

        third_worst = sorted(returns)[2]
        self.assertEqual(m.tail_observations_used, 3)
        self.assertAlmostEqual(m.historical_var_pct, -third_worst, places=12)
        expected_cvar = -statistics.fmean(sorted(returns)[:3])
        self.assertAlmostEqual(m.cvar_pct, expected_cvar, places=12)

    def test_parametric_var_matches_independent_z_sigma_minus_mu(self):
        returns = [0.01, -0.005, 0.015, -0.01, 0.008, -0.003, 0.012, -0.007,
                   0.005, -0.002] * 10
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.99)
        m = monitor.compute_var_metrics(*flat_book(returns))

        z = NormalDist().inv_cdf(0.99)
        expected = z * statistics.stdev(returns) - statistics.fmean(returns)
        self.assertAlmostEqual(m.parametric_var_pct, expected, places=12)
        # Sanity anchor: z_0.99 is 2.3263, not the 2.326 of the old lookup table.
        self.assertAlmostEqual(z, 2.32634787, places=7)

    def test_subtract_mean_drift_false_drops_the_drift_term(self):
        returns = [0.02] * 50 + [-0.01] * 50          # strongly positive drift
        with_drift = LiveValueAtRiskMonitor(
            confidence_level=0.99, var_limit_pct=0.99, subtract_mean_drift=True)
        without = LiveValueAtRiskMonitor(
            confidence_level=0.99, var_limit_pct=0.99, subtract_mean_drift=False)

        a = with_drift.compute_var_metrics(*flat_book(returns)).parametric_var_pct
        b = without.compute_var_metrics(*flat_book(returns)).parametric_var_pct
        self.assertAlmostEqual(b - a, statistics.fmean(returns), places=12)
        self.assertGreater(b, a)      # dropping a positive drift raises the measure

    def test_cvar_is_at_least_var_and_strictly_greater_on_a_fat_tail(self):
        returns = [-0.30, -0.12, -0.11, -0.10, -0.09] + [0.002] * 245
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.99)
        m = monitor.compute_var_metrics(*flat_book(returns))

        self.assertEqual(m.tail_observations_used, 3)
        self.assertGreaterEqual(m.cvar_pct, m.historical_var_pct)
        # 3rd worst is -0.11; mean of the 3 worst is (-0.30-0.12-0.11)/3 = -0.17667.
        self.assertAlmostEqual(m.historical_var_pct, 0.11, places=12)
        self.assertAlmostEqual(m.cvar_pct, 0.53 / 3.0, places=12)
        self.assertGreater(m.cvar_pct, m.historical_var_pct)


class TestWeightsExposureAndShorts(unittest.TestCase):

    def test_leverage_scales_var_linearly(self):
        returns = [0.01, -0.02] * 60
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.99)
        one_x = monitor.compute_var_metrics({"X": 1000.0}, {"X": 100.0},
                                            {"X": returns}, 100000.0)
        three_x = monitor.compute_var_metrics({"X": 3000.0}, {"X": 100.0},
                                              {"X": returns}, 100000.0)

        self.assertAlmostEqual(one_x.gross_exposure_pct, 1.0, places=12)
        self.assertAlmostEqual(three_x.gross_exposure_pct, 3.0, places=12)
        self.assertAlmostEqual(three_x.parametric_var_pct,
                               3.0 * one_x.parametric_var_pct, places=12)

    def test_short_position_nets_against_a_long_in_the_same_factor(self):
        # Equal and opposite quantities in two perfectly correlated series leave a
        # market-neutral book: every portfolio return is exactly 0, so VaR is 0
        # while GROSS exposure is 2.0x NAV.
        series = [0.01, -0.03, 0.02, -0.01] * 30
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        m = monitor.compute_var_metrics(
            {"A": 500.0, "B": -500.0},
            {"A": 100.0, "B": 100.0},
            {"A": series, "B": list(series)},
            100000.0,
        )
        self.assertAlmostEqual(m.parametric_var_pct, 0.0, places=12)
        self.assertAlmostEqual(m.historical_var_pct, 0.0, places=12)
        self.assertAlmostEqual(m.gross_exposure_pct, 1.0, places=12)
        self.assertAlmostEqual(m.net_exposure_pct, 0.0, places=12)
        self.assertFalse(m.is_breached)

    def test_zero_quantity_positions_are_ignored(self):
        returns = [0.01, -0.02] * 60
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.99)
        held_only = monitor.compute_var_metrics(
            {"X": 1000.0}, {"X": 100.0}, {"X": returns}, 100000.0)
        with_flat = monitor.compute_var_metrics(
            {"X": 1000.0, "FLAT": 0.0}, {"X": 100.0}, {"X": returns}, 100000.0)
        self.assertAlmostEqual(with_flat.parametric_var_pct,
                               held_only.parametric_var_pct, places=12)

    def test_empty_book_reports_zero_risk_and_approves(self):
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        status = monitor.evaluate_live_risk({}, {}, {}, 100000.0)
        self.assertTrue(status.approved)
        self.assertFalse(status.var_metrics.is_breached)
        self.assertEqual(status.var_metrics.parametric_var_usd, 0.0)
        self.assertEqual(status.var_metrics.breaching_measures, ())


class TestBreachDetectionAndAttribution(unittest.TestCase):

    def test_normal_book_is_approved(self):
        returns = [0.01, -0.005, 0.015, -0.01, 0.008, -0.003, 0.012, -0.007,
                   0.005, -0.002] * 10
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        status = monitor.evaluate_live_risk(*flat_book(returns))

        self.assertTrue(status.approved)
        self.assertFalse(status.var_metrics.is_breached)
        self.assertIsNone(status.breach_reason)
        self.assertLess(status.var_metrics.parametric_var_pct, 0.05)

    def test_volatile_book_breaches_and_blocks(self):
        returns = [0.05, -0.06, 0.07, -0.08, 0.06, -0.05, 0.04, -0.07,
                   0.08, -0.09] * 10
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        status = monitor.evaluate_live_risk(
            {"TSLA": 500.0}, {"TSLA": 200.0}, {"TSLA": returns}, 100000.0)

        self.assertFalse(status.approved)
        self.assertTrue(status.var_metrics.is_breached)
        self.assertIn("LIVE VAR BREACH", status.breach_reason)
        self.assertGreater(status.var_metrics.parametric_var_pct, 0.05)

    def test_breach_reason_names_the_measure_that_actually_breached(self):
        """
        Regression (2.0.0): the reason string used to quote the PARAMETRIC figure
        whatever tripped the limit, so a 30% historical breach was reported as
        "1-Day VaR (10.40%) >= limit (5.00%)" -- an audit record contradicting
        itself. A near-constant series with one crash breaches on the historical
        measure while the parametric measure stays under the limit. Three -8% days
        in 250 put the 3rd-worst loss (k = ceil(250 * 0.01) = 3) at 8% while the
        sample standard deviation keeps parametric VaR near 2.1%.
        """
        returns = [-0.08] * 3 + [0.0005] * 247
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        status = monitor.evaluate_live_risk(*flat_book(returns))

        m = status.var_metrics
        self.assertLess(m.parametric_var_pct, 0.05)       # parametric does NOT breach
        self.assertGreaterEqual(m.historical_var_pct, 0.05)
        self.assertEqual(m.breaching_measures, ("historical_var",))
        self.assertAlmostEqual(m.binding_var_pct, m.historical_var_pct, places=12)
        self.assertFalse(status.approved)
        self.assertAlmostEqual(m.parametric_var_pct, 0.020898, places=6)
        self.assertAlmostEqual(m.historical_var_pct, 0.08, places=12)
        self.assertIn("Breaching measure(s): historical_var.", status.breach_reason)
        # The binding value quoted is the historical one, not the parametric one.
        self.assertIn("8.00% of NAV", status.breach_reason)

    def test_both_measures_can_breach_and_binding_value_is_the_larger(self):
        returns = ([-0.09, 0.08] * 124) + [-0.40, 0.08]
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        m = monitor.compute_var_metrics(*flat_book(returns))

        self.assertEqual(set(m.breaching_measures), {"parametric_var", "historical_var"})
        self.assertAlmostEqual(
            m.binding_var_pct,
            max(m.parametric_var_pct, m.historical_var_pct),
            places=12,
        )

    def test_breach_is_inclusive_at_the_exact_limit(self):
        # Constant -5% every day: sigma = 0, so parametric VaR = -mu = 0.05 exactly,
        # and historical VaR = 0.05 exactly. Both sit ON the 5% limit.
        returns = [-0.05] * 120
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        m = monitor.compute_var_metrics(*flat_book(returns))

        self.assertAlmostEqual(m.portfolio_volatility_pct, 0.0, places=12)
        self.assertAlmostEqual(m.parametric_var_pct, 0.05, places=12)
        self.assertAlmostEqual(m.historical_var_pct, 0.05, places=12)
        self.assertTrue(m.is_breached)       # >= limit, not > limit

    def test_cvar_limit_is_off_by_default_and_effective_when_set(self):
        """CVaR must stay out of the verdict unless a cvar_limit_pct is supplied."""
        returns = [-0.30, -0.25, -0.20] + [0.001] * 247   # severe but rare tail
        book = flat_book(returns)

        off = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.30)
        m_off = off.compute_var_metrics(*book)
        self.assertNotIn("cvar", m_off.breaching_measures)

        on = LiveValueAtRiskMonitor(
            confidence_level=0.99, var_limit_pct=0.30, cvar_limit_pct=0.10)
        m_on = on.compute_var_metrics(*book)
        self.assertIn("cvar", m_on.breaching_measures)
        self.assertTrue(m_on.is_breached)
        # Same numbers either way -- only the verdict changes.
        self.assertAlmostEqual(m_on.cvar_pct, m_off.cvar_pct, places=12)

    def test_risk_reducing_order_is_allowed_through_a_live_breach(self):
        """
        A breach must not block the trades that would cure it. The override keeps
        ``breach_reason`` populated so the approval stays auditable.
        """
        returns = [0.05, -0.06, 0.07, -0.08, 0.06, -0.05, 0.04, -0.07,
                   0.08, -0.09] * 10
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        book = ({"TSLA": 500.0}, {"TSLA": 200.0}, {"TSLA": returns}, 100000.0)

        blocked = monitor.evaluate_live_risk(*book)
        allowed = monitor.evaluate_live_risk(*book, is_risk_reducing=True)

        self.assertFalse(blocked.approved)
        self.assertFalse(blocked.risk_reducing_override)
        self.assertTrue(allowed.approved)
        self.assertTrue(allowed.risk_reducing_override)
        self.assertTrue(allowed.var_metrics.is_breached)
        self.assertIn("LIVE VAR BREACH", allowed.breach_reason)

    def test_risk_reducing_flag_does_not_fabricate_a_breach(self):
        returns = [0.001, -0.002] * 60
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        status = monitor.evaluate_live_risk(*flat_book(returns), is_risk_reducing=True)
        self.assertTrue(status.approved)
        self.assertFalse(status.risk_reducing_override)
        self.assertIsNone(status.breach_reason)


class TestRegressionsForFixedDefects(unittest.TestCase):

    def test_ragged_return_series_are_rejected_not_front_truncated(self):
        """
        Regression (2.0.0): ``n = min(len(...))`` plus indexing ``0..n-1`` paired
        the OLDEST observations of a long series with the recent observations of a
        short one. A 50/50 book of one asset at +2% and one at -2% daily has a true
        VaR of 0; front-truncation reported 1.69% parametric / 1.00% historical.
        """
        long_series = [0.0] * 100 + [0.02] * 120     # 220 observations
        short_series = [-0.02] * 120                 # 120 observations
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)

        with self.assertRaises(VaRMonitorError) as ctx:
            monitor.compute_var_metrics(
                {"A": 500.0, "B": 500.0},
                {"A": 100.0, "B": 100.0},
                {"A": long_series, "B": short_series},
                100000.0,
            )
        self.assertIn("lengths differ", str(ctx.exception))

        # Correctly aligned (the caller trims to the common window) -> VaR is 0.
        aligned = monitor.compute_var_metrics(
            {"A": 500.0, "B": 500.0},
            {"A": 100.0, "B": 100.0},
            {"A": long_series[-120:], "B": short_series},
            100000.0,
        )
        self.assertAlmostEqual(aligned.parametric_var_pct, 0.0, places=12)
        self.assertAlmostEqual(aligned.historical_var_pct, 0.0, places=12)

    def test_held_symbol_without_return_history_raises_var_monitor_error(self):
        """Regression (2.0.0): this used to escape as a bare ``KeyError``."""
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        with self.assertRaises(VaRMonitorError) as ctx:
            monitor.compute_var_metrics(
                {"AAPL": 10.0, "MSFT": 5.0},
                {"AAPL": 100.0, "MSFT": 200.0},
                {"AAPL": [0.01] * 120},
                100000.0,
            )
        self.assertIn("MSFT", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, KeyError)

    def test_held_symbol_without_price_raises_var_monitor_error(self):
        """An unpriced position used to be silently dropped from the risk number."""
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        with self.assertRaises(VaRMonitorError) as ctx:
            monitor.compute_var_metrics(
                {"AAPL": 10.0, "MSFT": 5.0},
                {"AAPL": 100.0},
                {"AAPL": [0.01] * 120, "MSFT": [0.01] * 120},
                100000.0,
            )
        self.assertIn("MSFT", str(ctx.exception))
        self.assertIn("price", str(ctx.exception).lower())

    def test_non_finite_inputs_fail_closed_as_var_monitor_error(self):
        """
        Regression (2.0.0): NaN/Inf used to surface as
        ``AttributeError: 'float' object has no attribute 'numerator'`` from
        ``statistics`` internals, which slips past ``except VaRMonitorError``.
        """
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        good = [0.01, -0.02] * 60

        nan_returns = list(good)
        nan_returns[7] = float("nan")
        cases = {
            "nan return": ({"X": 1000.0}, {"X": 100.0}, {"X": nan_returns}, 100000.0),
            "nan price": ({"X": 1000.0}, {"X": float("nan")}, {"X": good}, 100000.0),
            "inf price": ({"X": 1000.0}, {"X": float("inf")}, {"X": good}, 100000.0),
            "nan quantity": ({"X": float("nan")}, {"X": 100.0}, {"X": good}, 100000.0),
            "nan nav": ({"X": 1000.0}, {"X": 100.0}, {"X": good}, float("nan")),
        }
        for label, args in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(VaRMonitorError):
                    monitor.compute_var_metrics(*args)

    def test_overflowing_position_weight_fails_closed(self):
        """
        Found in adversarial review: quantity * price / NAV can overflow to inf even
        when each input is individually finite, and the inf then surfaced from
        ``statistics.fmean`` as a bare ``ValueError`` ("-inf + inf in fsum") rather
        than a ``VaRMonitorError``.
        """
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        with self.assertRaises(VaRMonitorError) as ctx:
            monitor.compute_var_metrics(
                {"X": 1e300}, {"X": 1e300}, {"X": [0.01, -0.02] * 60}, 1e-300)
        self.assertIn("weight", str(ctx.exception).lower())

    def test_negative_price_is_rejected(self):
        """A negative price silently inverted the sign of the position's weight."""
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        with self.assertRaises(VaRMonitorError) as ctx:
            monitor.compute_var_metrics(
                {"X": 1000.0}, {"X": -100.0}, {"X": [0.01, -0.02] * 60}, 100000.0)
        self.assertIn("negative quantity", str(ctx.exception))

    def test_unsupported_confidence_level_uses_the_correct_quantile(self):
        """
        Regression (2.0.0): the z lookup fell back to 2.326 for any level outside
        {0.90, 0.95, 0.99}, so a 99.9% monitor UNDERSTATED VaR by ~25% and a 97.5%
        monitor overstated it by ~19%.
        """
        returns = [0.01, -0.02] * 125
        for level, true_z in ((0.975, 1.959964), (0.999, 3.090232), (0.99, 2.326348)):
            with self.subTest(confidence_level=level):
                monitor = LiveValueAtRiskMonitor(
                    confidence_level=level, var_limit_pct=0.99, min_observations=100)
                m = monitor.compute_var_metrics(*flat_book(returns))
                expected = true_z * statistics.stdev(returns) - statistics.fmean(returns)
                self.assertAlmostEqual(m.parametric_var_pct, expected, places=6)

        # And the levels are ordered as quantiles must be.
        def var_at(level):
            mon = LiveValueAtRiskMonitor(
                confidence_level=level, var_limit_pct=0.99, min_observations=100)
            return mon.compute_var_metrics(*flat_book(returns)).parametric_var_pct

        self.assertLess(var_at(0.95), var_at(0.99))
        self.assertLess(var_at(0.99), var_at(0.999))

    def test_sample_shorter_than_the_tail_bucket_is_rejected(self):
        """
        Regression (2.0.0): a 10-observation sample was accepted for a 99% VaR, and
        the "99% quantile" was then just the single worst of ten returns, with CVaR
        identical to it. The floor is now ceil(1/(1-c)) = 100 at 99%.
        """
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        self.assertEqual(monitor.required_observations(), 100)

        with self.assertRaises(VaRMonitorError) as ctx:
            monitor.compute_var_metrics(*flat_book([0.01, -0.02] * 5))   # n = 10
        self.assertIn("Insufficient return history", str(ctx.exception))

        # 99 is still short; 100 is accepted.
        with self.assertRaises(VaRMonitorError):
            monitor.compute_var_metrics(*flat_book([0.01, -0.02] * 49 + [0.01]))
        ok = monitor.compute_var_metrics(*flat_book([0.01, -0.02] * 50))
        self.assertEqual(ok.observations_used, 100)

    def test_min_observations_override_is_explicit_and_floored_at_two(self):
        relaxed = LiveValueAtRiskMonitor(
            confidence_level=0.99, var_limit_pct=0.05, min_observations=10)
        self.assertEqual(relaxed.required_observations(), 10)
        m = relaxed.compute_var_metrics(*flat_book([0.01, -0.02] * 5))
        self.assertEqual(m.observations_used, 10)

        floored = LiveValueAtRiskMonitor(
            confidence_level=0.99, var_limit_pct=0.05, min_observations=1)
        self.assertEqual(floored.required_observations(), 2)

    def test_short_sample_emits_a_one_year_observation_period_warning(self):
        monitor = LiveValueAtRiskMonitor(
            confidence_level=0.99, var_limit_pct=0.99, min_observations=100)
        with self.assertLogs("var_monitor", level=logging.WARNING) as captured:
            monitor.compute_var_metrics(*flat_book([0.01, -0.02] * 50))
        self.assertTrue(any("one-year" in line for line in captured.output))

    def test_invalid_configuration_is_rejected_at_construction(self):
        """
        Regression (2.0.0): ``confidence_level=1.5`` used to construct fine and then
        produce a NEGATIVE quantile index, reading the PROFIT tail and reporting a
        0.0% historical VaR on a loss-making book -- the breaker failing open.
        """
        bad_configs = [
            {"confidence_level": 1.5},
            {"confidence_level": 1.0},
            {"confidence_level": 0.5},
            {"confidence_level": 0.01},
            {"confidence_level": float("nan")},
            {"var_limit_pct": 0.0},
            {"var_limit_pct": -1.0},
            {"var_limit_pct": float("inf")},
            {"cvar_limit_pct": -0.1},
            {"min_observations": -5},
            {"min_observations": 3.5},
        ]
        for kwargs in bad_configs:
            with self.subTest(**kwargs):
                with self.assertRaises(VaRMonitorError):
                    LiveValueAtRiskMonitor(**kwargs)

    def test_non_positive_nav_is_rejected(self):
        monitor = LiveValueAtRiskMonitor(confidence_level=0.99, var_limit_pct=0.05)
        for nav in (0.0, -1000.0):
            with self.subTest(nav=nav):
                with self.assertRaises(VaRMonitorError):
                    monitor.compute_var_metrics(
                        {"X": 1000.0}, {"X": 100.0}, {"X": [0.01, -0.02] * 60}, nav)


class TestBackwardCompatibleShapes(unittest.TestCase):

    def test_original_dataclass_fields_still_construct_positionally(self):
        m = VaRMetrics(0.99, 5000.0, 0.05, 4800.0, 0.048, 6000.0, 0.06, True)
        self.assertEqual(m.confidence_level, 0.99)
        self.assertTrue(m.is_breached)
        self.assertEqual(m.breaching_measures, ())
        self.assertEqual(m.tail_observations_used, 0)

        status = LiveRiskStatus(approved=False, var_metrics=m, breach_reason="x")
        self.assertFalse(status.risk_reducing_override)


if __name__ == "__main__":
    unittest.main()
