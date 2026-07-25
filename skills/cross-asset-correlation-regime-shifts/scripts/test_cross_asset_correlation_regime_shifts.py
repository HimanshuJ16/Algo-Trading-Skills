import unittest
from cross_asset_correlation_regime_shifts import InputData, CrossAssetCorrelationRegimeShiftsEngine

class TestCrossAssetCorrelationRegimeShifts(unittest.TestCase):
    def test_process(self):
        engine = CrossAssetCorrelationRegimeShiftsEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = CrossAssetCorrelationRegimeShiftsEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
