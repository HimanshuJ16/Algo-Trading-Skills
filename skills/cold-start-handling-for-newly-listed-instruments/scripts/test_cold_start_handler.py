"""
Unit tests for cold-start-handling-for-newly-listed-instruments.

Coverage:
1.  Shrinkage arithmetic against the conjugate posterior scale computed independently
    of the implementation, plus fully numeric literals for the documented examples.
2.  The zero-NaN policy the skill's standards mandate: no input reaches the output as
    NaN or Inf, and inputs that would make the result meaningless raise instead.
3.  The degrees-of-freedom boundary at n_obs < 2, where there is no sample variance and
    the observed value (including NaN) must be ignored rather than multiplied by zero.
4.  Monotonicity of the size cap and of the sample weight in n_obs.
5.  Configuration validation.
6.  The legacy standard-deviation blend, and the Jensen inequality that motivates the
    variance-space default.
"""
import math
import unittest

from cold_start_handler import ColdStartHandler, InstrumentStatus


def conjugate_posterior_scale_vol(
    sample_vol: float, prior_vol: float, n_obs: int, nu_0: float
) -> float:
    """
    Independent reference implementation of the estimator under test.

    Scaled-inverse-chi-squared prior ``(nu_0, prior_vol**2)`` updated with a sample
    variance carrying ``nu = n_obs - 1`` degrees of freedom has posterior scale
    ``(nu_0 * prior_vol**2 + nu * sample_vol**2) / (nu_0 + nu)``. Written here in the
    sum-of-sums form rather than the weighted-average form the module uses, so agreement
    is evidence about the estimator and not an echo of the same expression.
    """
    nu = max(0, n_obs - 1)
    return math.sqrt((nu_0 * prior_vol**2 + nu * sample_vol**2) / (nu_0 + nu))


class TestShrinkageArithmetic(unittest.TestCase):
    def setUp(self):
        self.handler = ColdStartHandler(
            warmup_period_days=30, base_max_position_pct=1.0, prior_strength_days=10.0
        )

    def test_five_day_ipo_matches_documented_example(self):
        # 5 observations, sample vol 0.80, peer prior 0.20, nu_0 = 10.
        # w = 4 / 14; variance = (10 * 0.04 + 4 * 0.64) / 14 = 0.2114285714
        # sigma = 0.4598136...; cap = 5 / 30 = 0.1666667
        status = self.handler.process_instrument("NEW_IPO", 5, 0.80, 0.20)
        self.assertAlmostEqual(status.confidence_weight, 4.0 / 14.0, places=12)
        self.assertAlmostEqual(status.estimated_volatility, 0.4598136, places=6)
        self.assertAlmostEqual(status.max_position_cap_pct, 1.0 / 6.0, places=12)
        self.assertTrue(status.is_probationary)
        self.assertTrue(status.used_observed_volatility)

    def test_matches_independent_reference_across_sample_sizes(self):
        for n_obs in (2, 3, 5, 15, 30, 45, 250):
            with self.subTest(n_obs=n_obs):
                status = self.handler.process_instrument("REF", n_obs, 0.55, 0.22)
                self.assertAlmostEqual(
                    status.estimated_volatility,
                    conjugate_posterior_scale_vol(0.55, 0.22, n_obs, 10.0),
                    places=12,
                )

    def test_estimate_lies_between_sample_and_prior(self):
        status = self.handler.process_instrument("MID_IPO", 15, 0.80, 0.20)
        self.assertGreater(status.estimated_volatility, 0.20)
        self.assertLess(status.estimated_volatility, 0.80)
        self.assertAlmostEqual(status.estimated_volatility, math.sqrt(0.39), places=12)

    def test_graduation_governs_the_cap_not_the_shrinkage(self):
        # 45 observations: past the warmup window, so the cap is full and the instrument
        # is no longer probationary -- but 45 observations do not make a variance
        # estimate exact, so the prior still carries 10 / 54 of the weight.
        status = self.handler.process_instrument("MATURE", 45, 0.18, 0.25)
        self.assertFalse(status.is_probationary)
        self.assertEqual(status.probation_progress, 1.0)
        self.assertEqual(status.max_position_cap_pct, 1.0)
        self.assertAlmostEqual(status.confidence_weight, 44.0 / 54.0, places=12)
        self.assertLess(status.confidence_weight, 1.0)
        self.assertAlmostEqual(status.estimated_volatility, 0.1948694, places=6)
        self.assertNotAlmostEqual(status.estimated_volatility, 0.18, places=3)

    def test_sample_weight_never_reaches_one(self):
        for n_obs in (30, 250, 5000, 10**6):
            with self.subTest(n_obs=n_obs):
                self.assertLess(self.handler.calculate_shrinkage_weight(n_obs), 1.0)

    def test_prior_strength_controls_how_fast_the_sample_takes_over(self):
        tight = ColdStartHandler(prior_strength_days=60.0)
        loose = ColdStartHandler(prior_strength_days=2.0)
        self.assertLess(
            tight.calculate_shrinkage_weight(20), loose.calculate_shrinkage_weight(20)
        )
        # nu_0 is in units of observations: the sample carries exactly half the weight
        # once its degrees of freedom equal nu_0.
        self.assertAlmostEqual(loose.calculate_shrinkage_weight(3), 0.5, places=12)


class TestDegreesOfFreedomBoundary(unittest.TestCase):
    """n_obs < 2 yields no sample variance; the observed value must be ignored."""

    def setUp(self):
        self.handler = ColdStartHandler(warmup_period_days=30)

    def test_zero_observations_returns_pure_prior(self):
        status = self.handler.process_instrument(
            "DAY_ZERO", 0, peer_prior_volatility=0.25
        )
        self.assertEqual(status.confidence_weight, 0.0)
        self.assertEqual(status.estimated_volatility, 0.25)
        self.assertEqual(status.max_position_cap_pct, 0.0)
        self.assertFalse(status.used_observed_volatility)

    def test_one_observation_returns_pure_prior(self):
        status = self.handler.process_instrument("DAY_ONE", 1, 9.99, 0.25)
        self.assertEqual(status.confidence_weight, 0.0)
        self.assertEqual(status.estimated_volatility, 0.25)
        self.assertFalse(status.used_observed_volatility)

    def test_nan_sample_is_ignored_below_two_observations(self):
        # Regression: the pre-2.0 handler computed 0.0 * nan == nan here and emitted a
        # NaN volatility into the sizer. A caller with no returns yet has no sample vol
        # to report, and NaN is how that arrives in practice.
        for supplied in (float("nan"), None, 0.0):
            with self.subTest(supplied=supplied):
                status = self.handler.process_instrument("NAN_IN", 1, supplied, 0.25)
                self.assertTrue(math.isfinite(status.estimated_volatility))
                self.assertEqual(status.estimated_volatility, 0.25)

    def test_two_observations_is_the_first_weighted_sample(self):
        status = self.handler.process_instrument("DAY_TWO", 2, 0.80, 0.20)
        self.assertAlmostEqual(status.confidence_weight, 1.0 / 11.0, places=12)
        self.assertTrue(status.used_observed_volatility)


class TestZeroNaNPolicy(unittest.TestCase):
    """The skill's standards forbid emitting NaN/Inf for probationary instruments."""

    def setUp(self):
        self.handler = ColdStartHandler(warmup_period_days=30)

    def test_output_is_finite_and_positive_across_a_sweep(self):
        for n_obs in range(0, 91):
            for sample in (0.0, 0.01, 0.8, 12.0):
                with self.subTest(n_obs=n_obs, sample=sample):
                    status = self.handler.process_instrument("SWEEP", n_obs, sample, 0.2)
                    self.assertTrue(math.isfinite(status.estimated_volatility))
                    self.assertGreater(status.estimated_volatility, 0.0)
                    self.assertTrue(math.isfinite(status.max_position_cap_pct))

    def test_non_finite_prior_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.handler.process_instrument("BAD", 10, 0.3, bad)

    def test_zero_or_negative_prior_rejected(self):
        # A zero prior is the documented pitfall: it asserts a riskless instrument and
        # makes any volatility-scaled sizer divide by zero.
        for bad in (0.0, -0.1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.handler.process_instrument("BAD", 10, 0.3, bad)

    def test_missing_prior_rejected(self):
        with self.assertRaises(ValueError):
            self.handler.process_instrument("BAD", 10, 0.3)

    def test_non_finite_sample_rejected_when_it_carries_weight(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.handler.process_instrument("BAD", 10, bad, 0.2)

    def test_missing_or_negative_sample_rejected_when_it_carries_weight(self):
        with self.assertRaises(ValueError):
            self.handler.process_instrument("BAD", 10, None, 0.2)
        with self.assertRaises(ValueError):
            self.handler.process_instrument("BAD", 10, -0.3, 0.2)

    def test_implausible_magnitudes_rejected_rather_than_overflowing(self):
        # Squaring happens before blending, so an absurd input overflows to inf (or
        # underflows to 0.0) and the result is unusable. Reject it with a message that
        # names the cause instead of surfacing an OverflowError from the arithmetic.
        with self.assertRaises(ValueError):
            self.handler.process_instrument("HUGE", 10, 1e200, 0.2)
        with self.assertRaises(ValueError):
            self.handler.process_instrument("HUGE", 10, 0.2, 1e200)
        with self.assertRaises(ValueError):
            self.handler.process_instrument("TINY", 10, 0.0, 1e-300)

    def test_invalid_observation_counts_rejected(self):
        for bad in (-1, 3.5, "10", True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.handler.process_instrument("BAD", bad, 0.3, 0.2)


class TestPositionCap(unittest.TestCase):
    def test_cap_is_monotonically_non_decreasing(self):
        handler = ColdStartHandler(warmup_period_days=30, base_max_position_pct=0.04)
        previous_cap = -1.0
        previous_weight = -1.0
        for n_obs in range(0, 121):
            status = handler.process_instrument("RAMP", n_obs, 0.5, 0.2)
            self.assertGreaterEqual(status.max_position_cap_pct, previous_cap)
            self.assertGreaterEqual(status.confidence_weight, previous_weight)
            previous_cap = status.max_position_cap_pct
            previous_weight = status.confidence_weight

    def test_cap_never_exceeds_base(self):
        handler = ColdStartHandler(warmup_period_days=30, base_max_position_pct=0.04)
        for n_obs in (0, 1, 29, 30, 31, 1000):
            with self.subTest(n_obs=n_obs):
                status = handler.process_instrument("CAP", n_obs, 0.5, 0.2)
                self.assertLessEqual(status.max_position_cap_pct, 0.04)

    def test_cap_reaches_base_exactly_at_graduation(self):
        handler = ColdStartHandler(warmup_period_days=30, base_max_position_pct=0.04)
        self.assertLess(handler.process_instrument("G", 29, 0.5, 0.2).max_position_cap_pct, 0.04)
        self.assertEqual(handler.process_instrument("G", 30, 0.5, 0.2).max_position_cap_pct, 0.04)
        self.assertFalse(handler.process_instrument("G", 30, 0.5, 0.2).is_probationary)

    def test_floor_keeps_a_day_zero_instrument_tradeable(self):
        handler = ColdStartHandler(
            warmup_period_days=30, base_max_position_pct=0.04, probation_floor_pct=0.005
        )
        self.assertEqual(
            handler.process_instrument("FLOOR", 0, None, 0.5).max_position_cap_pct, 0.005
        )
        previous = -1.0
        for n_obs in range(0, 61):
            cap = handler.process_instrument("FLOOR", n_obs, 0.5, 0.2).max_position_cap_pct
            self.assertGreaterEqual(cap, previous)
            previous = cap


class TestConfigurationValidation(unittest.TestCase):
    def test_rejects_degenerate_warmup(self):
        for bad in (0, -5, 2.5, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ColdStartHandler(warmup_period_days=bad)

    def test_rejects_out_of_range_base_position(self):
        for bad in (0.0, -0.1, 1.5, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ColdStartHandler(base_max_position_pct=bad)

    def test_rejects_non_positive_prior_strength(self):
        for bad in (0.0, -1.0, float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ColdStartHandler(prior_strength_days=bad)

    def test_rejects_floor_above_base(self):
        with self.assertRaises(ValueError):
            ColdStartHandler(base_max_position_pct=0.04, probation_floor_pct=0.05)
        with self.assertRaises(ValueError):
            ColdStartHandler(probation_floor_pct=-0.01)


class TestLegacyStandardDeviationBlend(unittest.TestCase):
    def test_legacy_mode_reproduces_the_linear_blend(self):
        handler = ColdStartHandler(
            warmup_period_days=30, prior_strength_days=10.0, shrink_in_variance_space=False
        )
        status = handler.process_instrument("LEGACY", 5, 0.80, 0.20)
        w = 4.0 / 14.0
        self.assertAlmostEqual(status.estimated_volatility, w * 0.80 + (1 - w) * 0.20, places=12)

    def test_standard_deviation_blend_understates_volatility(self):
        # Jensen: sqrt is concave, so blending standard deviations is at or below
        # blending variances, with equality only when sample and prior agree.
        variance_space = ColdStartHandler(prior_strength_days=10.0)
        stdev_space = ColdStartHandler(
            prior_strength_days=10.0, shrink_in_variance_space=False
        )
        for n_obs in (2, 5, 15, 30, 90):
            with self.subTest(n_obs=n_obs):
                self.assertLess(
                    stdev_space.process_instrument("S", n_obs, 0.80, 0.20).estimated_volatility,
                    variance_space.process_instrument("V", n_obs, 0.80, 0.20).estimated_volatility,
                )
        agreeing = stdev_space.process_instrument("EQ", 10, 0.30, 0.30)
        self.assertAlmostEqual(agreeing.estimated_volatility, 0.30, places=12)


class TestStatusContract(unittest.TestCase):
    def test_status_is_immutable_and_echoes_the_request(self):
        status = ColdStartHandler().process_instrument("ECHO", 7, 0.4, 0.2)
        self.assertIsInstance(status, InstrumentStatus)
        self.assertEqual(status.symbol, "ECHO")
        self.assertEqual(status.n_obs, 7)
        with self.assertRaises(Exception):
            status.estimated_volatility = 0.0  # frozen dataclass


if __name__ == "__main__":
    unittest.main()
