import unittest
from fifo_vs_specific_lot_tax_accounting_methods import (
    TaxLotAccountingEngine, OpenTaxLot, TaxLotAccountingReport
)

class TestTaxLotAccountingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = TaxLotAccountingEngine()
        # Open Tax Lots:
        # Lot A: 100 shares @ $100.00, 400 days old (LTCG)
        # Lot B: 100 shares @ $150.00, 100 days old (STCG)
        # Lot C: 100 shares @ $120.00, 50 days old (STCG)
        self.open_lots = [
            OpenTaxLot("LOT_A", "AAPL", "2025-06-01", 100, 100.0, 400),
            OpenTaxLot("LOT_B", "AAPL", "2026-04-15", 100, 150.0, 100),
            OpenTaxLot("LOT_C", "AAPL", "2026-06-05", 100, 120.0, 50)
        ]

    def test_fifo_matching_strategy(self):
        # FIFO sells Lot A (oldest) first -> 100 shares @ $140.00 vs $100 cost = +$4,000 LTCG
        report = self.engine.process_sell_order(self.open_lots, sale_qty=100, sale_price=140.0, strategy="FIFO")

        self.assertEqual(report.matching_strategy_used, "FIFO")
        self.assertEqual(report.total_realized_gain_loss_usd, 4000.0)
        self.assertEqual(report.total_ltcg_gain_loss_usd, 4000.0)
        self.assertEqual(report.total_stcg_gain_loss_usd, 0.0)
        self.assertEqual(report.matched_lots[0].lot_id, "LOT_A")

    def test_hifo_matching_strategy_minimizes_gains(self):
        # HIFO sells Lot B (highest cost basis $150.00) first -> 100 shares @ $140.00 vs $150 cost = -$1,000 STCG loss!
        report = self.engine.process_sell_order(self.open_lots, sale_qty=100, sale_price=140.0, strategy="HIFO")

        self.assertEqual(report.matching_strategy_used, "HIFO")
        self.assertEqual(report.total_realized_gain_loss_usd, -1000.0)
        self.assertEqual(report.total_stcg_gain_loss_usd, -1000.0)
        self.assertEqual(report.matched_lots[0].lot_id, "LOT_B")

    def test_specific_lot_matching(self):
        # Specific Lot sells Lot C ($120.00) -> 100 shares @ $140.00 vs $120 cost = +$2,000 STCG
        report = self.engine.process_sell_order(self.open_lots, sale_qty=100, sale_price=140.0, strategy="SPECIFIC_LOT", target_lot_ids=["LOT_C"])

        self.assertEqual(report.matching_strategy_used, "SPECIFIC_LOT")
        self.assertEqual(report.total_realized_gain_loss_usd, 2000.0)
        self.assertEqual(report.total_stcg_gain_loss_usd, 2000.0)
        self.assertEqual(report.matched_lots[0].lot_id, "LOT_C")

if __name__ == '__main__':
    unittest.main()
