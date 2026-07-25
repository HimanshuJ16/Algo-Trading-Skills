import unittest
import sys
import os

# Add the script dir to path so we can import the module
sys.path.insert(0, os.path.dirname(__file__))
from eu_benchmark_regulation_for_strategies_referencing_indices import EuBenchmarkRegulationForStrategiesReferencingIndicesEngine

class TestEuBenchmarkRegulationForStrategiesReferencingIndices(unittest.TestCase):
    def setUp(self):
        self.engine = EuBenchmarkRegulationForStrategiesReferencingIndicesEngine()
        
    def test_valid(self):
        res = self.engine.check({"valid": True})
        self.assertTrue(res.is_compliant)
        
    def test_invalid(self):
        res = self.engine.check({"valid": False})
        self.assertFalse(res.is_compliant)
        
    def test_edge(self):
        res = self.engine.check({})
        self.assertFalse(res.is_compliant)

if __name__ == '__main__':
    unittest.main()
