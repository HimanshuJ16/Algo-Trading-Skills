"""Unit tests for the strategy performance decay diagnostic engine.

The statistical tests here derive their expected values independently of the
implementation:
  * Sharpe expectations come from closed-form algebra on hand-built series.
  * The Sharpe-difference standard error is re-derived through the delta method from
    the covariance matrix Omega that Ledoit & Wolf (2008) attribute to Jobson & Korkie
    (1981) / Memmel (2003) -- a matrix computation, not a restatement of the scalar
    formula the engine uses.
  * Calibration is checked by simulation against the nominal false-positive rate,
    which is the property the -1.96 threshold actually claims.
"""
import math
import unittest

import numpy as np
import pandas as pd

from strategy_performance_decay_detection_vs_market_wide_decay import (
    DecayClassification,
    DecayDiagnosticError,
    StrategyDecayDiagnosticsReport,
    StrategyPerformanceDecayDiagnosticEngine,
)


def _delta_method_sharpe_diff_se(target: np.ndarray, peer: np.ndarray) -> float:
    """Standard error of the per-period Sharpe difference, re-derived independently.

    Uses the delta method on nabla_f' Omega nabla_f with the i.i.d.-bivariate-normal
    Omega of Jobson & Korkie (1981) / Memmel (2003), as reproduced in Ledoit & Wolf
    (2008, Section 2). Deliberately expressed as a matrix product so that it does not
    reuse the engine's closed-form scalar expression.
    """
    n = len(target)
    mu_t, mu_p = float(np.mean(target)), float(np.mean(peer))
    var_t = float(np.var(target, ddof=1))
    var_p = float(np.var(peer, ddof=1))
    cov = float(np.cov(target, peer, ddof=1)[0, 1])

    # f(a, b, c, d) = a / sqrt(c) - b / sqrt(d) evaluated at (mu_t, mu_p, var_t, var_p).
    grad = np.array([
        1.0 / math.sqrt(var_t),
        -1.0 / math.sqrt(var_p),
        -0.5 * mu_t * var_t ** -1.5,
        0.5 * mu_p * var_p ** -1.5,
    ])
    omega = np.array([
        [var_t, cov, 0.0, 0.0],
        [cov, var_p, 0.0, 0.0],
        [0.0, 0.0, 2.0 * var_t ** 2, 2.0 * cov ** 2],
        [0.0, 0.0, 2.0 * cov ** 2, 2.0 * var_p ** 2],
    ])
    return math.sqrt(float(grad @ omega @ grad) / n)


def _standardize(shocks: np.ndarray) -> np.ndarray:
    """Rescales shocks to exact sample mean 0 and exact sample std 1 (ddof=1)."""
    centred = shocks - shocks.mean()
    return centred / centred.std(ddof=1)


def _series_with(mean: float, std: float, shocks: np.ndarray) -> pd.Series:
    """Builds a series whose sample mean and sample std are exactly `mean` and `std`.

    Lets a test state the annualized Sharpe ratio it wants rather than drawing one and
    hoping the sample lands where the assertion needs it.
    """
    return pd.Series(mean + std * _standardize(shocks))


class TestConstructorValidation(unittest.TestCase):
    def test_rejects_non_positive_or_non_integral_window(self):
        for bad in (0, -10, 2.5, "60", None):
            with self.subTest(bad=bad):
                with self.assertRaises(DecayDiagnosticError):
                    StrategyPerformanceDecayDiagnosticEngine(rolling_window_days=bad)

    def test_rejects_window_of_one(self):
        # A single observation has no sample standard deviation.
        with self.assertRaises(DecayDiagnosticError):
            StrategyPerformanceDecayDiagnosticEngine(rolling_window_days=1)

    def test_rejects_non_negative_z_threshold(self):
        # The test only fires on underperformance; a positive threshold would invert it.
        for bad in (0.0, 1.96, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(DecayDiagnosticError):
                    StrategyPerformanceDecayDiagnosticEngine(idiosyncratic_z_threshold=bad)

    def test_rejects_bad_periods_per_year_and_threshold(self):
        with self.assertRaises(DecayDiagnosticError):
            StrategyPerformanceDecayDiagnosticEngine(periods_per_year=0)
        with self.assertRaises(DecayDiagnosticError):
            StrategyPerformanceDecayDiagnosticEngine(periods_per_year=252.5)
        with self.assertRaises(DecayDiagnosticError):
            StrategyPerformanceDecayDiagnosticEngine(market_wide_sharpe_threshold=float("nan"))


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyPerformanceDecayDiagnosticEngine()
        rng = np.random.default_rng(11)
        self.clean = pd.Series(rng.normal(0.001, 0.01, 120))

    def test_interior_nan_is_rejected_not_dropped(self):
        # Dropping a NaN makes non-adjacent periods adjacent and shrinks the sample
        # without saying so; the previous implementation did exactly that.
        dirty = self.clean.copy()
        dirty.iloc[40] = np.nan
        with self.assertRaises(DecayDiagnosticError) as ctx:
            self.engine.evaluate_decay_cause("s", "p", dirty, self.clean)
        self.assertIn("NaN/Inf", str(ctx.exception))

    def test_infinite_value_is_rejected(self):
        dirty = self.clean.copy()
        dirty.iloc[3] = np.inf
        with self.assertRaises(DecayDiagnosticError):
            self.engine.evaluate_decay_cause("s", "p", dirty, self.clean)

    def test_return_at_or_below_minus_one_is_rejected(self):
        dirty = self.clean.copy()
        dirty.iloc[7] = -1.0
        with self.assertRaises(DecayDiagnosticError) as ctx:
            self.engine.evaluate_decay_cause("s", "p", dirty, self.clean)
        self.assertIn("-100%", str(ctx.exception))

    def test_duplicate_index_labels_are_rejected(self):
        dup = pd.Series(self.clean.to_numpy(), index=[0] * 120)
        with self.assertRaises(DecayDiagnosticError) as ctx:
            self.engine.evaluate_decay_cause("s", "p", dup, self.clean)
        self.assertIn("duplicate", str(ctx.exception))

    def test_unsorted_index_is_rejected(self):
        shuffled = self.clean.sample(frac=1.0, random_state=3)
        with self.assertRaises(DecayDiagnosticError) as ctx:
            self.engine.evaluate_decay_cause("s", "p", shuffled, self.clean)
        self.assertIn("sorted ascending", str(ctx.exception))

    def test_disjoint_indices_report_alignment_not_history(self):
        other = pd.Series(self.clean.to_numpy(), index=range(500, 620))
        with self.assertRaises(DecayDiagnosticError) as ctx:
            self.engine.evaluate_decay_cause("s", "p", self.clean, other)
        self.assertIn("no aligned observations", str(ctx.exception))

    def test_insufficient_history_is_rejected(self):
        short = self.clean.iloc[:59]
        with self.assertRaises(DecayDiagnosticError) as ctx:
            self.engine.evaluate_decay_cause("s", "p", short, self.clean.iloc[:59])
        self.assertIn("59", str(ctx.exception))

    def test_two_dimensional_and_non_numeric_input_rejected(self):
        with self.assertRaises(DecayDiagnosticError):
            self.engine.evaluate_decay_cause("s", "p", np.zeros((10, 2)), self.clean)
        with self.assertRaises(DecayDiagnosticError):
            self.engine.evaluate_decay_cause("s", "p", ["a"] * 120, self.clean)

    def test_non_finite_risk_free_rate_rejected(self):
        with self.assertRaises(DecayDiagnosticError):
            self.engine.evaluate_decay_cause(
                "s", "p", self.clean, self.clean, risk_free_rate_annual_pct=float("nan")
            )

    def test_partial_overlap_is_aligned_and_reported(self):
        peer = pd.Series(self.clean.to_numpy()[:100], index=range(20, 120))
        report = self.engine.evaluate_decay_cause("s", "p", self.clean, peer)
        self.assertEqual(report.observations, 60)
        self.assertTrue(any("dropped during alignment" in w for w in report.warnings))


class TestSharpeArithmetic(unittest.TestCase):
    """Closed-form checks of the annualized Sharpe ratio."""

    def test_alternating_series_matches_closed_form(self):
        # 60 observations alternating 0.02 / 0.00 with a zero risk-free rate:
        #   mean = 0.01 exactly, deviations = +/-0.01,
        #   s = sqrt(60 * 1e-4 / 59) = 0.01 * sqrt(60/59),
        #   Sharpe_per_period = sqrt(59/60), annualized = sqrt(252 * 59 / 60).
        engine = StrategyPerformanceDecayDiagnosticEngine(rolling_window_days=60)
        pattern = np.array([0.02, 0.0] * 30)
        report = engine.evaluate_decay_cause(
            "alt", "shifted", pd.Series(pattern),
            pd.Series(np.roll(pattern, 1)), risk_free_rate_annual_pct=0.0,
        )
        expected = math.sqrt(252.0 * 59.0 / 60.0)
        self.assertAlmostEqual(report.target_sharpe, expected, places=10)
        self.assertAlmostEqual(report.peer_benchmark_sharpe, expected, places=10)
        # Identical Sharpe ratios must give a zero difference and a zero statistic even
        # though the two series are perfectly negatively correlated.
        self.assertAlmostEqual(report.relative_excess_sharpe, 0.0, places=12)
        self.assertAlmostEqual(report.relative_sharpe_z_score, 0.0, places=10)
        self.assertAlmostEqual(report.p_value, 0.5, places=10)

    def test_risk_free_rate_is_deducted_per_period(self):
        # Constant-volatility series: raising the annual risk-free rate by 1 percentage
        # point must lower the annualized Sharpe by exactly 0.01 / annualized_sigma.
        engine = StrategyPerformanceDecayDiagnosticEngine(rolling_window_days=60)
        rng = np.random.default_rng(5)
        target = pd.Series(rng.normal(0.001, 0.01, 60))
        peer = pd.Series(rng.normal(0.001, 0.01, 60))
        low = engine.evaluate_decay_cause("s", "p", target, peer, risk_free_rate_annual_pct=0.0)
        high = engine.evaluate_decay_cause("s", "p", target, peer, risk_free_rate_annual_pct=1.0)
        sigma_annual = float(np.std(target.to_numpy(), ddof=1)) * math.sqrt(252)
        self.assertAlmostEqual(low.target_sharpe - high.target_sharpe, 0.01 / sigma_annual, places=9)


class TestSharpeDifferenceStatistic(unittest.TestCase):
    """The Memmel-corrected Jobson-Korkie statistic."""

    def setUp(self):
        self.engine = StrategyPerformanceDecayDiagnosticEngine(rolling_window_days=120)

    def test_matches_independent_delta_method_derivation(self):
        rng = np.random.default_rng(20)
        chol = np.array([[1.0, 0.0], [0.6, math.sqrt(1.0 - 0.36)]])
        for trial in range(5):
            with self.subTest(trial=trial):
                shocks = rng.standard_normal((120, 2)) @ chol.T
                target = 0.0012 + 0.011 * shocks[:, 0]
                peer = 0.0009 + 0.009 * shocks[:, 1]
                report = self.engine.evaluate_decay_cause(
                    "s", "p", pd.Series(target), pd.Series(peer), risk_free_rate_annual_pct=0.0
                )
                sharpe_t = float(np.mean(target)) / float(np.std(target, ddof=1))
                sharpe_p = float(np.mean(peer)) / float(np.std(peer, ddof=1))
                expected_z = (sharpe_t - sharpe_p) / _delta_method_sharpe_diff_se(target, peer)
                self.assertAlmostEqual(report.relative_sharpe_z_score, expected_z, places=9)

    def test_statistic_is_invariant_to_annualization_frequency(self):
        # z tests equality of Sharpe ratios; numerator and standard error scale
        # together, so the frequency changes the reported Sharpe but not the verdict.
        # Invariance holds for a fixed excess-return series, so the risk-free rate is
        # set to zero: a non-zero annual rate is divided by periods_per_year and would
        # deduct a different amount per period at each frequency.
        rng = np.random.default_rng(21)
        target = pd.Series(rng.normal(0.0005, 0.01, 120))
        peer = pd.Series(rng.normal(0.0015, 0.01, 120))
        daily = StrategyPerformanceDecayDiagnosticEngine(rolling_window_days=120, periods_per_year=252)
        monthly = StrategyPerformanceDecayDiagnosticEngine(rolling_window_days=120, periods_per_year=12)
        d = daily.evaluate_decay_cause("s", "p", target, peer, risk_free_rate_annual_pct=0.0)
        m = monthly.evaluate_decay_cause("s", "p", target, peer, risk_free_rate_annual_pct=0.0)
        self.assertAlmostEqual(d.relative_sharpe_z_score, m.relative_sharpe_z_score, places=9)
        self.assertAlmostEqual(d.target_sharpe / m.target_sharpe, math.sqrt(252.0 / 12.0), places=9)

    def test_correlation_is_used_and_reported(self):
        # The peer correlation is the dominant term in the variance of a Sharpe
        # difference. Holding both Sharpe ratios fixed, raising the correlation must
        # shrink the standard error and so enlarge |z|.
        rng = np.random.default_rng(22)
        base = _standardize(rng.standard_normal(120))
        # Gram-Schmidt against `base` so the two shock vectors are exactly orthogonal
        # and the realized sample correlation equals the target rho to the last bit.
        raw = _standardize(rng.standard_normal(120))
        projection = float(np.dot(raw, base)) / float(np.dot(base, base))
        orthogonal = _standardize(raw - projection * base)
        magnitudes = {}
        for rho in (0.0, 0.9):
            peer_shocks = rho * base + math.sqrt(1.0 - rho ** 2) * orthogonal
            # Both legs are rescaled to fixed mean and std, so the sample Sharpe ratios
            # are identical across rho and only the correlation term moves.
            report = self.engine.evaluate_decay_cause(
                "s", "p",
                _series_with(0.0010, 0.01, base),
                _series_with(0.0025, 0.01, peer_shocks),
                risk_free_rate_annual_pct=0.0,
            )
            self.assertAlmostEqual(report.return_correlation, rho, places=9)
            magnitudes[rho] = abs(report.relative_sharpe_z_score)
        self.assertGreater(magnitudes[0.9], magnitudes[0.0])

    def test_null_calibration_matches_nominal_false_positive_rate(self):
        # Two true-null strategies with equal Sharpe ratios and correlated returns. The
        # -1.96 threshold claims a 2.5% one-sided false-positive rate; this measures it.
        #
        # Regression on both sides. The pre-fix statistic compared the current Sharpe
        # difference against the dispersion of *overlapping* rolling Sharpe differences.
        # Overlapping windows are strongly autocorrelated, so that dispersion is not a
        # standard error: on this fixture it rejected the true null 6.6% of the time,
        # telling healthy strategies to decommission at over twice the advertised rate.
        # Given only `rolling_window` observations the same code instead produced a
        # hardcoded z of 0.0 and could never reject at all -- hence the lower bound.
        engine = StrategyPerformanceDecayDiagnosticEngine(rolling_window_days=60)
        rng = np.random.default_rng(2024)
        chol = np.array([[1.0, 0.0], [0.7, math.sqrt(1.0 - 0.49)]])
        trials = 2000
        rejections = 0
        for _ in range(trials):
            # 260 observations of history so the pre-fix rolling path is exercised too.
            shocks = rng.standard_normal((260, 2)) @ chol.T
            report = engine.evaluate_decay_cause(
                "s", "p",
                pd.Series(0.0008 + 0.01 * shocks[:, 0]),
                pd.Series(0.0008 + 0.01 * shocks[:, 1]),
            )
            if report.relative_sharpe_z_score <= -1.96:
                rejections += 1
        rate = rejections / trials
        self.assertLess(rate, 0.045, f"one-sided rejection rate {rate:.3%} far above the nominal 2.5%")
        self.assertGreater(rate, 0.010, f"one-sided rejection rate {rate:.3%} implausibly conservative")

    def test_threshold_is_inclusive_at_the_boundary(self):
        rng = np.random.default_rng(23)
        target = pd.Series(rng.normal(-0.0020, 0.01, 120))
        peer = pd.Series(rng.normal(0.0020, 0.01, 120))
        observed = self.engine.evaluate_decay_cause("s", "p", target, peer).relative_sharpe_z_score
        exactly_at = StrategyPerformanceDecayDiagnosticEngine(
            rolling_window_days=120, idiosyncratic_z_threshold=observed
        )
        just_beyond = StrategyPerformanceDecayDiagnosticEngine(
            rolling_window_days=120, idiosyncratic_z_threshold=observed - 1e-9
        )
        self.assertEqual(
            exactly_at.evaluate_decay_cause("s", "p", target, peer).classification,
            DecayClassification.IDIOSYNCRATIC_ALPHA_DECAY,
        )
        self.assertNotEqual(
            just_beyond.evaluate_decay_cause("s", "p", target, peer).classification,
            DecayClassification.IDIOSYNCRATIC_ALPHA_DECAY,
        )


class TestClassification(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyPerformanceDecayDiagnosticEngine()

    def test_idiosyncratic_alpha_decay_detection(self):
        rng = np.random.default_rng(42)
        decayed = pd.Series(np.concatenate([
            rng.normal(0.0010, 0.01, 60), rng.normal(-0.0015, 0.01, 60)
        ]))
        healthy_peers = pd.Series(rng.normal(0.0015, 0.01, 120))
        report = self.engine.evaluate_decay_cause(
            "DECAYED_ALPHA_STRAT", "STAT_ARB_PEER_INDEX", decayed, healthy_peers
        )
        self.assertEqual(report.classification, DecayClassification.IDIOSYNCRATIC_ALPHA_DECAY)
        self.assertLessEqual(report.relative_sharpe_z_score, -1.96)
        self.assertLess(report.p_value, 0.025)
        self.assertLess(report.target_sharpe, 0.50)
        self.assertGreaterEqual(report.peer_benchmark_sharpe, 0.50)
        self.assertIn("DECOMMISSION", report.recommended_action)

    def test_diagnosis_available_at_exactly_the_minimum_window(self):
        # Regression: with exactly `rolling_window` observations the previous
        # implementation had a one-element rolling history, fell through to a hardcoded
        # z of 0.0, and returned INCONCLUSIVE for a strategy at an annualized Sharpe of
        # -8 against peers at +1.7. The statistic must be computable on the window.
        rng = np.random.default_rng(1)
        target = pd.Series(rng.normal(-0.004, 0.01, 60))
        peer = pd.Series(rng.normal(0.0015, 0.01, 60))
        report = self.engine.evaluate_decay_cause("s", "p", target, peer)
        self.assertEqual(report.observations, 60)
        self.assertIsNotNone(report.relative_sharpe_z_score)
        self.assertEqual(report.classification, DecayClassification.IDIOSYNCRATIC_ALPHA_DECAY)

    def test_market_wide_regime_shift_detection(self):
        rng = np.random.default_rng(7)
        target = pd.Series(np.concatenate([
            rng.normal(0.0010, 0.01, 60), rng.normal(-0.0015, 0.01, 60)
        ]))
        peer = pd.Series(np.concatenate([
            rng.normal(0.0010, 0.01, 60), rng.normal(-0.0010, 0.01, 60)
        ]))
        report = self.engine.evaluate_decay_cause("TREND_STRAT", "TREND_PEER_INDEX", target, peer)
        self.assertEqual(report.classification, DecayClassification.MARKET_WIDE_REGIME_SHIFT)
        self.assertLess(report.target_sharpe, 0.50)
        self.assertLess(report.peer_benchmark_sharpe, 0.50)
        self.assertIn("PAUSE_OR_REDUCE_RISK", report.recommended_action)

    def test_healthy_strategy_is_never_told_to_decommission(self):
        # Regression: the pre-fix classifier fired IDIOSYNCRATIC_ALPHA_DECAY on
        # significance and peer health alone, so a strategy at an annualized Sharpe of
        # 3.34 was told "Strategy alpha is dead ... Initiate decommissioning" purely
        # because its peers ran hotter. Losing peers is an allocation question.
        rng = np.random.default_rng(31)
        shocks = rng.standard_normal(120)
        target = pd.Series(0.0016 + 0.01 * shocks)
        peer = pd.Series(0.0060 + 0.01 * shocks)
        report = self.engine.evaluate_decay_cause("s", "p", target, peer)
        self.assertGreaterEqual(report.target_sharpe, 0.50)
        self.assertLessEqual(report.relative_sharpe_z_score, -1.96)
        self.assertEqual(report.classification, DecayClassification.HEALTHY)
        self.assertNotIn("DECOMMISSION", report.recommended_action)
        self.assertTrue(any("capital-allocation question" in w for w in report.warnings))

    def test_constant_series_is_inconclusive_not_impaired(self):
        # Regression: a zero-volatility strategy previously reported Sharpe 0.0, which
        # sits below the 0.50 health threshold and classified a strictly profitable
        # strategy as decayed.
        rng = np.random.default_rng(9)
        constant = pd.Series([0.0005] * 120)
        peer = pd.Series(rng.normal(0.0010, 0.01, 120))
        report = self.engine.evaluate_decay_cause("s", "p", constant, peer)
        self.assertEqual(report.classification, DecayClassification.INCONCLUSIVE)
        self.assertIsNone(report.relative_sharpe_z_score)
        self.assertIsNone(report.p_value)
        self.assertTrue(math.isnan(report.target_sharpe))
        self.assertTrue(any("undefined, not zero" in w for w in report.warnings))

    def test_identical_series_have_no_defined_test(self):
        # np.corrcoef(v, v) returns 0.9999999999999999 for a sizeable fraction of real
        # inputs, so an absolute floor on the variance made this case classify
        # INCONCLUSIVE or HEALTHY depending on the last bit of the correlation. The
        # guard is on the dimensionless bracket, which is O(1) for any distinct pair.
        # Swept over many seeds so the assertion cannot pass by floating-point luck.
        for seed in range(40):
            with self.subTest(seed=seed):
                rng = np.random.default_rng(seed)
                series = pd.Series(rng.normal(0.001, 0.01, 120))
                report = self.engine.evaluate_decay_cause("s", "s_clone", series, series.copy())
                self.assertIsNone(report.relative_sharpe_z_score)
                self.assertIsNone(report.p_value)
                self.assertEqual(report.classification, DecayClassification.INCONCLUSIVE)
                self.assertAlmostEqual(report.relative_excess_sharpe, 0.0, places=12)

    def test_near_identical_series_with_different_sharpe_still_tests(self):
        # rho = 1 but unequal Sharpe ratios is not degenerate: the bracket reduces to
        # 0.5 * (Sh_t - Sh_p)^2, giving z = -sqrt(2T) exactly. Guarding on raw variance
        # magnitude rather than the dimensionless bracket would swallow this real case.
        rng = np.random.default_rng(29)
        shocks = rng.standard_normal(60)
        report = self.engine.evaluate_decay_cause(
            "s", "p",
            pd.Series(0.0005 + 0.01 * shocks),
            pd.Series(0.0040 + 0.01 * shocks),
            risk_free_rate_annual_pct=0.0,
        )
        self.assertAlmostEqual(report.relative_sharpe_z_score, -math.sqrt(2 * 60), places=6)

    def test_inconclusive_when_target_lags_healthy_peers_insignificantly(self):
        # Built to exact annualized Sharpe ratios of 0.30 (impaired) and 1.00 (healthy)
        # after a 2% risk-free deduction. Over 60 daily observations that 0.70 Sharpe
        # gap is nowhere near significant, so the honest answer is "monitor", not
        # "decommission".
        rng = np.random.default_rng(17)
        rf_per_period = 0.02 / 252.0
        std = 0.01

        def raw_mean(annual_sharpe: float) -> float:
            return annual_sharpe / math.sqrt(252.0) * std + rf_per_period

        target = _series_with(raw_mean(0.30), std, rng.standard_normal(60))
        peer = _series_with(raw_mean(1.00), std, rng.standard_normal(60))
        report = self.engine.evaluate_decay_cause("s", "p", target, peer)
        self.assertAlmostEqual(report.target_sharpe, 0.30, places=9)
        self.assertAlmostEqual(report.peer_benchmark_sharpe, 1.00, places=9)
        self.assertGreater(report.relative_sharpe_z_score, -1.96)
        self.assertEqual(report.classification, DecayClassification.INCONCLUSIVE)
        self.assertIn("MONITOR_CLOSELY", report.recommended_action)

    def test_report_is_self_describing(self):
        rng = np.random.default_rng(19)
        report = self.engine.evaluate_decay_cause(
            "S1", "P1",
            pd.Series(rng.normal(0.001, 0.01, 120)),
            pd.Series(rng.normal(0.001, 0.01, 120)),
        )
        self.assertIsInstance(report, StrategyDecayDiagnosticsReport)
        self.assertEqual(report.observations, 60)
        self.assertIn("S1 vs P1", report.audit_notes)
        self.assertIn(report.classification.value, report.audit_notes)
        self.assertAlmostEqual(
            report.relative_excess_sharpe,
            report.target_sharpe - report.peer_benchmark_sharpe,
            places=12,
        )


if __name__ == "__main__":
    unittest.main()
