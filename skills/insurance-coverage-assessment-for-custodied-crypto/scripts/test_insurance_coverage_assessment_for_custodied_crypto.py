import unittest
from insurance_coverage_assessment_for_custodied_crypto import (
    CustodyInsuranceAssessmentEngine, CustodyInsuranceSpec, CustodyInsuranceReport
)

class TestCustodyInsuranceAssessmentEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CustodyInsuranceAssessmentEngine()

    def test_pooled_dilution_and_shortfall_calculation(self):
        # Firm Hot AUM = $2M, Crime Limit = $5M -> 100% Hot Covered
        # Firm Cold AUM = $20M, Specie Limit = $250M, Total Custodian Cold AUM = $1B -> Dilution = 25%
        # Effective Cold Covered = $20M * 0.25 = $5M (25% covered)
        # Total Insured = $2M + $5M = $7M / $22M = 31.82% insured
        # Uninsured Shortfall = $15M
        spec = CustodyInsuranceSpec(
            custodian_name="BitGo Custody",
            firm_hot_wallet_aum_usd=2_000_000.0,
            firm_cold_wallet_aum_usd=20_000_000.0,
            hot_crime_policy_limit_usd=5_000_000.0,
            cold_specie_policy_limit_usd=250_000_000.0,
            total_custodian_cold_aum_usd=1_000_000_000.0
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.status, "PARTIALLY_INSURED_SHORTFALL")
        self.assertEqual(report.hot_wallet_coverage_pct, 100.0)
        self.assertEqual(report.cold_wallet_pro_rata_dilution_pct, 25.0)
        self.assertEqual(report.cold_wallet_effective_coverage_usd, 5_000_000.0)
        self.assertEqual(report.total_uninsured_shortfall_usd, 15_000_000.0)

    def test_critical_hot_wallet_uninsured_warning(self):
        # Hot AUM = $10M, Crime Limit = $2M -> Only 20% covered -> CRITICAL!
        spec = CustodyInsuranceSpec(
            custodian_name="Hot Wallet Custodian",
            firm_hot_wallet_aum_usd=10_000_000.0,
            firm_cold_wallet_aum_usd=0.0,
            hot_crime_policy_limit_usd=2_000_000.0,
            cold_specie_policy_limit_usd=100_000_000.0,
            total_custodian_cold_aum_usd=100_000_000.0
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.status, "CRITICAL_HOT_WALLET_UNINSURED")
        self.assertEqual(report.hot_wallet_coverage_pct, 20.0)

if __name__ == '__main__':
    unittest.main()
