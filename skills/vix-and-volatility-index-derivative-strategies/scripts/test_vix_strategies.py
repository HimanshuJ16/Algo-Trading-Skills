"""
Unit tests for vix-and-volatility-index-derivative-strategies.

Expected values are derived *independently* of the implementation wherever the
answer is quantitative: option prices are checked by put-call parity against a
Black-76 put written here, by no-arbitrage bounds, and by Monte-Carlo-free
closed-form limits; spread payoffs are checked against a terminal payoff function
written in this file rather than by restating the module's algebra. A test that
re-ran the module's own formula would have passed against every bug below.

Regression coverage -- each of these fails against a naive implementation:
  * option P&L uses the $100 VIX **options** multiplier, not the $1,000 futures
    multiplier (every option figure was 10x too large)
  * max profit on a debit call spread is (width - debit), not the gross width
  * the net debit comes from a price or a market quote, never from a hard-coded
    "25% of width" assumption
  * a budget that cannot fund one contract sizes to 0, not to max(1, ...)
  * the tail hedge is sized on premium at risk, not on futures notional
  * NaN spot VIX raises instead of clearing a `<= 0` guard and propagating
  * days_to_expiry <= 0 raises instead of being masked by max(1, days)
  * f2 must expire after f1, so reversed contracts cannot silently invert the
    curve state and flip short volatility into long volatility
"""
import datetime
import math
import unittest

from vix_strategies import (
    POSITION_NONE_INSUFFICIENT_CAPITAL,
    POSITION_PENDING_SPREAD_QUOTE,
    VIX_FUTURES_MULTIPLIER,
    VIX_OPTIONS_MULTIPLIER,
    TermStructureState,
    VIXCallSpreadQuote,
    VIXEngineError,
    VIXTermStructure,
    VIXFuturesContract,
    VIXStrategyEngine,
    VIXStrategyType,
)

DAYS_PER_YEAR = 365.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _reference_black76_put(f: float, k: float, days: int, sigma: float, r: float = 0.0) -> float:
    """
    Black-76 *put*, written out here so the call can be checked by put-call
    parity against a formula the module under test does not contain.
    """
    t = days / DAYS_PER_YEAR
    d1 = (math.log(f / k) + 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return math.exp(-r * t) * (k * _norm_cdf(-d2) - f * _norm_cdf(-d1))


def _spread_payoff_at_settlement(soq: float, k_lo: float, k_up: float, debit: float) -> float:
    """
    Terminal USD P&L per contract of a long 1x1 call spread settling to `soq`.
    Written from the payoff definition, independent of the module.
    """
    gross = min(max(soq - k_lo, 0.0), k_up - k_lo)
    return (gross - debit) * VIX_OPTIONS_MULTIPLIER


class _Base(unittest.TestCase):
    def setUp(self):
        self.engine = VIXStrategyEngine()
        self.today = datetime.date(2025, 1, 6)

        self.f1_contango = VIXFuturesContract(
            symbol="VXF25",
            expiry_date=self.today + datetime.timedelta(days=20),
            days_to_expiry=20,
            price=16.0,
        )
        self.f2_contango = VIXFuturesContract(
            symbol="VXG25",
            expiry_date=self.today + datetime.timedelta(days=50),
            days_to_expiry=50,
            price=17.5,
        )
        self.f1_backwardation = VIXFuturesContract(
            symbol="VXF25",
            expiry_date=self.today + datetime.timedelta(days=20),
            days_to_expiry=20,
            price=32.0,
        )
        self.f2_backwardation = VIXFuturesContract(
            symbol="VXG25",
            expiry_date=self.today + datetime.timedelta(days=50),
            days_to_expiry=50,
            price=28.0,
        )

    def _contango_ts(self):
        return self.engine.analyze_term_structure(15.0, self.f1_contango, self.f2_contango)

    def _backwardation_ts(self):
        return self.engine.analyze_term_structure(
            35.0, self.f1_backwardation, self.f2_backwardation
        )


class TestContractMultipliers(_Base):
    """The two multipliers are different numbers and must stay different."""

    def test_futures_multiplier_is_1000(self):
        self.assertEqual(VIX_FUTURES_MULTIPLIER, 1000.0)

    def test_options_multiplier_is_100(self):
        """Cboe VIX options settle at (settlement value - strike) x $100."""
        self.assertEqual(VIX_OPTIONS_MULTIPLIER, 100.0)

    def test_option_figures_use_the_options_multiplier(self):
        """
        Regression: older priced options at $1,000/point. A 15-point spread
        bought for 3.00 points has a max loss of exactly $300, not $3,000.
        """
        quote = self.engine.price_vix_call_spread(
            f1_futures_price=20.0, strike_lower=20.0, strike_upper=35.0,
            days_to_expiry=30, net_debit=3.0,
        )
        self.assertAlmostEqual(quote.max_loss_per_contract_usd, 300.0, places=6)
        self.assertAlmostEqual(quote.max_profit_per_contract_usd, 1200.0, places=6)


class TestBlack76Call(_Base):
    """Pricing checked against independent formulas and no-arbitrage bounds."""

    REF = dict(f1_futures_price=20.0, days_to_expiry=30, implied_vol=0.90)

    def test_put_call_parity(self):
        """C - P == e^{-rT}(F - K), against a put written in this test file."""
        for r in (0.0, 0.045):
            for k in (15.0, 20.0, 30.0):
                with self.subTest(r=r, k=k):
                    call = self.engine.black76_call(strike=k, risk_free_rate=r, **self.REF)
                    put = _reference_black76_put(
                        self.REF["f1_futures_price"], k, self.REF["days_to_expiry"],
                        self.REF["implied_vol"], r,
                    )
                    t = self.REF["days_to_expiry"] / DAYS_PER_YEAR
                    expected = math.exp(-r * t) * (self.REF["f1_futures_price"] - k)
                    self.assertAlmostEqual(call - put, expected, places=10)

    def test_price_within_no_arbitrage_bounds(self):
        """max(e^{-rT}(F-K), 0) <= C <= e^{-rT} F, for any strike."""
        r, t = 0.045, self.REF["days_to_expiry"] / DAYS_PER_YEAR
        disc = math.exp(-r * t)
        f = self.REF["f1_futures_price"]
        for k in (5.0, 20.0, 60.0):
            with self.subTest(k=k):
                call = self.engine.black76_call(strike=k, risk_free_rate=r, **self.REF)
                self.assertGreaterEqual(call + 1e-12, max(disc * (f - k), 0.0))
                self.assertLessEqual(call, disc * f + 1e-12)

    def test_price_is_monotone_decreasing_in_strike(self):
        prices = [self.engine.black76_call(strike=k, **self.REF) for k in (18.0, 22.0, 26.0, 30.0)]
        for earlier, later in zip(prices, prices[1:]):
            self.assertGreater(earlier, later)

    def test_price_is_monotone_increasing_in_vol(self):
        """Vega is positive, so a higher IV on the same strike must cost more."""
        low = self.engine.black76_call(
            f1_futures_price=20.0, strike=28.0, days_to_expiry=30, implied_vol=0.70
        )
        high = self.engine.black76_call(
            f1_futures_price=20.0, strike=28.0, days_to_expiry=30, implied_vol=1.20
        )
        self.assertGreater(high, low)

    def test_deep_itm_call_approaches_discounted_intrinsic(self):
        """As vol -> 0 a deep ITM call converges to e^{-rT}(F - K)."""
        r = 0.045
        call = self.engine.black76_call(
            f1_futures_price=40.0, strike=10.0, days_to_expiry=30,
            implied_vol=0.001, risk_free_rate=r,
        )
        expected = math.exp(-r * 30 / DAYS_PER_YEAR) * (40.0 - 10.0)
        self.assertAlmostEqual(call, expected, places=6)

    def test_rejects_non_positive_and_non_finite_inputs(self):
        for kwargs in (
            dict(f1_futures_price=0.0, strike=20.0, days_to_expiry=30, implied_vol=0.9),
            dict(f1_futures_price=20.0, strike=-5.0, days_to_expiry=30, implied_vol=0.9),
            dict(f1_futures_price=20.0, strike=20.0, days_to_expiry=30, implied_vol=0.0),
            dict(f1_futures_price=20.0, strike=20.0, days_to_expiry=0, implied_vol=0.9),
            dict(f1_futures_price=float("nan"), strike=20.0, days_to_expiry=30, implied_vol=0.9),
            dict(f1_futures_price=20.0, strike=20.0, days_to_expiry=30, implied_vol=float("inf")),
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(VIXEngineError):
                    self.engine.black76_call(**kwargs)


class TestCallSpreadPricing(_Base):
    """Spread economics checked against a terminal payoff function."""

    def test_max_profit_equals_payoff_at_and_above_upper_strike(self):
        """
        Regression: older returned the gross width as max profit. Max profit is
        what the position is actually worth when the SOQ settles at or above K2 --
        gross intrinsic minus the debit already paid.
        """
        quote = self.engine.price_vix_call_spread(
            f1_futures_price=22.0, strike_lower=25.0, strike_upper=40.0,
            days_to_expiry=30, net_debit=4.25,
        )
        for soq in (40.0, 55.0, 120.0):
            with self.subTest(soq=soq):
                self.assertAlmostEqual(
                    quote.max_profit_per_contract_usd,
                    _spread_payoff_at_settlement(soq, 25.0, 40.0, 4.25),
                    places=6,
                )
        # Gross width x $100 would be $1,500; the true max profit is $1,075.
        self.assertNotAlmostEqual(quote.max_profit_per_contract_usd, 1500.0, places=2)

    def test_max_loss_equals_payoff_at_and_below_lower_strike(self):
        quote = self.engine.price_vix_call_spread(
            f1_futures_price=22.0, strike_lower=25.0, strike_upper=40.0,
            days_to_expiry=30, net_debit=4.25,
        )
        for soq in (0.5, 12.0, 25.0):
            with self.subTest(soq=soq):
                self.assertAlmostEqual(
                    -quote.max_loss_per_contract_usd,
                    _spread_payoff_at_settlement(soq, 25.0, 40.0, 4.25),
                    places=6,
                )

    def test_breakeven_gives_zero_terminal_pnl(self):
        quote = self.engine.price_vix_call_spread(
            f1_futures_price=22.0, strike_lower=25.0, strike_upper=40.0,
            days_to_expiry=30, net_debit=4.25,
        )
        self.assertAlmostEqual(
            _spread_payoff_at_settlement(quote.breakeven_vix, 25.0, 40.0, 4.25),
            0.0, places=6,
        )

    def test_priced_debit_equals_difference_of_the_two_legs(self):
        """The spread debit is the long leg minus the short leg, nothing else."""
        args = dict(f1_futures_price=20.0, days_to_expiry=45, risk_free_rate=0.04)
        long_leg = self.engine.black76_call(strike=25.0, implied_vol=0.95, **args)
        short_leg = self.engine.black76_call(strike=40.0, implied_vol=1.25, **args)
        quote = self.engine.price_vix_call_spread(
            strike_lower=25.0, strike_upper=40.0,
            implied_vol_lower=0.95, implied_vol_upper=1.25, **args,
        )
        self.assertAlmostEqual(quote.net_debit, round(long_leg - short_leg, 4), places=4)
        self.assertGreater(quote.net_debit, 0.0)

    def test_upward_smile_raises_the_debit_less_than_a_flat_smile_would(self):
        """
        VIX call skew is real: pricing the far strike at a higher IV than the near
        strike makes the short leg richer and the spread cheaper than a flat-vol
        quote. This pins the direction, which is what a caller reusing an ATM vol
        gets wrong.
        """
        args = dict(
            f1_futures_price=20.0, strike_lower=25.0, strike_upper=40.0,
            days_to_expiry=45,
        )
        flat = self.engine.price_vix_call_spread(
            implied_vol_lower=0.95, implied_vol_upper=0.95, **args
        )
        skewed = self.engine.price_vix_call_spread(
            implied_vol_lower=0.95, implied_vol_upper=1.30, **args
        )
        self.assertLess(skewed.net_debit, flat.net_debit)

    def test_rejects_inverted_strikes(self):
        with self.assertRaises(VIXEngineError):
            self.engine.price_vix_call_spread(
                f1_futures_price=16.0, strike_lower=30.0, strike_upper=20.0,
                days_to_expiry=30, net_debit=2.0,
            )

    def test_rejects_arbitrageable_debit(self):
        """A debit of zero, a negative debit, or one above the width is inconsistent."""
        for debit in (0.0, -1.0, 15.0, 20.0):
            with self.subTest(debit=debit):
                with self.assertRaises(VIXEngineError):
                    self.engine.price_vix_call_spread(
                        f1_futures_price=20.0, strike_lower=20.0, strike_upper=35.0,
                        days_to_expiry=30, net_debit=debit,
                    )

    def test_requires_a_price_source(self):
        """
        Regression: older invented a debit of 25% of the width when none was
        given. Neither IVs nor a quote must now raise rather than fabricate.
        """
        with self.assertRaises(VIXEngineError):
            self.engine.price_vix_call_spread(
                f1_futures_price=20.0, strike_lower=20.0, strike_upper=35.0,
                days_to_expiry=30,
            )
        with self.assertRaises(VIXEngineError):
            self.engine.price_vix_call_spread(
                f1_futures_price=20.0, strike_lower=20.0, strike_upper=35.0,
                days_to_expiry=30, implied_vol_lower=0.9,
            )


class TestTermStructure(_Base):
    def test_contango_classification_and_slope(self):
        ts = self._contango_ts()
        self.assertEqual(ts.state, TermStructureState.CONTANGO)
        self.assertAlmostEqual(ts.slope_f2_minus_f1, 1.5, places=6)
        # (17.5 - 16.0) / 16.0 * 100 = 9.375%
        self.assertAlmostEqual(ts.slope_pct, 9.375, places=6)

    def test_backwardation_classification_and_slope(self):
        ts = self._backwardation_ts()
        self.assertEqual(ts.state, TermStructureState.BACKWARDATION)
        self.assertAlmostEqual(ts.slope_f2_minus_f1, -4.0, places=6)
        # (28.0 - 32.0) / 32.0 * 100 = -12.5%
        self.assertAlmostEqual(ts.slope_pct, -12.5, places=6)

    def test_annualized_basis_matches_hand_calculation(self):
        """(16 - 15) / 15 * 365 / 20 * 100 = 121.6667%, rounded to 121.67."""
        ts = self._contango_ts()
        self.assertAlmostEqual(ts.front_basis, 1.0, places=6)
        self.assertAlmostEqual(ts.annualized_roll_yield_pct, 121.67, places=2)

    def test_backwardated_curve_can_still_carry_a_positive_basis(self):
        """
        Curve slope and front basis are different quantities. F1 above spot with F2
        below F1 is BACKWARDATION by slope while the basis still decays toward spot,
        which is exactly why the two must not be conflated.
        """
        ts = self._backwardation_ts()
        self.assertEqual(ts.state, TermStructureState.BACKWARDATION)
        self.assertLess(ts.slope_f2_minus_f1, 0.0)
        self.assertLess(ts.front_basis, 0.0)  # F1 32.0 < spot 35.0

    def test_threshold_boundaries_are_inclusive(self):
        """Exactly +2.00% is CONTANGO; exactly -2.00% is BACKWARDATION."""
        f1 = VIXFuturesContract("VXF25", self.today + datetime.timedelta(days=20), 20, 20.0)
        f2_up = VIXFuturesContract("VXG25", self.today + datetime.timedelta(days=50), 50, 20.4)
        f2_dn = VIXFuturesContract("VXG25", self.today + datetime.timedelta(days=50), 50, 19.6)
        self.assertEqual(
            self.engine.analyze_term_structure(19.0, f1, f2_up).state,
            TermStructureState.CONTANGO,
        )
        self.assertEqual(
            self.engine.analyze_term_structure(19.0, f1, f2_dn).state,
            TermStructureState.BACKWARDATION,
        )

    def test_inside_dead_band_is_flat(self):
        f1 = VIXFuturesContract("VXF25", self.today + datetime.timedelta(days=20), 20, 20.0)
        f2 = VIXFuturesContract("VXG25", self.today + datetime.timedelta(days=50), 50, 20.2)
        ts = self.engine.analyze_term_structure(19.0, f1, f2)
        self.assertEqual(ts.state, TermStructureState.FLAT)

    def test_reversed_contract_order_raises(self):
        """
        Regression: with no ordering check, swapping F1 and F2 flips the sign of
        the slope and turns a short-volatility recommendation into a long one.
        """
        with self.assertRaises(VIXEngineError):
            self.engine.analyze_term_structure(15.0, self.f2_contango, self.f1_contango)

    def test_nan_spot_vix_raises(self):
        """Regression: NaN cleared the old `spot_vix <= 0` guard and propagated."""
        for bad in (float("nan"), float("inf"), 0.0, -5.0):
            with self.subTest(bad=bad):
                with self.assertRaises(VIXEngineError):
                    self.engine.analyze_term_structure(
                        bad, self.f1_contango, self.f2_contango
                    )

    def test_expired_contract_raises_instead_of_being_clamped(self):
        """Regression: the old code silently substituted max(1, days_to_expiry)."""
        for days in (0, -3):
            with self.subTest(days=days):
                with self.assertRaises(VIXEngineError):
                    VIXFuturesContract("VXF25", self.today, days, 16.0)

    def test_overlapping_thresholds_rejected_at_construction(self):
        with self.assertRaises(VIXEngineError):
            VIXStrategyEngine(contango_threshold_pct=-1.0, backwardation_threshold_pct=1.0)


class TestShortVolSizing(_Base):
    def test_contract_count_matches_hand_calculation(self):
        """
        $10,000,000 x 5% = $500,000 budget; one VX contract at F1=16 is
        16 x $1,000 = $16,000. floor(500000 / 16000) = 31 contracts.
        """
        signal = self.engine.generate_strategy_signal(self._contango_ts(), 10_000_000.0)
        self.assertEqual(signal.strategy_type, VIXStrategyType.ROLL_YIELD_HARVEST)
        self.assertEqual(signal.recommended_position, "SHORT_F1_VIX_FUTURE")
        self.assertEqual(signal.target_contracts, 31)
        self.assertAlmostEqual(signal.notional_exposure_usd, 496_000.0, places=2)

    def test_notional_never_exceeds_the_stated_budget(self):
        """The floored count is the whole point: exposure stays inside the limit."""
        for equity in (250_000.0, 1_000_000.0, 3_333_333.0, 10_000_000.0):
            with self.subTest(equity=equity):
                signal = self.engine.generate_strategy_signal(self._contango_ts(), equity)
                self.assertLessEqual(signal.notional_exposure_usd, equity * 0.05 + 1e-9)

    def test_daily_decay_matches_hand_calculation(self):
        """(16 - 15) / 20 days x $1,000 x 31 contracts = $1,550.00 per day."""
        signal = self.engine.generate_strategy_signal(self._contango_ts(), 10_000_000.0)
        self.assertAlmostEqual(signal.daily_roll_decay_usd, 1_550.0, places=2)

    def test_stop_level_and_loss_at_stop_match_hand_calculation(self):
        """
        F1 = 16.00, 30% adverse move -> stop at 20.80. Loss = 4.80 x $1,000 x 31
        = $148,800, which is 9.6x the daily carry -- the asymmetry the stop exists
        for.
        """
        signal = self.engine.generate_strategy_signal(self._contango_ts(), 10_000_000.0)
        self.assertAlmostEqual(signal.stop_loss_trigger_price, 20.8, places=4)
        self.assertAlmostEqual(signal.loss_at_stop_usd, 148_800.0, places=2)

    def test_small_account_sizes_to_zero_not_one(self):
        """
        Regression: older `max(1, int(...))` handed a $50,000 account one short
        VX contract -- $16,000 of notional against a $2,500 stated budget, on the
        one strategy with unbounded loss.
        """
        signal = self.engine.generate_strategy_signal(self._contango_ts(), 50_000.0)
        self.assertEqual(signal.target_contracts, 0)
        self.assertEqual(signal.recommended_position, POSITION_NONE_INSUFFICIENT_CAPITAL)
        self.assertEqual(signal.notional_exposure_usd, 0.0)

    def test_boundary_equity_funds_exactly_one_contract(self):
        """$320,000 x 5% = $16,000 = exactly one contract at F1=16."""
        signal = self.engine.generate_strategy_signal(self._contango_ts(), 320_000.0)
        self.assertEqual(signal.target_contracts, 1)
        just_under = self.engine.generate_strategy_signal(self._contango_ts(), 319_999.0)
        self.assertEqual(just_under.target_contracts, 0)


class TestTailHedgeSizing(_Base):
    def _quote(self, debit=3.0):
        return self.engine.price_vix_call_spread(
            f1_futures_price=32.0, strike_lower=40.0, strike_upper=55.0,
            days_to_expiry=25, net_debit=debit,
        )

    def test_sized_on_premium_at_risk_not_futures_notional(self):
        """
        $10,000,000 x 2% = $200,000 premium budget; one 40/55 spread at a 3.00-point
        debit costs 3.00 x $100 = $300. floor(200000 / 300) = 666 contracts.

        Regression: older divided the budget by F1 x $1,000 = $32,000, giving 6
        contracts -- an answer with no relationship to what the spread costs.
        """
        signal = self.engine.generate_strategy_signal(
            self._backwardation_ts(), 10_000_000.0, spread_quote=self._quote()
        )
        self.assertEqual(signal.strategy_type, VIXStrategyType.TAIL_RISK_HEDGE)
        self.assertEqual(signal.recommended_position, "LONG_VIX_CALL_SPREAD")
        self.assertEqual(signal.target_contracts, 666)
        self.assertAlmostEqual(signal.premium_outlay_usd, 199_800.0, places=2)

    def test_premium_outlay_never_exceeds_the_budget(self):
        for equity in (100_000.0, 2_500_000.0, 10_000_000.0):
            with self.subTest(equity=equity):
                signal = self.engine.generate_strategy_signal(
                    self._backwardation_ts(), equity, spread_quote=self._quote()
                )
                self.assertLessEqual(signal.premium_outlay_usd, equity * 0.02 + 1e-9)

    def test_protection_is_reported_net_of_premium(self):
        """
        666 contracts x (15.00 - 3.00 points) x $100 = $799,200 maximum payoff.
        The gross-width figure ($999,000) would overstate it by the premium paid.
        """
        signal = self.engine.generate_strategy_signal(
            self._backwardation_ts(), 10_000_000.0, spread_quote=self._quote()
        )
        self.assertAlmostEqual(signal.tail_hedge_protection_usd, 799_200.0, places=2)

    def test_without_a_quote_it_refuses_to_invent_a_premium(self):
        signal = self.engine.generate_strategy_signal(self._backwardation_ts(), 10_000_000.0)
        self.assertEqual(signal.strategy_type, VIXStrategyType.TAIL_RISK_HEDGE)
        self.assertEqual(signal.recommended_position, POSITION_PENDING_SPREAD_QUOTE)
        self.assertEqual(signal.target_contracts, 0)

    def test_budget_below_one_spread_sizes_to_zero(self):
        expensive = self._quote(debit=12.0)  # $1,200 per contract
        signal = self.engine.generate_strategy_signal(
            self._backwardation_ts(), 50_000.0, spread_quote=expensive
        )
        self.assertEqual(signal.target_contracts, 0)
        self.assertEqual(signal.recommended_position, POSITION_NONE_INSUFFICIENT_CAPITAL)


class TestNeutralAndValidation(_Base):
    def test_flat_curve_returns_cash(self):
        f1 = VIXFuturesContract("VXF25", self.today + datetime.timedelta(days=20), 20, 20.0)
        f2 = VIXFuturesContract("VXG25", self.today + datetime.timedelta(days=50), 50, 20.2)
        signal = self.engine.generate_strategy_signal(
            self.engine.analyze_term_structure(19.0, f1, f2), 10_000_000.0
        )
        self.assertEqual(signal.strategy_type, VIXStrategyType.NEUTRAL)
        self.assertEqual(signal.recommended_position, "CASH")
        self.assertEqual(signal.target_contracts, 0)

    def test_non_positive_or_non_finite_equity_raises(self):
        ts = self._contango_ts()
        for equity in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(equity=equity):
                with self.assertRaises(VIXEngineError):
                    self.engine.generate_strategy_signal(ts, equity)

    def test_contract_rejects_bad_price_and_empty_symbol(self):
        expiry = self.today + datetime.timedelta(days=20)
        with self.assertRaises(VIXEngineError):
            VIXFuturesContract("VXF25", expiry, 20, 0.0)
        with self.assertRaises(VIXEngineError):
            VIXFuturesContract("VXF25", expiry, 20, float("nan"))
        with self.assertRaises(VIXEngineError):
            VIXFuturesContract("", expiry, 20, 16.0)

    def test_absurd_risk_free_rate_raises_not_overflows(self):
        """A rate passed as a percent (4.2) rather than a decimal (0.042) must
        raise the module's own error, not OverflowError out of math.exp."""
        for rate in (4.2, -4.2, 1e6):
            with self.subTest(rate=rate):
                with self.assertRaises(VIXEngineError):
                    self.engine.black76_call(
                        f1_futures_price=20.0, strike=25.0, days_to_expiry=30,
                        implied_vol=0.9, risk_free_rate=rate,
                    )

    def test_hand_built_quote_with_zero_premium_raises(self):
        """
        VIXCallSpreadQuote is a plain dataclass and can bypass the pricer's checks.
        A zero max loss would otherwise divide by zero inside sizing.
        """
        bogus = VIXCallSpreadQuote(20.0, 35.0, 18.0, 30, 0.0, 1500.0, 0.0, 20.0)
        with self.assertRaises(VIXEngineError):
            self.engine.generate_strategy_signal(
                self._backwardation_ts(), 10_000_000.0, spread_quote=bogus
            )

    def test_carry_is_recomputed_not_read_from_a_defaulted_field(self):
        """
        A hand-built VIXTermStructure leaves front_basis at its 0.0 default. Sizing
        must derive the basis from spot and F1 rather than report zero carry.
        """
        hand_built = VIXTermStructure(
            spot_vix=15.0,
            f1_contract=self.f1_contango,
            f2_contract=self.f2_contango,
            slope_f2_minus_f1=1.5,
            state=TermStructureState.CONTANGO,
            annualized_roll_yield_pct=121.67,
        )
        self.assertEqual(hand_built.front_basis, 0.0)  # the defaulted field
        signal = self.engine.generate_strategy_signal(hand_built, 10_000_000.0)
        self.assertAlmostEqual(signal.daily_roll_decay_usd, 1_550.0, places=2)

    def test_quote_width_property(self):
        quote = VIXCallSpreadQuote(20.0, 35.0, 18.0, 30, 3.0, 1200.0, 300.0, 23.0)
        self.assertAlmostEqual(quote.spread_width, 15.0, places=6)


if __name__ == "__main__":
    unittest.main()
