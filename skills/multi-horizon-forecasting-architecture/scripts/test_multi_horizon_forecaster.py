import unittest
from multi_horizon_forecaster import Config, MultiHorizonForecaster

class TestMultiHorizonForecaster(unittest.TestCase):
    def test_init(self):
        obj = MultiHorizonForecaster(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = MultiHorizonForecaster(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
