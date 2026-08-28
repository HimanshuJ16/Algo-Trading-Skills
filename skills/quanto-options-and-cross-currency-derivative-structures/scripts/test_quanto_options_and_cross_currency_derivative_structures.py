"""
Tests for the quanto option pricing engine.

Expected values are derived **independently** of the engine wherever a number is
asserted:

- ``_merton_bs`` is the textbook Black-Scholes-Merton formula with a continuous
  dividend yield, coded from ``C = S e^{-qT} N(d1) - K e^{-rT} N(d2)``. Haugh
  (IEOR E4707, "Foreign Exchange, ADRs and Quanto-Securities", Section 4) shows
  a quanto call equals ``X_bar`` times a Merton call with dividend yield
  ``q_f = q + r_d - r_f + rho * sigma_x * sigma_S`` and strike ``K / X_bar``.
  That is a different arrangement of the algebra reaching the same price, so it
  is a real cross-check rather than a restatement of the engine's own code.
- Greeks are checked against Richardson-extrapolated central finite differences
  of the engine's *price*, which never touches the analytic Greek formulas.
- The put-call parity residual is checked directly.
- A deterministic Monte Carlo re-derives the price from the simulated payoff.
"""
import math
import random
import unittest

from quanto_options_and_cross_currency_derivative_structures import (
    InputData,
    QuantoOptionPricingReport,
    QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine,
    norm_cdf,
)

BASE = dict(
    spot_price=100.0,
    strike_price=100.0,
    time_to_expiry_years=1.0,
    domestic_rate=0.05,
    foreign_rate=0.02,
    dividend_yield=0.0,
    asset_volatility=0.20,
    fx_volatility=0.15,
    correlation=0.30,
    fixed_fx_rate=1.0,
)


def _merton_bs(cp: str, S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """Textbook Black-Scholes-Merton with continuous dividend yield. Independent of the engine."""
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if cp == "CALL":
        return S * math.exp(-q * T) * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * math.exp(-q * T) * norm_cdf(-d1)


def _quanto_via_merton(option_type: str, **overrides) -> float:
    """Haugh's equivalent formulation: X_bar * Merton(strike K / X_bar, yield q_f)."""
    p = {**BASE, **overrides}
    q_f = (
        p["dividend_yield"]
        + p["domestic_rate"]
        - p["foreign_rate"]
        + p["correlation"] * p["fx_volatility"] * p["asset_volatility"]
    )
    fx = p["fixed_fx_rate"]
    return fx * _merton_bs(
        option_type,
        p["spot_price"],
        p["strike_price"],
        p["time_to_expiry_years"],
        p["domestic_rate"],
        q_f,
        p["asset_volatility"],
    )


class QuantoPricingCoreTest(unittest.TestCase):
    """The price itself, against independently derived values."""

    def setUp(self):
        self.engine = QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine()

    def price(self, option_type="CALL", **overrides) -> float:
        return self.engine.price_quanto_option(
            InputData(option_type=option_type, **{**BASE, **overrides})
        ).quanto_option_price_domestic

    def test_degenerates_to_textbook_black_scholes(self):
        """
        With zero FX volatility and r_d == r_f == 5%, q = 0, the quanto adjustment
        and the rate mismatch both vanish and the price must collapse onto the
        standard worked example BS(100, 100, 1y, 5%, 20%) = 10.450584.
        """
        self.assertAlmostEqual(
            self.price(fx_volatility=0.0, domestic_rate=0.05, foreign_rate=0.05),
            10.450583572185565,
            places=10,
        )

    def test_matches_independent_merton_formulation(self):
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                self.assertAlmostEqual(
                    self.price(option_type), _quanto_via_merton(option_type), places=10
                )

    def test_matches_independent_merton_across_a_grid(self):
        for K in (80.0, 100.0, 125.0):
            for T in (0.25, 2.0):
                for rho in (-0.8, 0.0, 0.6):
                    for option_type in ("CALL", "PUT"):
                        with self.subTest(K=K, T=T, rho=rho, option_type=option_type):
                            ov = dict(strike_price=K, time_to_expiry_years=T, correlation=rho)
                            self.assertAlmostEqual(
                                self.price(option_type, **ov),
                                _quanto_via_merton(option_type, **ov),
                                places=9,
                            )

    def test_put_call_parity(self):
        """C - P = F_X e^{-r_d T} (S e^{mu T} - K), with mu the quanto drift."""
        report = self.engine.price_quanto_option(InputData(option_type="CALL", **BASE))
        put = self.price("PUT")
        expected = (
            BASE["fixed_fx_rate"]
            * math.exp(-BASE["domestic_rate"] * BASE["time_to_expiry_years"])
            * (report.quanto_forward_foreign - BASE["strike_price"])
        )
        self.assertAlmostEqual(report.quanto_option_price_domestic - put, expected, places=12)

    def test_monte_carlo_agreement(self):
        """
        Simulate S_T under the domestic risk-neutral measure and discount the
        realized payoff. Deterministic: fixed seed plus antithetic pairs.
        """
        S, K, T = BASE["spot_price"], BASE["strike_price"], BASE["time_to_expiry_years"]
        sigma, r_d = BASE["asset_volatility"], BASE["domestic_rate"]
        mu = (
            BASE["foreign_rate"]
            - BASE["dividend_yield"]
            - BASE["correlation"] * sigma * BASE["fx_volatility"]
        )
        rng = random.Random(20260827)
        drift, diffusion = (mu - 0.5 * sigma * sigma) * T, sigma * math.sqrt(T)
        total = 0.0
        paths = 400_000
        for _ in range(paths // 2):
            z = rng.gauss(0.0, 1.0)
            for zz in (z, -z):
                total += max(S * math.exp(drift + diffusion * zz) - K, 0.0)
        mc = BASE["fixed_fx_rate"] * math.exp(-r_d * T) * total / paths
        self.assertAlmostEqual(mc, self.price("CALL"), delta=0.02)

    def test_fixed_fx_rate_scales_the_price_linearly(self):
        """F_X is a pure multiplier on a domestic-currency payoff."""
        self.assertAlmostEqual(self.price(fixed_fx_rate=7.5), 7.5 * self.price(), places=10)

    def test_correlation_direction_moves_the_call_the_right_way(self):
        """
        rho > 0 lowers the drift (r_f - q - rho sigma_S sigma_X) and so cheapens
        the call; rho < 0 lifts it. A sign-flipped correlation convention shows
        up here first.
        """
        cheap, flat, rich = (
            self.price(correlation=r) for r in (0.9, 0.0, -0.9)
        )
        self.assertLess(cheap, flat)
        self.assertLess(flat, rich)

    def test_zero_fx_volatility_removes_the_quanto_adjustment(self):
        report = self.engine.price_quanto_option(InputData(**{**BASE, "fx_volatility": 0.0}))
        self.assertAlmostEqual(
            report.quanto_drift, BASE["foreign_rate"] - BASE["dividend_yield"], places=15
        )
        self.assertEqual(report.fx_correlation_sensitivity, 0.0)
        self.assertEqual(report.quanto_vega_drift_component, 0.0)

    def test_deterministic(self):
        a = self.engine.price_quanto_option(InputData(**BASE))
        b = QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine().price_quanto_option(
            InputData(**BASE)
        )
        self.assertEqual(a, b)


class QuantoDriftTest(unittest.TestCase):
    def setUp(self):
        self.engine = QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine()

    def test_drift_matches_hand_calculation(self):
        """r_f - q - rho sigma_S sigma_X = 0.02 - 0 - 0.30*0.20*0.15 = 0.011."""
        report = self.engine.price_quanto_option(InputData(**BASE))
        self.assertAlmostEqual(report.quanto_drift, 0.011, places=15)
        # d1 = (0 + (0.011 + 0.02)) / 0.20 = 0.155
        self.assertAlmostEqual(report.d1, 0.155, places=13)
        self.assertAlmostEqual(report.d2, 0.155 - 0.20, places=13)

    def test_dividend_yield_enters_the_drift_not_the_discounting(self):
        report = self.engine.price_quanto_option(InputData(**{**BASE, "dividend_yield": 0.03}))
        self.assertAlmostEqual(report.quanto_drift, 0.011 - 0.03, places=15)


class QuantoGreeksTest(unittest.TestCase):
    """
    Every Greek is validated against Richardson-extrapolated central differences
    of the price. The finite-difference path never uses the analytic formulas.
    """

    def setUp(self):
        self.engine = QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine()

    def price(self, option_type, **overrides) -> float:
        return self.engine.price_quanto_option(
            InputData(option_type=option_type, **{**BASE, **overrides})
        ).quanto_option_price_domestic

    def fd(self, option_type, field, x0, h):
        """Richardson-extrapolated first central difference: O(h^4) accurate."""
        def f(x):
            return self.price(option_type, **{field: x})

        coarse = (f(x0 + h) - f(x0 - h)) / (2 * h)
        fine = (f(x0 + h / 2) - f(x0 - h / 2)) / h
        return (4 * fine - coarse) / 3

    def test_delta(self):
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                report = self.engine.price_quanto_option(
                    InputData(option_type=option_type, **BASE)
                )
                self.assertAlmostEqual(
                    report.quanto_delta,
                    self.fd(option_type, "spot_price", BASE["spot_price"], 1e-3),
                    places=8,
                )

    def test_gamma(self):
        """Second difference of price; identical for call and put."""
        h = 0.05
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                S = BASE["spot_price"]
                second = (
                    self.price(option_type, spot_price=S + h)
                    - 2 * self.price(option_type, spot_price=S)
                    + self.price(option_type, spot_price=S - h)
                ) / (h * h)
                report = self.engine.price_quanto_option(
                    InputData(option_type=option_type, **BASE)
                )
                self.assertAlmostEqual(report.quanto_gamma, second, places=7)

    def test_vega_includes_the_drift_channel(self):
        """
        REGRESSION (v1.0.0 defect). sigma_S enters the quanto drift as well as
        d1/d2. v1.0.0 reported only the spot channel, 37.910160 for both sides;
        the true totals are 35.479670 (call) and 39.807548 (put).
        """
        expected = {"CALL": 35.47966984, "PUT": 39.80754803}
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                report = self.engine.price_quanto_option(
                    InputData(option_type=option_type, **BASE)
                )
                fd = self.fd(option_type, "asset_volatility", BASE["asset_volatility"], 1e-4)
                self.assertAlmostEqual(report.quanto_vega, fd, places=6)
                self.assertAlmostEqual(report.quanto_vega, expected[option_type], places=6)
                self.assertNotAlmostEqual(report.quanto_vega, 37.91016010, places=2)

    def test_vega_components_sum_to_the_total(self):
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                r = self.engine.price_quanto_option(InputData(option_type=option_type, **BASE))
                self.assertAlmostEqual(
                    r.quanto_vega_spot_component + r.quanto_vega_drift_component,
                    r.quanto_vega,
                    places=12,
                )
                # The spot channel alone is what plain Black-Scholes would give,
                # and it is the same for both sides. The total is not.
                self.assertAlmostEqual(r.quanto_vega_spot_component, 37.91016010, places=6)

    def test_call_and_put_vega_differ(self):
        """
        Plain Black-Scholes gives call and put the same vega. A quanto does not,
        because the drift channel enters with opposite signs. Equality here is
        the signature of the v1.0.0 bug.
        """
        call = self.engine.price_quanto_option(InputData(option_type="CALL", **BASE)).quanto_vega
        put = self.engine.price_quanto_option(InputData(option_type="PUT", **BASE)).quanto_vega
        self.assertGreater(abs(call - put), 1.0)

    def test_correlation_sensitivity_sign_and_magnitude(self):
        """
        REGRESSION (v1.0.0 defect). dV/drho is NEGATIVE for a call and POSITIVE
        for a put: more correlation lowers the drift, cheapening the call and
        enriching the put. v1.0.0 returned -1.264925 for the put -- correct
        magnitude, inverted sign -- so a mixed book double-counted correlation
        exposure instead of netting it.
        """
        expected = {"CALL": -1.62032684, "PUT": +1.26492529}
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                report = self.engine.price_quanto_option(
                    InputData(option_type=option_type, **BASE)
                )
                fd = self.fd(option_type, "correlation", BASE["correlation"], 1e-4)
                self.assertAlmostEqual(report.fx_correlation_sensitivity, fd, places=7)
                self.assertAlmostEqual(
                    report.fx_correlation_sensitivity, expected[option_type], places=6
                )
        self.assertLess(
            self.engine.price_quanto_option(
                InputData(option_type="CALL", **BASE)
            ).fx_correlation_sensitivity,
            0.0,
        )
        self.assertGreater(
            self.engine.price_quanto_option(
                InputData(option_type="PUT", **BASE)
            ).fx_correlation_sensitivity,
            0.0,
        )

    def test_correlation_sensitivity_holds_at_negative_correlation(self):
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                ov = {"correlation": -0.55}
                report = self.engine.price_quanto_option(
                    InputData(option_type=option_type, **{**BASE, **ov})
                )
                self.assertAlmostEqual(
                    report.fx_correlation_sensitivity,
                    self.fd(option_type, "correlation", -0.55, 1e-4),
                    places=7,
                )

    def test_index_scale_gamma_keeps_its_precision(self):
        """
        REGRESSION (v1.0.0 defect). The report used to round every field;
        quanto_gamma to 6 decimal places. On a Nikkei-scale underlying the true
        gamma is 4.9881789e-05, which that rounding flattened to 5e-05 -- one
        significant figure, a 0.24% error in the hedge ratio. Nothing is rounded
        now, so the reported value still agrees with a finite difference of the
        price to eight significant figures.
        """
        ov = {"spot_price": 38000.0, "strike_price": 38000.0}
        report = self.engine.price_quanto_option(InputData(**{**BASE, **ov}))
        h = 1.0
        second = (
            self.price("CALL", **{**ov, "spot_price": 38000.0 + h})
            - 2 * self.price("CALL", **ov)
            + self.price("CALL", **{**ov, "spot_price": 38000.0 - h})
        ) / (h * h)
        self.assertLess(abs(report.quanto_gamma / second - 1.0), 1e-7)
        self.assertAlmostEqual(report.quanto_gamma, 4.988178961e-05, places=14)
        # The value v1.0.0 would have returned, and the precision it destroyed.
        self.assertNotEqual(round(report.quanto_gamma, 6), report.quanto_gamma)


class QuantoInputValidationTest(unittest.TestCase):
    """
    Bad input must raise, not price. An engine that silently accepts a typo or a
    NaN hands the caller a number that looks like a price and is not one.
    """

    def setUp(self):
        self.engine = QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine()

    def assert_raises_on(self, **overrides):
        with self.assertRaises(ValueError):
            self.engine.price_quanto_option(InputData(**{**BASE, **overrides}))

    def test_non_positive_core_parameters(self):
        for field in (
            "spot_price",
            "strike_price",
            "time_to_expiry_years",
            "asset_volatility",
            "fixed_fx_rate",
        ):
            for bad in (0.0, -1.0):
                with self.subTest(field=field, value=bad):
                    self.assert_raises_on(**{field: bad})

    def test_negative_fx_volatility(self):
        """A negative sigma_X flips the sign of the drift adjustment silently."""
        self.assert_raises_on(fx_volatility=-0.15)

    def test_zero_fx_volatility_is_allowed(self):
        """A hard-pegged FX rate is a legitimate quanto input, not an error."""
        report = self.engine.price_quanto_option(InputData(**{**BASE, "fx_volatility": 0.0}))
        self.assertEqual(report.status, "QUANTO_PRICING_SUCCESSFUL")

    def test_correlation_outside_unit_interval(self):
        for bad in (1.0000001, -1.0000001, 5.0, -3.0):
            with self.subTest(value=bad):
                self.assert_raises_on(correlation=bad)

    def test_correlation_at_the_bounds_is_allowed(self):
        for edge in (-1.0, 1.0):
            with self.subTest(value=edge):
                report = self.engine.price_quanto_option(
                    InputData(**{**BASE, "correlation": edge})
                )
                self.assertTrue(math.isfinite(report.quanto_option_price_domestic))

    def test_non_finite_inputs(self):
        for field in (
            "spot_price",
            "strike_price",
            "time_to_expiry_years",
            "domestic_rate",
            "foreign_rate",
            "dividend_yield",
            "asset_volatility",
            "fx_volatility",
            "correlation",
            "fixed_fx_rate",
        ):
            for bad in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=bad):
                    self.assert_raises_on(**{field: bad})

    def test_unknown_option_type_raises_instead_of_defaulting_to_put(self):
        """
        REGRESSION (v1.0.0 defect). v1.0.0 branched `if == "CALL" else <put>`, so
        'CAL' priced as a put at 7.1044 and the report echoed 'CAL' back, leaving
        no trace of which side was actually priced.
        """
        for bad in ("CAL", "C", "P", "", "call option", "STRADDLE"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.engine.price_quanto_option(
                        InputData(option_type=bad, **BASE)
                    )

    def test_option_type_is_normalized(self):
        for good, expected in ((" call ", "CALL"), ("Put", "PUT"), ("cAlL", "CALL")):
            with self.subTest(value=good):
                report = self.engine.price_quanto_option(
                    InputData(option_type=good, **BASE)
                )
                self.assertEqual(report.option_type, expected)

    def test_non_string_option_type(self):
        with self.assertRaises(ValueError):
            self.engine.price_quanto_option(InputData(option_type=None, **BASE))


class QuantoReportTest(unittest.TestCase):
    def setUp(self):
        self.engine = QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine()

    def test_report_shape_and_status(self):
        report = self.engine.price_quanto_option(InputData(**BASE))
        self.assertIsInstance(report, QuantoOptionPricingReport)
        self.assertEqual(report.status, "QUANTO_PRICING_SUCCESSFUL")
        self.assertIn("QUANTO OPTION PRICING", report.audit_notes)
        self.assertIn("dV/drho", report.audit_notes)

    def test_quanto_forward_matches_the_drift(self):
        report = self.engine.price_quanto_option(InputData(**BASE))
        self.assertAlmostEqual(
            report.quanto_forward_foreign,
            BASE["spot_price"]
            * math.exp(report.quanto_drift * BASE["time_to_expiry_years"]),
            places=12,
        )


if __name__ == "__main__":
    unittest.main()
