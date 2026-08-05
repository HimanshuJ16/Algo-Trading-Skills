import unittest
from section_1256_contract_tax_treatment_us_futures import (
    Section1256ContractTaxTreatmentUsFuturesEngine, Record,
    Section1256Trade, Section1256ContractType, Form6781TaxSummary
)

class TestSection1256Legacy(unittest.TestCase):
    def test_initialization(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

class TestSection1256EngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = Section1256ContractTaxTreatmentUsFuturesEngine()

    def test_section_1256_60_40_split_calculation(self):
        # Trade 1: CME E-mini Futures ($100,000 realized PnL)
        self.engine.add_trade(Section1256Trade(
            trade_id="TRD_001",
            symbol="ES",
            contract_type=Section1256ContractType.REGULATED_FUTURES,
            realized_pnl_usd=100000.0
        ))
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.net_section_1256_pnl_usd, 100000.0)
        self.assertEqual(summary.long_term_portion_60_pct_usd, 60000.0)  # 60% = $60k
        self.assertEqual(summary.short_term_portion_40_pct_usd, 40000.0) # 40% = $40k
        # Tax: (60k * 0.20) + (40k * 0.37) = 12k + 14.8k = $26,800 (vs $37,000 for standard short term)
        self.assertEqual(summary.estimated_blended_tax_usd, 26800.0)
        self.assertEqual(summary.tax_savings_vs_short_term_usd, 10200.0) # $10,200 savings!

    def test_year_end_mark_to_market_unrealized(self):
        # SPX Index option open at year end with +$50,000 unrealized MTM gain
        self.engine.add_trade(Section1256Trade(
            trade_id="TRD_002",
            symbol="SPX",
            contract_type=Section1256ContractType.BROAD_INDEX_OPTIONS,
            realized_pnl_usd=20000.0,
            unrealized_mtm_pnl_usd=30000.0,
            is_open_at_year_end=True
        ))
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.net_section_1256_pnl_usd, 50000.0) # $20k + $30k MTM
        self.assertEqual(summary.long_term_portion_60_pct_usd, 30000.0)
        self.assertEqual(summary.short_term_portion_40_pct_usd, 20000.0)

    def test_non_qualifying_single_stock_ignored(self):
        # AAPL single stock option trade is excluded from Section 1256
        self.engine.add_trade(Section1256Trade(
            trade_id="TRD_003",
            symbol="AAPL",
            contract_type=Section1256ContractType.NON_QUALIFYING_SINGLE_STOCK,
            realized_pnl_usd=50000.0
        ))
        summary = self.engine.generate_form_6781_summary()
        self.assertEqual(summary.net_section_1256_pnl_usd, 0.0) # Excluded!

if __name__ == '__main__':
    unittest.main()
