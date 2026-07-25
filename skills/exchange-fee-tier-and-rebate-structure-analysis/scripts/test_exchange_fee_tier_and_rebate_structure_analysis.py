import unittest
from exchange_fee_tier_and_rebate_structure_analysis import Config, Engine

class TestExchangeFeeTierAndRebateStructureAnalysis(unittest.TestCase):
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
