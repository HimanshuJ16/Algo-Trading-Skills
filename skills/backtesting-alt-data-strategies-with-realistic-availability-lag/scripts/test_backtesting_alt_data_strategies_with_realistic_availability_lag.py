import unittest
from backtesting_alt_data_strategies_with_realistic_availability_lag import BacktestingAltDataStrategiesWithRealisticAvailabilityLag, BacktestingAltDataStrategiesWithRealisticAvailabilityLagConfig

class TestBacktestingAltDataStrategiesWithRealisticAvailabilityLag(unittest.TestCase):
    def test_init(self):
        config = BacktestingAltDataStrategiesWithRealisticAvailabilityLagConfig()
        obj = BacktestingAltDataStrategiesWithRealisticAvailabilityLag(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = BacktestingAltDataStrategiesWithRealisticAvailabilityLagConfig()
        obj = BacktestingAltDataStrategiesWithRealisticAvailabilityLag(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
