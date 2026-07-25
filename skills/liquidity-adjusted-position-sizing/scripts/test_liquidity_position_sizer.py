"""Unit tests for liquidity-adjusted-position-sizing."""
import unittest
from liquidity_position_sizer import LiquidityPositionSizer


class TestLiquidityPositionSizer(unittest.TestCase):
    def setUp(self):
        self.sizer = LiquidityPositionSizer(max_participation_pct=10.0, max_dtl_days=1.0)

    def test_illiquid_asset_position_is_capped(self):
        # Target $500,000 on $10 stock with 100,000 ADV (ADV value = $1M)
        # Max allowed @ 10% ADV for 1 DTL = 10,000 shares ($100,000)
        res = self.sizer.calculate_size("ILLIQ", target_capital_usd=500000.0, price=10.0, adv_shares_20d=100000.0)

        self.assertTrue(res.is_liquidity_constrained)
        self.assertEqual(res.liquidity_capped_capital_usd, 100000.0)
        self.assertEqual(res.liquidity_capped_shares, 10000.0)
        self.assertEqual(res.scaling_factor, 0.2)  # 20% of target

    def test_liquid_asset_position_uncapped(self):
        # Target $100,000 on $150 stock (666.67 shares) with 10,000,000 ADV
        res = self.sizer.calculate_size("AAPL", target_capital_usd=100000.0, price=150.0, adv_shares_20d=10000000.0)

        self.assertFalse(res.is_liquidity_constrained)
        self.assertEqual(res.liquidity_capped_capital_usd, 100000.0)
        self.assertEqual(res.scaling_factor, 1.0)


if __name__ == "__main__":
    unittest.main()
