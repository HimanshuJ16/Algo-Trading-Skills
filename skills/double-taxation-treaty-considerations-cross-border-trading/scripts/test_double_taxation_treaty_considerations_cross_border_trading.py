import unittest
from double_taxation_treaty_considerations_cross_border_trading import (
    DoubleTaxationTreatyEngine, DttTreatySpec, CrossBorderIncomePayment, DoubleTaxationAuditReport
)

class TestDoubleTaxationTreatyEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DoubleTaxationTreatyEngine()
        
        # US-UK DTT: Statutory = 30%, Treaty = 15% with Form W-8BEN-E
        self.engine.register_treaty(DttTreatySpec(
            residence_country="UK",
            source_country="US",
            statutory_wht_pct=0.30,
            treaty_wht_pct=0.15,
            required_documentation="Form W-8BEN-E"
        ))

    def test_dtt_treaty_reduces_wht_and_saves_tax_leakage(self):
        # $100,000 dividend from US company to UK fund with W-8BEN-E
        payment = CrossBorderIncomePayment(
            payment_id="PMT_01", residence_country="UK", source_country="US",
            income_type="EQUITY_DIVIDEND", gross_income_usd=100_000.0,
            has_valid_tax_documentation=True, resident_country_effective_tax_rate=0.25
        )
        report = self.engine.evaluate_cross_border_payment(payment)
        
        self.assertEqual(report.applied_wht_pct, 0.15)
        self.assertEqual(report.wht_tax_paid_usd, 15_000.0)
        self.assertEqual(report.statutory_wht_usd, 30_000.0)
        self.assertEqual(report.wht_tax_leakage_saved_usd, 15_000.0)
        self.assertEqual(report.eligible_foreign_tax_credit_usd, 15_000.0)

    def test_missing_documentation_falls_back_to_statutory_wht(self):
        # Missing W-8BEN-E -> Fallback to 30% statutory WHT
        payment = CrossBorderIncomePayment(
            payment_id="PMT_02", residence_country="UK", source_country="US",
            income_type="EQUITY_DIVIDEND", gross_income_usd=100_000.0,
            has_valid_tax_documentation=False
        )
        report = self.engine.evaluate_cross_border_payment(payment)
        
        self.assertEqual(report.applied_wht_pct, 0.30)
        self.assertEqual(report.wht_tax_paid_usd, 30_000.0)
        self.assertEqual(report.wht_tax_leakage_saved_usd, 0.0)
        self.assertIn("MISSING TAX DOCUMENTATION", report.applied_treaty_notes)

if __name__ == '__main__':
    unittest.main()
