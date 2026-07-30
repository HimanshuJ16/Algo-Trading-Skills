import unittest
from dark_pool_routing_logic import (
    DarkPoolRoutingEngine, DarkPoolVenueProfile, DarkPoolRoutingReport
)

class TestDarkPoolRoutingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DarkPoolRoutingEngine(max_acceptable_toxicity_bps=5.0, default_min_qty=200)

        # 3 Dark Pools
        # Pool A: Good fill rate (40%), Low toxicity (1.0 bps) -> Eligible
        # Pool B: High fill rate (50%), High toxicity (8.0 bps > 5.0bps limit) -> Toxic, Excluded!
        # Pool C: Low fill rate (30%), Low toxicity (0.5 bps) -> Eligible
        self.engine.register_venue(DarkPoolVenueProfile("UBS_PIN", "UBS PIN", historical_fill_rate=0.40, toxicity_score_bps=1.0, min_qty_threshold=100))
        self.engine.register_venue(DarkPoolVenueProfile("SIGMA_X", "GS Sigma X", historical_fill_rate=0.50, toxicity_score_bps=8.0, min_qty_threshold=100))
        self.engine.register_venue(DarkPoolVenueProfile("CROSSFINDER", "CS Crossfinder", historical_fill_rate=0.30, toxicity_score_bps=0.5, min_qty_threshold=100))

    def test_routing_excludes_toxic_venue_and_attaches_min_qty(self):
        report = self.engine.route_parent_order("AAPL", "BUY", total_quantity=10_000)
        
        self.assertEqual(report.total_parent_quantity, 10_000)
        self.assertEqual(len(report.child_directives), 2)
        
        # Verify GS Sigma X was excluded due to toxicity
        venue_ids = [d.venue_id for d in report.child_directives]
        self.assertNotIn("SIGMA_X", venue_ids)
        self.assertIn("UBS_PIN", venue_ids)
        self.assertIn("CROSSFINDER", venue_ids)
        
        # Verify MinQty instruction is attached to prevent pinging
        for child in report.child_directives:
            self.assertGreaterEqual(child.min_qty_instruction, 200)

if __name__ == '__main__':
    unittest.main()
