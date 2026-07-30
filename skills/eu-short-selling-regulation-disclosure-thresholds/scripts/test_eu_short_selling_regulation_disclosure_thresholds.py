import unittest
from eu_short_selling_regulation_disclosure_thresholds import (
    EuShortSellingRegulationEngine, EquityShortPositionState, EuSsrDisclosureReport
)

class TestEuShortSellingRegulationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EuShortSellingRegulationEngine(private_nca_threshold_pct=0.10, public_disclosure_threshold_pct=0.50)

    def test_public_disclosure_required_at_0_60_percent(self):
        # Issued = 100,000,000 shares. Net Short = 600,000 shares (0.60% >= 0.50% Public Disclosure Threshold!)
        pos = EquityShortPositionState(
            isin="DE0007100000", symbol="MBG",
            issued_share_capital_qty=100_000_000,
            long_shares_qty=0,
            short_shares_qty=600_000,
            has_valid_locate_agreement=True
        )
        report = self.engine.evaluate_short_position_disclosure(pos)
        
        self.assertTrue(report.is_short_execution_allowed)
        self.assertEqual(report.reporting_status, "PUBLIC_DISCLOSURE_REQUIRED")
        self.assertEqual(report.net_short_percentage, 0.60)
        self.assertEqual(report.reporting_deadline_description, "T+1 15:30 CET")

    def test_private_nca_notification_at_0_20_percent(self):
        # Net Short = 200,000 shares (0.20% >= 0.10% Private NCA Notification Threshold)
        pos = EquityShortPositionState(
            isin="DE0007100000", symbol="MBG",
            issued_share_capital_qty=100_000_000,
            long_shares_qty=0,
            short_shares_qty=200_000,
            has_valid_locate_agreement=True
        )
        report = self.engine.evaluate_short_position_disclosure(pos)
        
        self.assertEqual(report.reporting_status, "PRIVATE_NCA_NOTIFICATION_REQUIRED")
        self.assertEqual(report.net_short_percentage, 0.20)

    def test_naked_short_ban_breach_blocked(self):
        # Short position without valid locate agreement -> NAKED SHORT BAN BREACH!
        pos = EquityShortPositionState(
            isin="DE0007100000", symbol="MBG",
            issued_share_capital_qty=100_000_000,
            long_shares_qty=0,
            short_shares_qty=200_000,
            has_valid_locate_agreement=False          # Missing locate!
        )
        report = self.engine.evaluate_short_position_disclosure(pos)
        
        self.assertFalse(report.is_short_execution_allowed)
        self.assertEqual(report.reporting_status, "NAKED_SHORT_BAN_BREACH")

if __name__ == '__main__':
    unittest.main()
