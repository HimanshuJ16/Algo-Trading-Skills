import unittest
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from risk_override_audit_logger import RiskOverrideAuditLogger, RiskOverrideAuditLoggerConfig

class TestRiskOverrideAuditLogger(unittest.TestCase):
    def setUp(self):
        self.config = RiskOverrideAuditLoggerConfig(enabled=True)
        self.instance = RiskOverrideAuditLogger(self.config)

    def test_initialization(self):
        self.assertTrue(self.instance.config.enabled)

    def test_execute(self):
        result = self.instance.execute({"mock": "data"})
        self.assertTrue(result)

    def test_execute_disabled(self):
        self.instance.config.enabled = False
        result = self.instance.execute({"mock": "data"})
        self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()
