import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from override_access_control import OverrideAccessControlEngine, Result

class TestOverrideAccessControlEngine(unittest.TestCase):
    def setUp(self):
        self.engine = OverrideAccessControlEngine()
        
    def test_execute_true(self):
        res = self.engine.execute(True)
        self.assertTrue(res.success)
        
    def test_execute_false(self):
        res = self.engine.execute(False)
        self.assertFalse(res.success)

if __name__ == '__main__':
    unittest.main()
