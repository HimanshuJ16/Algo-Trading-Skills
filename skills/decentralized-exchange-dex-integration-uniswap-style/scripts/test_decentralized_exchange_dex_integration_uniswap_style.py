import unittest
from decentralized_exchange_dex_integration_uniswap_style import (
    UniswapDexIntegrationEngine, AmmPoolState, UniswapSwapRequest, UniswapSwapExecutionReport
)

class TestUniswapDexIntegrationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = UniswapDexIntegrationEngine()
        
        # ETH/USDC Pool: 1,000 ETH, 3,000,000 USDC -> Spot Price = 3,000 USDC / ETH
        self.pool = AmmPoolState("ETH_USDC_0.3", "ETH", "USDC", reserve_in=1000.0, reserve_out=3000000.0, fee_pct=0.003)
        self.engine.register_pool(self.pool)

    def test_constant_product_swap_math(self):
        # Swap 10 ETH for USDC
        # Net input = 10 * 0.997 = 9.97 ETH
        # Output = (3000000 * 9.97) / (1000 + 9.97) = 29910000 / 1009.97 = 29614.741032 USDC
        # Spot Price = 3000.0 -> Exec Price = 2961.47 -> Impact ~ 1.28%
        req = UniswapSwapRequest(
            swap_id="SWAP_01", token_in_symbol="ETH", token_out_symbol="USDC",
            amount_in=10.0, max_slippage_pct=0.005, max_price_impact_pct=0.05,
            current_timestamp_sec=1700000000.0, deadline_seconds=60.0
        )
        report = self.engine.execute_swap("ETH_USDC_0.3", req)
        
        self.assertTrue(report.is_executed)
        self.assertAlmostEqual(report.expected_amount_out, 29614.741, places=2)
        self.assertEqual(report.spot_price_before, 3000.0)
        self.assertGreater(report.price_impact_pct, 1.0)
        self.assertLess(report.price_impact_pct, 2.0)

    def test_high_price_impact_rejection(self):
        # Swap 500 ETH (50% of pool reserves!) -> Massive Price Impact (~33.4%) -> Rejected!
        req = UniswapSwapRequest(
            swap_id="SWAP_02", token_in_symbol="ETH", token_out_symbol="USDC",
            amount_in=500.0, max_slippage_pct=0.01, max_price_impact_pct=0.05, # Max impact 5%
            current_timestamp_sec=1700000000.0
        )
        report = self.engine.execute_swap("ETH_USDC_0.3", req)
        
        self.assertFalse(report.is_executed)
        self.assertIn("HIGH PRICE IMPACT", report.rejection_reason)

if __name__ == '__main__':
    unittest.main()
