import unittest
from currency_gain_loss_tax_treatment_for_forex_trading import (
    ForexTaxTreatmentEngine, ForexTradeRecord, ForexTaxReport
)

class TestForexTaxTreatmentEngine(unittest.TestCase):

    def setUp(self):
        # Ordinary = 37%, LTCG = 20%, STCG = 37% -> Blended Sec 1256 = 0.60*20% + 0.40*37% = 26.8%
        self.engine = ForexTaxTreatmentEngine(ordinary_income_rate=0.37, ltcg_rate=0.20, stcg_rate=0.37)

    def test_profitable_forex_recommends_sec1256_opt_out(self):
        trades = [
            ForexTradeRecord("T1", "EUR/USD", "SPOT_FOREX", 60_000.0, "2025-03-01"),
            ForexTradeRecord("T2", "USD/JPY", "SPOT_FOREX", 40_000.0, "2025-06-01")
        ] # Total PnL = $100,000
        
        report = self.engine.evaluate_forex_tax(trades)
        
        self.assertEqual(report.total_realized_pnl_usd, 100_000.0)
        self.assertEqual(report.sec988_tax_liability_usd, 37_000.0)
        self.assertEqual(report.sec1256_tax_liability_usd, 26_800.0)
        self.assertEqual(report.potential_tax_savings_usd, 10_200.0)
        self.assertEqual(report.recommended_election, "ELECT_SECTION_1256")

    def test_loss_making_forex_recommends_sec988_ordinary_loss(self):
        trades = [
            ForexTradeRecord("T1", "GBP/USD", "SPOT_FOREX", -50_000.0, "2025-04-01")
        ] # Net Loss = -$50,000
        
        report = self.engine.evaluate_forex_tax(trades)
        
        self.assertEqual(report.total_realized_pnl_usd, -50_000.0)
        # Sec 988 provides full ordinary loss deduction: -$50k * 37% = -$18,500 tax benefit
        self.assertEqual(report.sec988_tax_liability_usd, -18_500.0)
        # Sec 1256 caps capital loss deduction at $3,000: -$3,000 * 37% = -$1,110 tax benefit
        self.assertEqual(report.sec1256_tax_liability_usd, -1110.0)
        self.assertEqual(report.recommended_election, "REMAIN_SECTION_988")

if __name__ == '__main__':
    unittest.main()
