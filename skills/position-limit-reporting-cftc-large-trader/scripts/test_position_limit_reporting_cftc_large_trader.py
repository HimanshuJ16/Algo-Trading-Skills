import unittest
from position_limit_reporting_cftc_large_trader import (
    ComplianceChecker, ComplianceRecord, Config,
    PositionLimitReportingCFTCLargeTraderEngine,
    TraderAccountPosition, CFTCLimitSpec, CFTCLargeTraderReport
)

class TestPositionLimitReportingCFTCLargeTrader(unittest.TestCase):

    def setUp(self):
        self.checker = ComplianceChecker()
        self.config = Config(enabled=True)
        self.engine = PositionLimitReportingCFTCLargeTraderEngine(self.config)

    def test_single_check_legacy(self):
        res = self.checker.check_compliance("T1")
        self.assertTrue(res.is_compliant)

    def test_batch_check_legacy(self):
        res = self.checker.batch_check(["T1", "T2"])
        self.assertEqual(len(res), 2)
        self.assertTrue(all(r.is_compliant for r in res))

    def test_form_102a_reportable_trigger(self):
        # 2 accounts for ACME_FUND holding 200 contracts each in Crude Oil CL -> Aggregated 400 contracts
        # LTR Threshold = 350 contracts -> FORM_102A_REPORTABLE!
        positions = [
            TraderAccountPosition("ACC_1", "ACME_FUND", "CL", net_position=200.0, long_position=200.0),
            TraderAccountPosition("ACC_2", "ACME_FUND", "CL", net_position=200.0, long_position=200.0),
        ]
        spec = CFTCLimitSpec(commodity_code="CL", reporting_threshold_contracts=350.0, federal_speculative_limit=10000.0)

        report = self.engine.evaluate_entity_cftc_compliance("ACME_FUND", positions, spec)
        self.assertEqual(report.status, "FORM_102A_REPORTABLE")
        self.assertTrue(report.is_reportable)
        self.assertFalse(report.is_limit_breached)

    def test_speculative_limit_breach(self):
        # Position 12,000 contracts vs 10,000 limit -> SPECULATIVE_LIMIT_BREACHED!
        positions = [
            TraderAccountPosition("ACC_1", "APEX_CAPITAL", "CL", net_position=12000.0, long_position=12000.0)
        ]
        spec = CFTCLimitSpec(commodity_code="CL", reporting_threshold_contracts=350.0, federal_speculative_limit=10000.0)

        report = self.engine.evaluate_entity_cftc_compliance("APEX_CAPITAL", positions, spec)
        self.assertEqual(report.status, "SPECULATIVE_LIMIT_BREACHED")
        self.assertTrue(report.is_reportable)
        self.assertTrue(report.is_limit_breached)

if __name__ == '__main__':
    unittest.main()
