import math
import unittest
from asic_market_integrity_rules_automated_trading import (
    AopRejectionCode,
    AopOrderRequest,
    AsicAopPreTradeFilter,
    AsicKillSwitchManager,
    AsicMarketIntegrityConfig,
    ComplianceResult,
    KillSwitchAuditEntry,
    KillSwitchEvent,
)


class TestAsicMarketIntegrityRulesAutomatedTrading(unittest.TestCase):
    def setUp(self):
        self.config = AsicMarketIntegrityConfig(
            max_order_value_aud=500000.0,
            max_order_volume=10000,
            max_price_deviation_pct=0.05,  # 5%
        )
        self.kill_switch = AsicKillSwitchManager()
        self.filter = AsicAopPreTradeFilter(self.config, self.kill_switch)

    def test_valid_order(self):
        order = AopOrderRequest(symbol="BHP.AX", price=45.0, qty=1000, reference_price=44.5, order_id="ORD-1")
        res = self.filter.run_checks(order)
        self.assertTrue(res.is_compliant)
        self.assertIsNone(res.rejection_code)
        self.assertEqual(res.order_id, "ORD-1")
        self.assertGreater(res.checked_at_unix, 0)

    def test_kill_switch_blocks_orders(self):
        self.kill_switch.trigger_kill_switch(reason="ops halt", actor="trader-a")
        order = AopOrderRequest(symbol="BHP.AX", price=45.0, qty=1000, reference_price=44.5)
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("Kill Switch is currently active", res.reason)
        self.assertEqual(res.rejection_code, AopRejectionCode.KILL_SWITCH_ACTIVE)

    def test_max_volume_breach(self):
        order = AopOrderRequest(symbol="PEN.AX", price=0.10, qty=20000, reference_price=0.10)
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("exceeds AOP limit (10000)", res.reason)
        self.assertEqual(res.rejection_code, AopRejectionCode.VOLUME_LIMIT)

    def test_max_value_breach(self):
        order = AopOrderRequest(symbol="CBA.AX", price=100.0, qty=6000, reference_price=100.0)
        # Value = 600,000 > 500,000 limit
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("exceeds AOP limit ($500,000.00)", res.reason)
        self.assertEqual(res.rejection_code, AopRejectionCode.VALUE_LIMIT)

    def test_price_deviation_breach(self):
        order = AopOrderRequest(symbol="BHP.AX", price=50.0, qty=1000, reference_price=45.0)
        # Deviation = (50 - 45)/45 = 11.1% > 5% limit
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("Price deviation (11.1%) exceeds AOP limit", res.reason)
        self.assertEqual(res.rejection_code, AopRejectionCode.PRICE_DEVIATION)

    def test_invalid_qty_or_price(self):
        res = self.filter.run_checks(AopOrderRequest("X.AX", 45.0, 0, 44.5))
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.rejection_code, AopRejectionCode.INVALID_ORDER_FIELDS)

        res = self.filter.run_checks(AopOrderRequest("X.AX", -1.0, 100, 44.5))
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.rejection_code, AopRejectionCode.INVALID_ORDER_FIELDS)

    def test_zero_reference_price_rejected_not_crash(self):
        # Regression: previously caused ZeroDivisionError, taking the filter offline.
        order = AopOrderRequest(symbol="NEW.AX", price=1.0, qty=10, reference_price=0.0)
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.rejection_code, AopRejectionCode.ZERO_REFERENCE_PRICE)
        self.assertIn("Reference price", res.reason)

    def test_nan_price_does_not_bypass_filters(self):
        # NaN comparisons evaluate to False, so without explicit guards a NaN
        # price/qty would silently pass every limit check.
        order = AopOrderRequest(symbol="X.AX", price=float("nan"), qty=100, reference_price=44.5)
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.rejection_code, AopRejectionCode.NON_FINITE_INPUT)

    def test_inf_qty_does_not_bypass_filters(self):
        order = AopOrderRequest(symbol="X.AX", price=45.0, qty=math.inf, reference_price=44.5)
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.rejection_code, AopRejectionCode.NON_FINITE_INPUT)

    def test_nan_reference_price_rejected(self):
        order = AopOrderRequest(symbol="X.AX", price=45.0, qty=100, reference_price=float("nan"))
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.rejection_code, AopRejectionCode.NON_FINITE_INPUT)

    def test_price_deviation_boundary_exact_limit_passes(self):
        # deviation exactly equal to the limit must pass (strict > comparison).
        ref = 100.0
        price = ref * (1 + self.config.max_price_deviation_pct)  # exactly +5%
        order = AopOrderRequest(symbol="X.AX", price=price, qty=10, reference_price=ref)
        res = self.filter.run_checks(order)
        self.assertTrue(res.is_compliant, msg=f"boundary failed: {res.reason}")

    def test_kill_switch_audit_log_records_trigger_and_reset(self):
        self.kill_switch.trigger_kill_switch(reason="rogue algo", actor="ops")
        self.kill_switch.reset_kill_switch(reason="verified safe", actor="compliance")
        log = self.kill_switch.audit_log
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0].event, KillSwitchEvent.TRIGGERED)
        self.assertEqual(log[0].reason, "rogue algo")
        self.assertEqual(log[0].actor, "ops")
        self.assertEqual(log[1].event, KillSwitchEvent.RESET)
        self.assertGreater(log[1].timestamp_unix, 0)
        # After reset, is_halted must be False.
        self.assertFalse(self.kill_switch.is_halted)
        self.assertIsNone(self.kill_switch.triggered_at)

    def test_kill_switch_trigger_records_triggered_at(self):
        self.kill_switch.trigger_kill_switch()
        self.assertIsNotNone(self.kill_switch.triggered_at)
        self.assertTrue(self.kill_switch.is_halted)

    def test_audit_log_returned_is_a_copy(self):
        self.kill_switch.trigger_kill_switch()
        snapshot = self.kill_switch.audit_log
        snapshot.clear()
        # Mutating the returned snapshot must not corrupt internal state.
        self.assertEqual(len(self.kill_switch.audit_log), 1)


class TestAsicMarketIntegrityConfigValidation(unittest.TestCase):
    def test_non_positive_value_rejected(self):
        with self.assertRaises(ValueError):
            AsicMarketIntegrityConfig(max_order_value_aud=0.0, max_order_volume=100, max_price_deviation_pct=0.05)

    def test_non_positive_volume_rejected(self):
        with self.assertRaises(ValueError):
            AsicMarketIntegrityConfig(max_order_value_aud=100.0, max_order_volume=-1, max_price_deviation_pct=0.05)

    def test_non_positive_deviation_rejected(self):
        with self.assertRaises(ValueError):
            AsicMarketIntegrityConfig(max_order_value_aud=100.0, max_order_volume=100, max_price_deviation_pct=0.0)

    def test_deviation_over_100pct_rejected(self):
        with self.assertRaises(ValueError):
            AsicMarketIntegrityConfig(max_order_value_aud=100.0, max_order_volume=100, max_price_deviation_pct=1.5)

    def test_nan_limit_rejected(self):
        with self.assertRaises(ValueError):
            AsicMarketIntegrityConfig(max_order_value_aud=float("nan"), max_order_volume=100, max_price_deviation_pct=0.05)

    def test_non_int_volume_rejected(self):
        with self.assertRaises(ValueError):
            AsicMarketIntegrityConfig(max_order_value_aud=100.0, max_order_volume=100.5, max_price_deviation_pct=0.05)

    def test_valid_config_accepted(self):
        cfg = AsicMarketIntegrityConfig(max_order_value_aud=1.0, max_order_volume=1, max_price_deviation_pct=1.0)
        self.assertEqual(cfg.max_order_volume, 1)


if __name__ == "__main__":
    unittest.main()
