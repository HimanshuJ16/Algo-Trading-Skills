"""
Tests for the variance swap / volatility derivative pricing engine.

Every asserted number is derived **independently** of the engine:

- ``_bs_call`` / ``_bs_put`` are textbook Black-Scholes, coded from
  ``C = S N(d1) - K e^{-rT} N(d2)``. Under Black-Scholes with a flat volatility
  ``sigma``, the fair variance strike of the log contract *is* ``sigma^2`` -- DDKZ
  Figure 5 states this explicitly ("The theoretical fair variance for dK = 0 is then
  (20)^2 = 400"). Feeding the engine a dense, wide strip of Black-Scholes prices and
  checking that it returns ``sigma^2`` therefore tests the replication formula
  against an external closed form, not against itself.
- The strike-truncation cases reproduce DDKZ Table 4, which publishes ``(25.0)^2``
  from a 50%-200% strike range versus ``(23.0)^2`` from a 75%-125% range at one year,
  and ``(24.9)^2`` at three months.
- The skewed-chain case reproduces the worked example of DDKZ Table 1 (page 21),
  which the paper prices at ``K_var = (20.467)^2``.
- Realized variance, the notional conversion, and the MTM blend are checked against
  values computed by hand in the test body from the closed-form definitions.

Regression coverage for the defects fixed in v2.0.0 is marked ``REGRESSION``; each
such test fails against the previous implementation.
"""
import math
import unittest

from variance_swap_and_volatility_derivative_pricing import (
    OptionType,
    SwapType,
    OptionQuote,
    VarianceSwapContract,
    VarianceSwapPricingEngine,
    VariancePricingError,
)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_d1(s: float, k: float, r: float, t: float, sig: float) -> float:
    return (math.log(s / k) + (r + 0.5 * sig * sig) * t) / (sig * math.sqrt(t))


def _bs_call(s: float, k: float, r: float, t: float, sig: float) -> float:
    d1 = _bs_d1(s, k, r, t, sig)
    d2 = d1 - sig * math.sqrt(t)
    return s * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)


def _bs_put(s: float, k: float, r: float, t: float, sig: float) -> float:
    d1 = _bs_d1(s, k, r, t, sig)
    d2 = d1 - sig * math.sqrt(t)
    return k * math.exp(-r * t) * _norm_cdf(-d2) - s * _norm_cdf(-d1)


def _bs_chain(spot, r, t, vol_fn, low, high, step, two_sided=False):
    """Black-Scholes option strip. ``two_sided`` emits both a put and a call per strike."""
    forward = spot * math.exp(r * t)
    quotes, k = [], float(low)
    while k <= high + 1e-9:
        vol = vol_fn(k)
        if two_sided:
            quotes.append(OptionQuote(k, OptionType.PUT, _bs_put(spot, k, r, t, vol), vol))
            quotes.append(OptionQuote(k, OptionType.CALL, _bs_call(spot, k, r, t, vol), vol))
        elif k < forward:
            quotes.append(OptionQuote(k, OptionType.PUT, _bs_put(spot, k, r, t, vol), vol))
        else:
            quotes.append(OptionQuote(k, OptionType.CALL, _bs_call(spot, k, r, t, vol), vol))
        k += step
    return quotes


FLAT_20 = lambda k: 0.20
FLAT_25 = lambda k: 0.25


class TestRealizedVariance(unittest.TestCase):
    def setUp(self):
        self.engine = VarianceSwapPricingEngine()

    def test_matches_hand_computed_zero_mean_variance(self):
        """Prices alternating +1%/-1% give log returns of exactly +/- ln(1.01)."""
        prices = [100.0, 101.0, 100.0, 101.0, 100.0]
        res = self.engine.calculate_realized_variance(prices)

        r_sq = math.log(1.01) ** 2                      # every return has the same square
        expected_var = (252.0 / 4.0) * (4.0 * r_sq) * 10_000.0
        self.assertEqual(res.num_observations, 4)
        self.assertAlmostEqual(res.annualized_realized_var, expected_var, places=9)
        self.assertAlmostEqual(res.annualized_realized_vol_pct, math.sqrt(expected_var), places=9)
        self.assertAlmostEqual(res.daily_returns_variance, r_sq, places=12)

    def test_zero_mean_convention_is_not_a_sample_variance(self):
        """
        A strongly trending series has near-zero *sample* variance of returns but a
        large zero-mean variance. DDKZ (page 2) require the zero-mean convention.
        """
        prices = [100.0 * (1.02 ** i) for i in range(6)]
        res = self.engine.calculate_realized_variance(prices)

        r = math.log(1.02)
        self.assertAlmostEqual(res.annualized_realized_var, 252.0 * r * r * 10_000.0, places=9)
        self.assertGreater(res.annualized_realized_vol_pct, 25.0)   # a sample variance gives 0

    def test_annualization_factor_is_honoured(self):
        """DDKZ use 260 business days; the term sheet, not the default, decides."""
        prices = [100.0, 101.0, 100.0]
        at_252 = self.engine.calculate_realized_variance(prices, annualization_factor=252)
        at_260 = self.engine.calculate_realized_variance(prices, annualization_factor=260)
        self.assertAlmostEqual(at_260.annualized_realized_var / at_252.annualized_realized_var,
                               260.0 / 252.0, places=12)

    def test_rejects_degenerate_inputs(self):
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_realized_variance([100.0])
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_realized_variance([100.0, -5.0])
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_realized_variance([100.0, 0.0])
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_realized_variance([100.0, 101.0], annualization_factor=0)

    def test_rejects_non_finite_price(self):
        """REGRESSION: NaN used to flow through log() into the variance silently."""
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_realized_variance([100.0, float("nan"), 101.0])
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_realized_variance([100.0, float("inf")])


class TestFairStrikeReplication(unittest.TestCase):
    def setUp(self):
        self.engine = VarianceSwapPricingEngine()

    def test_recovers_black_scholes_flat_volatility(self):
        """
        REGRESSION. Under flat Black-Scholes vol the fair variance strike is sigma^2
        exactly (DDKZ Figure 5: "The theoretical fair variance for dK = 0 is then
        (20)^2 = 400"). The previous implementation subtracted a spurious
        ``(1/T)(F/S - 1 - ln(F/S))`` term and returned 387.34 here.
        """
        strip = _bs_chain(100.0, 0.05, 1.0, FLAT_20, 10.0, 400.0, 1.0)
        res = self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip)

        self.assertAlmostEqual(res.fair_variance_strike, 400.0, delta=0.5)
        self.assertAlmostEqual(res.fair_volatility_strike, 20.0, delta=0.02)
        self.assertAlmostEqual(res.forward_price, 100.0 * math.exp(0.05), places=9)

    def test_recovers_flat_volatility_at_multiple_levels(self):
        """The replication must be level-independent, not tuned to one volatility."""
        for vol_pct in (10.0, 25.0, 45.0):
            with self.subTest(vol=vol_pct):
                sigma = vol_pct / 100.0
                strip = _bs_chain(100.0, 0.03, 0.5, lambda k, s=sigma: s, 5.0, 500.0, 1.0)
                res = self.engine.calculate_fair_strikes(100.0, 0.03, 0.5, strip)
                self.assertAlmostEqual(res.fair_volatility_strike, vol_pct, delta=0.05)

    def test_two_sided_chain_matches_otm_only_strip(self):
        """
        REGRESSION. A full chain quotes a put *and* a call at every strike. Building
        the dK grid over the raw list halved every interior spacing, so the previous
        implementation returned 187.31 for this chain against 387.34 for the
        equivalent OTM-only strip -- a 53% understatement from passing a normal
        option chain.
        """
        otm_only = _bs_chain(100.0, 0.05, 1.0, FLAT_20, 10.0, 400.0, 1.0)
        two_sided = _bs_chain(100.0, 0.05, 1.0, FLAT_20, 10.0, 400.0, 1.0, two_sided=True)

        k_otm = self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, otm_only).fair_variance_strike
        k_full = self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, two_sided).fair_variance_strike

        self.assertAlmostEqual(k_otm, k_full, delta=0.5)
        self.assertAlmostEqual(k_full, 400.0, delta=0.5)

    def test_itm_quotes_are_discarded_not_integrated(self):
        """Adding deep-ITM quotes to a valid strip must not move the fair strike."""
        strip = _bs_chain(100.0, 0.05, 1.0, FLAT_20, 10.0, 400.0, 1.0)
        clean = self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip).fair_variance_strike

        polluted = list(strip) + [
            OptionQuote(50.0, OptionType.CALL, _bs_call(100.0, 50.0, 0.05, 1.0, 0.20)),
            OptionQuote(300.0, OptionType.PUT, _bs_put(100.0, 300.0, 0.05, 1.0, 0.20)),
        ]
        self.assertAlmostEqual(
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, polluted).fair_variance_strike,
            clean, places=9,
        )

    def test_reference_strike_is_largest_strike_at_or_below_forward(self):
        """S* = K_0, the Cboe anchor. Forward here is 105.127, so K_0 = 105."""
        strip = _bs_chain(100.0, 0.05, 1.0, FLAT_20, 10.0, 400.0, 1.0)
        res = self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip)
        self.assertEqual(res.reference_strike, 105.0)
        self.assertLessEqual(res.reference_strike, res.forward_price)

    def test_reproduces_ddkz_table_1_worked_example(self):
        """
        DDKZ page 21: S_0 = 100, r = 5%, T = 0.25, strikes 50-150 spaced 5 apart,
        ATM implied vol 20% rising 1 vol point per 5-point drop in strike. The paper
        prices this at K_var = (20.467)^2 = 418.90.
        """
        skew = lambda k: 0.20 + (100.0 - k) / 5.0 * 0.01
        strip = _bs_chain(100.0, 0.05, 0.25, skew, 50.0, 150.0, 5.0, two_sided=True)
        res = self.engine.calculate_fair_strikes(100.0, 0.05, 0.25, strip)

        self.assertAlmostEqual(math.sqrt(res.fair_variance_strike), 20.467, delta=0.01)
        self.assertEqual(res.reference_strike, 100.0)

    def test_skew_lifts_the_variance_strike_above_atm_implied(self):
        """A downside skew makes K_var exceed the ATM implied variance of 400."""
        skew = lambda k: 0.20 + (100.0 - k) / 5.0 * 0.01
        strip = _bs_chain(100.0, 0.05, 0.25, skew, 50.0, 150.0, 5.0, two_sided=True)
        self.assertGreater(
            self.engine.calculate_fair_strikes(100.0, 0.05, 0.25, strip).fair_variance_strike,
            400.0,
        )

    def test_reproduces_ddkz_table_4_truncation_bias(self):
        """
        DDKZ Table 4, flat 25% vol: a 50%-200% strike range recovers (25.0)^2 at both
        tenors; a 75%-125% range yields (24.9)^2 at three months and (23.0)^2 at one
        year. Truncation is a one-way *downward* bias that grows with maturity.
        """
        cases = [(0.25, 24.9), (1.0, 23.0)]
        for t, narrow_vol in cases:
            with self.subTest(t=t):
                wide = _bs_chain(100.0, 0.05, t, FLAT_25, 50.0, 200.0, 1.0)
                narrow = _bs_chain(100.0, 0.05, t, FLAT_25, 75.0, 125.0, 1.0)
                k_wide = self.engine.calculate_fair_strikes(100.0, 0.05, t, wide).fair_volatility_strike
                k_narrow = self.engine.calculate_fair_strikes(100.0, 0.05, t, narrow).fair_volatility_strike

                self.assertAlmostEqual(k_wide, 25.0, delta=0.05)
                self.assertAlmostEqual(k_narrow, narrow_vol, delta=0.1)
                self.assertLess(k_narrow, k_wide)

    def test_convexity_adjustment_is_exact_under_normal_vol(self):
        """
        DDKZ Appendix D Equation D4 takes realized volatility as normal, so
        K_var = K_vol^2 + Var(sigma_R) exactly.

        REGRESSION: the previous implementation set K_vol = sqrt(K_var) and then
        reported ``K_var - K_vol^2``, which is identically 0.0 for every input --
        a documented feature that never computed anything.
        """
        strip = _bs_chain(100.0, 0.05, 1.0, FLAT_20, 10.0, 400.0, 1.0)
        base = self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip)
        adjusted = self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip, vol_of_vol_points=5.0)

        self.assertEqual(base.convexity_adjustment_pct, 0.0)
        self.assertAlmostEqual(adjusted.convexity_adjustment_pct, 25.0, places=9)
        self.assertAlmostEqual(
            adjusted.fair_variance_strike - adjusted.fair_volatility_strike ** 2, 25.0, places=9)
        # K_vol < sqrt(K_var): the variance swap must not dominate the vol swap.
        self.assertLess(adjusted.fair_volatility_strike, math.sqrt(adjusted.fair_variance_strike))
        self.assertAlmostEqual(
            adjusted.fair_volatility_strike, math.sqrt(base.fair_variance_strike - 25.0), places=9)

    def test_num_options_used_counts_only_replicating_quotes(self):
        """REGRESSION: this used to report the length of the raw strip."""
        strip = [
            OptionQuote(80.0, OptionType.PUT, 1.50),
            OptionQuote(90.0, OptionType.PUT, 3.20),
            OptionQuote(100.0, OptionType.PUT, 6.50),
            OptionQuote(100.0, OptionType.CALL, 8.20),   # both count at K_0 = 100
            OptionQuote(110.0, OptionType.CALL, 4.10),
            OptionQuote(120.0, OptionType.CALL, 1.80),
            OptionQuote(90.0, OptionType.CALL, 14.00),   # ITM, discarded
        ]
        res = self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip)
        self.assertEqual(res.num_options_used, 6)
        self.assertEqual(res.reference_strike, 100.0)
        self.assertEqual(res.min_strike, 80.0)
        self.assertEqual(res.max_strike, 120.0)

    def test_k0_quote_is_the_put_call_average(self):
        """Cboe: Q(K_0) is the average of the K_0 put and the K_0 call."""
        both = [
            OptionQuote(90.0, OptionType.PUT, 3.0),
            OptionQuote(100.0, OptionType.PUT, 6.0),
            OptionQuote(100.0, OptionType.CALL, 10.0),
            OptionQuote(110.0, OptionType.CALL, 4.0),
        ]
        averaged = [
            OptionQuote(90.0, OptionType.PUT, 3.0),
            OptionQuote(100.0, OptionType.PUT, 8.0),     # (6 + 10) / 2
            OptionQuote(110.0, OptionType.CALL, 4.0),
        ]
        self.assertAlmostEqual(
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, both).fair_variance_strike,
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, averaged).fair_variance_strike,
            places=9,
        )

    def test_rejects_one_sided_strip(self):
        """
        REGRESSION. A calls-only chain used to silently drop every strike below the
        forward and return a number (171.04 against a true 400) with no error.
        """
        calls_only = [q for q in _bs_chain(100.0, 0.05, 1.0, FLAT_20, 10.0, 400.0, 1.0, True)
                      if q.option_type == OptionType.CALL]
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, calls_only)

        puts_only = [q for q in _bs_chain(100.0, 0.05, 1.0, FLAT_20, 10.0, 400.0, 1.0, True)
                     if q.option_type == OptionType.PUT]
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, puts_only)

    def test_rejects_strip_entirely_above_the_forward(self):
        strip = [OptionQuote(200.0, OptionType.CALL, 1.0), OptionQuote(210.0, OptionType.CALL, 0.5)]
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip)

    def test_rejects_malformed_quotes(self):
        # A base strip that prices cleanly on its own, so each assertion below can
        # only be raised by the malformed quote appended to it.
        good = [OptionQuote(90.0, OptionType.PUT, 3.0),
                OptionQuote(100.0, OptionType.PUT, 6.0),
                OptionQuote(110.0, OptionType.CALL, 4.0)]
        self.assertGreater(
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, good).fair_variance_strike, 0.0)
        for bad in (
            OptionQuote(0.0, OptionType.PUT, 1.0),               # zero strike -> div by zero
            OptionQuote(-10.0, OptionType.PUT, 1.0),
            OptionQuote(95.0, OptionType.PUT, -1.0),             # negative premium
            OptionQuote(95.0, OptionType.PUT, float("nan")),
            OptionQuote(float("inf"), OptionType.CALL, 1.0),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(VariancePricingError):
                    self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, good + [bad])

    def test_rejects_duplicate_quote_for_same_strike_and_type(self):
        strip = [
            OptionQuote(90.0, OptionType.PUT, 3.0),
            OptionQuote(90.0, OptionType.PUT, 3.5),
            OptionQuote(100.0, OptionType.PUT, 6.0),
            OptionQuote(110.0, OptionType.CALL, 4.0),
        ]
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip)

    def test_rejects_non_positive_spot_maturity_and_bad_vol_of_vol(self):
        strip = _bs_chain(100.0, 0.05, 1.0, FLAT_20, 10.0, 400.0, 5.0)
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_fair_strikes(-10.0, 0.05, 1.0, strip)
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_fair_strikes(100.0, 0.05, 0.0, strip)
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip, vol_of_vol_points=-1.0)
        with self.assertRaises(VariancePricingError):
            # vol-of-vol of 40 points against K_var ~ 400 leaves no real K_vol.
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip, vol_of_vol_points=40.0)

    def test_rejects_arbitrageable_zero_priced_strip(self):
        """All-zero premiums imply non-positive replicated variance: bad data, not 0% vol."""
        strip = [
            OptionQuote(90.0, OptionType.PUT, 0.0),
            OptionQuote(100.0, OptionType.PUT, 0.0),
            OptionQuote(110.0, OptionType.CALL, 0.0),
        ]
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, strip)

    def test_warns_on_truncated_strike_range(self):
        narrow = _bs_chain(100.0, 0.05, 1.0, FLAT_20, 80.0, 120.0, 1.0)
        with self.assertLogs("variance_swap_and_volatility_derivative_pricing", level="WARNING") as cm:
            self.engine.calculate_fair_strikes(100.0, 0.05, 1.0, narrow)
        self.assertIn("narrower", "".join(cm.output))


class TestContractNotionals(unittest.TestCase):
    def _contract(self, **kw):
        base = dict(
            contract_id="VAR-001", symbol="SPX", swap_type=SwapType.VARIANCE_SWAP,
            strike_vol_pct=20.0, vega_notional_usd=100_000.0, t_years=1.0,
            spot_price=100.0, risk_free_rate=0.05,
        )
        base.update(kw)
        return VarianceSwapContract(**base)

    def test_variance_notional_conversion(self):
        """N_var = N_vega / (2 * K_vol) = 100,000 / 40 = 2,500 (DDKZ Equation 43)."""
        c = self._contract()
        self.assertEqual(c.variance_notional_usd, 2500.0)
        self.assertEqual(c.strike_var, 400.0)

    def test_variance_notional_is_40x_smaller_than_vega_notional_at_20_vol(self):
        """The sizing trap: quoting variance notional as if it were vega notional."""
        c = self._contract()
        self.assertAlmostEqual(c.vega_notional_usd / c.variance_notional_usd, 40.0, places=12)

    def test_non_positive_volatility_strike_rejected(self):
        with self.assertRaises(VariancePricingError):
            _ = self._contract(strike_vol_pct=0.0).variance_notional_usd


class TestSeasonedMTM(unittest.TestCase):
    def setUp(self):
        self.engine = VarianceSwapPricingEngine()
        self.contract = VarianceSwapContract(
            contract_id="VAR-001", symbol="SPX", swap_type=SwapType.VARIANCE_SWAP,
            strike_vol_pct=20.0,           # K_var = 400, N_var = 2,500
            vega_notional_usd=100_000.0,
            t_years=1.0, spot_price=100.0, risk_free_rate=0.05,
        )
        self.strip = _bs_chain(100.0, 0.05, 0.5, FLAT_20, 10.0, 400.0, 1.0)

    def test_mtm_matches_hand_computed_blend(self):
        """
        Half the term has accrued at a hand-computable realized variance; the forward
        half is priced from a flat-20% Black-Scholes strip, so K_var_remaining ~ 400.

            V_exp = 0.5 * realized + 0.5 * K_rem
            MTM   = e^{-0.05 * 0.5} * 2500 * (V_exp - 400)
        """
        prices = [100.0, 101.0, 100.0, 101.0, 100.0]
        realized = self.engine.calculate_realized_variance(prices).annualized_realized_var

        res = self.engine.price_variance_swap_mtm(
            contract=self.contract, elapsed_t_years=0.5, price_history=prices,
            remaining_option_strip=self.strip, current_spot=100.0, current_risk_free_rate=0.05,
        )

        expected_v = 0.5 * realized + 0.5 * res.fair_remaining_var_strike
        expected_mtm = math.exp(-0.05 * 0.5) * 2500.0 * (expected_v - 400.0)
        self.assertAlmostEqual(res.current_mtm_usd, expected_mtm, places=6)
        self.assertAlmostEqual(res.realized_var_so_far, realized, places=9)
        self.assertAlmostEqual(res.fair_remaining_var_strike, 400.0, delta=1.0)
        self.assertEqual(res.unrealized_pnl_usd, res.current_mtm_usd)

    def test_high_realized_volatility_gives_positive_mark_to_a_long(self):
        prices = [100.0, 105.0, 95.0, 110.0, 90.0, 105.0]     # ~40% realized
        res = self.engine.price_variance_swap_mtm(
            contract=self.contract, elapsed_t_years=0.5, price_history=prices,
            remaining_option_strip=self.strip, current_spot=105.0, current_risk_free_rate=0.05,
        )
        self.assertGreater(res.realized_vol_so_far_pct, 25.0)
        self.assertGreater(res.current_mtm_usd, 0.0)

    def test_at_the_money_contract_marks_near_zero(self):
        """20% realized to date + a 20% forward strip against a 20% strike ~ zero mark."""
        daily = 0.20 / math.sqrt(252.0)         # every squared log return is daily^2
        prices = [100.0]
        for i in range(40):
            prices.append(prices[-1] * math.exp(daily * (1 if i % 2 else -1)))
        res = self.engine.price_variance_swap_mtm(
            contract=self.contract, elapsed_t_years=0.5, price_history=prices,
            remaining_option_strip=self.strip, current_spot=100.0, current_risk_free_rate=0.05,
        )
        # Realized annualizes to exactly 252 * daily^2 * 10_000 = 400.
        self.assertAlmostEqual(res.realized_var_so_far, 400.0, places=6)
        self.assertAlmostEqual(res.current_mtm_usd, 0.0, delta=2500.0 * 1.0)

    def test_current_spot_moves_the_mark(self):
        """
        REGRESSION. The forward-variance leg used to be priced off the *inception*
        spot regardless of where the underlying had moved, which also mis-places the
        put/call boundary.
        """
        prices = [100.0, 101.0, 100.0, 101.0, 100.0]
        kwargs = dict(contract=self.contract, elapsed_t_years=0.5, price_history=prices,
                      remaining_option_strip=self.strip, current_risk_free_rate=0.05)
        at_inception = self.engine.price_variance_swap_mtm(current_spot=100.0, **kwargs)
        moved = self.engine.price_variance_swap_mtm(current_spot=140.0, **kwargs)
        self.assertNotAlmostEqual(at_inception.fair_remaining_var_strike,
                                  moved.fair_remaining_var_strike, places=3)

    def test_defaults_to_inception_spot_with_a_warning(self):
        prices = [100.0, 101.0, 100.0]
        with self.assertLogs("variance_swap_and_volatility_derivative_pricing", level="WARNING") as cm:
            self.engine.price_variance_swap_mtm(
                contract=self.contract, elapsed_t_years=0.5, price_history=prices,
                remaining_option_strip=self.strip,
            )
        joined = "".join(cm.output)
        self.assertIn("current_spot", joined)
        self.assertIn("current_risk_free_rate", joined)

    def test_unseasoned_contract_reports_zero_accrual(self):
        """REGRESSION: a 0%-elapsed contract used to report the strike as 'realized'."""
        res = self.engine.price_variance_swap_mtm(
            contract=self.contract, elapsed_t_years=0.0, price_history=[],
            remaining_option_strip=self.strip, current_spot=100.0, current_risk_free_rate=0.05,
        )
        self.assertEqual(res.realized_var_so_far, 0.0)
        self.assertEqual(res.realized_vol_so_far_pct, 0.0)

    def test_fully_accrued_contract_uses_realized_variance_only(self):
        prices = [100.0, 101.0, 100.0, 101.0, 100.0]
        realized = self.engine.calculate_realized_variance(prices).annualized_realized_var
        res = self.engine.price_variance_swap_mtm(
            contract=self.contract, elapsed_t_years=1.0, price_history=prices,
            remaining_option_strip=[], current_spot=100.0, current_risk_free_rate=0.05,
        )
        self.assertAlmostEqual(res.current_mtm_usd, 2500.0 * (realized - 400.0), places=6)

    def test_rejects_accrued_contract_without_price_history(self):
        """REGRESSION: this used to substitute the contract strike for realized variance."""
        with self.assertRaises(VariancePricingError):
            self.engine.price_variance_swap_mtm(
                contract=self.contract, elapsed_t_years=0.5, price_history=[100.0],
                remaining_option_strip=self.strip, current_spot=100.0,
            )

    def test_rejects_missing_strip_while_time_remains(self):
        """REGRESSION: this used to fall back to realized variance for the forward leg."""
        with self.assertRaises(VariancePricingError):
            self.engine.price_variance_swap_mtm(
                contract=self.contract, elapsed_t_years=0.5,
                price_history=[100.0, 101.0, 100.0],
                remaining_option_strip=[], current_spot=100.0,
            )

    def test_rejects_volatility_swap_contract(self):
        """
        REGRESSION. A VOLATILITY_SWAP was marked on the variance-linear formula, which
        overstates it by the convexity bias.
        """
        vol_swap = VarianceSwapContract(
            contract_id="VOL-001", symbol="SPX", swap_type=SwapType.VOLATILITY_SWAP,
            strike_vol_pct=20.0, vega_notional_usd=100_000.0, t_years=1.0,
            spot_price=100.0, risk_free_rate=0.05,
        )
        with self.assertRaises(VariancePricingError):
            self.engine.price_variance_swap_mtm(
                contract=vol_swap, elapsed_t_years=0.5, price_history=[100.0, 101.0, 100.0],
                remaining_option_strip=self.strip, current_spot=100.0,
            )

    def test_rejects_zero_length_contract(self):
        """A zero-maturity contract used to raise a raw ZeroDivisionError."""
        degenerate = VarianceSwapContract(
            contract_id="VAR-000", symbol="SPX", swap_type=SwapType.VARIANCE_SWAP,
            strike_vol_pct=20.0, vega_notional_usd=100_000.0, t_years=0.0,
            spot_price=100.0, risk_free_rate=0.05,
        )
        with self.assertRaises(VariancePricingError):
            self.engine.price_variance_swap_mtm(
                contract=degenerate, elapsed_t_years=0.0, price_history=[],
                remaining_option_strip=self.strip, current_spot=100.0,
                current_risk_free_rate=0.05,
            )

    def test_rejects_elapsed_time_outside_contract_life(self):
        prices = [100.0, 101.0, 100.0]
        for bad_t in (-0.1, 1.5, float("nan")):
            with self.subTest(t=bad_t):
                with self.assertRaises(VariancePricingError):
                    self.engine.price_variance_swap_mtm(
                        contract=self.contract, elapsed_t_years=bad_t, price_history=prices,
                        remaining_option_strip=self.strip, current_spot=100.0,
                    )


if __name__ == "__main__":
    unittest.main()
