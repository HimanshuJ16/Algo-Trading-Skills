import unittest
from estimated_tax_payment_scheduling_for_active_trading_income import (
    EstimatedTaxSchedulerEngine, EstimatedTaxScheduleReport
)

class TestEstimatedTaxSchedulerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EstimatedTaxSchedulerEngine(high_agi_threshold_usd=150_000.0)

    def test_high_agi_110pct_safe_harbor_calculation(self):
        # Prior AGI = $200k (> $150k threshold) -> 110% Prior Tax ($50,000 * 1.10 = $55,000)
        # Projected Current Tax = $90,000 (90% = $81,000)
        # Required Annual = min($55,000, $81,000) = $55,000 -> Quarterly = $13,750
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="TRADER_QUANT_01",
            tax_year=2026,
            prior_year_agi_usd=200_000.0,
            prior_year_tax_usd=50_000.0,
            projected_current_year_tax_usd=90_000.0
        )
        
        self.assertEqual(report.applied_safe_harbor_pct, 110.0)
        self.assertEqual(report.safe_harbor_prior_year_usd, 55_000.0)
        self.assertEqual(report.safe_harbor_current_year_90pct_usd, 81_000.0)
        self.assertEqual(report.required_annual_tax_payment_usd, 55_000.0)
        self.assertEqual(report.quarterly_installment_usd, 13_750.0)
        self.assertEqual(len(report.installments), 4)
        self.assertEqual(report.installments[0].due_date_description, "April 15")

    def test_standard_100pct_safe_harbor_calculation(self):
        # Prior AGI = $120k (<= $150k threshold) -> 100% Prior Tax ($30,000 * 1.00 = $30,000)
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="TRADER_QUANT_02",
            tax_year=2026,
            prior_year_agi_usd=120_000.0,
            prior_year_tax_usd=30_000.0,
            projected_current_year_tax_usd=80_000.0
        )
        
        self.assertEqual(report.applied_safe_harbor_pct, 100.0)
        self.assertEqual(report.required_annual_tax_payment_usd, 30_000.0)
        self.assertEqual(report.quarterly_installment_usd, 7_500.0)

if __name__ == '__main__':
    unittest.main()
