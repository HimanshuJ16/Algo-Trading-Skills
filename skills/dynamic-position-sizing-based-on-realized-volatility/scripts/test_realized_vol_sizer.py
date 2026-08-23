"""Unit tests for dynamic-position-sizing-based-on-realized-volatility."""
import math
import unittest

from realized_vol_sizer import (
    RealizedVolPositionSizer,
    required_ewma_observations,
    RISKMETRICS_DAILY_DECAY,
    RISKMETRICS_MONTHLY_DECAY,
)

DAILY = math.sqrt(252.0)


def constant_abs_returns(annual_vol: float, n: int) -> list:
    """
    Alternating +/-d returns whose EWMA and population variance both equal d^2 exactly,
    so the expected annualized volatility is exactly ``annual_vol``. Deterministic —
    no RNG, so expected values can be derived by hand rather than sampled.
    """
    d = annual_vol / DAILY
    return [d if i % 2 == 0 else -d for i in range(n)]


class TestRequiredEwmaObservations(unittest.TestCase):
    """Independently verified against RiskMetrics Technical Document Table 5.7."""

    def test_reproduces_riskmetrics_table_5_7_at_1pct_tolerance(self):
        published = {0.85: 28, 0.86: 31, 0.87: 33, 0.88: 36, 0.89: 40, 0.90: 44,
                     0.91: 49, 0.92: 55, 0.93: 63, 0.94: 74, 0.95: 90, 0.96: 113,
                     0.97: 151, 0.98: 228, 0.99: 458}
        for decay, expected in published.items():
            with self.subTest(decay=decay):
                self.assertEqual(required_ewma_observations(decay, 0.01), expected)

    def test_reproduces_riskmetrics_table_5_7_across_tolerances_for_daily_lambda(self):
        # Table 5.7, row lambda = 0.94: 186 / 149 / 112 / 74 days.
        self.assertEqual(required_ewma_observations(0.94, 0.00001), 186)
        self.assertEqual(required_ewma_observations(0.94, 0.0001), 149)
        self.assertEqual(required_ewma_observations(0.94, 0.001), 112)
        self.assertEqual(required_ewma_observations(0.94, 0.01), 74)

    def test_riskmetrics_default_decay_constants(self):
        self.assertEqual(RISKMETRICS_DAILY_DECAY, 0.94)
        self.assertEqual(RISKMETRICS_MONTHLY_DECAY, 0.97)

    def test_invalid_parameters_rejected(self):
        for bad_decay in (0.0, 1.0, -0.5, 1.5):
            with self.assertRaises(ValueError):
                required_ewma_observations(bad_decay, 0.01)
        for bad_tol in (0.0, 1.0, -0.1):
            with self.assertRaises(ValueError):
                required_ewma_observations(0.94, bad_tol)


class TestRealizedVolPositionSizer(unittest.TestCase):
    def setUp(self):
        self.sizer = RealizedVolPositionSizer(
            target_annualized_vol=0.15,
            min_scalar=0.20,
            max_scalar=2.00,
            vol_floor=0.05,
        )

    # ------------------------------------------------------------------
    # Estimator correctness (independently derived expected values)
    # ------------------------------------------------------------------
    def test_ewma_recovers_known_volatility(self):
        """Constant |r| series: EWMA variance is exactly d^2 regardless of lambda."""
        rets = constant_abs_returns(0.60, 100)
        self.assertAlmostEqual(self.sizer.compute_ewma_volatility(rets), 0.60, places=10)

    def test_ewma_matches_hand_computed_recursion(self):
        """sigma^2 = lambda*sigma^2 + (1-lambda)*r^2, seeded with r0^2 (RiskMetrics Eq. 5.3)."""
        rets = [0.01, -0.02, 0.015, -0.005, 0.03] * 20  # 100 observations
        variance = rets[0] ** 2
        for r in rets[1:]:
            variance = 0.94 * variance + 0.06 * r * r
        expected = math.sqrt(variance) * DAILY
        self.assertAlmostEqual(self.sizer.compute_ewma_volatility(rets, 0.94), expected, places=12)

    def test_rolling_matches_hand_computed_sample_stdev(self):
        rets = [0.01, -0.02, 0.015, -0.005, 0.03] * 6  # 30 observations
        n = len(rets)
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        expected = math.sqrt(var) * DAILY
        self.assertAlmostEqual(self.sizer.compute_rolling_volatility(rets), expected, places=12)

    def test_estimators_are_returned_unfloored(self):
        """Reporting the floored value as 'realized volatility' overstated quiet series."""
        calm = constant_abs_returns(0.01, 100)   # 1% annualized, far below the 5% floor
        self.assertAlmostEqual(self.sizer.compute_ewma_volatility(calm), 0.01, places=10)
        self.assertLess(self.sizer.compute_ewma_volatility(calm), self.sizer.vol_floor)

    def test_annualization_factor_is_applied(self):
        hourly = RealizedVolPositionSizer(annualization_factor=252.0 * 6.5)
        rets = [0.001 if i % 2 == 0 else -0.001 for i in range(100)]
        self.assertAlmostEqual(
            hourly.compute_ewma_volatility(rets), 0.001 * math.sqrt(252.0 * 6.5), places=12
        )

    # ------------------------------------------------------------------
    # Sizing arithmetic
    # ------------------------------------------------------------------
    def test_high_volatility_scales_down_position(self):
        # 60% realized vs 15% target -> raw scalar exactly 0.25, inside [0.20, 2.00].
        result = self.sizer.calculate_position_size(
            symbol="BTC", base_capital_usd=100000.0, price=50000.0,
            returns_history=constant_abs_returns(0.60, 100), use_ewma=True,
        )
        self.assertTrue(result.is_scaled_down)
        self.assertFalse(result.is_leveraged_up)
        self.assertAlmostEqual(result.realized_annualized_vol, 0.60, places=4)
        self.assertAlmostEqual(result.bounded_vol_scalar, 0.25, places=6)
        self.assertAlmostEqual(result.adjusted_capital_usd, 25000.0, places=2)
        self.assertAlmostEqual(result.target_shares, 0.5, places=6)
        self.assertFalse(result.vol_floor_binding)

    def test_low_volatility_leverages_up_to_cap(self):
        # 4% realized -> raw scalar 3.75, clipped to the 2.0 cap.
        result = self.sizer.calculate_position_size(
            symbol="US_TREASURIES", base_capital_usd=100000.0, price=100.0,
            returns_history=constant_abs_returns(0.04, 100), use_ewma=True,
        )
        self.assertTrue(result.is_leveraged_up)
        self.assertEqual(result.bounded_vol_scalar, 2.0)
        self.assertEqual(result.adjusted_capital_usd, 200000.0)
        self.assertTrue(result.vol_floor_binding)  # 4% < 5% floor
        self.assertAlmostEqual(result.vol_used_for_scaling, 0.05, places=6)
        self.assertAlmostEqual(result.raw_vol_scalar, 3.0, places=6)  # 0.15/0.05, not 0.15/0.04

    def test_min_scalar_floor_is_enforced(self):
        # 300% vol -> raw scalar 0.05, clipped up to min_scalar 0.20.
        result = self.sizer.calculate_position_size(
            symbol="MEME", base_capital_usd=100000.0, price=10.0,
            returns_history=constant_abs_returns(3.00, 100), use_ewma=True,
        )
        self.assertAlmostEqual(result.raw_vol_scalar, 0.05, places=6)
        self.assertEqual(result.bounded_vol_scalar, 0.20)
        self.assertEqual(result.adjusted_capital_usd, 20000.0)

    def test_at_target_volatility_is_neutral(self):
        result = self.sizer.calculate_position_size(
            symbol="SPY", base_capital_usd=100000.0, price=500.0,
            returns_history=constant_abs_returns(0.15, 100), use_ewma=True,
        )
        self.assertAlmostEqual(result.bounded_vol_scalar, 1.0, places=6)
        self.assertFalse(result.is_leveraged_up)
        self.assertFalse(result.is_scaled_down)
        self.assertAlmostEqual(result.adjusted_capital_usd, 100000.0, places=2)

    def test_target_shares_floored_never_rounded_up(self):
        """Rounding up would place a position above the risk budget."""
        result = self.sizer.calculate_position_size(
            symbol="X", base_capital_usd=100000.0, price=3.0,
            returns_history=constant_abs_returns(0.15, 100), use_ewma=True,
        )
        # 100000/3 = 33333.333... -> floored to 33333.33, never 33333.34
        self.assertEqual(result.target_shares, 33333.33)
        self.assertLessEqual(result.target_shares * 3.0, result.adjusted_capital_usd)

    def test_zero_base_capital_yields_zero_position(self):
        result = self.sizer.calculate_position_size(
            symbol="X", base_capital_usd=0.0, price=10.0,
            returns_history=constant_abs_returns(0.15, 100), use_ewma=True,
        )
        self.assertEqual(result.adjusted_capital_usd, 0.0)
        self.assertEqual(result.target_shares, 0.0)

    def test_observations_used_is_reported(self):
        rets = constant_abs_returns(0.15, 120)
        result = self.sizer.calculate_position_size("X", 100000.0, 10.0, rets, use_ewma=True)
        self.assertEqual(result.observations_used, 120)

    # ------------------------------------------------------------------
    # Regression: corrupt / insufficient data previously produced a position
    # ------------------------------------------------------------------
    def test_non_finite_return_is_rejected_not_maximally_leveraged(self):
        """
        A NaN return collapsed the variance to 0.0 via math.sqrt(max(0.0, nan)),
        which floored to vol_floor and produced the MAXIMUM 2.0x scalar — i.e.
        $200,000 deployed on $100,000 of base capital from corrupt data.
        """
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                rets = constant_abs_returns(0.20, 100)
                rets[50] = bad
                with self.assertRaises(ValueError) as ctx:
                    self.sizer.calculate_position_size("X", 100000.0, 10.0, rets, use_ewma=True)
                self.assertIn("non-finite", str(ctx.exception))

    def test_constant_return_series_is_rejected_not_maximally_leveraged(self):
        """
        An all-zero (stale/halted feed) series has zero variance, which the vol floor
        converted into the MAXIMUM 2.0x scalar — $200,000 deployed on $100,000 from a
        feed that never ticked.
        """
        for use_ewma in (True, False):
            with self.subTest(use_ewma=use_ewma):
                with self.assertRaises(ValueError) as ctx:
                    self.sizer.calculate_position_size(
                        "STALE", 100000.0, 10.0, [0.0] * 100, use_ewma=use_ewma
                    )
                self.assertIn("stale", str(ctx.exception).lower())

    def test_constant_nonzero_return_is_not_caught_by_the_stale_guard(self):
        """
        Documents the guard's boundary honestly: under the RiskMetrics zero-mean
        convention a constant +0.3% return has non-zero mean-square, so the EWMA reads
        it as ~4.8% volatility rather than zero. Extending the guard to catch it would
        require a sample-mean test that contradicts the documented convention, so this
        case is handled by the floor (flagged via vol_floor_binding), not by rejection.
        """
        result = self.sizer.calculate_position_size(
            "FLAT", 100000.0, 10.0, [0.003] * 100, use_ewma=True
        )
        self.assertAlmostEqual(result.realized_annualized_vol, 0.003 * DAILY, places=4)
        self.assertTrue(result.vol_floor_binding)

    def test_genuinely_quiet_but_nonzero_series_still_sizes(self):
        """The stale-feed guard must not reject a real, merely quiet, asset."""
        result = self.sizer.calculate_position_size(
            "QUIET", 100000.0, 10.0, constant_abs_returns(0.02, 100), use_ewma=True
        )
        self.assertAlmostEqual(result.realized_annualized_vol, 0.02, places=4)
        self.assertTrue(result.vol_floor_binding)
        self.assertEqual(result.bounded_vol_scalar, 2.0)

    def test_non_numeric_return_is_rejected(self):
        rets = constant_abs_returns(0.20, 100)
        rets[3] = "0.01"
        with self.assertRaises(TypeError):
            self.sizer.calculate_position_size("X", 100000.0, 10.0, rets, use_ewma=True)

    def test_empty_history_raises_instead_of_sizing_at_full_capital(self):
        """Previously returned target_vol -> scalar exactly 1.0 -> full-size position."""
        for use_ewma in (True, False):
            with self.subTest(use_ewma=use_ewma):
                with self.assertRaises(ValueError) as ctx:
                    self.sizer.calculate_position_size("X", 100000.0, 10.0, [], use_ewma=use_ewma)
                self.assertIn("insufficient return history", str(ctx.exception).lower())

    def test_single_observation_raises(self):
        with self.assertRaises(ValueError):
            self.sizer.calculate_position_size("X", 100000.0, 10.0, [0.02], use_ewma=True)

    def test_ewma_requires_riskmetrics_effective_observation_count(self):
        """74 observations at lambda=0.94 / 1% tolerance (RiskMetrics Table 5.7)."""
        needed = required_ewma_observations(0.94, 0.01)
        self.assertEqual(needed, 74)
        just_short = constant_abs_returns(0.20, needed - 1)
        with self.assertRaises(ValueError):
            self.sizer.compute_ewma_volatility(just_short, 0.94)
        exactly_enough = constant_abs_returns(0.20, needed)
        self.assertAlmostEqual(self.sizer.compute_ewma_volatility(exactly_enough, 0.94), 0.20, places=10)

    def test_slower_decay_requires_more_history(self):
        rets = constant_abs_returns(0.20, 100)
        self.assertAlmostEqual(self.sizer.compute_ewma_volatility(rets, 0.94), 0.20, places=10)
        with self.assertRaises(ValueError):
            self.sizer.compute_ewma_volatility(rets, 0.97)  # needs 151

    def test_rolling_minimum_observations_enforced(self):
        sizer = RealizedVolPositionSizer(min_rolling_observations=20)
        with self.assertRaises(ValueError):
            sizer.compute_rolling_volatility(constant_abs_returns(0.20, 19))
        # For an even-length alternating +/-d series the sample mean is exactly 0, so
        # the (n-1) sample estimator returns d*sqrt(n/(n-1)) — the Bessel correction.
        self.assertAlmostEqual(
            sizer.compute_rolling_volatility(constant_abs_returns(0.20, 20)),
            0.20 * math.sqrt(20.0 / 19.0), places=12,
        )

    def test_rolling_and_ewma_embed_different_mean_assumptions(self):
        """
        RiskMetrics centres on zero (Sec. 5.3.1.2), so the EWMA uses raw r^2 and recovers
        d exactly; the rolling estimator subtracts the sample mean and applies (n-1).
        The two therefore disagree on identical data — by design, not by accident.
        """
        rets = constant_abs_returns(0.20, 100)
        ewma = self.sizer.compute_ewma_volatility(rets)
        rolling = self.sizer.compute_rolling_volatility(rets)
        self.assertAlmostEqual(ewma, 0.20, places=10)
        self.assertAlmostEqual(rolling, 0.20 * math.sqrt(100.0 / 99.0), places=12)
        self.assertNotAlmostEqual(ewma, rolling, places=6)

    def test_negative_base_capital_is_rejected(self):
        """Previously produced a negative allocation and negative share count."""
        with self.assertRaises(ValueError) as ctx:
            self.sizer.calculate_position_size(
                "X", -100000.0, 10.0, constant_abs_returns(0.20, 100), use_ewma=True
            )
        self.assertIn("base_capital_usd", str(ctx.exception))

    def test_invalid_price_and_symbol_are_rejected(self):
        rets = constant_abs_returns(0.20, 100)
        for bad_price in (0.0, -10.0, float("nan"), float("inf")):
            with self.subTest(price=bad_price):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_position_size("X", 100000.0, bad_price, rets, use_ewma=True)
        with self.assertRaises(ValueError):
            self.sizer.calculate_position_size("  ", 100000.0, 10.0, rets, use_ewma=True)

    # ------------------------------------------------------------------
    # Constructor validation
    # ------------------------------------------------------------------
    def test_constructor_rejects_incoherent_configuration(self):
        with self.assertRaises(ValueError):
            RealizedVolPositionSizer(target_annualized_vol=0.0)
        with self.assertRaises(ValueError):
            RealizedVolPositionSizer(vol_floor=0.0)          # unbounded leverage
        with self.assertRaises(ValueError):
            RealizedVolPositionSizer(min_scalar=2.0, max_scalar=0.5)
        with self.assertRaises(ValueError):
            RealizedVolPositionSizer(annualization_factor=0.0)
        with self.assertRaises(ValueError):
            RealizedVolPositionSizer(min_rolling_observations=1)


if __name__ == "__main__":
    unittest.main()
