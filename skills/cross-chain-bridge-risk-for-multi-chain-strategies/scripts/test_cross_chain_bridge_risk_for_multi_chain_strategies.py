import unittest
from cross_chain_bridge_risk_for_multi_chain_strategies import (
    CrossChainBridgeRiskManager, BridgeProfile, BridgeTransferDecision
)

class TestCrossChainBridgeRiskManager(unittest.TestCase):

    def setUp(self):
        # Portfolio NAV = $1,000,000
        self.manager = CrossChainBridgeRiskManager(
            portfolio_nav_usd=1_000_000.0,
            max_depeg_threshold_pct=1.0,
            max_allowed_finality_minutes=120.0
        )
        
        # Bridge 1 (Stargate Pool): 15% NAV limit ($150k), 15 mins finality, current in-flight = $100k
        self.b1 = BridgeProfile(
            bridge_id="STARGATE_POOL", name="Stargate Finance", bridge_type="LIQUIDITY_POOL",
            finality_delay_minutes=15.0, max_nav_pct_cap=0.15, current_inflight_usd=100_000.0, audit_score_pct=95.0
        )
        # Bridge 2 (Synapse Pool): 15% NAV limit ($150k), 20 mins finality, current in-flight = $20k
        self.b2 = BridgeProfile(
            bridge_id="SYNAPSE_POOL", name="Synapse Protocol", bridge_type="LIQUIDITY_POOL",
            finality_delay_minutes=20.0, max_nav_pct_cap=0.15, current_inflight_usd=20_000.0, audit_score_pct=90.0
        )
        # Bridge 3 (Arbitrum Canonical): 20% NAV limit, 10,080 mins (7 days!) finality
        self.b3 = BridgeProfile(
            bridge_id="ARBITRUM_CANONICAL", name="Arbitrum Rollup Bridge", bridge_type="CANONICAL_ROLLUP",
            finality_delay_minutes=10080.0, max_nav_pct_cap=0.20, current_inflight_usd=0.0, audit_score_pct=99.0
        )
        
        self.manager.register_bridge(self.b1)
        self.manager.register_bridge(self.b2)
        self.manager.register_bridge(self.b3)

    def test_depeg_blocks_transfer(self):
        # Native ETH = $3,000, Wrapped wETH = $2,910 (3.0% de-peg >= 1.0% threshold)
        decision = self.manager.evaluate_bridge_transfer(
            target_bridge_id="STARGATE_POOL", transfer_amount_usd=20_000.0,
            native_price=3000.0, wrapped_price=2910.0
        )
        self.assertFalse(decision.is_approved)
        self.assertEqual(decision.depeg_pct, 3.0)
        self.assertIn("de-peg", decision.reason.lower())

    def test_cap_breach_reroutes_to_failover(self):
        # Native ETH = $3,000, Wrapped wETH = $2,997 (0.1% de-peg)
        # Transfer $60,000 to STARGATE_POOL ($100k + $60k = $160k / $1M = 16% > 15% cap)
        # Should re-route to SYNAPSE_POOL ($20k + $60k = $80k / $1M = 8% <= 15% cap)
        decision = self.manager.evaluate_bridge_transfer(
            target_bridge_id="STARGATE_POOL", transfer_amount_usd=60_000.0,
            native_price=3000.0, wrapped_price=2997.0
        )
        self.assertTrue(decision.is_approved)
        self.assertTrue(decision.is_rerouted)
        self.assertEqual(decision.selected_bridge_id, "SYNAPSE_POOL")

    def test_finality_delay_breach(self):
        # Attempt transfer to ARBITRUM_CANONICAL (Finality 10,080 mins > 120 mins SLA)
        decision = self.manager.evaluate_bridge_transfer(
            target_bridge_id="ARBITRUM_CANONICAL", transfer_amount_usd=10_000.0,
            native_price=3000.0, wrapped_price=3000.0
        )
        # Should re-route to STARGATE_POOL (15m finality <= 120m, 100k + 10k = 110k / 1M = 11% <= 15%)
        self.assertTrue(decision.is_approved)
        self.assertTrue(decision.is_rerouted)
        self.assertEqual(decision.selected_bridge_id, "STARGATE_POOL")

if __name__ == '__main__':
    unittest.main()
