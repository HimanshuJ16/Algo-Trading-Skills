import unittest
from clock_synchronization_ptp_for_trading_hosts import Config, Engine

class TestClockSynchronizationPtpForTradingHosts(unittest.TestCase):
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
