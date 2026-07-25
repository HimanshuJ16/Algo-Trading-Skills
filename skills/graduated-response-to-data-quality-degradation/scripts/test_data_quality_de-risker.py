import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from data_quality_de-risker import DataQualityDeRiskerEngine, Result

class TestDataQualityDeRiskerEngine(unittest.TestCase):
    def setUp(self):
        self.engine = DataQualityDeRiskerEngine()
        
    def test_execute_true(self):
        res = self.engine.execute(True)
        self.assertTrue(res.success)
        
    def test_execute_false(self):
        res = self.engine.execute(False)
        self.assertFalse(res.success)

if __name__ == '__main__':
    unittest.main()
