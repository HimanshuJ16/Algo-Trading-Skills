import unittest
from colocation_latency_budget_accounting import Config, Engine

class TestColocationLatencyBudgetAccounting(unittest.TestCase):
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
