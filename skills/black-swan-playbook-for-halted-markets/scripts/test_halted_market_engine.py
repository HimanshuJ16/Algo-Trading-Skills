"""Unit tests for black-swan-playbook-for-halted-markets."""
import unittest
from halted_market_engine import (
    BlackSwanHaltedMarketEngine,
    MarketStatus,
    ProxyConfig,
    ProxyHedgeState,
)

# NVDA long 1,000 shares at 450.00 hedged with QQQ at 375.00, beta 1.5.
# Derived independently of the implementation:
#   position notional      = 1,000 * 450.00        = 450,000
#   beta-adjusted exposure = 1.5 * 450,000         = 675,000
#   QQQ units to short     = 675,000 / 375.00      = 1,800
NVDA_PRICE = 450.00
QQQ_PRICE = 375.00
EXPECTED_HEDGE_UNITS = 1800.0


class TestBlackSwanHaltedMarketEngine(unittest.TestCase):
    def setUp(self):
        self.engine = BlackSwanHaltedMarketEngine(proxy_config_map={
            "NVDA": ProxyConfig(symbol="QQQ", beta=1.5, liquidity_score=0.99, basis_risk_limit=0.05)
        })

    def _halt(self, **overrides):
        kwargs = dict(
            symbol="NVDA",
            status=MarketStatus.HALTED_LULD,
            halt_reason="LULD Limit State Breach",
            open_position_shares=1000.0,
            open_orders=[],
            current_basis_risk=0.02,
            asset_price=NVDA_PRICE,
            proxy_price=QQQ_PRICE,
        )
        kwargs.update(overrides)
        return self.engine.handle_halt_event(**kwargs)

    def _actions_of(self, report, action_type):
        return [a for a in report.actions_executed if a.action_type == action_type]

    # --- Halt lockdown & hedge sizing -------------------------------------------------

    def test_trading_halt_cancels_orders_and_deploys_proxy_hedge(self):
        open_orders = [
            {"id": "o1", "quantity": 500, "side": "BUY"},
            {"id": "o2", "quantity": 500, "side": "BUY"},
        ]

        report = self._halt(open_orders=open_orders)

        self.assertTrue(report.is_proxy_hedged)
        self.assertIsNone(report.hedge_suppression_reason)
        self.assertEqual(len(report.actions_executed), 3)

        # Action 1: Cancel Orders
        cancel_act = report.actions_executed[0]
        self.assertEqual(cancel_act.action_type, "CANCEL_ORDERS")
        self.assertEqual(cancel_act.quantity, 1000)
        self.assertEqual(cancel_act.order_ids, ["o1", "o2"])

        # Action 2: Adjust Risk Limits
        risk_act = report.actions_executed[1]
        self.assertEqual(risk_act.action_type, "ADJUST_RISK_LIMITS")

        # Action 3: Proxy Hedge, sized on beta-adjusted *notional*, not share count.
        hedge_act = report.actions_executed[2]
        self.assertEqual(hedge_act.action_type, "PROXY_HEDGE")
        self.assertEqual(hedge_act.target_symbol, "QQQ")
        self.assertEqual(hedge_act.side, "SELL")
        self.assertEqual(hedge_act.quantity, EXPECTED_HEDGE_UNITS)

    def test_hedge_size_scales_with_price_ratio_not_share_count(self):
        """Regression: share-count sizing (qty * beta) ignores the price ratio.

        With the proxy trading at twice the asset's price the correct hedge is half
        the share-count answer, so a `qty * beta` implementation cannot pass this.
        """
        engine = BlackSwanHaltedMarketEngine(proxy_config_map={
            "XYZ": ProxyConfig(symbol="SPY", beta=1.0, liquidity_score=1.0, basis_risk_limit=1.0)
        })
        decision = engine.calculate_proxy_hedge(
            "XYZ", open_position=1000.0, current_basis_risk=0.0,
            asset_price=100.0, proxy_price=200.0,
        )
        self.assertIsNotNone(decision.hedge)
        self.assertEqual(decision.hedge.quantity, 500.0)
        self.assertEqual(decision.hedge.side, "SELL")

    def test_short_position_hedges_long_in_proxy(self):
        report = self._halt(open_position_shares=-1000.0)
        hedge_act = self._actions_of(report, "PROXY_HEDGE")[0]
        self.assertEqual(hedge_act.side, "BUY")
        self.assertEqual(hedge_act.quantity, EXPECTED_HEDGE_UNITS)

    def test_negative_beta_proxy_flips_hedge_direction(self):
        """An inverse proxy must be bought, not sold, to hedge a long position."""
        engine = BlackSwanHaltedMarketEngine(proxy_config_map={
            "XYZ": ProxyConfig(symbol="SH", beta=-1.0, liquidity_score=1.0, basis_risk_limit=1.0)
        })
        decision = engine.calculate_proxy_hedge(
            "XYZ", open_position=100.0, current_basis_risk=0.0,
            asset_price=50.0, proxy_price=25.0,
        )
        self.assertEqual(decision.hedge.side, "BUY")
        self.assertEqual(decision.hedge.quantity, 200.0)

    def test_flat_position_produces_no_hedge(self):
        report = self._halt(open_position_shares=0.0)
        self.assertFalse(report.is_proxy_hedged)
        self.assertEqual(self._actions_of(report, "PROXY_HEDGE"), [])

    # --- Hedge suppression gates (all must fail closed) --------------------------------

    def test_high_basis_risk_aborts_hedge(self):
        report = self._halt(current_basis_risk=0.10)  # Above the 0.05 limit
        self.assertFalse(report.is_proxy_hedged)
        self.assertIn("Basis risk", report.hedge_suppression_reason)
        # Only the risk limit adjustment; no cancel (no orders), no hedge.
        self.assertEqual(len(report.actions_executed), 1)
        self.assertEqual(report.actions_executed[0].action_type, "ADJUST_RISK_LIMITS")

    def test_nan_basis_risk_fails_closed(self):
        """Regression: `nan > limit` is False, so a naive comparison fails *open*."""
        report = self._halt(current_basis_risk=float("nan"))
        self.assertFalse(report.is_proxy_hedged)
        self.assertEqual(self._actions_of(report, "PROXY_HEDGE"), [])
        self.assertIn("unusable", report.hedge_suppression_reason)
        self.assertNotIn("NVDA", self.engine.active_hedges)

    def test_market_wide_circuit_breaker_suppresses_proxy_hedge(self):
        """Regression: an MWCB halts every NMS security and US index futures alike,
        so the proxy itself is untradable and no hedge order may be emitted."""
        report = self._halt(
            status=MarketStatus.HALTED_CIRCUIT_BREAKER,
            halt_reason="Market Wide Circuit Breaker",
        )
        self.assertFalse(report.is_proxy_hedged)
        self.assertEqual(self._actions_of(report, "PROXY_HEDGE"), [])
        self.assertIn("Market-wide circuit breaker", report.hedge_suppression_reason)
        self.assertNotIn("NVDA", self.engine.active_hedges)
        # Lockdown still happens.
        self.assertEqual(len(self._actions_of(report, "ADJUST_RISK_LIMITS")), 1)

    def test_missing_prices_suppress_hedge(self):
        report = self._halt(asset_price=None, proxy_price=None)
        self.assertFalse(report.is_proxy_hedged)
        self.assertEqual(self._actions_of(report, "PROXY_HEDGE"), [])
        self.assertIn("asset_price", report.hedge_suppression_reason)

    def test_zero_proxy_price_suppresses_hedge_without_dividing_by_zero(self):
        report = self._halt(proxy_price=0.0)
        self.assertFalse(report.is_proxy_hedged)
        self.assertIn("proxy_price", report.hedge_suppression_reason)

    def test_unmapped_symbol_is_not_hedged_against_an_assumed_beta(self):
        report = self._halt(symbol="UNKNOWN_TICKER")
        self.assertFalse(report.is_proxy_hedged)
        self.assertEqual(self._actions_of(report, "PROXY_HEDGE"), [])
        self.assertIn("No proxy configuration", report.hedge_suppression_reason)

    def test_explicit_default_proxy_is_used_when_configured(self):
        engine = BlackSwanHaltedMarketEngine(
            proxy_config_map={},
            default_proxy=ProxyConfig(symbol="SPY", beta=1.0, liquidity_score=1.0, basis_risk_limit=0.05),
        )
        decision = engine.calculate_proxy_hedge(
            "UNKNOWN_TICKER", open_position=100.0, current_basis_risk=0.0,
            asset_price=200.0, proxy_price=400.0,
        )
        self.assertEqual(decision.hedge.proxy_symbol, "SPY")
        self.assertEqual(decision.hedge.quantity, 50.0)

    def test_liquidity_gate_blocks_illiquid_proxy_when_configured(self):
        engine = BlackSwanHaltedMarketEngine(
            proxy_config_map={
                "XYZ": ProxyConfig(symbol="THIN", beta=1.0, liquidity_score=0.2, basis_risk_limit=1.0)
            },
            min_liquidity_score=0.5,
        )
        decision = engine.calculate_proxy_hedge(
            "XYZ", open_position=100.0, current_basis_risk=0.0,
            asset_price=100.0, proxy_price=100.0,
        )
        self.assertIsNone(decision.hedge)
        self.assertIn("liquidity score", decision.suppression_reason)

    # --- Idempotency -------------------------------------------------------------------

    def test_repeated_halt_status_does_not_double_hedge(self):
        """Regression: exchanges re-disseminate halt status; a second message must not
        fire a second hedge nor overwrite the recorded hedge size."""
        first = self._halt()
        self.assertEqual(len(self._actions_of(first, "PROXY_HEDGE")), 1)

        second = self._halt()
        self.assertEqual(self._actions_of(second, "PROXY_HEDGE"), [])
        self.assertTrue(second.is_proxy_hedged)
        self.assertIn("already working", second.hedge_suppression_reason)
        self.assertEqual(self.engine.active_hedges["NVDA"].quantity, EXPECTED_HEDGE_UNITS)

    # --- Resumption --------------------------------------------------------------------

    def test_resume_auction_status(self):
        self.engine.active_hedges["NVDA"] = ProxyHedgeState(
            proxy_symbol="QQQ", quantity=1800.0, side="SELL", beta=1.5
        )

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
        self.assertEqual(auction_act.order_type, "LIMIT")

        # Action 2: Unwind Hedge
        unwind_act = report.actions_executed[1]
        self.assertEqual(unwind_act.action_type, "PROXY_HEDGE_UNWIND")
        self.assertEqual(unwind_act.target_symbol, "QQQ")
        self.assertEqual(unwind_act.quantity, 1800.0)
        self.assertEqual(unwind_act.side, "BUY")
        self.assertNotIn("NVDA", self.engine.active_hedges)

    def test_hedge_is_unwound_even_without_a_fair_value(self):
        """Regression: gating the unwind on the auction order leaves an orphaned
        proxy hedge -- naked directional exposure -- when fair value is unavailable."""
        self._halt()  # establishes the hedge
        report = self.engine.handle_halt_event(
            symbol="NVDA",
            status=MarketStatus.RESUME_AUCTION,
            halt_reason="Halt Resumed",
            open_position_shares=1000.0,
            open_orders=[],
            auction_fair_value=None,
        )
        self.assertEqual(self._actions_of(report, "AUCTION_RESUME_ORDER"), [])
        unwind = self._actions_of(report, "PROXY_HEDGE_UNWIND")
        self.assertEqual(len(unwind), 1)
        self.assertEqual(unwind[0].quantity, EXPECTED_HEDGE_UNITS)
        self.assertNotIn("NVDA", self.engine.active_hedges)

    def test_hedge_is_unwound_when_position_is_flat(self):
        self._halt()
        report = self.engine.handle_halt_event(
            symbol="NVDA",
            status=MarketStatus.RESUME_AUCTION,
            halt_reason="Halt Resumed",
            open_position_shares=0.0,
            open_orders=[],
            auction_fair_value=450.50,
        )
        self.assertEqual(self._actions_of(report, "AUCTION_RESUME_ORDER"), [])
        self.assertEqual(len(self._actions_of(report, "PROXY_HEDGE_UNWIND")), 1)

    def test_normal_status_unwinds_any_orphaned_hedge(self):
        """A symbol can go straight from halted to NORMAL without an auction message."""
        self._halt()
        report = self.engine.handle_halt_event(
            symbol="NVDA",
            status=MarketStatus.NORMAL,
            halt_reason="Resumed",
            open_position_shares=1000.0,
            open_orders=[],
        )
        self.assertEqual(len(self._actions_of(report, "PROXY_HEDGE_UNWIND")), 1)
        self.assertNotIn("NVDA", self.engine.active_hedges)

    def test_pre_open_is_treated_as_a_resumption_state(self):
        self._halt()
        report = self.engine.handle_halt_event(
            symbol="NVDA",
            status=MarketStatus.PRE_OPEN,
            halt_reason="Reopening cross",
            open_position_shares=1000.0,
            open_orders=[],
            auction_fair_value=450.50,
        )
        self.assertEqual(len(self._actions_of(report, "AUCTION_RESUME_ORDER")), 1)
        self.assertEqual(len(self._actions_of(report, "PROXY_HEDGE_UNWIND")), 1)

    def test_normal_status_with_no_hedge_is_a_no_op(self):
        report = self.engine.handle_halt_event(
            symbol="NVDA",
            status=MarketStatus.NORMAL,
            halt_reason="",
            open_position_shares=1000.0,
            open_orders=[],
        )
        self.assertEqual(report.actions_executed, [])
        self.assertIn("Market Normal", report.status_message)

    # --- Order cancellation hygiene ----------------------------------------------------

    def test_cancel_aggregate_uses_absolute_sizes_and_skips_foreign_symbols(self):
        open_orders = [
            {"id": "o1", "symbol": "NVDA", "quantity": 500, "side": "BUY"},
            {"id": "o2", "symbol": "NVDA", "quantity": -200, "side": "SELL"},
            {"id": "o3", "symbol": "AMD", "quantity": 900, "side": "BUY"},
        ]
        report = self._halt(open_orders=open_orders)
        cancel_act = self._actions_of(report, "CANCEL_ORDERS")[0]
        self.assertEqual(cancel_act.order_ids, ["o1", "o2"])
        self.assertEqual(cancel_act.quantity, 700)

    def test_cancel_action_tolerates_unusable_quantities(self):
        open_orders = [
            {"id": "o1", "quantity": 500},
            {"id": "o2", "quantity": None},
        ]
        report = self._halt(open_orders=open_orders)
        cancel_act = self._actions_of(report, "CANCEL_ORDERS")[0]
        self.assertEqual(cancel_act.quantity, 500)
        self.assertEqual(cancel_act.order_ids, ["o1", "o2"])

    # --- Input validation --------------------------------------------------------------

    def test_invalid_event_inputs_raise(self):
        with self.assertRaises(ValueError):
            self._halt(symbol="")
        with self.assertRaises(ValueError):
            self._halt(open_position_shares=float("nan"))
        with self.assertRaises(ValueError):
            self._halt(status="HALTED_LULD")

    def test_invalid_proxy_config_raises(self):
        with self.assertRaises(ValueError):
            ProxyConfig(symbol="QQQ", beta=float("nan"), liquidity_score=1.0, basis_risk_limit=0.05)
        with self.assertRaises(ValueError):
            ProxyConfig(symbol="QQQ", beta=1.0, liquidity_score=1.5, basis_risk_limit=0.05)
        with self.assertRaises(ValueError):
            ProxyConfig(symbol="QQQ", beta=1.0, liquidity_score=1.0, basis_risk_limit=-0.01)
        with self.assertRaises(ValueError):
            ProxyConfig(symbol="", beta=1.0, liquidity_score=1.0, basis_risk_limit=0.05)


if __name__ == "__main__":
    unittest.main()
