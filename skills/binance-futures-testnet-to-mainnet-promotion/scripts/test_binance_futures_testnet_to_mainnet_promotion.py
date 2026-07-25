import unittest
from binance_futures_testnet_to_mainnet_promotion import Config, IntegrationEngine

class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.config = Config("key", "secret")
        self.engine = IntegrationEngine(self.config)
        
    def test_connect(self):
        self.assertTrue(self.engine.connect())
        
    def test_disconnect(self):
        self.assertTrue(self.engine.disconnect())

if __name__ == '__main__':
    unittest.main()
