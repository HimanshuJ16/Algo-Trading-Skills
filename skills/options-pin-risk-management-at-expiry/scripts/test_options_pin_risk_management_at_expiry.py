import unittest
from options_pin_risk_management_at_expiry import InputData, OptionsPinRiskManagementAtExpiryEngine

class TestOptionsPinRiskManagementAtExpiry(unittest.TestCase):
    def test_process(self):
        engine = OptionsPinRiskManagementAtExpiryEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = OptionsPinRiskManagementAtExpiryEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
