"""
Unit tests for monte-carlo-strategy-robustness-testing skill.

Tests:
1. Equity-curve metrics against hand-computed drawdown and terminal equity.
2. Nearest-rank quantile estimator against independently derived order statistics.
3. Sequence shuffling: permutation invariance of terminal equity, drawdown gating.
4. Bootstrap resampling: terminal-wealth dispersion that shuffling cannot produce.
5. Execution-noise injection: zero-noise identity, widened drawdown, one-sided
   cost via `mean_shift`, absorbing barrier under extreme noise.
6. Determinism: repeat runs reproduce, and the caller's global RNG is untouched.
7. Input validation: NaN/Inf, returns <= -100%, absolute-P&L misuse, short logs,
   and infeasible engine configuration.
"""
import logging
import random
import unittest

from monte_carlo_engine import (
    MIN_RECOMMENDED_SIMULATIONS,
    MonteCarloError,
    MonteCarloResult,
    MonteCarloRobustnessEngine,
)

# 30 trades, all strictly > -1.0, mildly profitable.
PROFITABLE_RETURNS = [0.03, 0.02, -0.01, 0.04, -0.005, 0.025, 0.03, -0.01, 0.02, 0.015] * 3

# 21 trades with -15% loss clusters; breaches a 20% drawdown limit on most paths.
HIGH_RISK_RETURNS = [0.05, -0.15, -0.15, 0.05, -0.15, -0.15, 0.10] * 3


def _compound(initial: float, returns) -> float:
    """Independently computed terminal equity: C * prod(1 + r_i)."""
    equity = initial
    for r in returns:
        equity *= (1.0 + r)
    return equity


class TestEquityCurveMetrics(unittest.TestCase):
    """
    Drawdown is exercised through `run_noise_injection(noise_std=0.0)`, which is
    the only public mode that preserves the original trade order exactly.
    """

    def test_max_drawdown_and_terminal_equity_match_hand_computed_values(self):
        # C = 100,000. Path: 110,000 (peak) -> 88,000 -> 92,400 -> 92,400 -> 92,400.
        # Trough drawdown = (110,000 - 88,000) / 110,000 = 0.20 exactly.
        # Terminal equity = 100,000 * 1.10 * 0.80 * 1.05 = 92,400.
        engine = MonteCarloRobustnessEngine(
            initial_capital=100000.0, max_drawdown_limit=0.50, num_simulations=500, seed=1)
        res = engine.run_noise_injection([0.10, -0.20, 0.05, 0.0, 0.0], noise_std=0.0)

        self.assertAlmostEqual(res.p95_max_drawdown, 0.20, places=12)
        self.assertAlmostEqual(res.p99_max_drawdown, 0.20, places=12)
        self.assertAlmostEqual(res.median_final_equity, 92400.0, places=6)

    def test_drawdown_is_measured_against_running_peak_not_initial_capital(self):
        # Equity never falls below the 100,000 starting capital, but it does fall
        # 10% from its 121,000 peak. A drawdown measured off initial capital
        # would report 0.0 here.
        engine = MonteCarloRobustnessEngine(
            initial_capital=100000.0, max_drawdown_limit=0.50, num_simulations=500, seed=1)
        res = engine.run_noise_injection([0.10, 0.10, -0.10, 0.0, 0.0], noise_std=0.0)

        self.assertAlmostEqual(res.p95_max_drawdown, 0.10, places=12)
        self.assertAlmostEqual(res.median_final_equity, 108900.0, places=6)


class TestQuantileEstimator(unittest.TestCase):

    def test_nearest_rank_matches_hand_computed_order_statistics(self):
        # n = 10, x_(k) = k. Nearest rank picks x_(ceil(q*n)).
        values = [float(v) for v in range(1, 11)]
        q = MonteCarloRobustnessEngine._nearest_rank_quantile
        self.assertEqual(q(values, 0.50), 5.0)   # ceil(5.0)  = 5
        self.assertEqual(q(values, 0.95), 10.0)  # ceil(9.5)  = 10
        self.assertEqual(q(values, 0.99), 10.0)  # ceil(9.9)  = 10
        self.assertEqual(q(values, 0.05), 1.0)   # ceil(0.5)  = 1

    def test_nearest_rank_returns_an_observed_value_never_an_interpolation(self):
        values = [0.10, 0.90]
        self.assertIn(MonteCarloRobustnessEngine._nearest_rank_quantile(values, 0.50), values)

    def test_nearest_rank_clamps_on_degenerate_input(self):
        # Regression guard: the previous `int(q * n)` indexing raised IndexError
        # for num_simulations = 0 and could index past the end.
        self.assertEqual(MonteCarloRobustnessEngine._nearest_rank_quantile([7.0], 0.99), 7.0)
        self.assertEqual(MonteCarloRobustnessEngine._nearest_rank_quantile([7.0], 0.0), 7.0)


class TestSequenceShuffling(unittest.TestCase):

    def setUp(self):
        self.engine = MonteCarloRobustnessEngine(
            initial_capital=100000.0, max_drawdown_limit=0.20,
            num_simulations=500, seed=42)

    def test_terminal_equity_is_permutation_invariant_and_flagged_as_such(self):
        # Multiplication commutes, so every permutation of a fixed multiset of
        # returns ends at the identical equity. `median_final_equity` is therefore
        # a constant, not a quantile of a distribution, and callers are told so.
        res = self.engine.run_sequence_shuffling(PROFITABLE_RETURNS)
        expected = _compound(100000.0, PROFITABLE_RETURNS)

        self.assertTrue(res.final_equity_is_path_invariant)
        self.assertEqual(res.mode, "SEQUENCE_SHUFFLING")
        self.assertAlmostEqual(res.median_final_equity, expected, places=6)

    def test_robust_strategy_passes_signoff(self):
        res = self.engine.run_sequence_shuffling(PROFITABLE_RETURNS)

        self.assertEqual(res.num_simulations, 500)
        self.assertLess(res.p95_max_drawdown, 0.20)
        self.assertLessEqual(res.p95_max_drawdown, res.p99_max_drawdown)
        self.assertEqual(res.risk_of_ruin_pct, 0.0)
        self.assertTrue(res.is_robust)

    def test_high_drawdown_strategy_fails_signoff(self):
        res = self.engine.run_sequence_shuffling(HIGH_RISK_RETURNS)

        self.assertGreater(res.p95_max_drawdown, 0.20)
        self.assertGreater(res.risk_of_ruin_pct, 1.0)
        self.assertFalse(res.is_robust)

    def test_breach_probability_is_zero_when_no_path_can_reach_the_limit(self):
        # A 100% limit is unreachable without a -100% trade, which validation
        # rejects; so the breach count must be exactly zero.
        engine = MonteCarloRobustnessEngine(max_drawdown_limit=1.0, num_simulations=500, seed=3)
        res = engine.run_sequence_shuffling(HIGH_RISK_RETURNS)
        self.assertEqual(res.risk_of_ruin_pct, 0.0)

    def test_breach_probability_is_total_when_every_path_exceeds_the_limit(self):
        engine = MonteCarloRobustnessEngine(max_drawdown_limit=0.01, num_simulations=500, seed=3)
        res = engine.run_sequence_shuffling(HIGH_RISK_RETURNS)
        self.assertEqual(res.risk_of_ruin_pct, 100.0)
        self.assertFalse(res.is_robust)


class TestBootstrapResampling(unittest.TestCase):

    def setUp(self):
        self.engine = MonteCarloRobustnessEngine(
            initial_capital=100000.0, max_drawdown_limit=0.20,
            num_simulations=500, seed=42)

    def test_bootstrap_produces_terminal_wealth_dispersion(self):
        # Resampling with replacement draws a different multiset per path, so
        # unlike shuffling the terminal equity genuinely varies.
        res = self.engine.run_bootstrap_resampling(PROFITABLE_RETURNS)
        invariant_equity = _compound(100000.0, PROFITABLE_RETURNS)

        self.assertFalse(res.final_equity_is_path_invariant)
        self.assertEqual(res.mode, "BOOTSTRAP_RESAMPLING")
        self.assertGreater(abs(res.median_final_equity - invariant_equity), 1.0)

    def test_bootstrap_reports_a_wider_tail_than_shuffling(self):
        # Shuffling holds the trade multiset fixed; bootstrap can draw the worst
        # trades repeatedly, so its DD_99 must be at least as deep.
        shuffled = self.engine.run_sequence_shuffling(PROFITABLE_RETURNS)
        booted = self.engine.run_bootstrap_resampling(PROFITABLE_RETURNS)
        self.assertGreaterEqual(booted.p99_max_drawdown, shuffled.p99_max_drawdown)


class TestNoiseInjection(unittest.TestCase):

    def setUp(self):
        self.engine = MonteCarloRobustnessEngine(
            initial_capital=100000.0, max_drawdown_limit=0.20,
            num_simulations=500, seed=42)

    def test_zero_noise_reproduces_the_unperturbed_path(self):
        res = self.engine.run_noise_injection(PROFITABLE_RETURNS, noise_std=0.0)
        self.assertEqual(res.mode, "NOISE_INJECTION")
        self.assertAlmostEqual(
            res.median_final_equity, _compound(100000.0, PROFITABLE_RETURNS), places=6)

    def test_noise_widens_the_drawdown_distribution(self):
        clean = self.engine.run_noise_injection(PROFITABLE_RETURNS, noise_std=0.0)
        noisy = self.engine.run_noise_injection(PROFITABLE_RETURNS, noise_std=0.01)
        self.assertGreater(noisy.p95_max_drawdown, clean.p95_max_drawdown)

    def test_negative_mean_shift_models_one_sided_execution_cost(self):
        # Symmetric zero-mean noise is a sensitivity test, not slippage: it
        # leaves the median edge roughly intact. A negative `mean_shift` is what
        # actually charges the strategy for every fill.
        symmetric = self.engine.run_noise_injection(PROFITABLE_RETURNS, noise_std=0.005)
        with_cost = self.engine.run_noise_injection(
            PROFITABLE_RETURNS, noise_std=0.005, mean_shift=-0.01)

        self.assertLess(with_cost.median_final_equity, symmetric.median_final_equity)
        # The perturbation has mean `mean_shift`, so the median path should track
        # the deterministic curve built from cost-adjusted returns r_i - 0.01.
        deterministic = _compound(100000.0, [r - 0.01 for r in PROFITABLE_RETURNS])
        self.assertAlmostEqual(
            with_cost.median_final_equity, deterministic, delta=0.10 * deterministic)

    def test_extreme_noise_never_produces_negative_equity(self):
        # Regression guard: an unclamped `equity *= (1 + r)` with r < -1 flips the
        # sign of equity and reports a drawdown above 100%.
        engine = MonteCarloRobustnessEngine(num_simulations=500, seed=5)
        res = engine.run_noise_injection(PROFITABLE_RETURNS, noise_std=5.0)

        self.assertGreaterEqual(res.median_final_equity, 0.0)
        self.assertLessEqual(res.p99_max_drawdown, 1.0)

    def test_noise_parameters_are_validated(self):
        for kwargs in ({"noise_std": -0.01}, {"noise_std": float("nan")},
                       {"noise_std": 0.01, "mean_shift": -1.0},
                       {"noise_std": 0.01, "mean_shift": float("inf")}):
            with self.subTest(**kwargs):
                with self.assertRaises(MonteCarloError):
                    self.engine.run_noise_injection(PROFITABLE_RETURNS, **kwargs)


class TestDeterminism(unittest.TestCase):

    def test_repeat_runs_on_the_same_engine_reproduce(self):
        # Regression guard: seeding the module-level RNG once in __init__ left
        # the second call on an engine drawing from a different stream, so a
        # recorded sign-off could not be reproduced.
        engine = MonteCarloRobustnessEngine(
            max_drawdown_limit=0.20, num_simulations=500, seed=42)
        first = engine.run_sequence_shuffling(HIGH_RISK_RETURNS)
        second = engine.run_sequence_shuffling(HIGH_RISK_RETURNS)

        self.assertEqual(first, second)
        self.assertIsInstance(first, MonteCarloResult)

    def test_equal_seeds_agree_and_different_seeds_disagree(self):
        def run(seed):
            return MonteCarloRobustnessEngine(
                max_drawdown_limit=0.20, num_simulations=500, seed=seed
            ).run_bootstrap_resampling(HIGH_RISK_RETURNS)

        self.assertEqual(run(7), run(7))
        self.assertNotEqual(run(7).median_final_equity, run(8).median_final_equity)

    def test_engine_does_not_disturb_the_callers_global_random_stream(self):
        # Regression guard: `random.seed(seed)` in __init__ silently reseeded the
        # caller's global RNG.
        random.seed(999)
        expected = [random.random() for _ in range(3)]

        random.seed(999)
        observed = [random.random()]
        MonteCarloRobustnessEngine(num_simulations=500, seed=1).run_bootstrap_resampling(
            PROFITABLE_RETURNS)
        observed.extend(random.random() for _ in range(2))

        self.assertEqual(observed, expected)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MonteCarloRobustnessEngine(num_simulations=500, seed=42)

    def test_non_finite_trade_return_is_rejected(self):
        # Regression guard: NaN compares False against every bound, so the peak
        # and drawdown updates were both skipped and a corrupted trade log
        # reported is_robust=True - a silent pass of the capital-deployment gate.
        for bad in (float("nan"), float("inf"), float("-inf"), None, "0.02", True):
            with self.subTest(bad=bad):
                returns = [0.02, bad, 0.03, 0.01, -0.02, 0.04]
                with self.assertRaises(MonteCarloError):
                    self.engine.run_sequence_shuffling(returns)

    def test_return_at_or_below_total_loss_is_rejected(self):
        # -1.0 wipes the account; below -1.0 is not representable at all in a
        # multiplicative equity model (it drives equity negative).
        for bad in (-1.0, -1.5, -100.0):
            with self.subTest(bad=bad):
                with self.assertRaises(MonteCarloError):
                    self.engine.run_bootstrap_resampling([0.02, bad, 0.03, 0.01, -0.02, 0.04])

    def test_absolute_currency_pnl_is_rejected_rather_than_silently_simulated(self):
        # AI-agent misuse case: a trade log in dollars looks superficially valid.
        # The first loss below -1.0 must stop the run with an actionable message.
        pnl_in_dollars = [1500.0, -2200.0, 900.0, -400.0, 1100.0, -750.0]
        with self.assertRaises(MonteCarloError) as ctx:
            self.engine.run_sequence_shuffling(pnl_in_dollars)
        self.assertIn("fractional", str(ctx.exception))

    def test_overflowing_equity_is_rejected_rather_than_reported_as_robust(self):
        # Validation bounds returns from below (> -1.0) but not from above, so a
        # mostly-winning log of absolute currency P&L compounds past the float
        # range. `inf` equity makes every drawdown comparison NaN-false, which
        # would otherwise yield max_dd = 0.0 and a passing verdict.
        huge_wins = [2000.0] * 120
        with self.assertRaises(MonteCarloError) as ctx:
            self.engine.run_sequence_shuffling(huge_wins)
        self.assertIn("fractional", str(ctx.exception))

    def test_any_iterable_of_returns_is_accepted(self):
        # An agent may hand over a tuple or generator; that must not surface as a
        # bare TypeError from an internal len() call.
        expected = _compound(100000.0, PROFITABLE_RETURNS)
        for returns in (tuple(PROFITABLE_RETURNS), iter(PROFITABLE_RETURNS)):
            with self.subTest(kind=type(returns).__name__):
                res = self.engine.run_sequence_shuffling(returns)
                self.assertAlmostEqual(res.median_final_equity, expected, places=6)

    def test_insufficient_trade_history_is_rejected_in_every_mode(self):
        short_log = [0.01, 0.02, -0.01]
        with self.assertRaises(MonteCarloError):
            self.engine.run_sequence_shuffling(short_log)
        with self.assertRaises(MonteCarloError):
            self.engine.run_bootstrap_resampling(short_log)
        with self.assertRaises(MonteCarloError):
            self.engine.run_noise_injection(short_log, noise_std=0.001)

    def test_infeasible_engine_configuration_is_rejected(self):
        # Regression guards: initial_capital=0 raised ZeroDivisionError, a
        # negative capital silently reported 0.0 drawdown, and num_simulations=0
        # raised IndexError - all from inside the simulation loop.
        for kwargs in ({"initial_capital": 0.0}, {"initial_capital": -5000.0},
                       {"initial_capital": float("nan")},
                       {"max_drawdown_limit": 0.0}, {"max_drawdown_limit": 1.5},
                       {"max_drawdown_limit": -0.10},
                       {"num_simulations": 0}, {"num_simulations": -10},
                       {"num_simulations": 1.5}, {"seed": "42"},
                       {"max_risk_of_ruin_pct": -1.0}, {"max_risk_of_ruin_pct": 101.0}):
            with self.subTest(**kwargs):
                with self.assertRaises(MonteCarloError):
                    MonteCarloRobustnessEngine(**kwargs)


class TestSignoffThresholds(unittest.TestCase):

    def test_risk_of_ruin_ceiling_is_configurable(self):
        # The 1% ceiling is a house risk-appetite parameter, not a fixed rule.
        strict = MonteCarloRobustnessEngine(
            max_drawdown_limit=0.05, num_simulations=500, seed=11, max_risk_of_ruin_pct=0.0)
        lenient = MonteCarloRobustnessEngine(
            max_drawdown_limit=0.05, num_simulations=500, seed=11, max_risk_of_ruin_pct=100.0)

        strict_res = strict.run_bootstrap_resampling(PROFITABLE_RETURNS)
        lenient_res = lenient.run_bootstrap_resampling(PROFITABLE_RETURNS)

        self.assertEqual(strict_res.risk_of_ruin_pct, lenient_res.risk_of_ruin_pct)
        self.assertGreater(strict_res.risk_of_ruin_pct, 0.0)
        self.assertFalse(strict_res.is_robust)
        self.assertTrue(lenient_res.is_robust)

    def test_under_sampling_is_warned_not_silently_accepted(self):
        with self.assertLogs("monte_carlo_engine", level=logging.WARNING) as captured:
            MonteCarloRobustnessEngine(num_simulations=MIN_RECOMMENDED_SIMULATIONS - 1)
        self.assertTrue(any("below the recommended" in line for line in captured.output))

    def test_no_warning_at_or_above_the_recommended_sample_size(self):
        logger = logging.getLogger("monte_carlo_engine")
        with self.assertLogs(logger, level=logging.DEBUG) as captured:
            logger.debug("probe")  # assertLogs requires at least one record.
            MonteCarloRobustnessEngine(num_simulations=MIN_RECOMMENDED_SIMULATIONS)
        self.assertFalse(any(rec.levelno >= logging.WARNING for rec in captured.records))


if __name__ == "__main__":
    unittest.main()
