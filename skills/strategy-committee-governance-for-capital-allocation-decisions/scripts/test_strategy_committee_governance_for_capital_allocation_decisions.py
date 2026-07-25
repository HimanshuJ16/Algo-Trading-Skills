import unittest
from strategy_committee_governance_for_capital_allocation_decisions import Config, Engine

class TestStrategyCommitteeGovernanceForCapitalAllocationDecisions(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
