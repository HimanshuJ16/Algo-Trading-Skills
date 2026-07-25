import unittest
from quanto_options_and_cross_currency_derivative_structures import InputData, QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine

class TestQuantoOptionsAndCrossCurrencyDerivativeStructures(unittest.TestCase):
    def test_process(self):
        engine = QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
