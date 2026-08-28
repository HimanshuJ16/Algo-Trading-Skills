"""
Unit tests for options-implied-volatility-surface-construction.

Expected values are derived independently of the implementation:

- Black-Scholes prices are cross-checked against ``statistics.NormalDist().cdf``
  (a different normal-CDF path from the module's ``math.erf``), against the
  standard textbook value for the ATM one-year call, and against put-call parity,
  which is a model-independent identity.
- Smile volatilities are computed by hand from the documented formula in the
  docstrings below.
- Implied volatilities are checked by round trip: price at a known sigma, invert,
  recover it.

Several tests are explicit regressions -- each is annotated with the behavior of
the previous implementation that it would have caught.
"""
import contextlib
import logging
import math
import statistics
import unittest

from options_implied_volatility_surface import (
    BUTTERFLY_TOLERANCE_FRACTION_OF_SPOT,
    IV_CONDITIONING_WARN_SIGMA_RESOLUTION,
    MIN_STRIKE_IV,
    STATUS_ARBITRAGE_FREE,
    STATUS_BUTTERFLY_VIOLATION,
    STATUS_CALENDAR_VIOLATION,
    STATUS_STATIC_VIOLATION,
    STATUS_UNAUDITED,
    IVSurfaceConfig,
    OptionMarketQuote,
    OptionsIVSurfaceConstructionEngine,
    _solve_3x3,
)

# Keep test output clean without globally disabling logging, which would break
# the assertLogs assertions below.
_MODULE_LOGGER = "options_implied_volatility_surface"
logging.getLogger(_MODULE_LOGGER).addHandler(logging.NullHandler())
logging.getLogger(_MODULE_LOGGER).propagate = False


@contextlib.contextmanager
def capture_warnings():
    """
    Collects WARNING+ records from the module logger without asserting that any
    were emitted, which assertLogs cannot do.
    """
    records = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector(level=logging.WARNING)
    logger = logging.getLogger(_MODULE_LOGGER)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def reference_bs_call(spot, strike, tte, vol, r, q=0.0):
    """
    Independent Black-Scholes-Merton call, using statistics.NormalDist rather than
    the module's math.erf-based CDF.
    """
    cdf = statistics.NormalDist().cdf
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * tte) / (vol * math.sqrt(tte))
    d2 = d1 - vol * math.sqrt(tte)
    return spot * math.exp(-q * tte) * cdf(d1) - strike * math.exp(-r * tte) * cdf(d2)


class TestBlackScholesPricing(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsIVSurfaceConstructionEngine()

    def test_atm_one_year_call_matches_textbook_and_independent_cdf(self):
        """
        S=K=100, T=1, sigma=20%, r=5%, q=0 is the standard worked example; the
        call is 10.4506. Cross-checked against an independent NormalDist path.
        """
        price = self.engine.black_scholes_price("CALL", 100.0, 100.0, 1.0, 0.20, 0.05)
        self.assertAlmostEqual(price, 10.4506, places=4)
        self.assertAlmostEqual(price, reference_bs_call(100.0, 100.0, 1.0, 0.20, 0.05), places=12)

    def test_put_call_parity_holds_with_dividend_yield(self):
        """
        C - P = S e^{-qT} - K e^{-rT} is model-independent. It fails if the put
        branch or the dividend discounting is wrong.
        """
        args = dict(spot=100.0, strike=95.0, tte=0.75, vol=0.28, r=0.04, q=0.02)
        call = self.engine.black_scholes_price("CALL", **args)
        put = self.engine.black_scholes_price("PUT", **args)
        expected = 100.0 * math.exp(-0.02 * 0.75) - 95.0 * math.exp(-0.04 * 0.75)
        self.assertAlmostEqual(call - put, expected, places=12)

    def test_zero_tte_and_zero_vol_return_intrinsic(self):
        """
        REGRESSION: the previous implementation clamped tte to 1e-4 and vol to
        0.01, so an expired option and a zero-volatility option both returned a
        small fictitious time value instead of intrinsic.
        """
        expired_call = self.engine.black_scholes_price("CALL", 110.0, 100.0, 0.0, 0.20, 0.05)
        self.assertEqual(expired_call, 10.0)

        expired_put = self.engine.black_scholes_price("PUT", 90.0, 100.0, 0.0, 0.20, 0.05)
        self.assertEqual(expired_put, 10.0)

        # Zero vol: discounted intrinsic under the forward measure, not a clamp.
        zero_vol_call = self.engine.black_scholes_price("CALL", 100.0, 100.0, 1.0, 0.0, 0.05)
        self.assertAlmostEqual(zero_vol_call, 100.0 - 100.0 * math.exp(-0.05), places=12)

    def test_invalid_pricing_inputs_raise(self):
        """
        REGRESSION: strike=0 previously raised ZeroDivisionError from math.log,
        and a NaN volatility propagated silently into a NaN price.
        """
        for kwargs in (
            dict(spot=100.0, strike=0.0, tte=1.0, vol=0.2, r=0.05),
            dict(spot=-100.0, strike=100.0, tte=1.0, vol=0.2, r=0.05),
            dict(spot=100.0, strike=100.0, tte=-1.0, vol=0.2, r=0.05),
            dict(spot=100.0, strike=100.0, tte=1.0, vol=-0.2, r=0.05),
            dict(spot=100.0, strike=100.0, tte=1.0, vol=float("nan"), r=0.05),
            dict(spot=100.0, strike=100.0, tte=1.0, vol=0.2, r=float("inf")),
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self.engine.black_scholes_price("CALL", **kwargs)

    def test_unknown_option_type_raises_instead_of_pricing_a_put(self):
        """
        REGRESSION: the previous code took the put branch for any string that was
        not 'CALL', so 'C' silently returned a put price.
        """
        with self.assertRaises(ValueError):
            self.engine.black_scholes_price("C", 100.0, 100.0, 1.0, 0.20, 0.05)
        with self.assertRaises(ValueError):
            self.engine.black_scholes_price("STRADDLE", 100.0, 100.0, 1.0, 0.20, 0.05)


class TestImpliedVolatilityInversion(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsIVSurfaceConstructionEngine()

    def test_round_trip_is_accurate_wherever_it_is_not_flagged(self):
        """
        Sweeps strikes from 30% ITM to 40% OTM, one month to two years, and
        volatilities from 20% to 85%, and asserts the property that makes the
        conditioning warning trustworthy:

            no warning  =>  the round trip is accurate to
                            IV_CONDITIONING_WARN_SIGMA_RESOLUTION (1e-6).

        Accuracy does degrade towards the edge of this grid -- a one-month 30%-ITM
        call has a vega of ~1.4e-8 and round-trips only to ~1.6e-6 -- and every
        such case is flagged. An inversion that was silently wrong by more than
        the advertised resolution would fail this test.

        The well-conditioned core (strikes within 25% of spot, at least three
        months) is held to a far tighter bound, since that is where quotes that
        actually feed a smile fit live.
        """
        flagged = 0
        unflagged = 0
        for option_type in ("CALL", "PUT"):
            for strike in (70.0, 95.0, 100.0, 105.0, 140.0):
                for tte in (0.08, 0.25, 2.0):
                    for sigma in (0.20, 0.45, 0.85):
                        with self.subTest(option_type, strike=strike, tte=tte, sigma=sigma):
                            price = self.engine.black_scholes_price(
                                option_type, 100.0, strike, tte, sigma, 0.05
                            )
                            with capture_warnings() as records:
                                recovered = self.engine.implied_volatility_from_price(
                                    option_type, strike, tte, price
                                )
                            if records:
                                flagged += 1
                                continue
                            unflagged += 1
                            self.assertLess(
                                abs(recovered - sigma), IV_CONDITIONING_WARN_SIGMA_RESOLUTION
                            )
                            if 0.75 <= strike / 100.0 <= 1.25 and tte >= 0.25:
                                self.assertAlmostEqual(recovered, sigma, places=9)

        # The sweep must exercise both branches, or the property is vacuous in
        # one direction.
        self.assertGreater(flagged, 0)
        self.assertGreater(unflagged, flagged)

    def test_round_trip_holds_with_a_dividend_yield(self):
        engine = OptionsIVSurfaceConstructionEngine(
            IVSurfaceConfig(spot_price=100.0, risk_free_rate=0.04, dividend_yield=0.025)
        )
        for option_type in ("CALL", "PUT"):
            for strike in (85.0, 100.0, 120.0):
                with self.subTest(option_type, strike=strike):
                    price = engine.black_scholes_price(
                        option_type, 100.0, strike, 0.75, 0.33, 0.04, 0.025
                    )
                    self.assertAlmostEqual(
                        engine.implied_volatility_from_price(option_type, strike, 0.75, price),
                        0.33, places=7,
                    )

    def test_no_arbitrage_bounds_are_the_zero_and_infinite_vol_limits(self):
        lower, upper = self.engine.no_arbitrage_price_bounds("CALL", 100.0, 90.0, 1.0, 0.05, 0.0)
        self.assertAlmostEqual(lower, 100.0 - 90.0 * math.exp(-0.05), places=12)
        self.assertAlmostEqual(upper, 100.0, places=12)

        lower_put, upper_put = self.engine.no_arbitrage_price_bounds(
            "PUT", 100.0, 110.0, 1.0, 0.05, 0.0
        )
        self.assertAlmostEqual(lower_put, 110.0 * math.exp(-0.05) - 100.0, places=12)
        self.assertAlmostEqual(upper_put, 110.0 * math.exp(-0.05), places=12)

    def test_quote_outside_no_arbitrage_bounds_raises(self):
        """
        A price at or below intrinsic, or at or above the upper bound, has no
        implied volatility. Returning a clamped number would feed a fabricated
        volatility into the smile fit.
        """
        lower, upper = self.engine.no_arbitrage_price_bounds("CALL", 100.0, 90.0, 1.0, 0.05, 0.0)
        for bad_price in (lower, lower - 1.0, upper, upper + 1.0, 0.0, -5.0):
            with self.subTest(price=bad_price):
                with self.assertRaises(ValueError):
                    self.engine.implied_volatility_from_price("CALL", 90.0, 1.0, bad_price)

    def test_price_above_solver_ceiling_raises_rather_than_returning_the_ceiling(self):
        price_at_ceiling = self.engine.black_scholes_price("CALL", 100.0, 100.0, 1.0, 5.0, 0.05)
        with self.assertRaises(ValueError):
            self.engine.implied_volatility_from_price(
                "CALL", 100.0, 1.0, 0.5 * (price_at_ceiling + 100.0)
            )

    def test_zero_tte_quote_raises_because_sigma_is_unidentified(self):
        with self.assertRaises(ValueError):
            self.engine.implied_volatility_from_price("CALL", 100.0, 0.0, 5.0)

    def test_low_vega_quote_still_inverts(self):
        """
        The regime where Newton-Raphson is unsafe: vega is small, so the Newton
        step residual/vega is large and the iteration can leave the domain.
        Bisection is unaffected. Vega here is ~0.016, four orders of magnitude
        below the ATM value, and the round trip still holds to 7 places.
        """
        sigma = 0.25
        price = self.engine.black_scholes_price("CALL", 100.0, 130.0, 0.08, sigma, 0.05)
        self.assertGreater(price, 0.0)
        self.assertLess(self.engine.vega(100.0, 130.0, 0.08, sigma, 0.05), 0.1)
        self.assertAlmostEqual(
            self.engine.implied_volatility_from_price("CALL", 130.0, 0.08, price),
            sigma, places=7,
        )

    def test_vega_matches_the_closed_form_and_is_type_independent(self):
        d1 = (math.log(100.0 / 95.0) + (0.05 + 0.5 * 0.20 ** 2) * 0.5) / (0.20 * math.sqrt(0.5))
        expected = 100.0 * statistics.NormalDist().pdf(d1) * math.sqrt(0.5)
        self.assertAlmostEqual(
            self.engine.vega(100.0, 95.0, 0.5, 0.20, 0.05), expected, places=10
        )
        self.assertEqual(self.engine.vega(100.0, 95.0, 0.0, 0.20, 0.05), 0.0)
        self.assertEqual(self.engine.vega(100.0, 95.0, 0.5, 0.0, 0.05), 0.0)

    def test_vega_rejects_negative_inputs_like_the_pricer(self):
        """
        Returning 0.0 for a negative tte would make an invalid quote look merely
        ill-conditioned rather than invalid.
        """
        with self.assertRaises(ValueError):
            self.engine.vega(100.0, 95.0, -0.5, 0.20, 0.05)
        with self.assertRaises(ValueError):
            self.engine.vega(100.0, 95.0, 0.5, -0.20, 0.05)

    def test_ill_conditioned_quote_is_flagged_not_silently_precise(self):
        """
        A 3-month 40%-OTM call at 8% volatility has a time value of ~2.6e-15
        dollars. Every volatility within roughly +/-0.0075 prices to the same
        float64 number, so the returned figure is arbitrary within that band. It
        must be logged as unreliable rather than handed back as an 8-decimal
        answer that a smile fit would then weight equally with a real quote.
        """
        price = self.engine.black_scholes_price("CALL", 100.0, 140.0, 0.25, 0.08, 0.05)
        with self.assertLogs(_MODULE_LOGGER, level="WARNING") as captured:
            recovered = self.engine.implied_volatility_from_price("CALL", 140.0, 0.25, price)
        self.assertIn("poorly identified", captured.output[0])
        # The point of the warning: the answer is materially off despite the
        # solver converging to float64 precision on the bracket.
        self.assertGreater(abs(recovered - 0.08), 1e-3)

    def test_well_conditioned_quote_is_not_flagged(self):
        price = self.engine.black_scholes_price("CALL", 100.0, 100.0, 1.0, 0.20, 0.05)
        logger = logging.getLogger(_MODULE_LOGGER)
        with self.assertNoLogs(logger, level="WARNING"):
            self.engine.implied_volatility_from_price("CALL", 100.0, 1.0, price)

    def test_price_underflowing_to_intrinsic_carries_no_volatility(self):
        """
        Beyond the ill-conditioned band the time value underflows entirely and
        the price equals the no-arbitrage lower bound. There is no implied
        volatility to return, and fabricating one would poison the wing of the fit.
        """
        price = self.engine.black_scholes_price("CALL", 100.0, 140.0, 0.02, 0.20, 0.05)
        lower, _ = self.engine.no_arbitrage_price_bounds("CALL", 100.0, 140.0, 0.02, 0.05, 0.0)
        self.assertEqual(price, lower)
        with self.assertRaises(ValueError):
            self.engine.implied_volatility_from_price("CALL", 140.0, 0.02, price)


class TestParametricSmile(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsIVSurfaceConstructionEngine(IVSurfaceConfig(
            spot_price=100.0, risk_free_rate=0.05,
            atm_vol=0.20, skew_alpha=-0.30, smile_beta=0.50,
        ))

    def test_smile_matches_the_documented_formula_exactly(self):
        """
        REGRESSION: the previous implementation multiplied the skew/smile offset
        by an undocumented 0.5, so it returned half the documented offset.

        Hand-computed from IV(m) = 0.20 + (-0.30)(m-1) + 0.50(m-1)^2:
            m = 1.00, x =  0.00 -> 0.20
            m = 0.90, x = -0.10 -> 0.20 + 0.030 + 0.0050 = 0.2350  (old code: 0.2175)
            m = 1.10, x = +0.10 -> 0.20 - 0.030 + 0.0050 = 0.1750  (old code: 0.1875)
            m = 0.80, x = -0.20 -> 0.20 + 0.060 + 0.0200 = 0.2800  (old code: 0.2400)
        """
        self.assertAlmostEqual(self.engine.evaluate_strike_iv(100.0, 0.25), 0.2000, places=12)
        self.assertAlmostEqual(self.engine.evaluate_strike_iv(90.0, 0.25), 0.2350, places=12)
        self.assertAlmostEqual(self.engine.evaluate_strike_iv(110.0, 0.25), 0.1750, places=12)
        self.assertAlmostEqual(self.engine.evaluate_strike_iv(80.0, 0.25), 0.2800, places=12)

    def test_put_skew_elevates_downside_volatility(self):
        self.assertGreater(
            self.engine.evaluate_strike_iv(90.0, 0.25),
            self.engine.evaluate_strike_iv(110.0, 0.25),
        )

    def test_atm_vol_override_shifts_the_level_without_touching_the_offset(self):
        base = self.engine.evaluate_strike_iv(90.0, 0.25)
        shifted = self.engine.evaluate_strike_iv(90.0, 0.25, atm_vol=0.30)
        self.assertAlmostEqual(shifted - base, 0.10, places=12)

    def test_clamped_wing_is_logged_not_silent(self):
        """
        A clamped wing is no longer the parametric surface and can mask an
        arbitrage, so the binding must be visible in the log.
        """
        steep = OptionsIVSurfaceConstructionEngine(
            IVSurfaceConfig(atm_vol=0.20, skew_alpha=-2.0, smile_beta=0.0)
        )
        with self.assertLogs(_MODULE_LOGGER, level="WARNING") as captured:
            iv = steep.evaluate_strike_iv(120.0, 1.0)
        self.assertEqual(iv, MIN_STRIKE_IV)
        self.assertIn("clamped", captured.output[0].lower())

    def test_invalid_smile_inputs_raise(self):
        """
        REGRESSION: the previous implementation returned config.atm_vol for a
        non-positive strike or tte, silently substituting the ATM level for a
        wing it could not evaluate.
        """
        for strike, tte in ((0.0, 0.25), (-10.0, 0.25), (100.0, 0.0), (100.0, -1.0)):
            with self.subTest(strike=strike, tte=tte):
                with self.assertRaises(ValueError):
                    self.engine.evaluate_strike_iv(strike, tte)


class TestSmileCalibration(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsIVSurfaceConstructionEngine(IVSurfaceConfig(
            spot_price=100.0, risk_free_rate=0.05, dividend_yield=0.01,
        ))

    def _quotes_from(self, atm, alpha, beta, strikes, tte):
        """Exact BS prices generated from a known smile, for recovery testing."""
        quotes = []
        for strike in strikes:
            x = strike / 100.0 - 1.0
            sigma = atm + alpha * x + beta * x * x
            price = self.engine.black_scholes_price(
                "CALL", 100.0, strike, tte, sigma, 0.05, 0.01
            )
            quotes.append(OptionMarketQuote(strike, tte, price, "CALL"))
        return quotes

    def test_calibration_recovers_the_generating_parameters(self):
        atm, alpha, beta = 0.24, -0.45, 0.80
        quotes = self._quotes_from(atm, alpha, beta, [85, 90, 95, 100, 105, 110, 115], 0.5)
        result = self.engine.calibrate_smile_from_quotes(quotes)

        self.assertAlmostEqual(result.atm_vol, atm, places=6)
        self.assertAlmostEqual(result.skew_alpha, alpha, places=6)
        self.assertAlmostEqual(result.smile_beta, beta, places=6)
        self.assertEqual(result.quotes_used, 7)
        self.assertLess(result.rms_error, 1e-6)
        self.assertEqual(result.tte_years, 0.5)
        self.assertEqual(len(result.implied_vols), 7)

    def test_calibration_uses_the_dividend_yield_from_config(self):
        """
        Inverting with q=0 against quotes generated with q=1% biases every IV, so
        a recovery to 6dp is itself the proof that q was carried through.
        """
        quotes = self._quotes_from(0.22, -0.30, 0.50, [90, 95, 100, 105, 110], 1.0)
        result = self.engine.calibrate_smile_from_quotes(quotes)
        self.assertAlmostEqual(result.atm_vol, 0.22, places=6)

    def test_mixed_expirations_raise(self):
        """A smile is one expiration; mixing tenors fits a cross-section as a slice."""
        quotes = self._quotes_from(0.22, -0.30, 0.50, [95, 100, 105], 0.5)
        quotes.append(self._quotes_from(0.22, -0.30, 0.50, [100], 1.0)[0])
        with self.assertRaises(ValueError):
            self.engine.calibrate_smile_from_quotes(quotes)

    def test_too_few_quotes_raise(self):
        quotes = self._quotes_from(0.22, -0.30, 0.50, [95, 105], 0.5)
        with self.assertRaises(ValueError):
            self.engine.calibrate_smile_from_quotes(quotes)

    def test_repeated_moneyness_is_reported_as_unidentified_not_fitted(self):
        quotes = self._quotes_from(0.22, -0.30, 0.50, [100, 100, 100], 0.5)
        with self.assertRaises(ValueError):
            self.engine.calibrate_smile_from_quotes(quotes)

    def test_empty_quotes_raise(self):
        with self.assertRaises(ValueError):
            self.engine.calibrate_smile_from_quotes([])

    def test_solver_recovers_a_known_linear_system(self):
        """_solve_3x3 checked against a hand-chosen system with solution (1, -2, 3)."""
        matrix = [[2.0, 1.0, -1.0], [-3.0, -1.0, 2.0], [-2.0, 1.0, 2.0]]
        rhs = [
            2.0 * 1.0 + 1.0 * -2.0 + -1.0 * 3.0,
            -3.0 * 1.0 + -1.0 * -2.0 + 2.0 * 3.0,
            -2.0 * 1.0 + 1.0 * -2.0 + 2.0 * 3.0,
        ]
        solved = _solve_3x3(matrix, rhs)
        for got, want in zip(solved, (1.0, -2.0, 3.0)):
            self.assertAlmostEqual(got, want, places=10)


class TestSurfaceGridAndArbitrageAudits(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsIVSurfaceConstructionEngine(IVSurfaceConfig(
            spot_price=100.0, risk_free_rate=0.05,
            atm_vol=0.20, skew_alpha=-0.30, smile_beta=0.50,
        ))

    def test_benign_surface_is_audited_and_clean(self):
        report = self.engine.construct_surface_grid([90.0, 100.0, 110.0], [0.25, 0.50, 1.0])

        self.assertEqual(report.status, STATUS_ARBITRAGE_FREE)
        self.assertEqual(report.total_surface_points, 9)
        self.assertTrue(report.calendar_audit_performed)
        self.assertTrue(report.butterfly_audit_performed)
        self.assertTrue(report.is_arbitrage_free)

        atm_short = next(
            p for p in report.grid_points if p.strike == 100.0 and p.tte_years == 0.25
        )
        atm_long = next(
            p for p in report.grid_points if p.strike == 100.0 and p.tte_years == 1.0
        )
        self.assertGreater(atm_long.total_variance, atm_short.total_variance)
        # w = sigma^2 t, computed by hand: 0.20^2 * 0.25 and 0.20^2 * 1.0.
        self.assertAlmostEqual(atm_short.total_variance, 0.01, places=12)
        self.assertAlmostEqual(atm_long.total_variance, 0.04, places=12)

    def test_grid_point_carries_the_forward_and_log_forward_moneyness(self):
        report = self.engine.construct_surface_grid([110.0], [2.0])
        point = report.grid_points[0]
        expected_forward = 100.0 * math.exp(0.05 * 2.0)
        self.assertAlmostEqual(point.forward_price, expected_forward, places=12)
        self.assertAlmostEqual(
            point.log_forward_moneyness, math.log(110.0 / expected_forward), places=12
        )
        self.assertAlmostEqual(point.moneyness, 1.10, places=12)

    def test_calendar_audit_is_at_fixed_log_forward_moneyness_not_fixed_strike(self):
        """
        REGRESSION -- this is the defect the rewrite exists to fix.

        Gatheral & Jacquier (arXiv:1204.0646) Lemma 2.1 requires d/dt w(k,t) >= 0
        at constant K/F, and their proof compares K_1/F_{t_1} = K_2/F_{t_2}. The
        previous implementation compared total variance at constant *strike*.

        With a steep put skew and r > q the forward drifts, so the two are
        different comparisons. Here the old fixed-strike scan reports every strike
        monotone, while the correct fixed-k scan finds a genuine violation at
        k = ln(102 / F(0.5)) = -0.005197:

            t = 0.50: K = 102.000, m = 1.0200, sigma = 0.20 - 2.0(0.0200) = 0.1600
                      w = 0.1600^2 * 0.50 = 0.012800
            t = 1.00: K = 104.582, m = 1.0458, sigma = 0.20 - 2.0(0.0458) = 0.10836
                      w = 0.10836^2 * 1.00 = 0.011741   <-- w fell
        """
        steep = OptionsIVSurfaceConstructionEngine(
            IVSurfaceConfig(atm_vol=0.20, skew_alpha=-2.0, smile_beta=0.0)
        )
        strikes = [98.0, 100.0, 102.0]
        expirations = [0.5, 1.0]

        # What the previous fixed-strike check saw: no violation anywhere.
        for strike in strikes:
            variances = [steep.evaluate_strike_iv(strike, t) ** 2 * t for t in expirations]
            self.assertGreater(variances[1], variances[0])

        report = steep.construct_surface_grid(strikes, expirations)
        self.assertEqual(len(report.calendar_violations), 1)
        violation = report.calendar_violations[0]
        self.assertAlmostEqual(violation.log_forward_moneyness, -0.0051973727, places=9)
        self.assertAlmostEqual(violation.total_variance_short, 0.0128, places=12)
        self.assertAlmostEqual(violation.total_variance_long, 0.0117412729, places=9)
        self.assertGreater(violation.shortfall, 0.0)
        self.assertFalse(report.is_arbitrage_free)

    def test_inverted_atm_term_structure_is_calendar_arbitrage(self):
        """
        40% vol at 3 months falling to 15% at 1 year: w(0.25) = 0.04 but
        w(1.0) = 0.0225. Total variance cannot fall, so this must be flagged.
        """
        report = self.engine.construct_surface_grid(
            [100.0], [0.25, 1.0], atm_vol_by_tte={0.25: 0.40, 1.0: 0.15},
        )
        self.assertEqual(report.status, STATUS_CALENDAR_VIOLATION)
        self.assertEqual(len(report.calendar_violations), 1)
        violation = report.calendar_violations[0]
        self.assertLess(violation.total_variance_long, violation.total_variance_short)

    def test_rising_atm_term_structure_is_clean(self):
        report = self.engine.construct_surface_grid(
            [95.0, 100.0, 105.0], [0.25, 1.0], atm_vol_by_tte={0.25: 0.18, 1.0: 0.22},
        )
        self.assertEqual(report.calendar_violations, [])
        self.assertEqual(report.status, STATUS_ARBITRAGE_FREE)

    def test_steep_skew_produces_a_negative_risk_neutral_density(self):
        """
        A put skew steep enough to make the call price concave in strike is a
        butterfly with negative cost. alpha = -2.0 gives, by hand from the
        documented smile at S = 100:

            K =  95, x = -0.05 -> sigma = 0.20 + 0.10 = 0.30
            K = 100, x =  0.00 -> sigma = 0.20
            K = 105, x = +0.05 -> sigma = 0.20 - 0.10 = 0.10

        Equal spacing, so the butterfly is 0.5 C(95) + 0.5 C(105) - C(100), priced
        independently below.
        """
        steep = OptionsIVSurfaceConstructionEngine(
            IVSurfaceConfig(atm_vol=0.20, skew_alpha=-2.0, smile_beta=0.0)
        )
        report = steep.construct_surface_grid([95.0, 100.0, 105.0], [1.0])

        self.assertEqual(report.status, STATUS_BUTTERFLY_VIOLATION)
        self.assertEqual(len(report.butterfly_violations), 1)

        expected = (
            0.5 * reference_bs_call(100.0, 95.0, 1.0, 0.30, 0.05)
            + 0.5 * reference_bs_call(100.0, 105.0, 1.0, 0.10, 0.05)
            - reference_bs_call(100.0, 100.0, 1.0, 0.20, 0.05)
        )
        self.assertLess(expected, 0.0)
        violation = report.butterfly_violations[0]
        self.assertAlmostEqual(violation.butterfly_value, expected, places=10)
        self.assertEqual(
            (violation.strike_low, violation.strike_mid, violation.strike_high),
            (95.0, 100.0, 105.0),
        )
        self.assertFalse(report.is_arbitrage_free)

    def test_unequally_spaced_strikes_use_spacing_weights(self):
        """
        Listed chains are not equally spaced, and the naive equal-weighted
        butterfly is the wrong test on them.

        With K = 90, 95, 150 the convexity weights are (150-95)/60 = 0.91667 and
        (95-90)/60 = 0.08333. On this benign surface the correctly weighted
        butterfly is positive while the equal-weighted one is negative -- equal
        weighting would report an arbitrage that does not exist.
        """
        strikes = [90.0, 95.0, 150.0]
        report = self.engine.construct_surface_grid(strikes, [0.5, 1.0])
        self.assertEqual(report.butterfly_violations, [])

        prices = {
            k: reference_bs_call(100.0, k, 0.5, self.engine.evaluate_strike_iv(k, 0.5), 0.05)
            for k in strikes
        }
        weight_low = (150.0 - 95.0) / 60.0
        weight_high = (95.0 - 90.0) / 60.0
        self.assertAlmostEqual(weight_low + weight_high, 1.0, places=12)

        weighted = weight_low * prices[90.0] + weight_high * prices[150.0] - prices[95.0]
        self.assertGreater(weighted, 0.0)

        equal_weighted = 0.5 * prices[90.0] + 0.5 * prices[150.0] - prices[95.0]
        self.assertLess(equal_weighted, 0.0)

    def test_both_violations_report_the_combined_status(self):
        steep = OptionsIVSurfaceConstructionEngine(
            IVSurfaceConfig(atm_vol=0.20, skew_alpha=-2.0, smile_beta=0.0)
        )
        report = steep.construct_surface_grid([98.0, 100.0, 102.0], [0.5, 1.0])
        self.assertTrue(report.calendar_violations)
        self.assertTrue(report.butterfly_violations)
        self.assertEqual(report.status, STATUS_STATIC_VIOLATION)

    def test_surface_too_sparse_to_audit_is_not_reported_arbitrage_free(self):
        """
        REGRESSION: the previous implementation returned ARBITRAGE_FREE_SURFACE
        for any grid in which no violation was found, including grids on which no
        check could run at all. Unaudited is not the same as clean.
        """
        single_expiry = self.engine.construct_surface_grid([90.0, 100.0, 110.0], [0.5])
        self.assertEqual(single_expiry.status, STATUS_UNAUDITED)
        self.assertFalse(single_expiry.calendar_audit_performed)
        self.assertTrue(single_expiry.butterfly_audit_performed)
        self.assertFalse(single_expiry.is_arbitrage_free)

        two_strikes = self.engine.construct_surface_grid([95.0, 105.0], [0.25, 1.0])
        self.assertEqual(two_strikes.status, STATUS_UNAUDITED)
        self.assertTrue(two_strikes.calendar_audit_performed)
        self.assertFalse(two_strikes.butterfly_audit_performed)
        self.assertFalse(two_strikes.is_arbitrage_free)

    def test_duplicate_strikes_and_expirations_are_deduplicated(self):
        report = self.engine.construct_surface_grid(
            [100.0, 100.0, 90.0, 110.0], [0.5, 0.5, 1.0]
        )
        self.assertEqual(report.total_surface_points, 6)

    def test_empty_grid_inputs_raise(self):
        with self.assertRaises(ValueError):
            self.engine.construct_surface_grid([], [0.5])
        with self.assertRaises(ValueError):
            self.engine.construct_surface_grid([100.0], [])

    def test_missing_term_structure_entry_raises(self):
        """
        Substituting the flat config level for a missing tenor would fabricate a
        term structure and could turn a real calendar violation into a clean report.
        """
        with self.assertRaises(ValueError):
            self.engine.construct_surface_grid(
                [100.0], [0.25, 1.0], atm_vol_by_tte={0.25: 0.40},
            )

    def test_invalid_grid_values_raise(self):
        for strikes, expirations in (
            ([0.0, 100.0], [0.5]),
            ([100.0], [0.0, 0.5]),
            ([float("nan")], [0.5]),
            ([100.0], [float("inf")]),
        ):
            with self.subTest(strikes=strikes, expirations=expirations):
                with self.assertRaises(ValueError):
                    self.engine.construct_surface_grid(strikes, expirations)

    def test_dividend_yield_moves_the_forward(self):
        engine = OptionsIVSurfaceConstructionEngine(
            IVSurfaceConfig(spot_price=100.0, risk_free_rate=0.05, dividend_yield=0.03)
        )
        self.assertAlmostEqual(engine.forward_price(2.0), 100.0 * math.exp(0.02 * 2.0), places=12)

    def test_butterfly_tolerance_is_floating_point_slack_only(self):
        """
        The tolerance must stay far below any tradeable butterfly. At spot 100 it
        is 1e-8 dollars -- eight orders of magnitude below one cent.
        """
        self.assertLess(BUTTERFLY_TOLERANCE_FRACTION_OF_SPOT * 100.0, 1e-6)


class TestConfigValidation(unittest.TestCase):

    def test_invalid_config_values_raise_at_construction(self):
        for kwargs in (
            dict(spot_price=0.0),
            dict(spot_price=-100.0),
            dict(atm_vol=0.0),
            dict(atm_vol=-0.2),
            dict(risk_free_rate=float("nan")),
            dict(skew_alpha=float("inf")),
            dict(dividend_yield=float("nan")),
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    IVSurfaceConfig(**kwargs)

    def test_defaults_are_valid(self):
        config = IVSurfaceConfig()
        self.assertEqual(config.spot_price, 100.0)
        self.assertEqual(config.dividend_yield, 0.0)


if __name__ == "__main__":
    unittest.main()
