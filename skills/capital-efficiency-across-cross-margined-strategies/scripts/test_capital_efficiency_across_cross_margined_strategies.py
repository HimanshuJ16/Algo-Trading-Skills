import unittest
from capital_efficiency_across_cross_margined_strategies import (
    CrossMarginOptimizer,
    Position
)

class TestCrossMarginOptimizer(unittest.TestCase):
    
    def setUp(self):
        self.corr_matrix = {
            "BTC": {"ETH": 0.90, "SOL": 0.70},
            "ETH": {"SOL": 0.80}
        }
        # Haircut of 0.80 means a 0.90 correlation acts as 0.72 for offset
        self.optimizer = CrossMarginOptimizer(self.corr_matrix, correlation_haircut=0.80)

    def test_isolated_margin_no_offset(self):
        # Two longs. They don't offset.
        pos1 = Position("BTC", delta_usd=100000, base_margin_usd=10000)
        pos2 = Position("ETH", delta_usd=50000, base_margin_usd=5000)
        
        report = self.optimizer.calculate_margin([pos1, pos2])
        
        self.assertEqual(report.isolated_margin_usd, 15000)
        self.assertEqual(report.total_offset_usd, 0.0)
        self.assertEqual(report.cross_margin_usd, 15000)
        self.assertEqual(report.capital_efficiency_ratio, 1.0)

    def test_perfect_hedge_high_correlation(self):
        # Long BTC, Short ETH. Highly correlated (0.90)
        pos1 = Position("BTC", delta_usd=100000, base_margin_usd=10000)
        pos2 = Position("ETH", delta_usd=-100000, base_margin_usd=10000)
        
        report = self.optimizer.calculate_margin([pos1, pos2])
        
        self.assertEqual(report.isolated_margin_usd, 20000)
        
        # Max overlap is 10000. 
        # Effective corr = 0.90 * 0.80 = 0.72
        # Offset = 10000 * 0.72 = 7200
        self.assertAlmostEqual(report.total_offset_usd, 7200, places=2)
        
        # Cross Margin = 20000 - 7200 = 12800
        self.assertAlmostEqual(report.cross_margin_usd, 12800, places=2)
        
        # CER = 20000 / 12800 = 1.5625
        self.assertAlmostEqual(report.capital_efficiency_ratio, 1.5625)

    def test_uncorrelated_assets(self):
        # Long BTC, Short GOLD (Correlation not in matrix -> assumed 0.0)
        pos1 = Position("BTC", delta_usd=100000, base_margin_usd=10000)
        pos2 = Position("GOLD", delta_usd=-100000, base_margin_usd=5000)
        
        report = self.optimizer.calculate_margin([pos1, pos2])
        
        self.assertEqual(report.isolated_margin_usd, 15000)
        self.assertEqual(report.total_offset_usd, 0.0)
        self.assertEqual(report.cross_margin_usd, 15000)

if __name__ == '__main__':
    unittest.main()
