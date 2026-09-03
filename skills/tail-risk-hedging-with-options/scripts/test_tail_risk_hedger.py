"""
Unit tests for tail-risk-hedging-with-options.

Expected values for the pricing tests are derived *independently* of the
implementation -- by put-call parity, by central finite differences of the price
function, and by closed-form bounds -- rather than by re-running the module's own
Greek formulas. A test that restates the implementation's formula would have
passed against the delta bug this suite now pins.

Regression coverage (each of these fails against the pre-fix implementation):
  * put delta is N(d1)-1, not N(-d1)-1  -- was 12.4x too large in magnitude
  * the annual carry budget is spread across the roll cycle, not spent per tranche
  * hedged notional is capped at portfolio notional
  * non-finite inputs raise instead of producing a NaN plan or crashing mid-sizing
"""
import math
import unittest

from tail_risk_hedger import (
    BINDING_BUDGET,
    BINDING_NOTIONAL_CAP,
    Config,
    Engine,
    HedgingResult,
    TailRiskHedger,
)

# Reference contract used throughout: 15% OTM, 3 months, 25% vol.
REF = dict(S=100.0, K=85.0, T=0.25, r=0.04, sigma=0.25)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _reference_call_price(S, K, T, r, sigma, q=0.0):
    """
    Black-Scholes *call* price, written out here so the put can be checked by
    put-call parity against a formula the module under test does not contain.
    """
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


class TestBlackScholesPut(unittest.TestCase):
    """Pricing and Greeks."""

    def setUp(self):
        self.hedger = TailRiskHedger()

    def _price(self, **overrides):
        args = {**REF, **overrides}
        return self.hedger.black_scholes_put(**args)["price"]

    def test_price_satisfies_put_call_parity(self):
        """C - P == S*e^-qT - K*e^-rT, checked against an independent call formula."""
        for q in (0.0, 0.013, 0.03):
            with self.subTest(q=q):
                put = self._price(dividend_yield=q)
                call = _reference_call_price(**REF, q=q)
                lhs = call - put
                rhs = REF["S"] * math.exp(-q * REF["T"]) - REF["K"] * math.exp(
                    -REF["r"] * REF["T"]
                )
                self.assertAlmostEqual(lhs, rhs, places=10)

    def test_price_matches_independently_computed_value(self):
        """Hard-coded reference value for the 15% OTM / 90d / 25% vol contract."""
        self.assertAlmostEqual(self._price(), 0.4385900, places=6)

    def test_delta_matches_finite_difference_not_the_old_formula(self):
        """
        Regression: put delta is N(d1)-1 = -0.0746, not N(-d1)-1 = -0.9254.

        The expected value comes from a central finite difference of the *price*,
        so it is independent of whatever delta expression the module uses.
        """
        h = 1e-5 * REF["S"]
        fd_delta = (self._price(S=REF["S"] + h) - self._price(S=REF["S"] - h)) / (2 * h)

        delta = self.hedger.black_scholes_put(**REF)["delta"]
        self.assertAlmostEqual(delta, fd_delta, places=7)
        self.assertAlmostEqual(delta, -0.074559, places=6)

        # The pre-fix expression. Pinned explicitly so the bug cannot creep back.
        d1 = self.hedger.black_scholes_put(**REF)["d1"]
        old_buggy_delta = _norm_cdf(-d1) - 1.0
        self.assertAlmostEqual(old_buggy_delta, -0.925441, places=6)
        self.assertNotAlmostEqual(delta, old_buggy_delta, places=3)

    def test_delta_is_bounded_by_minus_one_and_zero(self):
        """A put delta lives in [-1, 0] at every moneyness."""
        for K in (50.0, 85.0, 100.0, 130.0, 200.0):
            with self.subTest(K=K):
                delta = self.hedger.black_scholes_put(**{**REF, "K": K})["delta"]
                self.assertGreaterEqual(delta, -1.0)
                self.assertLessEqual(delta, 0.0)

    def test_deep_otm_put_delta_is_near_zero(self):
        """A 50% OTM put is nearly deltaless -- the property the old code inverted."""
        delta = self.hedger.black_scholes_put(**{**REF, "K": 50.0})["delta"]
        self.assertGreater(delta, -0.01)

    def test_gamma_and_vega_match_finite_differences(self):
        greeks = self.hedger.black_scholes_put(**REF)

        h = 1e-4 * REF["S"]
        fd_gamma = (
            self._price(S=REF["S"] + h) - 2 * self._price() + self._price(S=REF["S"] - h)
        ) / (h * h)
        self.assertAlmostEqual(greeks["gamma"], fd_gamma, places=6)

        hv = 1e-6
        # vega is reported per 1 volatility *point*, i.e. per 0.01 of sigma.
        fd_vega = (
            self._price(sigma=REF["sigma"] + hv) - self._price(sigma=REF["sigma"] - hv)
        ) / (2 * hv) / 100.0
        self.assertAlmostEqual(greeks["vega"], fd_vega, places=6)

    def test_theta_matches_finite_difference_and_is_negative(self):
        """Theta is reported per calendar day and a long put decays."""
        h = 1e-6
        fd_theta_per_year = -(
            self._price(T=REF["T"] + h) - self._price(T=REF["T"] - h)
        ) / (2 * h)
        theta = self.hedger.black_scholes_put(**REF)["theta"]
        self.assertAlmostEqual(theta, fd_theta_per_year / 365.0, places=8)
        self.assertLess(theta, 0.0)

    def test_price_is_bounded_by_discounted_strike(self):
        """0 < P < K*e^{-rT} for a European put on a non-negative asset."""
        price = self._price()
        self.assertGreater(price, 0.0)
        self.assertLess(price, REF["K"] * math.exp(-REF["r"] * REF["T"]))

    def test_dividend_yield_raises_the_put_price(self):
        """Carry lowers the forward, so a put on a dividend payer is worth more."""
        self.assertGreater(self._price(dividend_yield=0.013), self._price())
        self.assertGreater(
            self._price(dividend_yield=0.03), self._price(dividend_yield=0.013)
        )

    def test_skew_sensitivity_of_a_deep_otm_put(self):
        """
        Documents why `volatility` has no default: at 15% OTM / 90 DTE the premium
        is several times larger at a realistic skewed IV than at ATM-like vol.
        """
        args = dict(S=400.0, K=340.0, T=90 / 365.0, r=0.04)
        at_20 = self.hedger.black_scholes_put(**args, sigma=0.20)["price"]
        at_30 = self.hedger.black_scholes_put(**args, sigma=0.30)["price"]
        self.assertGreater(at_30 / at_20, 4.0)

    def test_invalid_inputs_raise_instead_of_returning_a_zero_price(self):
        bad = [
            {"S": 0.0}, {"S": -100.0}, {"K": 0.0}, {"T": 0.0}, {"T": -0.25},
            {"sigma": 0.0}, {"sigma": -0.25},
            {"S": float("nan")}, {"sigma": float("nan")}, {"sigma": float("inf")},
            {"r": float("nan")}, {"T": float("inf")},
        ]
        for override in bad:
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    self.hedger.black_scholes_put(**{**REF, **override})


class TestCarryBudget(unittest.TestCase):
    """The budget is annual. Spending it per tranche was the headline bug."""

    PV = 1_000_000.0
    SPOT = 400.0

    def _plan(self, **cfg_overrides):
        cfg = Config(**{"budget_pct": 0.02, "otm_pct": 0.15, "dte_target": 90,
                        "roll_dte": 30, **cfg_overrides})
        return TailRiskHedger(config=cfg).plan_systematic_otm_put_hedge(
            portfolio_value=self.PV, spot_price=self.SPOT, volatility=0.30,
            risk_free_rate=0.04, contract_multiplier=100,
        )

    def test_annualized_carry_never_exceeds_the_stated_budget(self):
        """
        Regression: with a 90/30 roll the program buys 6.08 tranches a year, so
        spending the full 2% on each tranche realises ~12% of annual drag.
        """
        res = self._plan()
        self.assertTrue(res.hedged)
        self.assertLessEqual(res.annualized_carry_pct, 0.02 + 1e-12)
        # And the single tranche costs far less than the whole annual budget.
        self.assertLess(res.cost, self.PV * 0.02)

    def test_tranche_budget_is_the_holding_period_share_of_the_annual_budget(self):
        res = self._plan()
        holding_days = 90 - 30
        expected = self.PV * 0.02 * holding_days / 365.0
        self.assertAlmostEqual(res.tranche_budget, expected, places=6)
        self.assertAlmostEqual(res.rolls_per_year, 365.0 / holding_days, places=9)

    def test_shorter_holding_period_buys_a_smaller_tranche(self):
        """More rolls per year must mean less premium per roll, not more total."""
        slow = self._plan(dte_target=90, roll_dte=30)   # 60-day hold
        fast = self._plan(dte_target=90, roll_dte=60)   # 30-day hold
        self.assertLess(fast.tranche_budget, slow.tranche_budget)
        self.assertLessEqual(fast.annualized_carry_pct, 0.02 + 1e-12)
        self.assertLessEqual(slow.annualized_carry_pct, 0.02 + 1e-12)

    def test_cost_is_a_whole_number_of_contracts(self):
        res = self._plan()
        self.assertAlmostEqual(res.cost, res.options_bought * res.option_price, places=6)
        self.assertIsInstance(res.options_bought, int)

    def test_zero_budget_buys_nothing(self):
        res = self._plan(budget_pct=0.0)
        self.assertFalse(res.hedged)
        self.assertEqual(res.options_bought, 0)
        self.assertEqual(res.cost, 0.0)


class TestNotionalCap(unittest.TestCase):
    """A hedge may not cover more shares than the portfolio owns."""

    def test_notional_cap_binds_when_puts_are_cheap(self):
        """
        Regression: budget-only sizing on cheap deep-OTM puts built a position of
        ~394% of portfolio notional -- a leveraged short, not a hedge.
        """
        cfg = Config(budget_pct=0.02, otm_pct=0.15, dte_target=90, roll_dte=30)
        res = TailRiskHedger(config=cfg).plan_systematic_otm_put_hedge(
            portfolio_value=1_000_000.0, spot_price=400.0,
            volatility=0.12,  # unrealistically low -> very cheap puts
            contract_multiplier=100,
        )
        self.assertEqual(res.binding_constraint, BINDING_NOTIONAL_CAP)
        self.assertLessEqual(res.notional_coverage_ratio, 1.0)
        self.assertLessEqual(res.hedged_notional, 1_000_000.0)

    def test_budget_binds_at_realistic_skewed_vol(self):
        cfg = Config(budget_pct=0.02, otm_pct=0.15, dte_target=90, roll_dte=30)
        res = TailRiskHedger(config=cfg).plan_systematic_otm_put_hedge(
            portfolio_value=1_000_000.0, spot_price=400.0, volatility=0.30,
            contract_multiplier=100,
        )
        self.assertEqual(res.binding_constraint, BINDING_BUDGET)
        self.assertLess(res.notional_coverage_ratio, 1.0)

    def test_zero_cap_reports_the_cap_not_the_budget(self):
        """
        A cap that permits nothing must say so. Reporting BUDGET here would send
        the operator to widen a budget that was never the constraint.
        """
        cfg = Config(budget_pct=0.02, max_hedge_notional_pct=0.0)
        res = TailRiskHedger(config=cfg).plan_systematic_otm_put_hedge(
            portfolio_value=1_000_000.0, spot_price=400.0, volatility=0.30,
            contract_multiplier=100,
        )
        self.assertFalse(res.hedged)
        self.assertEqual(res.options_bought, 0)
        self.assertEqual(res.binding_constraint, BINDING_NOTIONAL_CAP)

    def test_exhausted_budget_still_reports_budget(self):
        """The mirror case: a zero budget under a generous cap reports BUDGET."""
        cfg = Config(budget_pct=0.0, max_hedge_notional_pct=1.0)
        res = TailRiskHedger(config=cfg).plan_systematic_otm_put_hedge(
            portfolio_value=1_000_000.0, spot_price=400.0, volatility=0.30,
            contract_multiplier=100,
        )
        self.assertFalse(res.hedged)
        self.assertEqual(res.binding_constraint, BINDING_BUDGET)

    def test_raising_the_cap_permits_more_contracts(self):
        common = dict(portfolio_value=1_000_000.0, spot_price=400.0,
                      volatility=0.12, contract_multiplier=100)
        tight = TailRiskHedger(
            config=Config(max_hedge_notional_pct=0.5)
        ).plan_systematic_otm_put_hedge(**common)
        loose = TailRiskHedger(
            config=Config(max_hedge_notional_pct=1.0)
        ).plan_systematic_otm_put_hedge(**common)
        self.assertLess(tight.options_bought, loose.options_bought)


class TestStressPayoffs(unittest.TestCase):
    """Crash payoffs must be visible net of the premium that bought them."""

    def setUp(self):
        cfg = Config(budget_pct=0.02, otm_pct=0.15, dte_target=90, roll_dte=30)
        self.res = TailRiskHedger(config=cfg).plan_systematic_otm_put_hedge(
            portfolio_value=1_000_000.0, spot_price=400.0, volatility=0.30,
            contract_multiplier=100,
        )
        # Without a non-empty position the payoff assertions below would all hold
        # trivially on a table of zeros.
        self.assertGreater(self.res.options_bought, 0)
        self.assertGreater(self.res.cost, 0.0)

    def test_net_payout_is_gross_minus_total_premium(self):
        for key, sc in self.res.stress_scenarios.items():
            with self.subTest(key=key):
                self.assertAlmostEqual(
                    sc.net_payout, sc.gross_payout - self.res.cost, places=6
                )

    def test_shallow_drop_leaves_a_15pct_otm_put_worthless(self):
        """A -10% move never reaches a strike 15% below spot."""
        sc = self.res.stress_scenarios["drop_10pct"]
        self.assertEqual(sc.gross_payout, 0.0)
        self.assertLess(sc.net_payout, 0.0)          # premium is a pure loss here
        self.assertLess(sc.net_coverage_ratio, 0.0)

    def test_intrinsic_payoff_matches_hand_computation(self):
        """
        At spot 400 the strike is 340. A -30% shock lands spot at 280, so each
        share is worth 340 - 280 = 60 and each 100-share contract 6000.
        """
        sc = self.res.stress_scenarios["drop_30pct"]
        self.assertAlmostEqual(sc.terminal_spot, 280.0, places=9)
        self.assertAlmostEqual(
            sc.gross_payout, 60.0 * 100 * self.res.options_bought, places=6
        )

    def test_payoff_is_convex_across_deepening_shocks(self):
        """Each additional 10% of shock adds at least as much payout as the last."""
        payouts = [
            self.res.stress_scenarios[f"drop_{p}pct"].gross_payout
            for p in (10, 20, 30, 40)
        ]
        increments = [b - a for a, b in zip(payouts, payouts[1:])]
        self.assertTrue(all(b >= a - 1e-6 for a, b in zip(increments, increments[1:])))

    def test_legacy_gross_payout_fields_mirror_the_scenario_table(self):
        self.assertAlmostEqual(
            self.res.crash_payout_20pct_drop,
            self.res.stress_scenarios["drop_20pct"].gross_payout, places=6,
        )
        self.assertAlmostEqual(
            self.res.crash_payout_30pct_drop,
            self.res.stress_scenarios["drop_30pct"].gross_payout, places=6,
        )


class TestPlanInputValidation(unittest.TestCase):

    def setUp(self):
        self.hedger = TailRiskHedger(config=Config())

    def test_non_finite_inputs_raise_rather_than_crashing_mid_sizing(self):
        """
        Regression: a NaN volatility used to pass the `price <= 0` guard and then
        raise from inside int(budget // nan), after pricing reported success.
        """
        for override in (
            {"volatility": float("nan")},
            {"volatility": float("inf")},
            {"volatility": 0.0},
            {"spot_price": float("nan")},
            {"spot_price": 0.0},
            {"portfolio_value": float("nan")},
            {"contract_multiplier": 0},
        ):
            with self.subTest(override=override):
                kwargs = {"portfolio_value": 1_000_000.0, "spot_price": 400.0,
                          "volatility": 0.30, **override}
                with self.assertRaises(ValueError):
                    self.hedger.plan_systematic_otm_put_hedge(**kwargs)

    def test_non_positive_portfolio_value_returns_an_unhedged_plan(self):
        res = self.hedger.plan_systematic_otm_put_hedge(
            portfolio_value=0.0, spot_price=400.0, volatility=0.30
        )
        self.assertFalse(res.hedged)
        self.assertEqual(res.options_bought, 0)


class TestConfigValidation(unittest.TestCase):

    def test_roll_dte_must_be_shorter_than_dte_target(self):
        with self.assertRaises(ValueError):
            Config(dte_target=90, roll_dte=90)
        with self.assertRaises(ValueError):
            Config(dte_target=30, roll_dte=60)

    def test_otm_pct_must_describe_an_otm_put(self):
        for bad in (0.0, 1.0, -0.15, 1.5, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    Config(otm_pct=bad)

    def test_negative_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            Config(budget_pct=-0.01)

    def test_holding_period_derivations(self):
        cfg = Config(dte_target=90, roll_dte=30)
        self.assertEqual(cfg.holding_days, 60)
        self.assertAlmostEqual(cfg.rolls_per_year, 365.0 / 60.0, places=9)


class TestLegacySurface(unittest.TestCase):
    """Entry points retained for existing callers."""

    def test_hedge_success(self):
        res = TailRiskHedger(budget_pct=0.05).hedge(100000, 100)
        self.assertTrue(res.hedged)
        self.assertEqual(res.options_bought, 50)
        self.assertEqual(res.cost, 5000)
        self.assertAlmostEqual(res.carry_cost_pct, 0.05, places=9)

    def test_hedge_insufficient_budget(self):
        res = TailRiskHedger(budget_pct=0.01).hedge(1000, 50)
        self.assertFalse(res.hedged)
        self.assertEqual(res.options_bought, 0)
        self.assertEqual(res.cost, 0.0)

    def test_hedge_rejects_non_finite_inputs(self):
        hedger = TailRiskHedger(budget_pct=0.05)
        with self.assertRaises(ValueError):
            hedger.hedge(float("nan"), 100)
        with self.assertRaises(ValueError):
            hedger.hedge(100000, float("nan"))

    def test_hedge_returns_unhedged_for_non_positive_price(self):
        res = TailRiskHedger(budget_pct=0.05).hedge(100000, 0.0)
        self.assertFalse(res.hedged)

    def test_legacy_engine(self):
        eng = Engine(Config())
        self.assertTrue(eng.config.enabled)
        self.assertTrue(eng.run())
        self.assertIsInstance(eng.hedger, TailRiskHedger)

    def test_hedging_result_positional_construction(self):
        res = HedgingResult(False, 0, 0.0)
        self.assertFalse(res.hedged)
        self.assertEqual(res.greeks, {})
        self.assertEqual(res.stress_scenarios, {})


if __name__ == "__main__":
    unittest.main()
