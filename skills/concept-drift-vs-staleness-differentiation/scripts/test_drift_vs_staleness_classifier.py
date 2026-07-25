import unittest
from drift_vs_staleness_classifier import Config, DriftVsStalenessClassifier

class TestDriftVsStalenessClassifier(unittest.TestCase):
    def test_init(self):
        cfg = Config()
        obj = DriftVsStalenessClassifier(cfg)
        self.assertEqual(obj.config.name, "concept-drift-vs-staleness-differentiation")
        
    def test_process(self):
        cfg = Config()
        obj = DriftVsStalenessClassifier(cfg)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
