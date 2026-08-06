import unittest
from uk_fca_algorithmic_trading_systems_controls import (
    ControlStatus,
    ViolationType,
    OrderIntent,
    RTS6ControlConfig,
    SystemCapacityState,
    UKFCAAlgoControlsEngine,
)


class TestUKFCAAlgoControlsEngine(unittest.TestCase):
    def setUp(self):
        self.engine = UKFCAAlgoControlsEngine()
        self.config = RTS6ControlConfig(
            max_price_collar_pct=2.5,
            max_order_value_gbp=500_000.0,
            max_order_volume=10_000.0,
            max_otr_ratio=100.0,
            system_capacity_kill_pct=95.0,
        )
        self.capacity = SystemCapacityState(
            current_msg_rate_per_sec=100.0,
            max_msg_rate_per_sec=1000.0,  # 10% capacity
            total_orders_sent=500,
            total_trades_executed=50,     # OTR = 10.0
        )
        self.valid_order = OrderIntent(
            order_id="ORD-101",
            algo_id="ALGO-STATARB",
            symbol="VOD.L",
            side="BUY",
            price=100.0,
            quantity=1_000.0,           # Order Value = £100,000
            current_nbbo_mid=100.0,
            current_credit_used_gbp=100_000.0,
            max_credit_limit_gbp=1_000_000.0,
        )

    def test_valid_order_passes_pre_trade_controls(self):
        res = self.engine.evaluate_pre_trade_controls(self.valid_order, self.capacity, self.config)
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.PASSED)
        self.assertEqual(res.violation_type, ViolationType.NONE)

    def test_price_collar_breach_rejection(self):
        bad_price_order = OrderIntent(
            order_id="ORD-PRICE-BAD",
            algo_id="ALGO-STATARB",
            symbol="VOD.L",
            side="BUY",
            price=105.0,                # 5.0% price deviation > 2.5% collar!
            quantity=1_000.0,
            current_nbbo_mid=100.0,
        )
        res = self.engine.evaluate_pre_trade_controls(bad_price_order, self.capacity, self.config)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.REJECTED)
        self.assertEqual(res.violation_type, ViolationType.PRICE_COLLAR)

    def test_max_order_value_breach_rejection(self):
        large_val_order = OrderIntent(
            order_id="ORD-VAL-BAD",
            algo_id="ALGO-STATARB",
            symbol="VOD.L",
            side="BUY",
            price=100.0,
            quantity=6_000.0,           # Value = £600,000 > max £500,000 cap!
            current_nbbo_mid=100.0,
        )
        res = self.engine.evaluate_pre_trade_controls(large_val_order, self.capacity, self.config)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.REJECTED)
        self.assertEqual(res.violation_type, ViolationType.MAX_ORDER_VALUE)

    def test_max_order_volume_breach_rejection(self):
        large_vol_order = OrderIntent(
            order_id="ORD-VOL-BAD",
            algo_id="ALGO-STATARB",
            symbol="VOD.L",
            side="BUY",
            price=10.0,                 # Value = £150,000
            quantity=15_000.0,          # Shares = 15,000 > max 10,000 limit!
            current_nbbo_mid=10.0,
        )
        res = self.engine.evaluate_pre_trade_controls(large_vol_order, self.capacity, self.config)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.REJECTED)
        self.assertEqual(res.violation_type, ViolationType.MAX_ORDER_VOLUME)

    def test_otr_threshold_breach_rejection(self):
        high_otr_capacity = SystemCapacityState(
            current_msg_rate_per_sec=100.0,
            max_msg_rate_per_sec=1000.0,
            total_orders_sent=1500,
            total_trades_executed=10,   # OTR = 150.0 > max 100.0!
        )
        res = self.engine.evaluate_pre_trade_controls(self.valid_order, high_otr_capacity, self.config)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.REJECTED)
        self.assertEqual(res.violation_type, ViolationType.ORDER_TO_TRADE_RATIO)

    def test_capacity_exceeded_throttling(self):
        stress_capacity = SystemCapacityState(
            current_msg_rate_per_sec=980.0, # 98% utilization > 95% kill limit!
            max_msg_rate_per_sec=1000.0,
            total_orders_sent=100,
            total_trades_executed=10,
        )
        res = self.engine.evaluate_pre_trade_controls(self.valid_order, stress_capacity, self.config)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.THROTTLED)
        self.assertEqual(res.violation_type, ViolationType.CAPACITY_EXCEEDED)

    def test_credit_limit_exceeded_rejection(self):
        over_credit_order = OrderIntent(
            order_id="ORD-CREDIT-BAD",
            algo_id="ALGO-STATARB",
            symbol="VOD.L",
            side="BUY",
            price=100.0,
            quantity=4_000.0,           # Value = £400,000
            current_nbbo_mid=100.0,
            current_credit_used_gbp=700_000.0, # Total = £1.1M > £1.0M limit!
            max_credit_limit_gbp=1_000_000.0,
        )
        res = self.engine.evaluate_pre_trade_controls(over_credit_order, self.capacity, self.config)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.REJECTED)
        self.assertEqual(res.violation_type, ViolationType.CREDIT_LIMIT_EXCEEDED)

    def test_kill_switch_activation_and_rejection(self):
        kill_res = self.engine.trigger_kill_switch("ALGO-STATARB", "Test Emergency Halt")
        self.assertTrue(kill_res.is_activated)

        res = self.engine.evaluate_pre_trade_controls(self.valid_order, self.capacity, self.config)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.KILL_SWITCH_ACTIVATED)
        self.assertEqual(res.violation_type, ViolationType.KILL_SWITCH_ACTIVE)

        # Reset kill switch and verify order passes again
        self.engine.reset_kill_switch("ALGO-STATARB")
        res_after = self.engine.evaluate_pre_trade_controls(self.valid_order, self.capacity, self.config)
        self.assertTrue(res_after.is_compliant)


if __name__ == "__main__":
    unittest.main()

