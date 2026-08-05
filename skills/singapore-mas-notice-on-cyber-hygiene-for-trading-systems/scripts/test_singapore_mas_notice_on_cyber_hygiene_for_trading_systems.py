import unittest
from singapore_mas_notice_on_cyber_hygiene_for_trading_systems import (
    ComplianceChecker, ComplianceRecord,
    SingaporeMASCyberHygieneEngine, TradingSystemAsset,
    MASCyberHygieneRequirement, MASCyberHygieneAuditReport
)

class TestComplianceLegacy(unittest.TestCase):
    def setUp(self):
        self.checker = ComplianceChecker()

    def test_single_check(self):
        res = self.checker.check_compliance("T1")
        self.assertTrue(res.is_compliant)

    def test_batch_check(self):
        res = self.checker.batch_check(["T1", "T2"])
        self.assertEqual(len(res), 2)
        self.assertTrue(all(r.is_compliant for r in res))

class TestSingaporeMASCyberHygieneEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SingaporeMASCyberHygieneEngine()

    def test_fully_compliant_trading_asset(self):
        asset = TradingSystemAsset(
            system_id="SYS_ORDER_ROUTER_01",
            system_name="SGX FIX Order Router",
            asset_type="ORDER_ROUTER",
            has_mfa_enabled=True,
            admin_accounts_restricted=True,
            critical_patches_applied_within_30d=True,
            baseline_security_hardened=True,
            network_perimeter_firewalled=True,
            anti_malware_active=True
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.compliance_score_pct, 100.0)
        self.assertEqual(len(report.failed_requirements), 0)

    def test_non_compliant_asset_missing_mfa_and_patches(self):
        asset = TradingSystemAsset(
            system_id="SYS_TRADING_DB_02",
            system_name="Market Data DB Host",
            asset_type="TRADING_DB",
            has_mfa_enabled=False,                       # Failed
            admin_accounts_restricted=True,
            critical_patches_applied_within_30d=False,   # Failed
            baseline_security_hardened=True,
            network_perimeter_firewalled=True,
            anti_malware_active=True
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.compliance_score_pct, 66.67) # 4/6 = 66.67%
        self.assertIn(MASCyberHygieneRequirement.MULTI_FACTOR_AUTH, report.failed_requirements)
        self.assertIn(MASCyberHygieneRequirement.SECURITY_PATCH_MANAGEMENT, report.failed_requirements)
        self.assertEqual(len(report.mandatory_remediations), 2)

if __name__ == '__main__':
    unittest.main()
