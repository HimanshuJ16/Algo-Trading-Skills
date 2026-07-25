import unittest
from tick_to_trade_latency_measurement import Config, Engine

class TestTickToTradeLatencyMeasurement(unittest.TestCase):
    def test_init(self):
        config = Config()
        instance = Engine(config)
        self.assertTrue(instance.config.enabled)
        
    def test_execute(self):
        config = Config()
        instance = Engine(config)
        self.assertTrue(instance.execute())

if __name__ == '__main__':
    unittest.main()
