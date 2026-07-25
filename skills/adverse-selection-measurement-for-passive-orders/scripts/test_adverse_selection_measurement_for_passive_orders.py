import unittest
from adverse_selection_measurement_for_passive_orders import MarkoutConfig, MarkoutEngine, PassiveFill

class TestAdverseSelectionMeasurement(unittest.TestCase):
    def setUp(self):
        self.config = MarkoutConfig(horizons_sec=[1.0, 5.0])
        self.engine = MarkoutEngine(self.config)
        
        # Simulate market data: 
        # T=0 (100), T=1 (99), T=5 (98) - Price is dropping steadily
        self.market_timestamps = [0.0, 1.0, 5.0]
        self.market_mids = [100.0, 99.0, 98.0]

    def test_toxic_buy_order(self):
        # We passively BUY at T=0 for 100.0. 
        # Since price drops to 99 and then 98, this is toxic adverse selection.
        fills = [PassiveFill("1", 0.0, "BUY", 100.0, 100)]
        
        report = self.engine.evaluate_fills(fills, self.market_timestamps, self.market_mids)
        
        self.assertTrue(report.is_toxic)
        # Horizon 1.0s: (99/100 - 1) * 10000 = -100 bps
        self.assertEqual(report.average_markouts_bps[1.0], -100.0)
        # Horizon 5.0s: (98/100 - 1) * 10000 = -200 bps
        self.assertEqual(report.average_markouts_bps[5.0], -200.0)

    def test_profitable_sell_order(self):
        # We passively SELL at T=0 for 100.0.
        # Since price drops to 99 and 98, this is highly profitable (good markout).
        fills = [PassiveFill("2", 0.0, "SELL", 100.0, 100)]
        
        report = self.engine.evaluate_fills(fills, self.market_timestamps, self.market_mids)
        
        self.assertFalse(report.is_toxic)
        # Horizon 1.0s: (100/99 - 1) * 10000 ≈ +101.01 bps
        self.assertAlmostEqual(report.average_markouts_bps[1.0], 101.01, places=1)
        # Horizon 5.0s: (100/98 - 1) * 10000 ≈ +204.08 bps
        self.assertAlmostEqual(report.average_markouts_bps[5.0], 204.08, places=1)

if __name__ == '__main__':
    unittest.main()
