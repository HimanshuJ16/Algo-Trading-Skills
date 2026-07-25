import unittest
from risk_parity_allocation_across_strategies import RiskParityAllocationAcrossStrategies, RiskParityAllocationAcrossStrategiesConfig

class TestRiskParityAllocationAcrossStrategies(unittest.TestCase):
    def test_execute_true(self):
        config = RiskParityAllocationAcrossStrategiesConfig(enabled=True)
        engine = RiskParityAllocationAcrossStrategies(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = RiskParityAllocationAcrossStrategiesConfig(enabled=False)
        engine = RiskParityAllocationAcrossStrategies(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
