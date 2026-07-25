import unittest
from ice_futures_us_eu_integration import Config, IntegrationEngine

class TestIntegrationEngine(unittest.TestCase):
    def setUp(self):
        self.config = Config(api_key="test_key")
        self.engine = IntegrationEngine(self.config)
        
    def test_connect(self):
        self.assertTrue(self.engine.connect())
        
    def test_process(self):
        self.assertEqual(self.engine.process(), "success")

if __name__ == '__main__':
    unittest.main()
