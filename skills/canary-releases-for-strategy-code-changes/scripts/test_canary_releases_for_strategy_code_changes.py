import unittest
from canary_releases_for_strategy_code_changes import (
    StrategyCanaryRouter,
    StrategyRegistration,
    DeploymentPhase,
    OrderSignal
)

class TestCanaryReleases(unittest.TestCase):
    
    def setUp(self):
        self.router = StrategyCanaryRouter()
        
    def test_shadow_mode_drops_order(self):
        self.router.register_strategy(StrategyRegistration("strat_alpha", DeploymentPhase.SHADOW))
        
        signal = OrderSignal("strat_alpha", "AAPL", 1000, 150.0, "BUY")
        routed = self.router.route_order(signal)
        
        # Shadow should return None (dropped)
        self.assertIsNone(routed)

    def test_canary_mode_scales_correctly(self):
        self.router.register_strategy(
            StrategyRegistration("strat_beta", DeploymentPhase.CANARY, canary_scale_factor=0.10, min_lot_size=1)
        )
        
        signal = OrderSignal("strat_beta", "AAPL", 1000, 150.0, "BUY")
        routed = self.router.route_order(signal)
        
        # 10% of 1000 = 100
        self.assertIsNotNone(routed)
        self.assertEqual(routed.quantity, 100)

    def test_canary_mode_lot_rounding(self):
        # 5% scale, but min lot is 100
        self.router.register_strategy(
            StrategyRegistration("strat_gamma", DeploymentPhase.CANARY, canary_scale_factor=0.05, min_lot_size=100)
        )
        
        # 5% of 2500 is 125. Nearest floored lot of 100 is 100.
        signal = OrderSignal("strat_gamma", "AAPL", 2500, 150.0, "BUY")
        routed = self.router.route_order(signal)
        
        self.assertIsNotNone(routed)
        self.assertEqual(routed.quantity, 100)
        
    def test_canary_mode_lot_drops_if_too_small(self):
        self.router.register_strategy(
            StrategyRegistration("strat_gamma", DeploymentPhase.CANARY, canary_scale_factor=0.05, min_lot_size=100)
        )
        
        # 5% of 1000 is 50. Floor nearest 100 is 0. Should drop.
        signal = OrderSignal("strat_gamma", "AAPL", 1000, 150.0, "BUY")
        routed = self.router.route_order(signal)
        
        self.assertIsNone(routed)

    def test_production_mode_full_routing(self):
        self.router.register_strategy(StrategyRegistration("strat_delta", DeploymentPhase.PRODUCTION))
        
        signal = OrderSignal("strat_delta", "AAPL", 1000, 150.0, "BUY")
        routed = self.router.route_order(signal)
        
        self.assertIsNotNone(routed)
        self.assertEqual(routed.quantity, 1000)

if __name__ == '__main__':
    unittest.main()
