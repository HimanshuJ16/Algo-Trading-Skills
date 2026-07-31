import unittest
from new_strategy_onboarding_checklist import (
    NewStrategyOnboardingEngine, StrategyOnboardingPayload, OnboardingPolicyConfig, OnboardingAuditReport
)

class TestNewStrategyOnboardingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = NewStrategyOnboardingEngine()

    def test_fully_compliant_strategy_onboarding_passed(self):
        # Fully compliant payload
        payload = StrategyOnboardingPayload(
            strategy_id="STAT_ARB_v2",
            strategy_name="Statistical Arbitrage Equity",
            author="Quant Team A",
            walk_forward_score=0.82,
            regimes_covered=4,
            backtest_sharpe=2.1,
            paper_trading_days=20,
            paper_trading_errors=0,
            kill_switch_integrated=True,
            model_card_completed=True,
            compliance_approved=True
        )

        report = self.engine.audit_strategy_onboarding(payload)

        self.assertTrue(report.is_onboarding_approved)
        self.assertEqual(report.status, "ONBOARDING_PASSED")
        self.assertEqual(report.total_gates_passed, 4)
        self.assertEqual(report.total_gates_count, 4)

    def test_non_compliant_strategy_onboarding_rejected(self):
        # Failing paper trading days (5 < 14) and compliance sign-off missing -> ONBOARDING_REJECTED!
        payload = StrategyOnboardingPayload(
            strategy_id="TREND_CTA_v1",
            strategy_name="CTA Trend Following",
            author="Quant Team B",
            walk_forward_score=0.75,
            regimes_covered=3,
            backtest_sharpe=1.8,
            paper_trading_days=5,               # FAIL (< 14)
            paper_trading_errors=0,
            kill_switch_integrated=True,
            model_card_completed=True,
            compliance_approved=False          # FAIL
        )

        report = self.engine.audit_strategy_onboarding(payload)

        self.assertFalse(report.is_onboarding_approved)
        self.assertEqual(report.status, "ONBOARDING_REJECTED")
        self.assertEqual(report.total_gates_passed, 2)
        self.assertIn("OPERATIONAL_GATE", report.audit_notes)
        self.assertIn("COMPLIANCE_GATE", report.audit_notes)

if __name__ == '__main__':
    unittest.main()
