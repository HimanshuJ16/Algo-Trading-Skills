import unittest
from early_exercise_assignment_risk_management import (
    EarlyExerciseRiskEngine, ShortOptionPosition, EarlyExerciseAuditReport
)

class TestEarlyExerciseRiskEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EarlyExerciseRiskEngine()

    def test_short_call_ex_dividend_early_assignment_risk(self):
        # Short Call: Strike = $100, Underlying = $105, Price = $5.20 -> Intrinsic = $5.00, Extrinsic = $0.20
        # Dividend = $1.00 paid tomorrow -> Div ($1.00) > Extrinsic ($0.20) -> CRITICAL RISK!
        pos = ShortOptionPosition(
            position_id="POS_CALL_01", symbol="AAPL", option_type="CALL", exercise_style="AMERICAN",
            strike=100.0, option_market_price=5.20, underlying_price=105.0, contracts_qty=10,
            days_to_expiry=15.0, upcoming_dividend_usd=1.00, days_to_ex_div=0.5
        )
        report = self.engine.audit_short_position_assignment_risk(pos)
        
        self.assertEqual(report.risk_level, "CRITICAL_ASSIGNMENT_RISK")
        self.assertEqual(report.recommended_action, "CLOSE_OR_ROLL_SHORT_CALL")
        self.assertEqual(report.assignment_probability_pct, 100.0)
        self.assertEqual(report.extrinsic_value_usd, 0.20)

    def test_european_option_safe_from_early_assignment(self):
        # Same parameters but EUROPEAN option style -> LOW RISK!
        pos = ShortOptionPosition(
            position_id="POS_CALL_02", symbol="SPX", option_type="CALL", exercise_style="EUROPEAN",
            strike=100.0, option_market_price=5.20, underlying_price=105.0, contracts_qty=10,
            days_to_expiry=15.0, upcoming_dividend_usd=1.00, days_to_ex_div=0.5
        )
        report = self.engine.audit_short_position_assignment_risk(pos)
        
        self.assertEqual(report.risk_level, "LOW_RISK")
        self.assertEqual(report.assignment_probability_pct, 0.0)

if __name__ == '__main__':
    unittest.main()
