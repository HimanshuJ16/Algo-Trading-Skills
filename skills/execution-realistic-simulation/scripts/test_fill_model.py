"""
Unit tests for execution-realistic-simulation skill.

Tests:
1. Directional fill pricing (BUY at Ask + impact, SELL at Bid - impact).
2. Square-root market impact scaling with order size and ADV.
3. Liquidity depth partial fill detection.
4. Indian Options & Equity complete statutory fee stack calculations (Brokerage, STT, Txn, GST, Stamp).
5. Backward compatibility with standalone simulate_fill_price/estimate_fees.
"""
import unittest
from fill_model import (
    FeeBreakdown,
    MarketType,
    RealisticExecutionSimulator,
    SimulatedFillResult,
    estimate_fees,
    simulate_fill_price,
)


class TestExecutionRealisticSimulation(unittest.TestCase):

    def setUp(self):
        self.sim = RealisticExecutionSimulator(impact_gamma=0.5)

    def test_directional_fill_prices(self):
        mid = 100.0
        half_spread = 0.5
        adv = 100_000.0

        # BUY fill should be > Ask (100.5)
        buy_res = self.sim.simulate_fill(
            side="BUY",
            order_size=1000.0,
            mid_price=mid,
            half_spread=half_spread,
            adv=adv,
        )
        self.assertGreater(buy_res.fill_price, 100.5)

        # SELL fill should be < Bid (99.5)
        sell_res = self.sim.simulate_fill(
            side="SELL",
            order_size=1000.0,
            mid_price=mid,
            half_spread=half_spread,
            adv=adv,
        )
        self.assertLess(sell_res.fill_price, 99.5)

    def test_square_root_market_impact_scaling(self):
        mid = 500.0
        half_spread = 1.0
        adv = 10_000.0

        res_small = self.sim.simulate_fill("BUY", 100.0, mid, half_spread, adv)
        res_large = self.sim.simulate_fill("BUY", 2000.0, mid, half_spread, adv)

        # Larger order must have higher fill price due to market impact
        self.assertGreater(res_large.fill_price, res_small.fill_price)

    def test_liquidity_depth_partial_fill(self):
        res = self.sim.simulate_fill(
            side="BUY",
            order_size=500.0,
            mid_price=100.0,
            half_spread=0.5,
            adv=50_000.0,
            market_depth_available=200.0,  # Only 200 available
        )

        self.assertTrue(res.is_partial_fill)
        self.assertEqual(res.filled_qty, 200.0)

    def test_fee_stack_calculation(self):
        turnover = 100_000.0  # ₹1 Lakh turnover
        fees = self.sim.calculate_fees(
            turnover=turnover, market_type=MarketType.INDIAN_OPTIONS, side="SELL"
        )

        self.assertGreater(fees.brokerage, 0.0)
        self.assertGreater(fees.stt, 0.0)  # STT applies on sell options
        self.assertGreater(fees.gst, 0.0)
        self.assertGreater(fees.total_fees, fees.brokerage)

    def test_backward_compatibility(self):
        fill_p = simulate_fill_price(100.0, 0.5, "buy", 100, 10000)
        self.assertGreater(fill_p, 100.5)

        fee = estimate_fees(100000)
        self.assertGreater(fee, 20)


if __name__ == "__main__":
    unittest.main()
