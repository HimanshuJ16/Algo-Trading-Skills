import unittest
from explainable_boosting_pricer import Config, ExplainableBoostingPricer

class TestExplainableBoostingPricer(unittest.TestCase):
    def test_init(self):
        cfg = Config()
        obj = ExplainableBoostingPricer(cfg)
        self.assertEqual(obj.config.name, "explainable-boosting-machines-for-regulated-signals")
        
    def test_process(self):
        cfg = Config()
        obj = ExplainableBoostingPricer(cfg)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
