"""Unit tests for dynamic-position-sizing-based-on-realized-volatility."""
import unittest
import random
from realized_vol_sizer import RealizedVolPositionSizer


class TestRealizedVolPositionSizer(unittest.TestCase):
    def setUp(self):
        random.seed(42)
        self.sizer = RealizedVolPositionSizer(
            target_annualized_vol=0.15,
            min_scalar=0.20,
            max_scalar=2.00,
            vol_floor=0.05,
        )

    def test_high_volatility_scales_down_position(self):
        # 60% annualized volatility return series (high vol crash regime)
        high_vol_returns = [random.gauss(0, 0.60 / (252**0.5)) for _ in range(30)]

        result = self.sizer.calculate_position_size(
            symbol="BTC",
            base_capital_usd=100000.0,
            price=50000.0,
            returns_history=high_vol_returns,
            use_ewma=True,
        )

        self.assertTrue(result.is_scaled_down)
        self.assertFalse(result.is_leveraged_up)
        self.assertLess(result.bounded_vol_scalar, 0.5)
        self.assertLess(result.adjusted_capital_usd, 100000.0)

    def test_low_volatility_leverages_up_position(self):
        # 5% annualized volatility return series (ultra calm regime)
        low_vol_returns = [random.gauss(0, 0.04 / (252**0.5)) for _ in range(30)]

        result = self.sizer.calculate_position_size(
            symbol="US_TREASURIES",
            base_capital_usd=100000.0,
            price=100.0,
            returns_history=low_vol_returns,
            use_ewma=True,
        )

        self.assertTrue(result.is_leveraged_up)
        self.assertEqual(result.bounded_vol_scalar, 2.0)  # Max scalar cap hit
        self.assertEqual(result.adjusted_capital_usd, 200000.0)


if __name__ == "__main__":
    unittest.main()
