"""
Unit tests for mifid-ii-algo-trading-compliance-eu skill.

Coverage:
1. Article 15(1)(a) price collar: breach, boundary equality, per-instrument override,
   non-finite and non-positive reference prices.
2. Article 15(1)(b)/(c) order value and volume caps, including the signed-quantity and
   non-finite regressions where a negative notional slipped under a positive cap.
3. Article 15(1)(d) message limit: throttling, and amend/cancel messages counting
   against the same budget.
4. RTS 6 Art. 12(3) order tagging, with RTS 22 Field 29 trading-capacity validation.
5. Article 12 kill switch: halt-then-cancel ordering, cancellation-failure handling,
   audit record, and Article 15(3) attributed re-enable.
6. Article 9 audit trail: durable sink fan-out, bounded buffer, sink-failure isolation.
7. Constructor limit validation and backward compatibility with PreTradeRiskControls.
"""
import math
import threading
import unittest
from unittest.mock import Mock

from pretrade_risk_checks import (
    KillSwitchCancellationError,
    MiFID2ComplianceManager,
    MiFID2OrderTag,
    PreTradeRiskControls,
)


class TestPriceCollar(unittest.TestCase):
    """RTS 6 Article 15(1)(a)."""

    def setUp(self):
        self.mgr = MiFID2ComplianceManager(
            max_order_value=50_000.0,
            max_volume=1_000.0,
            max_msgs_per_sec=5,
            price_collar_pct=0.05,
            price_collar_pct_by_symbol={"DE0007100000": 0.02},
            cancel_resting_orders_fn=Mock(),
        )

    def test_price_collar_rejection(self):
        # Ref 100.0, order 110.0 -> 10% deviation against a 5% collar.
        res = self.mgr.validate_pretrade_order(
            price=110.0, quantity=10, reference_price=100.0, symbol="EURUSD"
        )
        self.assertFalse(res.approved)
        self.assertFalse(res.price_collar_pass)
        self.assertIn("Price collar breach", res.rejection_reasons[0])

    def test_price_collar_boundary_is_inclusive(self):
        # 105.0 vs 100.0 is exactly 5%: at the limit, not beyond it.
        self.assertTrue(self.mgr.check_price_collar(105.0, 100.0))
        # 95.0 vs 100.0 is exactly -5%.
        self.assertTrue(self.mgr.check_price_collar(95.0, 100.0))
        # One cent past the boundary must fail.
        self.assertFalse(self.mgr.check_price_collar(105.01, 100.0))

    def test_per_instrument_collar_override(self):
        # Article 15(1)(a) requires collars to differentiate between instruments.
        # DE0007100000 is configured at 2%, everything else defaults to 5%.
        self.assertEqual(self.mgr.collar_pct_for("DE0007100000"), 0.02)
        self.assertEqual(self.mgr.collar_pct_for("EURUSD"), 0.05)

        # 103.0 vs 100.0 is 3%: inside the 5% default, outside the 2% override.
        self.assertTrue(self.mgr.check_price_collar(103.0, 100.0, symbol="EURUSD"))
        self.assertFalse(self.mgr.check_price_collar(103.0, 100.0, symbol="DE0007100000"))

        tight = self.mgr.validate_pretrade_order(
            price=103.0, quantity=1, reference_price=100.0, symbol="DE0007100000"
        )
        self.assertFalse(tight.approved)
        self.assertIn(">2%", tight.rejection_reasons[0])

    def test_non_finite_prices_are_rejected(self):
        self.assertFalse(self.mgr.check_price_collar(float("nan"), 100.0))
        self.assertFalse(self.mgr.check_price_collar(float("inf"), 100.0))
        self.assertFalse(self.mgr.check_price_collar(100.0, float("nan")))

    def test_non_positive_reference_price_fails_closed_with_distinct_reason(self):
        # A percentage collar is undefined at or below zero; the gate must fail closed
        # and must not report it as an ordinary deviation breach.
        self.assertFalse(self.mgr.check_price_collar(10.0, 0.0))
        self.assertFalse(self.mgr.check_price_collar(-10.0, -10.0))

        res = self.mgr.validate_pretrade_order(
            price=10.0, quantity=1, reference_price=0.0, symbol="EURUSD"
        )
        self.assertFalse(res.approved)
        self.assertIn("not evaluable", res.rejection_reasons[0])

    def test_negative_reference_price_allowed_only_when_explicitly_enabled(self):
        permissive = MiFID2ComplianceManager(
            price_collar_pct=0.05, allow_non_positive_reference_price=True
        )
        # -102 vs -100 is a 2% deviation once the sign is handled.
        self.assertTrue(permissive.check_price_collar(-102.0, -100.0))
        self.assertFalse(permissive.check_price_collar(-110.0, -100.0))
        # Zero remains unusable regardless of the flag.
        self.assertFalse(permissive.check_price_collar(1.0, 0.0))


class TestOrderValueAndVolume(unittest.TestCase):
    """RTS 6 Article 15(1)(b) and 15(1)(c)."""

    def setUp(self):
        self.mgr = MiFID2ComplianceManager(
            max_order_value=50_000.0, max_volume=1_000.0, max_msgs_per_sec=5
        )

    def test_max_order_value_and_volume_rejections(self):
        # Notional 60,000 > 50,000 cap.
        res_val = self.mgr.validate_pretrade_order(
            price=600.0, quantity=100, reference_price=600.0, symbol="EURUSD"
        )
        self.assertFalse(res_val.approved)
        self.assertFalse(res_val.order_value_pass)

        # Volume 2,000 > 1,000 cap.
        res_vol = self.mgr.validate_pretrade_order(
            price=10.0, quantity=2000, reference_price=10.0, symbol="EURUSD"
        )
        self.assertFalse(res_vol.approved)
        self.assertFalse(res_vol.volume_pass)

    def test_boundary_values_are_inclusive(self):
        # Exactly at the caps: 50 x 1000 == 50,000 notional and 1,000 units.
        self.assertTrue(self.mgr.check_order_value(50.0, 1000))
        self.assertTrue(self.mgr.check_volume(1000))
        self.assertFalse(self.mgr.check_order_value(50.001, 1000))
        self.assertFalse(self.mgr.check_volume(1000.001))

    def test_signed_quantity_cannot_bypass_the_notional_cap(self):
        # Regression: price * quantity for a negative (sell-signed) quantity produced a
        # negative notional, which is <= any positive cap, so the check passed.
        self.assertFalse(self.mgr.check_order_value(600.0, -100))
        self.assertFalse(self.mgr.check_volume(-100))

        res = self.mgr.validate_pretrade_order(
            price=600.0, quantity=-100, reference_price=600.0, symbol="EURUSD"
        )
        self.assertFalse(res.approved)
        self.assertFalse(res.order_value_pass)
        self.assertFalse(res.volume_pass)

    def test_zero_and_non_finite_quantities_are_rejected(self):
        self.assertFalse(self.mgr.check_volume(0))
        self.assertFalse(self.mgr.check_volume(float("nan")))
        self.assertFalse(self.mgr.check_volume(float("inf")))
        self.assertFalse(self.mgr.check_order_value(float("nan"), 10))
        self.assertFalse(self.mgr.check_order_value(10.0, float("inf")))

    def test_approved_order_passes_every_control(self):
        res = self.mgr.validate_pretrade_order(
            price=100.0, quantity=10, reference_price=100.0, symbol="EURUSD"
        )
        self.assertTrue(res.approved)
        self.assertEqual(res.rejection_reasons, [])
        self.assertTrue(
            all(
                [
                    res.price_collar_pass,
                    res.order_value_pass,
                    res.volume_pass,
                    res.message_rate_pass,
                ]
            )
        )


class TestMessageRateLimit(unittest.TestCase):
    """RTS 6 Article 15(1)(d)."""

    def setUp(self):
        self.mgr = MiFID2ComplianceManager(
            max_order_value=50_000.0, max_volume=1_000.0, max_msgs_per_sec=5
        )

    def test_message_rate_throttling(self):
        for _ in range(5):
            res = self.mgr.validate_pretrade_order(
                price=10.0, quantity=10, reference_price=10.0, symbol="EURUSD"
            )
            self.assertTrue(res.approved)

        overflow = self.mgr.validate_pretrade_order(
            price=10.0, quantity=10, reference_price=10.0, symbol="EURUSD"
        )
        self.assertFalse(overflow.approved)
        self.assertFalse(overflow.message_rate_pass)

    def test_amend_and_cancel_messages_consume_the_same_budget(self):
        # Article 15(1)(d) covers submission, modification AND cancellation, so amend
        # and cancel traffic must count even though it never reaches the pre-trade gate.
        self.assertTrue(self.mgr.record_message("NEW"))
        self.assertTrue(self.mgr.record_message("AMEND"))
        self.assertTrue(self.mgr.record_message("CANCEL"))
        self.assertTrue(self.mgr.record_message("CANCEL"))
        self.assertTrue(self.mgr.record_message("AMEND"))
        # Budget of 5 is now spent purely on amend/cancel traffic.
        self.assertFalse(self.mgr.record_message("NEW"))

        res = self.mgr.validate_pretrade_order(
            price=10.0, quantity=10, reference_price=10.0, symbol="EURUSD"
        )
        self.assertFalse(res.message_rate_pass)

    def test_rejected_message_does_not_consume_budget(self):
        for _ in range(5):
            self.assertTrue(self.mgr.record_message("NEW"))
        self.assertFalse(self.mgr.record_message("NEW"))
        # The refused message must not have been recorded, or the window would grow
        # without bound under sustained overload.
        self.assertEqual(len(self.mgr.message_timestamps), 5)

    def test_unknown_message_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mgr.record_message("HEARTBEAT")

    def test_concurrent_submissions_never_exceed_the_cap(self):
        mgr = MiFID2ComplianceManager(
            max_order_value=1e9, max_volume=1e9, max_msgs_per_sec=50
        )
        accepted = []
        lock = threading.Lock()

        def submit():
            ok = mgr.record_message("NEW")
            with lock:
                accepted.append(ok)

        threads = [threading.Thread(target=submit) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(accepted), 50)
        self.assertEqual(len(mgr.message_timestamps), 50)


class TestOrderTagging(unittest.TestCase):
    """RTS 6 Article 12(3) attribution; RTS 22 Field 29 code set."""

    def setUp(self):
        self.mgr = MiFID2ComplianceManager()

    def test_mifid2_order_tagging(self):
        tag = self.mgr.tag_order(client_id="CLIENT_999", short_selling=False)
        self.assertEqual(tag.algo_id, "ALGO_EUR_VOL_01")
        self.assertEqual(tag.client_id, "CLIENT_999")
        self.assertEqual(tag.trading_capacity, "DEAL")
        self.assertGreater(tag.timestamp_ns, 0)

    def test_all_rts22_trading_capacities_are_accepted(self):
        for capacity in ("DEAL", "MTCH", "AOTC"):
            tag = self.mgr.tag_order(client_id="CLIENT_1", trading_capacity=capacity)
            self.assertEqual(tag.trading_capacity, capacity)

    def test_invalid_trading_capacity_is_rejected(self):
        # "MATCH" and "AAGT" are not RTS 22 Field 29 values; the codes are MTCH/AOTC.
        for bad in ("MATCH", "AAGT", "deal", ""):
            with self.assertRaises(ValueError):
                self.mgr.tag_order(client_id="CLIENT_1", trading_capacity=bad)

    def test_empty_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            self.mgr.tag_order(client_id="")
        with self.assertRaises(ValueError):
            MiFID2OrderTag(
                algo_id="",
                client_id="C1",
                trading_capacity="DEAL",
                short_selling_flag=False,
                timestamp_ns=1,
            )


class TestKillSwitch(unittest.TestCase):
    """RTS 6 Article 12 kill functionality and Article 15(3) re-enabling."""

    def setUp(self):
        self.cancel_mock = Mock()
        self.mgr = MiFID2ComplianceManager(
            max_order_value=50_000.0,
            max_volume=1_000.0,
            max_msgs_per_sec=5,
            cancel_resting_orders_fn=self.cancel_mock,
        )

    def test_rts6_kill_switch(self):
        result = self.mgr.trigger_rts6_kill_switch("compliance_officer_bob", "Market instability")
        self.assertTrue(self.mgr.kill_switch_active)
        self.assertTrue(result.halted)
        self.assertTrue(result.cancellation_confirmed)
        self.cancel_mock.assert_called_once()

        res = self.mgr.validate_pretrade_order(
            price=10.0, quantity=10, reference_price=10.0, symbol="EURUSD"
        )
        self.assertFalse(res.approved)

    def test_halted_orders_do_not_consume_rate_budget(self):
        self.mgr.trigger_rts6_kill_switch("op", "halt")
        for _ in range(20):
            self.mgr.validate_pretrade_order(
                price=10.0, quantity=10, reference_price=10.0, symbol="EURUSD"
            )
        self.assertEqual(len(self.mgr.message_timestamps), 0)

    def test_kill_switch_is_audited(self):
        self.mgr.trigger_rts6_kill_switch("compliance_officer_bob", "Market instability")
        record = self.mgr.audit_log[-1]
        self.assertEqual(record["event"], "KILL_SWITCH_TRIGGERED")
        self.assertEqual(record["operator_id"], "compliance_officer_bob")
        self.assertEqual(record["reason"], "Market instability")
        self.assertTrue(record["cancellation_confirmed"])

    def test_cancellation_failure_halts_anyway_and_is_recorded(self):
        # A venue mass-cancel that fails must never leave the gate open, and must never
        # be reported as a successful kill.
        failing = MiFID2ComplianceManager(
            cancel_resting_orders_fn=Mock(side_effect=ConnectionError("venue gateway down"))
        )
        with self.assertRaises(KillSwitchCancellationError):
            failing.trigger_rts6_kill_switch("op", "gateway loss")

        self.assertTrue(failing.kill_switch_active)
        record = failing.audit_log[-1]
        self.assertEqual(record["event"], "KILL_SWITCH_TRIGGERED")
        self.assertFalse(record["cancellation_confirmed"])
        self.assertIn("venue gateway down", record["cancellation_error"])

    def test_kill_switch_requires_an_operator_id(self):
        with self.assertRaises(ValueError):
            self.mgr.trigger_rts6_kill_switch("", "no attribution")
        self.assertFalse(self.mgr.kill_switch_active)

    def test_reset_requires_an_operator_and_restores_flow(self):
        self.mgr.trigger_rts6_kill_switch("op", "halt")
        with self.assertRaises(ValueError):
            self.mgr.reset_kill_switch("", "unattributed re-enable")
        self.assertTrue(self.mgr.kill_switch_active)

        self.mgr.reset_kill_switch("designated_staff_alice", "incident closed")
        self.assertFalse(self.mgr.kill_switch_active)

        record = self.mgr.audit_log[-1]
        self.assertEqual(record["event"], "KILL_SWITCH_RESET")
        self.assertEqual(record["operator_id"], "designated_staff_alice")
        self.assertTrue(record["was_active"])

        res = self.mgr.validate_pretrade_order(
            price=10.0, quantity=10, reference_price=10.0, symbol="EURUSD"
        )
        self.assertTrue(res.approved)


class TestAuditTrail(unittest.TestCase):
    """RTS 6 Article 9 / Annex I evidence, Article 28 retention hand-off."""

    def test_decisions_are_forwarded_to_the_durable_sink(self):
        sink = Mock()
        mgr = MiFID2ComplianceManager(max_msgs_per_sec=50, audit_sink=sink)
        mgr.validate_pretrade_order(
            price=100.0, quantity=1, reference_price=100.0, symbol="DE0007100000"
        )
        sink.assert_called_once()
        record = sink.call_args[0][0]
        self.assertEqual(record["event"], "PRETRADE_DECISION")
        self.assertEqual(record["symbol"], "DE0007100000")
        self.assertTrue(record["approved"])
        self.assertIn("timestamp", record)
        self.assertEqual(record["algo_id"], mgr.algo_id)

    def test_sink_failure_does_not_break_the_gate(self):
        mgr = MiFID2ComplianceManager(
            max_msgs_per_sec=50, audit_sink=Mock(side_effect=IOError("disk full"))
        )
        res = mgr.validate_pretrade_order(
            price=100.0, quantity=1, reference_price=100.0, symbol="EURUSD"
        )
        self.assertTrue(res.approved)
        self.assertEqual(mgr.audit_sink_failures, 1)
        self.assertEqual(len(mgr.audit_log), 1)

    def test_in_memory_buffer_is_bounded(self):
        mgr = MiFID2ComplianceManager(max_msgs_per_sec=1000, max_audit_records=10)
        for _ in range(50):
            mgr.validate_pretrade_order(
                price=100.0, quantity=1, reference_price=100.0, symbol="EURUSD"
            )
        self.assertEqual(len(mgr.audit_log), 10)

    def test_symbol_is_required_for_an_audit_record(self):
        mgr = MiFID2ComplianceManager()
        with self.assertRaises(ValueError):
            mgr.validate_pretrade_order(
                price=100.0, quantity=1, reference_price=100.0, symbol=""
            )


class TestLimitValidation(unittest.TestCase):
    """Misconfiguration must fail at construction, not silently at the gate."""

    def test_invalid_limits_are_rejected(self):
        for kwargs in (
            {"max_order_value": 0},
            {"max_order_value": float("inf")},
            {"max_volume": -1},
            {"max_msgs_per_sec": 0},
            {"price_collar_pct": 0},
            {"price_collar_pct": 1.5},
            {"price_collar_pct": float("nan")},
            {"max_audit_records": 0},
            {"price_collar_pct_by_symbol": {"X": 0}},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    MiFID2ComplianceManager(**kwargs)


class TestBackwardCompatibility(unittest.TestCase):
    def setUp(self):
        self.legacy = PreTradeRiskControls(
            max_order_value=10000, max_volume=500, max_msgs_per_sec=10, price_collar_pct=0.05
        )

    def test_backward_compatibility(self):
        self.assertTrue(self.legacy.check_price_collar(101, 100))
        self.assertFalse(self.legacy.check_price_collar(110, 100))
        self.assertTrue(self.legacy.check_order_value(10, 100))
        self.assertFalse(self.legacy.check_volume(501))
        self.assertTrue(self.legacy.check_message_rate())

    def test_all_checks_returns_a_result_mapping(self):
        out = self.legacy.all_checks(price=100.0, quantity=1, reference_price=100.0)
        self.assertTrue(out["approved"])
        self.assertEqual(out["rejection_reasons"], [])
        # The unspecified-symbol default must be an obvious sentinel, not a real symbol.
        self.assertEqual(self.legacy.mgr.audit_log[-1]["symbol"], "UNSPECIFIED")

    def test_all_checks_accepts_an_explicit_symbol(self):
        self.legacy.all_checks(
            price=100.0, quantity=1, reference_price=100.0, symbol="DE0007100000"
        )
        self.assertEqual(self.legacy.mgr.audit_log[-1]["symbol"], "DE0007100000")


if __name__ == "__main__":
    unittest.main()
