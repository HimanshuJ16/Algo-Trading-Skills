"""Unit tests for black-swan-playbook-for-halted-markets."""
import unittest
from halted_market_engine import BlackSwanHaltedMarketEngine, MarketStatus


class TestBlackSwanHaltedMarketEngine(unittest.TestCase):
    def setUp(self):
        self.engine = BlackSwanHaltedMarketEngine(proxy_hedge_map={"NVDA": "QQQ"})

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
            beta_to_proxy=1.2,
        )

        self.assertTrue(report.is_proxy_hedged)
        self.assertEqual(len(report.actions_executed), 2)

        # Action 1: Cancel Orders
        cancel_act = report.actions_executed[0]
        self.assertEqual(cancel_act.action_type, "CANCEL_ORDERS")
        self.assertEqual(cancel_act.quantity, 1000)

        # Action 2: Proxy Hedge
        hedge_act = report.actions_executed[1]
        self.assertEqual(hedge_act.action_type, "PROXY_HEDGE")
        self.assertEqual(hedge_act.target_symbol, "QQQ")
        self.assertEqual(hedge_act.side, "SELL")
        self.assertEqual(hedge_act.quantity, 1200.0)

    def test_resume_auction_status(self):
        report = self.engine.handle_halt_event(
            symbol="NVDA",
            status=MarketStatus.RESUME_AUCTION,
            halt_reason="Halt Resumed",
            open_position_shares=0.0,
            open_orders=[],
        )

        self.assertFalse(report.is_proxy_hedged)
        self.assertIn("AUCTION RESUME DETECTED", report.status_message)


if __name__ == "__main__":
    unittest.main()
