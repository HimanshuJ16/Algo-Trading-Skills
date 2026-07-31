import unittest
from options_pin_risk_management_at_expiry import (
    OptionsPinRiskManagementEngine, ExpiryOptionPosition, PinRiskPolicyConfig, PinRiskReport
)

class TestOptionsPinRiskManagementEngine(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsPinRiskManagementEngine()

    def test_short_call_high_pin_risk_close_action(self):
        # Short 10 AAPL Calls @ $100.50 spot vs $100.00 strike (0.5% distance < 1.0%) at 1.0 hr to expiry
        pos = ExpiryOptionPosition(
            symbol="AAPL240119C00100000",
            underlying_symbol="AAPL",
            strike=100.0,
            option_type="CALL",
            position_qty=-10,
            spot_price=100.50,
            hours_to_expiry=1.0
        )
        report = self.engine.audit_position_pin_risk(pos)
        self.assertTrue(report.is_pin_risk_high)
        self.assertEqual(report.status, "HIGH_PIN_RISK_ACTION_REQUIRED")
        self.assertEqual(report.recommended_action, "CLOSE_POSITION_BEFORE_EXPIRY")
        self.assertEqual(report.max_assigned_notional_usd, 100500.0)

    def test_deep_otm_low_pin_risk_hold_action(self):
        # Short 10 AAPL Calls @ $90.00 spot vs $100.00 strike (10% distance > 1.0%)
        pos = ExpiryOptionPosition(
            symbol="AAPL240119C00100000",
            underlying_symbol="AAPL",
            strike=100.0,
            option_type="CALL",
            position_qty=-10,
            spot_price=90.00,
            hours_to_expiry=1.0
        )
        report = self.engine.audit_position_pin_risk(pos)
        self.assertFalse(report.is_pin_risk_high)
        self.assertEqual(report.status, "LOW_PIN_RISK_SAFE")
        self.assertEqual(report.recommended_action, "HOLD_TO_EXPIRY")

if __name__ == '__main__':
    unittest.main()
