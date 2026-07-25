"""Unit tests for black-swan-playbook-for-halted-markets."""
import unittest
from halted_market_engine import BlackSwanHaltedMarketEngine, MarketStatus, ProxyConfig

class TestBlackSwanHaltedMarketEngine(unittest.TestCase):
    def setUp(self):
        self.engine = BlackSwanHaltedMarketEngine(proxy_config_map={
            "NVDA": ProxyConfig(symbol="QQQ", beta=1.5, liquidity_score=0.99, basis_risk_limit=0.05)
        })

    def test_trading_halt_cancels_orders_and_deploys_proxy_hedge(self):
        open_orders = [
            {"id": "o1", "quantity": 500, "side": "BUY"},
            {"id": "o2", "quantity": 500, "side": "BUY"},
        ]

        report = self.engine.handle_halt_event(
            symbol="NVDA",
            status=MarketStatus.HALTED_LULD,
            halt_reason="LULD Limit State Breach",
            open_position_shares=1000.0,
            open_orders=open_orders,
            current_basis_risk=0.02
        )

        self.assertTrue(report.is_proxy_hedged)
        self.assertEqual(len(report.actions_executed), 3)

        # Action 1: Cancel Orders
        cancel_act = report.actions_executed[0]
        self.assertEqual(cancel_act.action_type, "CANCEL_ORDERS")
        self.assertEqual(cancel_act.quantity, 1000)

        # Action 2: Adjust Risk Limits
        risk_act = report.actions_executed[1]
        self.assertEqual(risk_act.action_type, "ADJUST_RISK_LIMITS")

        # Action 3: Proxy Hedge
        hedge_act = report.actions_executed[2]
        self.assertEqual(hedge_act.action_type, "PROXY_HEDGE")
        self.assertEqual(hedge_act.target_symbol, "QQQ")
        self.assertEqual(hedge_act.side, "SELL")
        self.assertEqual(hedge_act.quantity, 1500.0) # 1000 * 1.5 beta

    def test_high_basis_risk_aborts_hedge(self):
        report = self.engine.handle_halt_event(
            symbol="NVDA",
            status=MarketStatus.HALTED_CIRCUIT_BREAKER,
            halt_reason="Market Wide Circuit Breaker",
            open_position_shares=1000.0,
            open_orders=[],
            current_basis_risk=0.10 # Above the 0.05 limit
        )
        self.assertFalse(report.is_proxy_hedged)
        # Should only have risk limit adjustment, no hedge action
        self.assertEqual(len(report.actions_executed), 1)
        self.assertEqual(report.actions_executed[0].action_type, "ADJUST_RISK_LIMITS")

    def test_resume_auction_status(self):
        # Set up a prior hedge state
        self.engine.active_hedges["NVDA"] = 1500.0
        
        report = self.engine.handle_halt_event(
            symbol="NVDA",
            status=MarketStatus.RESUME_AUCTION,
            halt_reason="Halt Resumed",
            open_position_shares=1000.0,
            open_orders=[],
            auction_fair_value=450.50
        )

        self.assertFalse(report.is_proxy_hedged)
        self.assertIn("AUCTION RESUME DETECTED", report.status_message)
        self.assertEqual(len(report.actions_executed), 2)
        
        # Action 1: Participate in auction
        auction_act = report.actions_executed[0]
        self.assertEqual(auction_act.action_type, "AUCTION_RESUME_ORDER")
        self.assertEqual(auction_act.price, 450.50)
        self.assertEqual(auction_act.side, "SELL")
        
        # Action 2: Unwind Hedge
        unwind_act = report.actions_executed[1]
        self.assertEqual(unwind_act.action_type, "PROXY_HEDGE_UNWIND")
        self.assertEqual(unwind_act.quantity, 1500.0)
        self.assertEqual(unwind_act.side, "BUY")


if __name__ == "__main__":
    unittest.main()
