import unittest
from american_vs_european_style_option_exercise_handling import (
    OptionState,
    EarlyExerciseEvaluator
)

class TestEarlyExerciseEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = EarlyExerciseEvaluator()

    def test_call_no_dividend_never_exercise(self):
        # Deep ITM Call, no dividend.
        # Intrinsic = 110 - 100 = 10. Market = 11. Time Value = 1.
        state = OptionState(
            option_type='CALL',
            spot_price=110.0,
            strike_price=100.0,
            market_price=11.0
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("Never early exercise a Call option without an imminent dividend", reason)

    def test_call_with_dividend_exercise(self):
        # ITM Call, Ex-Div tomorrow.
        # Intrinsic = 10. Market = 11. Time Value = 1. Dividend = 2.0.
        # Dividend > Time Value -> Exercise.
        state = OptionState(
            option_type='CALL',
            spot_price=110.0,
            strike_price=100.0,
            market_price=11.0,
            is_ex_dividend_tomorrow=True,
            dividend_amount=2.0
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)
        self.assertIn("exceeds remaining time value", reason)
        
    def test_call_with_small_dividend_no_exercise(self):
        # ITM Call, Ex-Div tomorrow.
        # Intrinsic = 10. Market = 11. Time Value = 1. Dividend = 0.5.
        # Dividend < Time Value -> DO NOT Exercise.
        state = OptionState(
            option_type='CALL',
            spot_price=110.0,
            strike_price=100.0,
            market_price=11.0,
            is_ex_dividend_tomorrow=True,
            dividend_amount=0.5
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("does not exceed remaining time value", reason)

    def test_put_deep_itm_exercise(self):
        # Deep ITM Put (Illiquid, trading below intrinsic)
        # Intrinsic = 100 - 50 = 50. Market = 49. (Arbitrage condition)
        state = OptionState(
            option_type='PUT',
            spot_price=50.0,
            strike_price=100.0,
            market_price=49.0
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertTrue(should_exercise)
        self.assertIn("Intrinsic value (50.00) > Market value", reason)

    def test_put_itm_but_time_value_no_exercise(self):
        # ITM Put
        # Intrinsic = 50. Market = 52. Time Value = 2.
        state = OptionState(
            option_type='PUT',
            spot_price=50.0,
            strike_price=100.0,
            market_price=52.0
        )
        should_exercise, reason = self.evaluator.evaluate(state)
        self.assertFalse(should_exercise)
        self.assertIn("Put continuation value (Market Price) > Intrinsic value", reason)

if __name__ == '__main__':
    unittest.main()
