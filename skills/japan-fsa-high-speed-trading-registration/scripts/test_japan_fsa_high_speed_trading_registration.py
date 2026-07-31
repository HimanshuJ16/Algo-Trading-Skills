import unittest
from japan_fsa_high_speed_trading_registration import (
    JapanFsaHstComplianceEngine, JapanFsaHstTraderSpec, JapanFsaHstReport
)

class TestJapanFsaHstComplianceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = JapanFsaHstComplianceEngine(max_order_value_limit_jpy=100_000_000.0)

    def test_registered_fsa_hst_trader_approval(self):
        spec = JapanFsaHstTraderSpec(
            trader_id="HFT_ALPHA_TOKYO_01",
            fsa_hst_reg_id="KLFB_No_3120",
            is_registered_with_fsa=True,
            is_algo_automated=True,
            is_colocated=True,
            latency_ms=2.0,
            order_value_jpy=50_000_000.0,
            has_kill_switch_enabled=True,
            has_resident_compliance_manager=True
        )
        report = self.engine.audit_japan_fsa_hst_trader(spec)

        self.assertEqual(report.status, "FSA_HST_APPROVED")
        self.assertTrue(report.is_hst_classified)
        self.assertTrue(report.is_fsa_registered)
        self.assertTrue(report.is_kill_switch_active)

    def test_unregistered_hst_trader_rejection(self):
        # Co-located automated HFT but is_registered_with_fsa = False -> REJECTED_UNREGISTERED_HST!
        spec = JapanFsaHstTraderSpec(
            trader_id="ROGUE_HFT",
            fsa_hst_reg_id="",
            is_registered_with_fsa=False,
            is_algo_automated=True,
            is_colocated=True,
            latency_ms=1.5,
            order_value_jpy=10_000_000.0,
            has_kill_switch_enabled=True,
            has_resident_compliance_manager=False
        )
        report = self.engine.audit_japan_fsa_hst_trader(spec)

        self.assertEqual(report.status, "REJECTED_UNREGISTERED_HST")
        self.assertTrue(report.is_hst_classified)
        self.assertFalse(report.is_fsa_registered)

    def test_missing_kill_switch_rejection(self):
        # has_kill_switch_enabled = False -> REJECTED_MISSING_KILL_SWITCH!
        spec = JapanFsaHstTraderSpec(
            trader_id="HFT_ALPHA_TOKYO_01",
            fsa_hst_reg_id="KLFB_No_3120",
            is_registered_with_fsa=True,
            is_algo_automated=True,
            is_colocated=True,
            latency_ms=2.0,
            order_value_jpy=50_000_000.0,
            has_kill_switch_enabled=False,
            has_resident_compliance_manager=True
        )
        report = self.engine.audit_japan_fsa_hst_trader(spec)

        self.assertEqual(report.status, "REJECTED_MISSING_KILL_SWITCH")
        self.assertFalse(report.is_kill_switch_active)

if __name__ == '__main__':
    unittest.main()
