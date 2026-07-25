import unittest
from kraken_websocket_v2_auth_and_subscriptions import Config, IntegrationEngine

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
