import unittest
from mark_to_market_election_for_active_traders_us import (
    MarkToMarketTaxEngine, RealizedTrade, TaxLot, MtmTaxReport
)

class TestMarkToMarketTaxEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MarkToMarketTaxEngine(max_capital_loss_deduction_usd=3000.0)

        self.realized_trades = [
            RealizedTrade("T1", "AAPL", "2026-03-15", sell_price=150.0, cost_basis=200.0, quantity=100.0, potential_wash_sale_loss=5000.0) # -$5,000 loss
        ]
        self.open_lots = [
            TaxLot("L1", "NVDA", "2026-11-01", buy_price=100.0, quantity=100.0, year_end_fmv_price=150.0) # +$5,000 MTM gain
        ]

    def test_section_475f_mtm_elected_tax_calculation(self):
        # Realized = -$5,000
        # MTM Open Gain = +$5,000
        # Wash Sales = Exempt ($0.00)
        # Total MTM Ordinary P&L = $0.00 -> Form 4797 Part II!
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True,
            realized_trades=self.realized_trades,
            open_tax_lots=self.open_lots
        )

        self.assertEqual(report.status, "MTM_ORDINARY_TAX_COMPUTED")
        self.assertTrue(report.is_mtm_elected)
        self.assertEqual(report.wash_sale_disallowed_usd, 0.0)
        self.assertEqual(report.unrealized_mtm_pl_usd, 5000.0)
        self.assertEqual(report.total_reportable_taxable_pl_usd, 0.0)
        self.assertEqual(report.tax_form_mapping, "Form 4797 Part II (Ordinary Income)")

    def test_standard_capital_accounting_wash_sale_and_loss_cap(self):
        # Realized = -$5,000, Wash Sale Disallowed = +$5,000 -> Net = $0.00
        # If Realized was -$10,000, Wash sale = $0 -> Net = -$10,000 -> Loss deduction capped at -$3,000 (Schedule D)!
        realized_heavy_loss = [
            RealizedTrade("T2", "TSLA", "2026-05-10", sell_price=100.0, cost_basis=200.0, quantity=100.0, potential_wash_sale_loss=0.0) # -$10,000 loss
        ]
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=False,
            realized_trades=realized_heavy_loss,
            open_tax_lots=self.open_lots
        )

        self.assertEqual(report.status, "CAPITAL_TAX_COMPUTED")
        self.assertFalse(report.is_mtm_elected)
        self.assertEqual(report.unrealized_mtm_pl_usd, 0.0) # MTM open lots ignored!
        self.assertEqual(report.total_reportable_taxable_pl_usd, -10000.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -3000.0) # Capped at -$3,000!
        self.assertEqual(report.tax_form_mapping, "Form 8949 / Schedule D (Capital)")

if __name__ == '__main__':
    unittest.main()
