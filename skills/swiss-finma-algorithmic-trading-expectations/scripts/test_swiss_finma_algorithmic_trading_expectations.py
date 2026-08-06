import unittest
from swiss_finma_algorithmic_trading_expectations import (
    Config, Engine,
    ComplianceChecker, SwissFINMAComplianceEngine,
    AlgoTradingSystemAuditSpec, ComplianceRecord
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")

    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())

class TestCompliance(unittest.TestCase):
    def setUp(self):
        self.checker = ComplianceChecker()
        self.finma_engine = SwissFINMAComplianceEngine()

    def test_single_check(self):
        res = self.checker.check_compliance("T1")
        self.assertTrue(res.is_compliant)

    def test_batch_check(self):
        res = self.checker.batch_check(["T1", "T2"])
        self.assertEqual(len(res), 2)
        self.assertTrue(all(r.is_compliant for r in res))

    def test_finma_audit_fully_compliant(self):
        spec = AlgoTradingSystemAuditSpec(
            algo_id="CH_HFT_SWAP_01",
            strategy_version="2.1.0",
            governance_owner="Chief Risk Officer",
            has_pre_trade_risk_limits=True,
            has_independent_kill_switch=True,
            has_algo_inventory_registration=True,
            max_message_rate_per_sec=50,
            has_microsecond_audit_trail=True
        )
        record = self.finma_engine.audit_algo_system(spec)
        self.assertTrue(record.is_compliant)
        self.assertEqual(record.finma_score_pct, 100.0)
        self.assertEqual(len(record.failed_controls), 0)

    def test_finma_audit_missing_kill_switch_and_audit_trail(self):
        spec = AlgoTradingSystemAuditSpec(
            algo_id="CH_BAD_BOT",
            strategy_version="0.9",
            governance_owner="Unassigned",
            has_pre_trade_risk_limits=True,
            has_independent_kill_switch=False, # Missing kill switch!
            has_algo_inventory_registration=True,
            max_message_rate_per_sec=150,     # Exceeds 100 msgs/s
            has_microsecond_audit_trail=False  # Missing microsecond timestamps
        )
        record = self.finma_engine.audit_algo_system(spec)
        self.assertFalse(record.is_compliant)
        self.assertEqual(record.finma_score_pct, 40.0)
        self.assertEqual(len(record.failed_controls), 3)

if __name__ == '__main__':
    unittest.main()
