import unittest
from strategy_specific_vs_shared_risk_budget_allocation import Config, Engine

class TestStrategySpecificVsSharedRiskBudgetAllocation(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
