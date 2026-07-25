import unittest
from prime_brokerage_multi_venue_consolidation import Config, IntegrationEngine

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
