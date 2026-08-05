import unittest
from external_risk_reporter import (
    InputData, ExternalRiskReporter,
    RiskReportingForExternalStakeholdersEngine, PortfolioRiskState,
    ExternalRiskReport, StakeholderType
)

class TestLegacyExternalRiskReporter(unittest.TestCase):
    def setUp(self):
        self.engine = ExternalRiskReporter()

    def test_process_valid(self):
        data = InputData("test", 10.0)
        self.assertTrue(self.engine.process(data))

    def test_process_invalid(self):
        data = InputData("test", -10.0)
        self.assertFalse(self.engine.process(data))

    def test_history(self):
        data = InputData("test", 10.0)
        self.engine.process(data)
        self.assertEqual(len(self.engine.history), 1)

class TestRiskReportingForExternalStakeholdersEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RiskReportingForExternalStakeholdersEngine()
        self.state = PortfolioRiskState(
            fund_name="ALPHA_QUANT_FUND_LP",
            report_date_iso="2026-08-05",
            total_aum_usd=50000000.0,
            net_asset_value_usd=50000000.0,
            gross_exposure_usd=125000000.0,  # 2.5x gross leverage
            net_exposure_usd=15000000.0,     # 0.3x net leverage
            daily_var_99_pct=1.85,
            annualized_sharpe_ratio=2.1,
            max_drawdown_pct=6.5,
            top_sector_concentrations={"TECH": 25.0, "FINANCE": 15.0, "HEALTHCARE": 10.0, "ENERGY": 8.0, "CRYPTO": 5.0, "MISC": 2.0},
            liquidity_days_to_liquidate_pct={"1_DAY": 85.0, "7_DAYS": 100.0},
            proprietary_positions=[{"symbol": "SECRET_ALPHA_TICKER", "qty": 10000}]
        )

    def test_lp_report_generation_with_redaction(self):
        report = self.engine.generate_external_report(self.state, StakeholderType.LIMITED_PARTNER)
        self.assertEqual(report.stakeholder_type, StakeholderType.LIMITED_PARTNER)
        self.assertEqual(report.gross_leverage, 2.5)
        self.assertEqual(report.net_leverage, 0.3)
        self.assertEqual(report.daily_var_99_pct, 1.85)
        self.assertTrue(report.are_proprietary_positions_redacted)
        self.assertIsNotNone(report.report_signature)
        self.assertEqual(len(report.disclosed_concentrations), 5)  # LP gets top 5

    def test_regulator_report_generation(self):
        report = self.engine.generate_external_report(self.state, StakeholderType.REGULATOR)
        self.assertEqual(report.stakeholder_type, StakeholderType.REGULATOR)
        self.assertEqual(len(report.disclosed_concentrations), 6)  # Regulator gets full list
        self.assertTrue(report.are_proprietary_positions_redacted)

if __name__ == '__main__':
    unittest.main()
