import unittest
import sys
import os

# Add the script dir to path so we can import the module
sys.path.insert(0, os.path.dirname(__file__))
from regulatory_sandbox_programs_for_fintech_testing import RegulatorySandboxProgramsForFintechTestingEngine

class TestRegulatorySandboxProgramsForFintechTesting(unittest.TestCase):
    def setUp(self):
        self.engine = RegulatorySandboxProgramsForFintechTestingEngine()
        
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
