import unittest
from order_book_microstructure_signal_research import (
    OrderBookMicrostructureSignalResearchEngine, OrderBookTick, MicrostructureFeatures, MicrostructureSignalReport
)

class TestOrderBookMicrostructureSignalResearchEngine(unittest.TestCase):

    def setUp(self):
        self.engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=2)

    def test_feature_extraction(self):
        ticks = [
            OrderBookTick(1000, "AAPL", 150.0, 100.0, 150.1, 100.0),
            OrderBookTick(2000, "AAPL", 150.0, 200.0, 150.1, 50.0),   # Bid qty increased (+100), Ask qty decreased (-50) -> OFI = +150
            OrderBookTick(3000, "AAPL", 150.1, 150.0, 150.2, 100.0)
        ]
        feats = self.engine.extract_features(ticks)
        self.assertEqual(len(feats), 3)

        # Tick 1: Bid=100, Ask=50 -> VOI = (200 - 50)/250 = +0.60
        self.assertEqual(feats[1].voi, 0.60)
        self.assertEqual(feats[1].ofi, 150.0) # +100 - (-50) = 150

    def test_signal_efficacy_and_ic(self):
        # Generate 15 ticks where rising OFI leads to rising prices
        ticks = []
        p_bid = 100.0
        p_ask = 100.1
        for i in range(20):
            p_bid += (i * 0.05)
            p_ask += (i * 0.05)
            qty = 100.0 + (i * 50.0) # Rising bid volume
            ticks.append(OrderBookTick(1000 * i, "NVDA", p_bid, qty, p_ask, 50.0))

        report = self.engine.evaluate_signal_efficacy(ticks)
        self.assertEqual(report.status, "PREDICTIVE_ALPHA_FOUND")
        self.assertGreaterEqual(report.ic_ofi_forward_return, 0.05)
        self.assertGreaterEqual(report.hit_ratio_pct, 50.0)

if __name__ == '__main__':
    unittest.main()
