import unittest
from portfolio_construction_with_transaction_cost_awareness import Config, Engine

class TestPortfolioConstructionWithTransactionCostAwareness(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
