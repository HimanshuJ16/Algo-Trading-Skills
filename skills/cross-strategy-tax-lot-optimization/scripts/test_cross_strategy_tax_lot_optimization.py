import unittest
from cross_strategy_tax_lot_optimization import (
    CrossStrategyTaxLotOptimizer, TaxLot, TaxOptimizationResult
)

class TestCrossStrategyTaxLotOptimizer(unittest.TestCase):

    def setUp(self):
        self.optimizer = CrossStrategyTaxLotOptimizer(wash_sale_window_days=30)
        
        # 3 Tax Lots for AAPL
        # Lot A: $150 basis, 400 days old (LTCG)
        # Lot B: $200 basis, 100 days old (STCG / High Basis for Loss)
        # Lot C: $100 basis, 50 days old (STCG / Low Basis)
        self.optimizer.add_tax_lot(TaxLot("LOT_A", "StatArb", "AAPL", "2025-01-01", days_held=400, cost_basis_per_share=150.0, quantity=100.0))
        self.optimizer.add_tax_lot(TaxLot("LOT_B", "TrendFollow", "AAPL", "2025-10-01", days_held=100, cost_basis_per_share=200.0, quantity=100.0))
        self.optimizer.add_tax_lot(TaxLot("LOT_C", "AltData", "AAPL", "2025-12-01", days_held=50, cost_basis_per_share=100.0, quantity=100.0))

    def test_hifo_min_tax_selection(self):
        # Current Market Price = $180
        # HIFO should select LOT_B first ($200 basis), realizing a $20/share loss ($2,000 total loss)
        res = self.optimizer.optimize_sell_order("AAPL", sell_quantity=100.0, current_market_price=180.0, method="HIFO_MIN_TAX")
        
        self.assertEqual(len(res.executed_lots), 1)
        executed = res.executed_lots[0]
        self.assertEqual(executed.lot_id, "LOT_B")
        self.assertEqual(executed.realized_gain_loss_usd, -2000.0) # $180 - $200 = -$20 * 100
        self.assertFalse(executed.is_wash_sale_triggered)

    def test_wash_sale_interception(self):
        # Pod_Gamma bought AAPL 10 days ago
        self.optimizer.register_recent_buy("AAPL", "Pod_Gamma", days_ago=10)
        
        # Sell 100 shares via HIFO (triggers LOT_B loss)
        res = self.optimizer.optimize_sell_order("AAPL", sell_quantity=100.0, current_market_price=180.0, method="HIFO_MIN_TAX")
        
        self.assertTrue(res.wash_sale_warning)
        self.assertTrue(res.executed_lots[0].is_wash_sale_triggered)

if __name__ == '__main__':
    unittest.main()
