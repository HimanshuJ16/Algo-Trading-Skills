"""
Unit tests for american-vs-european-style-option-exercise-handling skill.

Tests:
1. Non-dividend call never exercised (golden rule).
2. Dividend call exercised when dividend > time value.
3. Dividend call not exercised when dividend < time value.
4. Deep ITM put exercised (below parity).
5. ITM put not exercised (market > intrinsic).
6. OTM/ATM option never exercised.
7. Below-parity call exercised even without dividend.
8. Below-parity call with dividend exercised.
9. Dividend == time_value boundary — not exercised.
10. Invalid option type raises ValueError.
11. Lowercase option_type normalized.
12. Negative spot price rejected.
13. Negative strike price rejected.
14. Negative market price rejected.
15. Negative dividend amount rejected.
16. NaN price rejected.
17. Frozen OptionState immutability.
18. Intrinsic value property correctness.
"""
import math
import unittest
from american_vs_european_style_option_exercise_handling import (
    OptionState,
    EarlyExerciseEvaluator,
)


class TestEarlyExerciseEvaluator(unittest.TestCase):

    def setUp(self):
        self.evaluator = EarlyExerciseEvaluator()

    # --- Golden rule: non-dividend call ---

    def test_call_no_dividend_never_exercise(self):
        state = OptionState(
            option_type="CALL",
            spot_price=110.0,
            strike_price=100.0,
            market_price=11.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("Never early exercise a Call option without an imminent dividend", reason)

    # --- Dividend call ---

    def test_call_with_dividend_exercise(self):
        state = OptionState(
            option_type="CALL",
            spot_price=110.0,
            strike_price=100.0,
            market_price=11.0,
            is_ex_dividend_tomorrow=True,
            dividend_amount=2.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)
        self.assertIn("exceeds remaining time value", reason)

    def test_call_with_small_dividend_no_exercise(self):
        state = OptionState(
            option_type="CALL",
            spot_price=110.0,
            strike_price=100.0,
            market_price=11.0,
            is_ex_dividend_tomorrow=True,
            dividend_amount=0.5,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("does not exceed remaining time value", reason)

    # --- Dividend boundary: dividend == time_value ---

    def test_call_dividend_equals_time_value_no_exercise(self):
        # Intrinsic = 10, Market = 11, Time Value = 1, Dividend = 1.0
        # Strictly greater required — equal does not exercise.
        state = OptionState(
            option_type="CALL",
            spot_price=110.0,
            strike_price=100.0,
            market_price=11.0,
            is_ex_dividend_tomorrow=True,
            dividend_amount=1.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)

    # --- Below-parity call (no dividend) ---

    def test_call_below_parity_no_dividend_exercise(self):
        # Intrinsic = 10, Market = 8 (below parity). No dividend.
        # Selling gets 8, exercising gets 10 — exercise is optimal.
        state = OptionState(
            option_type="CALL",
            spot_price=110.0,
            strike_price=100.0,
            market_price=8.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)
        self.assertIn("below parity", reason.lower())

    # --- Below-parity call with dividend ---

    def test_call_below_parity_with_dividend_exercise(self):
        state = OptionState(
            option_type="CALL",
            spot_price=110.0,
            strike_price=100.0,
            market_price=8.0,
            is_ex_dividend_tomorrow=True,
            dividend_amount=2.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)

    # --- Put rules ---

    def test_put_deep_itm_exercise(self):
        state = OptionState(
            option_type="PUT",
            spot_price=50.0,
            strike_price=100.0,
            market_price=49.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)
        self.assertIn("Intrinsic value", reason)

    def test_put_itm_but_time_value_no_exercise(self):
        state = OptionState(
            option_type="PUT",
            spot_price=50.0,
            strike_price=100.0,
            market_price=52.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("Do not exercise", reason)

    # --- OTM / ATM ---

    def test_otm_call_never_exercise(self):
        state = OptionState(
            option_type="CALL",
            spot_price=90.0,
            strike_price=100.0,
            market_price=1.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("OTM", reason)

    def test_atm_call_never_exercise(self):
        state = OptionState(
            option_type="CALL",
            spot_price=100.0,
            strike_price=100.0,
            market_price=2.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("OTM", reason)

    def test_otm_put_never_exercise(self):
        state = OptionState(
            option_type="PUT",
            spot_price=110.0,
            strike_price=100.0,
            market_price=1.0,
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("OTM", reason)

    # --- Input validation ---

    def test_invalid_option_type_raises(self):
        with self.assertRaises(ValueError):
            OptionState(
                option_type="STOCK",
                spot_price=100.0,
                strike_price=100.0,
                market_price=5.0,
            )

    def test_lowercase_option_type_normalized(self):
        state = OptionState(
            option_type="call",
            spot_price=110.0,
            strike_price=100.0,
            market_price=11.0,
        )
        self.assertEqual(state.option_type, "CALL")
        should_exercise, _ = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)

    def test_negative_spot_price_rejected(self):
        with self.assertRaises(ValueError):
            OptionState(option_type="CALL", spot_price=-1.0, strike_price=100.0, market_price=5.0)

    def test_negative_strike_price_rejected(self):
        with self.assertRaises(ValueError):
            OptionState(option_type="CALL", spot_price=100.0, strike_price=-1.0, market_price=5.0)

    def test_negative_market_price_rejected(self):
        with self.assertRaises(ValueError):
            OptionState(option_type="CALL", spot_price=100.0, strike_price=100.0, market_price=-1.0)

    def test_negative_dividend_amount_rejected(self):
        with self.assertRaises(ValueError):
            OptionState(
                option_type="CALL", spot_price=100.0, strike_price=100.0, market_price=5.0,
                is_ex_dividend_tomorrow=True, dividend_amount=-1.0,
            )

    def test_nan_price_rejected(self):
        with self.assertRaises(ValueError):
            OptionState(option_type="CALL", spot_price=math.nan, strike_price=100.0, market_price=5.0)

    # --- Immutability ---

    def test_option_state_is_frozen(self):
        state = OptionState(option_type="CALL", spot_price=100.0, strike_price=100.0, market_price=5.0)
        with self.assertRaises(Exception):
            state.spot_price = 200.0

    # --- Properties ---

    def test_intrinsic_value_call(self):
        state = OptionState(option_type="CALL", spot_price=110.0, strike_price=100.0, market_price=12.0)
        self.assertAlmostEqual(state.intrinsic_value, 10.0)

    def test_intrinsic_value_put(self):
        state = OptionState(option_type="PUT", spot_price=90.0, strike_price=100.0, market_price=12.0)
        self.assertAlmostEqual(state.intrinsic_value, 10.0)

    def test_intrinsic_value_otm(self):
        state = OptionState(option_type="CALL", spot_price=90.0, strike_price=100.0, market_price=2.0)
        self.assertAlmostEqual(state.intrinsic_value, 0.0)

    def test_time_value(self):
        state = OptionState(option_type="CALL", spot_price=110.0, strike_price=100.0, market_price=12.0)
        self.assertAlmostEqual(state.time_value, 2.0)


if __name__ == "__main__":
    unittest.main()
