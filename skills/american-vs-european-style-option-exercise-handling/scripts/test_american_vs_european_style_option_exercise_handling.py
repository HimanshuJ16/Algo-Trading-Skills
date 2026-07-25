import unittest
from american_vs_european_style_option_exercise_handling import InputData, AmericanVsEuropeanStyleOptionExerciseHandlingEngine

class TestAmericanVsEuropeanStyleOptionExerciseHandling(unittest.TestCase):
    def test_process(self):
        engine = AmericanVsEuropeanStyleOptionExerciseHandlingEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = AmericanVsEuropeanStyleOptionExerciseHandlingEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
