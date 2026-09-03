"""
Unit tests for american-vs-european-style-option-exercise-handling.

The decision under test is the holder's: exercise now, or sell? The engine's rule
is ``intrinsic > bid``, and the suite is built around three things:

1. **The invariant.** A holder must never exercise while the bid is at or above
   intrinsic, because exercising realises only intrinsic. Swept over calls, puts,
   dividends and price levels in ``test_never_exercise_at_or_above_parity_sweep``.
2. **The v1 regression.** v1.x exercised whenever ``dividend > quoted time
   value``, which fires across ``0 <= TV < D`` and destroys ``TV`` per share. The
   cases it got wrong are pinned explicitly and named ``*_regression_*``.
3. **An independent oracle.** ``test_matches_black_scholes_continuation_*`` prices
   the continuation value with a Black-Scholes implementation written here in the
   test, entirely independent of the module under test (which contains no option
   pricing model at all), and asserts the engine agrees with it.
"""
import math
import unittest

from american_vs_european_style_option_exercise_handling import (
    DividendCaptureTest,
    EarlyExerciseEvaluator,
    OptionState,
)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF. Independent of the module under test."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _black_scholes_call(spot: float, strike: float, tau: float,
                        sigma: float, rate: float) -> float:
    """European call value. Used only as an independent oracle in this suite."""
    d1 = ((math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * tau)
          / (sigma * math.sqrt(tau)))
    d2 = d1 - sigma * math.sqrt(tau)
    return spot * _norm_cdf(d1) - strike * math.exp(-rate * tau) * _norm_cdf(d2)


class TestEarlyExerciseEvaluator(unittest.TestCase):

    def setUp(self):
        self.evaluator = EarlyExerciseEvaluator()

    # --- The invariant: never exercise at or above parity --------------

    def test_never_exercise_at_or_above_parity_sweep(self):
        """No combination of inputs may exercise while the bid >= intrinsic.

        Exercising realises exactly the intrinsic value, so whenever the bid is at
        or above it, selling weakly dominates. This is the property v1.x violated.
        """
        for option_type, spot, strike in (
            ("CALL", 110.0, 100.0),
            ("CALL", 500.0, 100.0),
            ("CALL", 100.01, 100.0),
            ("PUT", 50.0, 100.0),
            ("PUT", 99.99, 100.0),
        ):
            intrinsic = (spot - strike) if option_type == "CALL" else (strike - spot)
            for excess in (0.0, 0.001, 0.5, 5.0):
                for is_ex_div, dividend in (
                    (False, 0.0), (True, 0.01), (True, 2.0), (True, 1000.0),
                ):
                    state = OptionState(
                        option_type=option_type,
                        spot_price=spot,
                        strike_price=strike,
                        market_price=intrinsic + excess,
                        is_ex_dividend_tomorrow=is_ex_div,
                        dividend_amount=dividend,
                    )
                    should_exercise, reason = self.evaluator.evaluate(state)
                    with self.subTest(option_type=option_type, spot=spot,
                                      excess=excess, dividend=dividend):
                        self.assertFalse(
                            should_exercise,
                            f"exercised at bid={state.market_price} vs "
                            f"intrinsic={intrinsic}: {reason}",
                        )

    def test_below_parity_always_exercises_sweep(self):
        """Whenever the bid is strictly below intrinsic, exercising wins."""
        for option_type, spot, strike in (
            ("CALL", 110.0, 100.0), ("PUT", 50.0, 100.0),
        ):
            intrinsic = (spot - strike) if option_type == "CALL" else (strike - spot)
            for shortfall in (0.0001, 0.05, 2.0, intrinsic):
                for is_ex_div, dividend in ((False, 0.0), (True, 2.0)):
                    state = OptionState(
                        option_type=option_type,
                        spot_price=spot,
                        strike_price=strike,
                        market_price=intrinsic - shortfall,
                        is_ex_dividend_tomorrow=is_ex_div,
                        dividend_amount=dividend,
                    )
                    with self.subTest(option_type=option_type,
                                      shortfall=shortfall, dividend=dividend):
                        self.assertTrue(self.evaluator.evaluate(state)[0])

    # --- v1.x regressions: the dividend is not a trigger ---------------

    def test_regression_dividend_above_quoted_time_value_does_not_exercise(self):
        """Bid 11.00, intrinsic 10.00, dividend 2.00.

        v1.x fired here because ``dividend (2.00) > time_value (1.00)``, exercising
        for 10.00 when the position could be sold for 11.00 -- a certain loss of
        1.00 per share, 100.00 per contract. The bid is cum-dividend and already
        prices the pending dividend; comparing it against the quoted time value
        counts the dividend twice.
        """
        state = OptionState(
            option_type="CALL",
            spot_price=110.0,
            strike_price=100.0,
            market_price=11.0,
            is_ex_dividend_tomorrow=True,
            dividend_amount=2.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertAlmostEqual(state.early_exercise_edge, -1.0)
        self.assertIn("count it twice", reason)

    def test_regression_decision_is_independent_of_dividend_size(self):
        """Holding the quote fixed, no dividend size may flip the decision.

        v1.x flipped from hold to exercise as soon as the dividend crossed the
        quoted time value of 1.00.
        """
        verdicts = set()
        for dividend in (0.0, 0.5, 0.999, 1.0, 1.001, 2.0, 50.0):
            state = OptionState(
                option_type="CALL",
                spot_price=110.0,
                strike_price=100.0,
                market_price=11.0,
                is_ex_dividend_tomorrow=dividend > 0.0,
                dividend_amount=dividend,
            )
            verdicts.add(self.evaluator.evaluate(state)[0])
        self.assertEqual(verdicts, {False})

    def test_regression_dividend_call_at_exercise_boundary_still_exercises(self):
        """The corrected rule must not lose the true dividend exercise.

        At the boundary the American call's fair value sits at parity, so the bid
        prints just below it. The engine must still fire.
        """
        state = OptionState(
            option_type="CALL",
            spot_price=110.0,
            strike_price=100.0,
            market_price=9.95,
            is_ex_dividend_tomorrow=True,
            dividend_amount=2.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)
        self.assertIn("ex-dividend date is pending", reason)
        self.assertIn("cut-off", reason)

    # --- Independent Black-Scholes oracle ------------------------------

    def test_matches_black_scholes_continuation_hold_case(self):
        """Model says hold; engine must agree when fed the fair bid.

        S=110 cum-div, K=100, D=2.00 tomorrow, tau=90/365, sigma=25%, r=4%.
        Continuation = BS on the ex-dividend underlying (108) and is worth more
        than the 10.00 realised by exercising, so holding is optimal. v1.x
        exercised here because D=2.00 exceeded the quoted time value.
        """
        spot, strike, dividend = 110.0, 100.0, 2.0
        continuation = _black_scholes_call(
            spot - dividend, strike, 90.0 / 365.0, 0.25, 0.04
        )
        intrinsic = spot - strike
        self.assertGreater(continuation, intrinsic)  # the oracle's verdict: hold

        state = OptionState(
            option_type="CALL",
            spot_price=spot,
            strike_price=strike,
            market_price=continuation,  # fair American value = max(hold, intrinsic)
            is_ex_dividend_tomorrow=True,
            dividend_amount=dividend,
        )
        self.assertFalse(self.evaluator.evaluate(state)[0])
        self.assertGreater(state.time_value, 0.0)
        self.assertLess(state.time_value, dividend)  # exactly v1.x's failure band

    def test_matches_black_scholes_continuation_exercise_case(self):
        """Model says exercise; engine must agree when fed the fair bid.

        Same contract with a 30-day tail: continuation falls below the 10.00
        realised by exercising, so the American value is pinned at parity and the
        bid prints at or below it.
        """
        spot, strike, dividend = 110.0, 100.0, 2.0
        continuation = _black_scholes_call(
            spot - dividend, strike, 30.0 / 365.0, 0.25, 0.04
        )
        intrinsic = spot - strike
        self.assertLess(continuation, intrinsic)  # the oracle's verdict: exercise

        state = OptionState(
            option_type="CALL",
            spot_price=spot,
            strike_price=strike,
            market_price=intrinsic - 0.05,  # American pinned at parity, bid below
            is_ex_dividend_tomorrow=True,
            dividend_amount=dividend,
        )
        self.assertTrue(self.evaluator.evaluate(state)[0])

    # --- Non-dividend call: Merton's no-early-exercise result ----------

    def test_call_no_dividend_never_exercise(self):
        state = OptionState(
            option_type="CALL", spot_price=110.0, strike_price=100.0,
            market_price=11.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("never optimal", reason)

    def test_call_below_parity_no_dividend_exercise(self):
        """Intrinsic 10.00, bid 8.00. Selling gets 8.00; exercising gets 10.00."""
        state = OptionState(
            option_type="CALL", spot_price=110.0, strike_price=100.0,
            market_price=8.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)
        self.assertIn("stale, wide or crossed", reason)

    # --- Puts ----------------------------------------------------------

    def test_put_below_parity_exercise(self):
        state = OptionState(
            option_type="PUT", spot_price=50.0, strike_price=100.0,
            market_price=49.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)
        self.assertIn("Exercise", reason)

    def test_put_below_parity_does_not_credit_the_dividend(self):
        """A pending dividend must never be offered as a reason to exercise a put.

        It makes put early exercise *less* attractive, not more: the exercising
        holder gives up the stock and the dividend with it.
        """
        state = OptionState(
            option_type="PUT", spot_price=50.0, strike_price=100.0,
            market_price=49.0, is_ex_dividend_tomorrow=True, dividend_amount=2.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)
        self.assertIn("less attractive, not more", reason)
        self.assertNotIn("dividend-driven exercise", reason)

    def test_put_above_parity_no_exercise(self):
        state = OptionState(
            option_type="PUT", spot_price=50.0, strike_price=100.0,
            market_price=52.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("interest on the strike", reason)

    # --- Exact-parity boundary -----------------------------------------

    def test_exact_parity_does_not_exercise(self):
        """Value-neutral at parity; selling avoids delivery, so do not exercise."""
        for option_type, spot, strike, intrinsic in (
            ("CALL", 110.0, 100.0, 10.0),
            ("PUT", 90.0, 100.0, 10.0),
        ):
            for is_ex_div, dividend in ((False, 0.0), (True, 5.0)):
                state = OptionState(
                    option_type=option_type, spot_price=spot, strike_price=strike,
                    market_price=intrinsic,
                    is_ex_dividend_tomorrow=is_ex_div, dividend_amount=dividend,
                )
                with self.subTest(option_type=option_type, dividend=dividend):
                    self.assertFalse(self.evaluator.evaluate(state)[0])
                    self.assertAlmostEqual(state.early_exercise_edge, 0.0)

    def test_exact_parity_warns_against_doing_nothing(self):
        """At parity, "do not exercise" must not be read as "hold".

        The quote has no time value left to protect, so holding through an ex-date
        is strictly worse than either selling or exercising. An agent routing on
        the boolean alone would sit on the position; the reason must say otherwise.
        """
        state = OptionState(
            option_type="CALL", spot_price=110.0, strike_price=100.0,
            market_price=10.0, is_ex_dividend_tomorrow=True, dividend_amount=5.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("holding is worth less than either", reason)
        self.assertIn("rather than doing nothing", reason)

    def test_above_parity_does_not_warn_against_holding(self):
        """With time value still in the quote, holding is a legitimate choice."""
        state = OptionState(
            option_type="CALL", spot_price=110.0, strike_price=100.0,
            market_price=10.5, is_ex_dividend_tomorrow=True, dividend_amount=5.0,
        )
        self.assertNotIn("doing nothing", self.evaluator.evaluate(state)[1])

    # --- OTM / ATM ------------------------------------------------------

    def test_otm_and_atm_never_exercise(self):
        for option_type, spot, strike in (
            ("CALL", 90.0, 100.0),
            ("CALL", 100.0, 100.0),
            ("PUT", 110.0, 100.0),
            ("PUT", 100.0, 100.0),
        ):
            state = OptionState(
                option_type=option_type, spot_price=spot, strike_price=strike,
                market_price=1.0, is_ex_dividend_tomorrow=True, dividend_amount=5.0,
            )
            with self.subTest(option_type=option_type, spot=spot):
                should_exercise, reason = self.evaluator.evaluate(state)
                self.assertFalse(should_exercise)
                self.assertIn("OTM", reason)

    def test_otm_call_with_worthless_bid_does_not_exercise(self):
        """A zero bid must not be read as a below-parity signal when OTM."""
        state = OptionState(
            option_type="CALL", spot_price=90.0, strike_price=100.0,
            market_price=0.0,
        )
        self.assertFalse(self.evaluator.evaluate(state)[0])

    # --- Input validation -----------------------------------------------

    def test_invalid_option_type_raises(self):
        for bad in ("STOCK", "", "CALLS", "c all"):
            with self.subTest(option_type=bad):
                with self.assertRaises(ValueError):
                    OptionState(option_type=bad, spot_price=100.0,
                                strike_price=100.0, market_price=5.0)

    def test_non_string_option_type_raises(self):
        with self.assertRaises(ValueError):
            OptionState(option_type=None, spot_price=100.0,
                        strike_price=100.0, market_price=5.0)

    def test_option_type_normalized(self):
        state = OptionState(option_type="  call  ", spot_price=110.0,
                            strike_price=100.0, market_price=11.0)
        self.assertEqual(state.option_type, "CALL")
        self.assertFalse(self.evaluator.evaluate(state)[0])

    def test_negative_amounts_rejected(self):
        for field in ("spot_price", "strike_price", "market_price",
                      "dividend_amount"):
            kwargs = dict(option_type="CALL", spot_price=100.0,
                          strike_price=100.0, market_price=5.0,
                          dividend_amount=0.0)
            kwargs[field] = -1.0
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    OptionState(**kwargs)

    def test_non_finite_amounts_rejected(self):
        for bad in (math.nan, math.inf, -math.inf):
            for field in ("spot_price", "strike_price", "market_price",
                          "dividend_amount"):
                kwargs = dict(option_type="CALL", spot_price=100.0,
                              strike_price=100.0, market_price=5.0,
                              dividend_amount=0.0)
                kwargs[field] = bad
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ValueError):
                        OptionState(**kwargs)

    def test_bool_price_rejected(self):
        """``True`` is an int in Python; accepting it would silently mean 1.00."""
        with self.assertRaises(ValueError):
            OptionState(option_type="CALL", spot_price=True,
                        strike_price=100.0, market_price=5.0)

    def test_non_numeric_price_rejected(self):
        with self.assertRaises(ValueError):
            OptionState(option_type="CALL", spot_price="110",
                        strike_price=100.0, market_price=5.0)

    def test_integer_prices_coerced_to_float(self):
        state = OptionState(option_type="CALL", spot_price=110,
                            strike_price=100, market_price=11)
        self.assertIsInstance(state.spot_price, float)
        self.assertAlmostEqual(state.intrinsic_value, 10.0)

    # --- Immutability and properties -------------------------------------

    def test_option_state_is_frozen(self):
        state = OptionState(option_type="CALL", spot_price=100.0,
                            strike_price=100.0, market_price=5.0)
        with self.assertRaises(Exception):
            state.spot_price = 200.0

    def test_intrinsic_value(self):
        call = OptionState(option_type="CALL", spot_price=110.0,
                           strike_price=100.0, market_price=12.0)
        put = OptionState(option_type="PUT", spot_price=90.0,
                          strike_price=100.0, market_price=12.0)
        otm = OptionState(option_type="CALL", spot_price=90.0,
                          strike_price=100.0, market_price=2.0)
        self.assertAlmostEqual(call.intrinsic_value, 10.0)
        self.assertAlmostEqual(put.intrinsic_value, 10.0)
        self.assertAlmostEqual(otm.intrinsic_value, 0.0)

    def test_time_value_is_not_clamped_below_zero(self):
        """A negative time value is the below-parity signal; it must survive."""
        state = OptionState(option_type="CALL", spot_price=110.0,
                            strike_price=100.0, market_price=8.0)
        self.assertAlmostEqual(state.time_value, -2.0)
        self.assertAlmostEqual(state.early_exercise_edge, 2.0)

    def test_early_exercise_edge_sign(self):
        rich = OptionState(option_type="CALL", spot_price=110.0,
                           strike_price=100.0, market_price=12.0)
        self.assertAlmostEqual(rich.time_value, 2.0)
        self.assertAlmostEqual(rich.early_exercise_edge, -2.0)


class TestDividendCaptureTest(unittest.TestCase):
    """The exact Merton condition: D > p_ex + K * (1 - exp(-r * tau))."""

    def setUp(self):
        self.evaluator = EarlyExerciseEvaluator()
        self.state = OptionState(
            option_type="CALL", spot_price=105.0, strike_price=100.0,
            market_price=5.20, is_ex_dividend_tomorrow=True,
            dividend_amount=0.75,
        )

    def test_time_value_ex_dividend_matches_hand_calculation(self):
        """K=100, p_ex=0.60, r=5%, tau=15/365.

        100 * (1 - exp(-0.05 * 15/365)) = 0.2052684 (series expansion of exp),
        + 0.60 put = 0.8052684. D=0.75 falls short, so exercise is not optimal --
        a position the conservative "D > cum-dividend extrinsic" desk screen would
        have flagged.
        """
        result = self.evaluator.dividend_capture_test(
            self.state, same_strike_put_price=0.60,
            risk_free_rate=0.05, years_to_expiry=15.0 / 365.0,
        )
        self.assertIsInstance(result, DividendCaptureTest)
        self.assertAlmostEqual(result.interest_on_strike, 0.2052684, places=6)
        self.assertAlmostEqual(result.time_value_ex_dividend, 0.8052684, places=6)
        self.assertFalse(result.is_exercise_optimal)
        self.assertIn("hold or sell", result.detail)

    def test_exercise_optimal_when_dividend_clears_the_hurdle(self):
        result = self.evaluator.dividend_capture_test(
            self.state, same_strike_put_price=0.10,
            risk_free_rate=0.05, years_to_expiry=15.0 / 365.0,
        )
        self.assertAlmostEqual(result.time_value_ex_dividend, 0.3052684, places=6)
        self.assertTrue(result.is_exercise_optimal)  # 0.75 > 0.3053
        self.assertIn("exercise is optimal", result.detail)

    def test_boundary_requires_strictly_greater(self):
        """D exactly equal to TV_ex is indifference; do not exercise."""
        result = self.evaluator.dividend_capture_test(
            self.state, same_strike_put_price=0.75,
            risk_free_rate=0.0, years_to_expiry=1.0,
        )
        self.assertAlmostEqual(result.interest_on_strike, 0.0)
        self.assertAlmostEqual(result.time_value_ex_dividend, 0.75)
        self.assertFalse(result.is_exercise_optimal)

    def test_zero_rate_reduces_to_the_put_price(self):
        result = self.evaluator.dividend_capture_test(
            self.state, same_strike_put_price=0.40,
            risk_free_rate=0.0, years_to_expiry=0.5,
        )
        self.assertAlmostEqual(result.time_value_ex_dividend, 0.40)

    def test_negative_rate_gives_negative_interest_on_strike(self):
        """Under a negative rate, paying the strike early is a benefit, not a cost."""
        result = self.evaluator.dividend_capture_test(
            self.state, same_strike_put_price=0.0,
            risk_free_rate=-0.01, years_to_expiry=1.0,
        )
        self.assertLess(result.interest_on_strike, 0.0)
        self.assertAlmostEqual(
            result.interest_on_strike, 100.0 * (1.0 - math.exp(0.01)), places=9
        )

    def test_agrees_with_quote_based_rule_on_a_fair_quote(self):
        """Given a fair quote the two tests are the same test.

        Fair cum-dividend call = intrinsic_ex + TV_ex - ... expressed directly:
        holding is worth (S - D - K) + TV_ex, and exercising is worth (S - K), so
        exercise wins exactly when D > TV_ex. Both routes must return the same
        verdict for the same scenario.
        """
        spot, strike, dividend, tv_ex = 110.0, 100.0, 2.0, 0.80
        fair_bid = (spot - dividend - strike) + tv_ex  # = 8.80, below parity
        state = OptionState(
            option_type="CALL", spot_price=spot, strike_price=strike,
            market_price=fair_bid, is_ex_dividend_tomorrow=True,
            dividend_amount=dividend,
        )
        quote_verdict = self.evaluator.evaluate(state)[0]
        model_verdict = self.evaluator.dividend_capture_test(
            state, same_strike_put_price=tv_ex,
            risk_free_rate=0.0, years_to_expiry=1.0,
        ).is_exercise_optimal
        self.assertTrue(quote_verdict)
        self.assertEqual(quote_verdict, model_verdict)

    def test_put_is_rejected(self):
        put = OptionState(
            option_type="PUT", spot_price=90.0, strike_price=100.0,
            market_price=10.5, is_ex_dividend_tomorrow=True, dividend_amount=1.0,
        )
        with self.assertRaises(ValueError) as ctx:
            self.evaluator.dividend_capture_test(
                put, same_strike_put_price=0.5, risk_free_rate=0.05,
                years_to_expiry=0.25,
            )
        self.assertIn("calls only", str(ctx.exception))

    def test_no_pending_dividend_is_rejected(self):
        for is_ex_div, dividend in ((False, 1.0), (True, 0.0)):
            state = OptionState(
                option_type="CALL", spot_price=110.0, strike_price=100.0,
                market_price=11.0, is_ex_dividend_tomorrow=is_ex_div,
                dividend_amount=dividend,
            )
            with self.subTest(is_ex_dividend_tomorrow=is_ex_div, dividend=dividend):
                with self.assertRaises(ValueError):
                    self.evaluator.dividend_capture_test(
                        state, same_strike_put_price=0.5, risk_free_rate=0.05,
                        years_to_expiry=0.25,
                    )

    def test_invalid_model_inputs_rejected(self):
        bad_kwargs = (
            dict(same_strike_put_price=-0.01, risk_free_rate=0.05,
                 years_to_expiry=0.25),
            dict(same_strike_put_price=math.nan, risk_free_rate=0.05,
                 years_to_expiry=0.25),
            dict(same_strike_put_price=0.5, risk_free_rate=math.nan,
                 years_to_expiry=0.25),
            dict(same_strike_put_price=0.5, risk_free_rate=math.inf,
                 years_to_expiry=0.25),
            dict(same_strike_put_price=0.5, risk_free_rate=True,
                 years_to_expiry=0.25),
            dict(same_strike_put_price=0.5, risk_free_rate=0.05,
                 years_to_expiry=-0.25),
            dict(same_strike_put_price=0.5, risk_free_rate=0.05,
                 years_to_expiry=math.inf),
        )
        for kwargs in bad_kwargs:
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self.evaluator.dividend_capture_test(self.state, **kwargs)


if __name__ == "__main__":
    unittest.main()
