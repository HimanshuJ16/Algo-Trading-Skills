import unittest
from multi_jurisdiction_tax_residency_implications import (
    MultiJurisdictionTaxEngine, TaxEntityProfile, CrossBorderTradeIncome, TaxResidencyReport
)

class TestMultiJurisdictionTaxEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MultiJurisdictionTaxEngine()

    def test_dtaa_wht_and_foreign_tax_credit_offset(self):
        # UK Company receiving $100k US dividend income.
        # US Statutory WHT = 30%, US-UK DTAA WHT = 15% ($15,000 paid).
        # UK Corporate Tax = 25% ($25,000 liability).
        # FTC Offset = $15,000. Net UK Tax Payable = $25,000 - $15,000 = $10,000.
        profile = TaxEntityProfile(
            entity_id="UK_TRADING_LTD",
            primary_incorporation_country="UK",
            days_spent_per_country={"UK": 200, "US": 20}
        )

        income = CrossBorderTradeIncome(
            source_country="US",
            destination_country="UK",
            income_type="DIVIDEND",
            gross_income_usd=100000.0,
            statutory_foreign_wht_rate=0.30,
            dtaa_treaty_wht_rate=0.15,
            destination_corp_tax_rate=0.25
        )

        report = self.engine.evaluate_tax_residency(profile, [income])

        self.assertEqual(report.status, "TAX_AUDIT_SUCCESS")
        self.assertEqual(report.foreign_tax_paid_usd, 15000.0)
        self.assertEqual(report.domestic_tax_liability_usd, 25000.0)
        self.assertEqual(report.foreign_tax_credit_usd, 15000.0)
        self.assertEqual(report.net_tax_payable_usd, 10000.0)
        self.assertFalse(report.dual_residency_flag)

    def test_183_day_rule_and_poem_dual_residency(self):
        # Cayman entity with POEM in Singapore and 190 days spent in Singapore -> Dual residency!
        profile = TaxEntityProfile(
            entity_id="CAYMAN_FUND_LTD",
            primary_incorporation_country="CAYMAN",
            days_spent_per_country={"CAYMAN": 30, "SINGAPORE": 190},
            poem_country="SINGAPORE"
        )

        report = self.engine.evaluate_tax_residency(profile, [])

        self.assertTrue(report.dual_residency_flag)
        self.assertTrue(report.poem_exposure_flag)
        self.assertIn("SINGAPORE", report.tax_resident_countries)

if __name__ == '__main__':
    unittest.main()
