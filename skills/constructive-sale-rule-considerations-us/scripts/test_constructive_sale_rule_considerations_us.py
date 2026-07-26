import unittest
from datetime import date
from constructive_sale_rule_considerations_us import (
    ConstructiveSaleRuleEngine, AppreciatedPosition, OffsettingTransaction
)

class TestConstructiveSaleRuleEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ConstructiveSaleRuleEngine()
        # Appreciated position: 1,000 shares, Basis $100/sh ($100k), FMV $250/sh ($250k) -> Gain $150k
        self.appreciated_pos = AppreciatedPosition(
            symbol="NVDA", quantity=1000, cost_basis_per_share=100.0, fair_market_value_per_share=250.0
        )
        # Loss position: 1,000 shares, Basis $200/sh ($200k), FMV $150/sh ($150k) -> Loss -$50k
        self.loss_pos = AppreciatedPosition(
            symbol="BAD_STOCK", quantity=1000, cost_basis_per_share=200.0, fair_market_value_per_share=150.0
        )

    def test_loss_position_not_applicable(self):
        offsetting = OffsettingTransaction(
            transaction_type="SHORT_SALE",
            entry_date=date(2025, 6, 1),
            close_date=None,
            tax_year_end_date=date(2025, 12, 31)
        )
        res = self.engine.evaluate_transaction(self.loss_pos, offsetting)
        self.assertEqual(res.status, "NOT_APPLICABLE")
        self.assertEqual(res.realized_taxable_gain, 0.0)

    def test_safe_harbor_qualified(self):
        # Entry Dec 1, 2025. Closed Jan 15, 2026 (Before Jan 30 deadline). Held unhedged 60+ days.
        offsetting = OffsettingTransaction(
            transaction_type="SHORT_SALE",
            entry_date=date(2025, 12, 1),
            close_date=date(2026, 1, 15),
            tax_year_end_date=date(2025, 12, 31),
            re_hedged_during_60_days=False
        )
        res = self.engine.evaluate_transaction(self.appreciated_pos, offsetting)
        self.assertEqual(res.status, "SAFE_HARBOR_QUALIFIED")
        self.assertEqual(res.realized_taxable_gain, 0.0)

    def test_late_close_triggers_constructive_sale(self):
        # Closed Feb 15, 2026 (Passed Jan 30 deadline) -> Triggers constructive sale on entry date
        offsetting = OffsettingTransaction(
            transaction_type="SHORT_SALE",
            entry_date=date(2025, 12, 1),
            close_date=date(2026, 2, 15),
            tax_year_end_date=date(2025, 12, 31)
        )
        res = self.engine.evaluate_transaction(self.appreciated_pos, offsetting)
        self.assertEqual(res.status, "CONSTRUCTIVE_SALE_TRIGGERED")
        self.assertEqual(res.realized_taxable_gain, 150000.0) # $250k - $100k
        self.assertEqual(res.constructive_sale_date, date(2025, 12, 1))

    def test_rehedged_in_60_days_triggers_constructive_sale(self):
        # Closed Jan 10, 2026, but re-hedged on Day 25 post-close -> Triggers constructive sale
        offsetting = OffsettingTransaction(
            transaction_type="EQUITY_SWAP",
            entry_date=date(2025, 11, 1),
            close_date=date(2026, 1, 10),
            tax_year_end_date=date(2025, 12, 31),
            re_hedged_during_60_days=True
        )
        res = self.engine.evaluate_transaction(self.appreciated_pos, offsetting)
        self.assertEqual(res.status, "CONSTRUCTIVE_SALE_TRIGGERED")
        self.assertEqual(res.realized_taxable_gain, 150000.0)

if __name__ == '__main__':
    unittest.main()
