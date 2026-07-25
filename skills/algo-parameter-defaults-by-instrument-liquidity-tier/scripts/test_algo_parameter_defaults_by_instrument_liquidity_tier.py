import unittest
from algo_parameter_defaults_by_instrument_liquidity_tier import (
    ExecutionParameterManager, 
    LiquidityTier
)

class TestExecutionParameterManager(unittest.TestCase):
    def setUp(self):
        self.manager = ExecutionParameterManager(
            high_adv_threshold=500_000.0, 
            medium_adv_threshold=50_000.0
        )

    def test_high_liquidity_assignment(self):
        profile = self.manager.get_profile(1_000_000.0)
        self.assertEqual(profile.tier, LiquidityTier.HIGH)
        self.assertEqual(profile.default_algo_type, "TWAP")
        self.assertTrue(profile.cross_spread_allowed)
        self.assertEqual(profile.max_participation_rate, 0.05)

    def test_medium_liquidity_assignment(self):
        profile = self.manager.get_profile(100_000.0)
        self.assertEqual(profile.tier, LiquidityTier.MEDIUM)
        self.assertEqual(profile.default_algo_type, "VWAP")
        self.assertFalse(profile.cross_spread_allowed)

    def test_low_liquidity_assignment(self):
        profile = self.manager.get_profile(10_000.0)
        self.assertEqual(profile.tier, LiquidityTier.LOW)
        self.assertEqual(profile.default_algo_type, "IS")
        self.assertFalse(profile.cross_spread_allowed)
        self.assertEqual(profile.passive_buffer_bps, 20.0)

if __name__ == '__main__':
    unittest.main()
