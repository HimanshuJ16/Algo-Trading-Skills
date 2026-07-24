"""
Unit tests for mifid-ii-algo-trading-compliance-eu skill.

Tests:
1. Price collar deviation check rejection.
2. Max order value and volume limit checks.
3. Message rate limit throttling (msgs/sec).
4. MiFID II RTS 6 order tagging payload.
5. RTS 6 Article 18 kill switch trigger with resting order cancellation callback.
6. Backward compatibility with PreTradeRiskControls class.
"""
import unittest
from unittest.mock import Mock
from pretrade_risk_checks import (
    MiFID2ComplianceManager,
    PreTradeRiskControls,
    RTS6PreTradeResult,
)


class TestMiFID2PreTradeRiskChecks(unittest.TestCase):

    def setUp(self):
        self.cancel_mock = Mock()
        self.mgr = MiFID2ComplianceManager(
            max_order_value=50_000.0,
            max_volume=1_000.0,
            max_msgs_per_sec=5,
            price_collar_pct=0.05,  # 5% max deviation
            cancel_resting_orders_fn=self.cancel_mock,
        )

    def test_price_collar_rejection(self):
        # Ref price = 100.0. Order price = 110.0 (10% > 5% collar limit)
        res = self.mgr.validate_pretrade_order(price=110.0, quantity=10, reference_price=100.0)
        self.assertFalse(res.approved)
        self.assertFalse(res.price_collar_pass)
        self.assertIn("Price collar breach", res.rejection_reasons[0])

    def test_max_order_value_and_volume_rejections(self):
        # Value = 60,000 > 50,000 limit
        res_val = self.mgr.validate_pretrade_order(price=600.0, quantity=100, reference_price=600.0)
        self.assertFalse(res_val.approved)
        self.assertFalse(res_val.order_value_pass)

        # Volume = 2,000 > 1,000 limit
        res_vol = self.mgr.validate_pretrade_order(price=10.0, quantity=2000, reference_price=10.0)
        self.assertFalse(res_vol.approved)
        self.assertFalse(res_vol.volume_pass)

    def test_message_rate_throttling(self):
        # 5 messages allowed per sec
        for _ in range(5):
            res = self.mgr.validate_pretrade_order(price=10.0, quantity=10, reference_price=10.0)
            self.assertTrue(res.approved)

        # 6th message should fail rate limit
        res_overflow = self.mgr.validate_pretrade_order(price=10.0, quantity=10, reference_price=10.0)
        self.assertFalse(res_overflow.approved)
        self.assertFalse(res_overflow.message_rate_pass)

    def test_mifid2_order_tagging(self):
        tag = self.mgr.tag_order(client_id="CLIENT_999", short_selling=False)
        self.assertEqual(tag.algo_id, "ALGO_EUR_VOL_01")
        self.assertEqual(tag.client_id, "CLIENT_999")
        self.assertEqual(tag.trading_capacity, "DEAL")

    def test_rts6_kill_switch(self):
        self.mgr.trigger_rts6_kill_switch("compliance_officer_bob", "Market instability")
        self.assertTrue(self.mgr.kill_switch_active)
        self.cancel_mock.assert_called_once()

        # All subsequent orders must be rejected
        res = self.mgr.validate_pretrade_order(price=10.0, quantity=10, reference_price=10.0)
        self.assertFalse(res.approved)

    def test_backward_compatibility(self):
        legacy = PreTradeRiskControls(
            max_order_value=10000, max_volume=500, max_msgs_per_sec=10, price_collar_pct=0.05
        )
        self.assertTrue(legacy.check_price_collar(101, 100))
        self.assertFalse(legacy.check_price_collar(110, 100))


if __name__ == "__main__":
    unittest.main()
