"""
Unit tests for synthetic-data-generation-for-backtest-augmentation.

Expected values are derived independently of the implementation: moments are
recomputed with ``statistics`` (not with the module's own helpers), the GBM path
is reconstructed from the raw normal draws, and the GARCH recursion is unrolled
by hand for the first few steps.

Tests marked REGRESSION document the the older behavior they would have caught.
Run from this directory: ``python test_synthetic_data_generator.py``.
"""
import logging
import math
import statistics
import unittest

import numpy as np

from synthetic_data_generator import (
    DEFAULT_VOL_TOLERANCE,
    GARCHConfig,
    GARCHPath,
    GBMConfig,
    SyntheticDataGenerator,
    SyntheticValidationReport,
)

# Keep test output clean without globally disabling logging, which would break
# the assertLogs assertions below.
logging.getLogger("synthetic_data_generator").addHandler(logging.NullHandler())
logging.getLogger("synthetic_data_generator").propagate = False


def pop_moments(sample):
    """Reference moments computed with ``statistics``, independent of the module."""
    mean = statistics.fmean(sample)
    stdev = statistics.pstdev(sample, mu=mean)
    skew = statistics.fmean([(x - mean) ** 3 for x in sample]) / stdev ** 3
    kurt = statistics.fmean([(x - mean) ** 4 for x in sample]) / stdev ** 4
    return mean, stdev, skew, kurt


class TestGBM(unittest.TestCase):
    def setUp(self):
        self.generator = SyntheticDataGenerator(seed=123)

    def test_shape_and_anchor(self):
        paths = self.generator.generate_gbm(GBMConfig(mu=0.05, sigma=0.2, S0=100.0, steps=10))
        self.assertEqual(len(paths), 11)
        self.assertEqual(paths[0], 100.0)
        self.assertTrue(np.all(paths > 0.0))

    def test_path_matches_closed_form_from_the_same_normals(self):
        """
        Reconstruct the path from the raw draws of an identically seeded stream.
        This pins the discretization -- an Ito correction with the wrong sign, or
        a missing sqrt(dt), fails here.
        """
        mu, sigma, S0, dt, steps = 0.07, 0.20, 250.0, 1.0 / 252.0, 64
        paths = SyntheticDataGenerator(seed=99).generate_gbm(
            GBMConfig(mu=mu, sigma=sigma, S0=S0, dt=dt, steps=steps)
        )
        z = np.random.default_rng(99).standard_normal(steps)

        expected = S0
        for t in range(steps):
            expected *= math.exp(
                (mu - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * z[t]
            )
            self.assertAlmostEqual(paths[t + 1], expected, delta=abs(expected) * 1e-12)

    def test_zero_volatility_is_deterministic_compounding(self):
        """sigma=0 collapses GBM to exp(mu*t); an independent closed-form check."""
        mu, dt, steps = 0.10, 1.0 / 252.0, 252
        paths = self.generator.generate_gbm(
            GBMConfig(mu=mu, sigma=0.0, S0=100.0, dt=dt, steps=steps)
        )
        self.assertAlmostEqual(paths[-1], 100.0 * math.exp(mu * steps * dt), places=9)

    def test_log_returns_are_iid_normal_with_the_specified_parameters(self):
        """
        Over a long path the realized per-bar log-return mean and sd must match
        the theoretical (mu - sigma^2/2)*dt and sigma*sqrt(dt).
        """
        mu, sigma, dt, steps = 0.08, 0.25, 1.0 / 252.0, 200_000
        paths = SyntheticDataGenerator(seed=7).generate_gbm(
            GBMConfig(mu=mu, sigma=sigma, S0=100.0, dt=dt, steps=steps)
        )
        log_returns = np.diff(np.log(paths))
        self.assertAlmostEqual(
            float(log_returns.mean()), (mu - 0.5 * sigma ** 2) * dt, delta=1e-4
        )
        self.assertAlmostEqual(
            float(log_returns.std()), sigma * math.sqrt(dt), delta=1e-4
        )

    def test_reproducible_for_a_given_seed(self):
        cfg = GBMConfig(mu=0.05, sigma=0.2, S0=100.0, steps=50)
        a = SyntheticDataGenerator(seed=11).generate_gbm(cfg)
        b = SyntheticDataGenerator(seed=11).generate_gbm(cfg)
        c = SyntheticDataGenerator(seed=12).generate_gbm(cfg)
        np.testing.assert_array_equal(a, b)
        self.assertFalse(np.array_equal(a, c))

    def test_invalid_configs_raise(self):
        cases = {
            "S0 <= 0": GBMConfig(mu=0.0, sigma=0.2, S0=0.0),
            "negative S0": GBMConfig(mu=0.0, sigma=0.2, S0=-100.0),
            "negative sigma": GBMConfig(mu=0.0, sigma=-0.2, S0=100.0),
            "dt <= 0": GBMConfig(mu=0.0, sigma=0.2, S0=100.0, dt=0.0),
            "steps < 1": GBMConfig(mu=0.0, sigma=0.2, S0=100.0, steps=0),
            "nan mu": GBMConfig(mu=float("nan"), sigma=0.2, S0=100.0),
            "inf sigma": GBMConfig(mu=0.0, sigma=float("inf"), S0=100.0),
        }
        for label, cfg in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.generator.generate_gbm(cfg)


class TestGARCH(unittest.TestCase):
    def setUp(self):
        self.generator = SyntheticDataGenerator(seed=123)

    def test_shapes_and_anchor(self):
        path = self.generator.generate_garch(
            GARCHConfig(omega=1e-5, alpha=0.1, beta=0.85, S0=100.0, steps=50)
        )
        self.assertIsInstance(path, GARCHPath)
        self.assertEqual(len(path.prices), 51)
        self.assertEqual(len(path.sigmas), 51)
        self.assertEqual(len(path.returns), 50)
        self.assertEqual(path.prices[0], 100.0)

    def test_two_tuple_unpacking_remains_supported(self):
        """an earlier signature returned (prices, sigmas); that must still work."""
        prices, sigmas = self.generator.generate_garch(GARCHConfig(steps=10))
        self.assertEqual(len(prices), 11)
        self.assertEqual(len(sigmas), 11)

    def test_returns_are_exposed_and_reconcile_with_prices(self):
        """
        REGRESSION: a naive implementation built a ``returns`` array and then
        discarded it, so a caller following the documented workflow had no return
        series to validate. ``returns[t-1]`` must be exactly the log return over
        ``prices[t-1] -> prices[t]``.
        """
        path = self.generator.generate_garch(GARCHConfig(steps=200))
        np.testing.assert_allclose(
            path.returns, np.diff(np.log(path.prices)), rtol=0, atol=1e-12
        )

    def test_recursion_unrolled_by_hand(self):
        """
        Independently unroll sigma_t^2 = omega + alpha*eps_{t-1}^2 + beta*sigma_{t-1}^2
        for the first steps using the same seeded normals.
        """
        omega, alpha, beta, mu = 2e-5, 0.12, 0.80, 0.0003
        path = SyntheticDataGenerator(seed=31).generate_garch(
            GARCHConfig(omega=omega, alpha=alpha, beta=beta, mu=mu, S0=50.0, steps=6)
        )
        z = np.random.default_rng(31).standard_normal(6)

        uncond_var = omega / (1.0 - alpha - beta)
        sigma_prev_sq = uncond_var
        eps_prev_sq = uncond_var
        price = 50.0
        for t in range(6):
            sigma_sq = omega + alpha * eps_prev_sq + beta * sigma_prev_sq
            sigma = math.sqrt(sigma_sq)
            eps = sigma * z[t]
            ret = mu + eps
            price *= math.exp(ret)

            self.assertAlmostEqual(path.sigmas[t + 1], sigma, places=15)
            self.assertAlmostEqual(path.returns[t], ret, places=15)
            self.assertAlmostEqual(path.prices[t + 1], price, delta=abs(price) * 1e-12)

            sigma_prev_sq, eps_prev_sq = sigma_sq, eps * eps

    def test_recursion_starts_at_the_stationary_point(self):
        """
        REGRESSION: a naive implementation set eps_0 = 0, so
        sigma_1^2 = omega + beta*sigma_bar^2 < sigma_bar^2 and early bars were
        systematically under-volatile. sigma_0 and sigma_1 must both equal the
        unconditional standard deviation exactly.
        """
        omega, alpha, beta = 1e-5, 0.10, 0.85
        uncond_sd = math.sqrt(omega / (1.0 - alpha - beta))
        path = self.generator.generate_garch(
            GARCHConfig(omega=omega, alpha=alpha, beta=beta, steps=5)
        )
        self.assertAlmostEqual(path.sigmas[0], uncond_sd, places=15)
        self.assertAlmostEqual(path.sigmas[1], uncond_sd, places=15)

        old_sigma_1 = math.sqrt(omega + beta * uncond_sd ** 2)
        self.assertLess(old_sigma_1, uncond_sd)  # the behavior this replaces

    def test_long_run_variance_converges_to_the_unconditional_variance(self):
        omega, alpha, beta = 1e-5, 0.10, 0.85
        uncond_sd = math.sqrt(omega / (1.0 - alpha - beta))
        path = SyntheticDataGenerator(seed=17).generate_garch(
            GARCHConfig(omega=omega, alpha=alpha, beta=beta, mu=0.0, steps=300_000)
        )
        self.assertAlmostEqual(float(path.returns.std()), uncond_sd, delta=uncond_sd * 0.05)

    def test_volatility_clusters(self):
        """
        Squared returns must be positively autocorrelated at lag 1 -- that is what
        distinguishes a GARCH path from the IID GBM baseline, which must not be.
        """
        garch = SyntheticDataGenerator(seed=5).generate_garch(
            GARCHConfig(omega=1e-5, alpha=0.12, beta=0.85, mu=0.0, steps=100_000)
        ).returns
        gbm_prices = SyntheticDataGenerator(seed=5).generate_gbm(
            GBMConfig(mu=0.0, sigma=0.22, S0=100.0, steps=100_000)
        )
        gbm = np.diff(np.log(gbm_prices))

        def lag1_autocorr_of_squares(r):
            sq = r ** 2
            sq = sq - sq.mean()
            return float((sq[:-1] * sq[1:]).mean() / (sq ** 2).mean())

        self.assertGreater(lag1_autocorr_of_squares(garch), 0.10)
        self.assertLess(abs(lag1_autocorr_of_squares(gbm)), 0.02)

    def test_nonstationary_parameters_are_rejected_not_clamped(self):
        """
        REGRESSION: alpha + beta >= 1 was silently accepted, and
        ``omega / max(1 - alpha - beta, 0.001)`` fabricated an unconditional
        variance for a process that has none -- the exact pitfall SKILL.md
        documents. It must raise.
        """
        for alpha, beta in ((0.50, 0.70), (0.10, 0.90), (0.00, 1.00)):
            with self.subTest(alpha=alpha, beta=beta), self.assertRaises(ValueError) as ctx:
                self.generator.generate_garch(
                    GARCHConfig(omega=1e-5, alpha=alpha, beta=beta, steps=10)
                )
            self.assertIn("alpha + beta < 1", str(ctx.exception))

        # Just inside the boundary must still be accepted.
        self.generator.generate_garch(GARCHConfig(omega=1e-5, alpha=0.10, beta=0.8999, steps=10))

    def test_invalid_configs_raise(self):
        cases = {
            "omega = 0": GARCHConfig(omega=0.0),
            "omega < 0": GARCHConfig(omega=-1e-5),
            "alpha < 0": GARCHConfig(alpha=-0.1),
            "beta < 0": GARCHConfig(beta=-0.1),
            "S0 <= 0": GARCHConfig(S0=0.0),
            "steps < 1": GARCHConfig(steps=0),
            "nan mu": GARCHConfig(mu=float("nan")),
        }
        for label, cfg in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.generator.generate_garch(cfg)


class TestIIDBootstrap(unittest.TestCase):
    def setUp(self):
        self.generator = SyntheticDataGenerator(seed=123)
        self.hist = np.array([0.01, -0.01, 0.02, -0.02])

    def test_length_and_membership(self):
        sampled = self.generator.bootstrap_returns(self.hist, 10)
        self.assertEqual(len(sampled), 10)
        for r in sampled:
            self.assertIn(r, self.hist)

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            self.generator.bootstrap_returns(self.hist, 0)
        with self.assertRaises(ValueError):
            self.generator.bootstrap_returns(np.array([]), 10)
        with self.assertRaises(ValueError):
            self.generator.bootstrap_returns(np.array([0.01, np.nan]), 10)


class TestCircularBlockBootstrap(unittest.TestCase):
    def setUp(self):
        self.generator = SyntheticDataGenerator(seed=123)
        self.hist = np.array([0.01, -0.01, 0.02, -0.02, 0.03, -0.03])

    def test_length_and_membership(self):
        sampled = self.generator.block_bootstrap_returns(self.hist, 10, block_size=2)
        self.assertEqual(len(sampled), 10)
        for r in sampled:
            self.assertIn(r, self.hist)

    def test_output_length_is_exact_when_steps_is_not_a_multiple_of_block_size(self):
        sampled = self.generator.block_bootstrap_returns(self.hist, 7, block_size=3)
        self.assertEqual(len(sampled), 7)

    def test_every_observation_is_equally_likely(self):
        """
        REGRESSION: a naive implementation drew block starts from
        ``[0, n - block_size]`` -- a non-circular moving-block bootstrap despite
        being documented as circular. With n=20, block_size=5 the first and last
        observations appeared at ~0.26x the rate of interior ones. Wrapping makes
        every observation appear in exactly ``block_size`` blocks, so the
        empirical weights must be uniform.
        """
        n, block_size, draws = 20, 5, 3000
        series = np.arange(n, dtype=float)  # value == index, so occurrences are countable
        generator = SyntheticDataGenerator(seed=2024)

        counts = np.zeros(n)
        for _ in range(draws):
            for value in generator.block_bootstrap_returns(series, steps=n, block_size=block_size):
                counts[int(round(value))] += 1

        weights = counts / counts.mean()
        self.assertAlmostEqual(float(weights.min()), 1.0, delta=0.10)
        self.assertAlmostEqual(float(weights.max()), 1.0, delta=0.10)
        # The old implementation's end weight was ~0.26; assert we are nowhere near it.
        self.assertGreater(float(weights[0]), 0.80)
        self.assertGreater(float(weights[-1]), 0.80)

    def test_blocks_are_contiguous_and_wrap(self):
        """
        Every consecutive pair inside a block must be adjacent modulo n. With a
        block_size equal to n, one draw must reproduce the whole series rotated.
        """
        n = 8
        series = np.arange(n, dtype=float)
        sampled = SyntheticDataGenerator(seed=4).block_bootstrap_returns(
            series, steps=n, block_size=n
        )
        self.assertCountEqual(sampled.tolist(), series.tolist())
        for i in range(n - 1):
            self.assertEqual(int(sampled[i + 1]), (int(sampled[i]) + 1) % n)

    def test_preserves_serial_dependence_that_iid_bootstrap_destroys(self):
        """
        Build a series of long alternating runs. A block bootstrap must retain
        most of the lag-1 autocorrelation; the IID bootstrap must destroy it.
        """
        block = [0.02] * 10 + [-0.02] * 10
        series = np.array(block * 40, dtype=float)

        def lag1_autocorr(x):
            c = x - x.mean()
            return float((c[:-1] * c[1:]).mean() / (c ** 2).mean())

        generator = SyntheticDataGenerator(seed=8)
        blocked = generator.block_bootstrap_returns(series, steps=4000, block_size=20)
        iid = generator.bootstrap_returns(series, steps=4000)

        # 18 of every 20 adjacent pairs share a sign, so the source autocorrelation
        # is 0.80 by construction.
        self.assertAlmostEqual(lag1_autocorr(series), 0.80, delta=0.01)
        self.assertGreater(lag1_autocorr(blocked), 0.60)
        self.assertLess(abs(lag1_autocorr(iid)), 0.10)

    def test_block_size_zero_raises_instead_of_hanging(self):
        """
        REGRESSION: block_size <= 0 produced empty slices, so an earlier
        ``while sum(len(b) for b in blocks) < steps`` loop never terminated and
        grew the block list until memory was exhausted.
        """
        with self.assertRaises(ValueError):
            self.generator.block_bootstrap_returns(self.hist, 10, block_size=0)
        with self.assertRaises(ValueError):
            self.generator.block_bootstrap_returns(self.hist, 10, block_size=-3)

    def test_block_size_larger_than_series_raises(self):
        with self.assertRaises(ValueError):
            self.generator.block_bootstrap_returns(self.hist, 10, block_size=len(self.hist) + 1)

    def test_block_size_one_warns(self):
        with self.assertLogs("synthetic_data_generator", level="WARNING") as logs:
            self.generator.block_bootstrap_returns(self.hist, 10, block_size=1)
        self.assertIn("IID bootstrap", "".join(logs.output))

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            self.generator.block_bootstrap_returns(self.hist, 0)
        with self.assertRaises(ValueError):
            self.generator.block_bootstrap_returns(np.array([0.01, np.inf, 0.02]), 10, 2)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.generator = SyntheticDataGenerator(seed=123)

    def test_report_matches_independently_computed_moments(self):
        hist = np.random.default_rng(42).normal(0.001, 0.015, size=252)
        synth = np.random.default_rng(123).normal(0.001, 0.015, size=252)
        report = self.generator.validate_synthetic_path(hist, synth)

        h_mean, h_sd, h_skew, h_kurt = pop_moments(hist.tolist())
        s_mean, s_sd, s_skew, s_kurt = pop_moments(synth.tolist())

        self.assertAlmostEqual(report.mean_return_historical, h_mean, places=12)
        self.assertAlmostEqual(report.mean_return_synthetic, s_mean, places=12)
        self.assertAlmostEqual(report.volatility_historical, h_sd, places=12)
        self.assertAlmostEqual(report.volatility_synthetic, s_sd, places=12)
        self.assertAlmostEqual(report.skewness_historical, h_skew, places=10)
        self.assertAlmostEqual(report.skewness_synthetic, s_skew, places=10)
        self.assertAlmostEqual(report.kurtosis_historical, h_kurt, places=10)
        self.assertAlmostEqual(report.kurtosis_synthetic, s_kurt, places=10)
        self.assertTrue(report.is_statistically_consistent)
        self.assertEqual(report.observations_historical, 252)
        self.assertEqual(report.observations_synthetic, 252)

    def test_kurtosis_of_a_gaussian_sample_is_three_at_every_return_scale(self):
        """
        REGRESSION: a naive code divided by ``vol**4 + 1e-9``. At a daily
        return scale (sigma = 0.01) vol**4 = 1e-8, so the additive epsilon was 10%
        of the denominator and kurtosis came back 2.74 instead of ~3.0; at
        sigma = 0.001 it reported 0.003. The reported moments must be
        scale-invariant.
        """
        rng = np.random.default_rng(1)
        for sigma in (0.20, 0.01, 0.001, 0.0001):
            with self.subTest(sigma=sigma):
                sample = rng.normal(0.0, sigma, size=200_000)
                report = self.generator.validate_synthetic_path(sample, sample)
                self.assertAlmostEqual(report.kurtosis_synthetic, 3.0, delta=0.10)
                self.assertAlmostEqual(report.skewness_synthetic, 0.0, delta=0.05)

    def test_moments_are_scale_invariant(self):
        """Scaling a series by k must leave skewness and kurtosis unchanged."""
        base = np.random.default_rng(9).normal(0.0, 1.0, size=5000)
        a = self.generator.validate_synthetic_path(base, base)
        b = self.generator.validate_synthetic_path(base * 1e-4, base * 1e-4)
        self.assertAlmostEqual(a.skewness_synthetic, b.skewness_synthetic, places=8)
        self.assertAlmostEqual(a.kurtosis_synthetic, b.kurtosis_synthetic, places=8)

    def test_values_are_not_rounded(self):
        """
        REGRESSION: the report rounded to 6 decimals, which zeroed the mean of an
        intraday-scale return series.
        """
        hist = np.random.default_rng(3).normal(1e-7, 1e-5, size=5000)
        report = self.generator.validate_synthetic_path(hist, hist)
        self.assertNotEqual(report.mean_return_synthetic, 0.0)
        self.assertAlmostEqual(
            report.mean_return_synthetic, statistics.fmean(hist.tolist()), places=15
        )

    def test_tolerance_boundary_is_inclusive(self):
        """
        The gate is ``<=``, so a relative error exactly at the tolerance passes.

        Uses 0.25 rather than the 0.35 default because 0.25 and 1.25 are exactly
        representable in binary: hist has population sd exactly 1.0, hist*1.25 has
        exactly 1.25, and the relative error is exactly 0.25. Testing a boundary
        with a value that is not exactly representable tests floating-point
        rounding rather than the comparison operator.
        """
        hist = np.array([-1.0, 1.0] * 50)
        self.assertEqual(statistics.pstdev(hist.tolist(), mu=0.0), 1.0)

        at_boundary = hist * 1.25
        beyond = hist * (1.25 + 1e-6)

        self.assertTrue(
            self.generator.validate_synthetic_path(
                hist, at_boundary, vol_tolerance=0.25
            ).is_statistically_consistent
        )
        self.assertFalse(
            self.generator.validate_synthetic_path(
                hist, beyond, vol_tolerance=0.25
            ).is_statistically_consistent
        )
        # The module default is what the report records when none is passed.
        self.assertEqual(
            self.generator.validate_synthetic_path(hist, at_boundary).vol_tolerance,
            DEFAULT_VOL_TOLERANCE,
        )

    def test_tolerance_is_configurable_and_reported(self):
        hist = np.array([-1.0, 1.0] * 50)
        synth = hist * 1.20
        self.assertTrue(
            self.generator.validate_synthetic_path(hist, synth, vol_tolerance=0.25)
            .is_statistically_consistent
        )
        strict = self.generator.validate_synthetic_path(hist, synth, vol_tolerance=0.10)
        self.assertFalse(strict.is_statistically_consistent)
        self.assertAlmostEqual(strict.volatility_relative_error, 0.20, places=12)
        self.assertEqual(strict.vol_tolerance, 0.10)

    def test_constant_synthetic_series_reports_undefined_moments_not_zero(self):
        hist = np.random.default_rng(5).normal(0.0, 0.01, size=100)
        synth = np.full(100, 0.001)
        report = self.generator.validate_synthetic_path(hist, synth)
        self.assertIsNone(report.skewness_synthetic)
        self.assertIsNone(report.kurtosis_synthetic)
        self.assertEqual(report.volatility_synthetic, 0.0)
        self.assertFalse(report.is_statistically_consistent)

    def test_constant_historical_series_raises(self):
        """
        REGRESSION: a zero-volatility baseline was divided by max(h_vol, 1e-6).
        Two different constant series both carry ~1e-19 residual dispersion, so
        the relative error came out near zero and an earlier report certified
        them ``is_statistically_consistent = True`` -- a passing verdict produced
        by the absence of data.
        """
        with self.assertRaises(ValueError):
            self.generator.validate_synthetic_path(np.full(100, 0.001), np.full(100, 0.002))

    def test_non_finite_and_undersized_inputs_raise(self):
        good = np.random.default_rng(6).normal(0.0, 0.01, size=100)
        cases = {
            "nan in synthetic": (good, np.array([0.01, np.nan, 0.02])),
            "inf in historical": (np.array([0.01, np.inf, 0.02]), good),
            "empty synthetic": (good, np.array([])),
            "single observation": (good, np.array([0.01])),
            "2-D input": (good, np.zeros((10, 2))),
        }
        for label, (hist, synth) in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.generator.validate_synthetic_path(hist, synth)

    def test_invalid_tolerance_raises(self):
        good = np.random.default_rng(6).normal(0.0, 0.01, size=100)
        for bad in (-0.1, float("nan"), float("inf")):
            with self.subTest(tolerance=bad), self.assertRaises(ValueError):
                self.generator.validate_synthetic_path(good, good, vol_tolerance=bad)

    def test_small_sample_warns(self):
        small = np.array([0.01, -0.01, 0.02, -0.02, 0.005])
        with self.assertLogs("synthetic_data_generator", level="WARNING") as logs:
            self.generator.validate_synthetic_path(small, small)
        self.assertIn("sampling error", "".join(logs.output))

    def test_original_positional_construction_of_the_report_still_works(self):
        report = SyntheticValidationReport(0.001, 0.001, 0.01, 0.011, 0.1, 3.2, True)
        self.assertTrue(report.is_statistically_consistent)
        self.assertIsNone(report.skewness_historical)


class TestGeneratorContract(unittest.TestCase):
    def test_seedless_construction_warns(self):
        with self.assertLogs("synthetic_data_generator", level="WARNING") as logs:
            SyntheticDataGenerator(seed=None)
        self.assertIn("not reproducible", "".join(logs.output))

    def test_non_integer_seed_raises(self):
        for bad in ("42", 4.2, True):
            with self.subTest(seed=bad), self.assertRaises(ValueError):
                SyntheticDataGenerator(seed=bad)

    def test_end_to_end_garch_path_validates_against_its_own_baseline(self):
        """
        Smoke test of the documented workflow: generate a GARCH path, take its
        returns, and validate them against an empirical baseline drawn at the
        same unconditional volatility.
        """
        omega, alpha, beta = 1e-5, 0.10, 0.85
        uncond_sd = math.sqrt(omega / (1.0 - alpha - beta))
        path = SyntheticDataGenerator(seed=77).generate_garch(
            GARCHConfig(omega=omega, alpha=alpha, beta=beta, mu=0.0, steps=2000)
        )
        baseline = np.random.default_rng(78).normal(0.0, uncond_sd, size=2000)

        report = SyntheticDataGenerator(seed=1).validate_synthetic_path(baseline, path.returns)
        self.assertTrue(report.is_statistically_consistent)
        # GARCH mixes normals of differing variance, so its unconditional
        # distribution is leptokurtic relative to the Gaussian baseline.
        self.assertGreater(report.kurtosis_synthetic, report.kurtosis_historical)


if __name__ == "__main__":
    unittest.main()
