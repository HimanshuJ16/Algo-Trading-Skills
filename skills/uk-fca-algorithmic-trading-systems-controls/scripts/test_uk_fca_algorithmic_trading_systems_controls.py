import dataclasses
import logging
import math
import unittest

from uk_fca_algorithmic_trading_systems_controls import (
    GLOBAL_SCOPE,
    ControlStatus,
    CreditState,
    FCAControlError,
    OrderIntent,
    RTS6ControlConfig,
    SystemCapacityState,
    UKFCAAlgoControlsEngine,
    ViolationType,
)


def order(**overrides):
    """A compliant baseline order; override one field per test."""
    base = dict(
        order_id="ORD-101",
        algo_id="ALGO-STATARB",
        symbol="VOD.L",
        side="BUY",
        price=100.0,
        quantity=1_000.0,  # value = 100,000
        reference_price=100.0,
    )
    base.update(overrides)
    return OrderIntent(**base)


class ControlsTestBase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.cancelled_scopes = []
        self.engine = UKFCAAlgoControlsEngine(
            mass_cancel_handler=self._record_cancel
        )
        self.config = RTS6ControlConfig(
            max_price_collar_pct=2.5,
            max_order_value_gbp=500_000.0,
            max_order_volume=10_000.0,
            max_credit_limit_gbp=1_000_000.0,
            max_unexecuted_to_transaction_ratio=100.0,
            system_capacity_kill_pct=95.0,
        )
        self.capacity = SystemCapacityState(
            current_msg_rate_per_sec=100.0,
            max_msg_rate_per_sec=1_000.0,  # 10% utilisation
            total_orders_sent=500,
            total_trades_executed=50,  # ratio = 500/50 - 1 = 9.0
        )
        self.credit = CreditState(used_gbp=100_000.0)

    def _record_cancel(self, scope):
        self.cancelled_scopes.append(scope)
        return 7

    def evaluate(self, o=None, capacity=None, credit=None):
        return self.engine.evaluate_pre_trade_controls(
            o if o is not None else order(),
            capacity if capacity is not None else self.capacity,
            self.config,
            credit if credit is not None else self.credit,
        )


class TestHappyPath(ControlsTestBase):
    def test_compliant_order_passes(self):
        res = self.evaluate()
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.PASSED)
        self.assertEqual(res.violation_type, ViolationType.NONE)

    def test_result_timestamp_is_timezone_aware(self):
        # Audit records must be unambiguous in time; a naive UTC stamp is not.
        self.assertIsNotNone(self.evaluate().timestamp.tzinfo)

    def test_sell_side_is_accepted(self):
        self.assertTrue(self.evaluate(order(side="SELL")).is_compliant)


class TestErroneousOrderRejection(ControlsTestBase):
    """Regression tests for the fail-open gap: NaN and non-positive fields used to
    pass every control because NaN compares False against every threshold."""

    def test_nan_price_is_rejected(self):
        res = self.evaluate(order(price=float("nan")))
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.violation_type, ViolationType.INVALID_ORDER)

    def test_nan_quantity_is_rejected(self):
        res = self.evaluate(order(quantity=float("nan")))
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.violation_type, ViolationType.INVALID_ORDER)

    def test_infinite_price_is_rejected_as_invalid_not_as_collar_breach(self):
        res = self.evaluate(order(price=float("inf")))
        self.assertEqual(res.violation_type, ViolationType.INVALID_ORDER)

    def test_negative_quantity_is_rejected(self):
        # A negative quantity produced a negative notional, which passed the value cap
        # and *reduced* projected credit utilisation.
        res = self.evaluate(order(quantity=-50_000.0))
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.violation_type, ViolationType.INVALID_ORDER)

    def test_zero_quantity_is_rejected(self):
        self.assertEqual(
            self.evaluate(order(quantity=0.0)).violation_type, ViolationType.INVALID_ORDER
        )

    def test_negative_price_is_rejected(self):
        self.assertEqual(
            self.evaluate(order(price=-100.0)).violation_type, ViolationType.INVALID_ORDER
        )

    def test_unknown_side_is_rejected(self):
        self.assertEqual(
            self.evaluate(order(side="buy")).violation_type, ViolationType.INVALID_ORDER
        )

    def test_nan_credit_utilisation_is_rejected(self):
        res = self.evaluate(credit=CreditState(used_gbp=float("nan")))
        self.assertEqual(res.violation_type, ViolationType.INVALID_ORDER)


class TestReferencePrice(ControlsTestBase):
    def test_zero_reference_price_rejects_instead_of_skipping_the_collar(self):
        # Previously the collar was skipped when the reference was <= 0, so an order
        # 100x away from the market passed.
        res = self.evaluate(order(price=9_999.0, quantity=1.0, reference_price=0.0))
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.violation_type, ViolationType.INVALID_REFERENCE_PRICE)

    def test_negative_reference_price_is_rejected(self):
        res = self.evaluate(order(reference_price=-100.0))
        self.assertEqual(res.violation_type, ViolationType.INVALID_REFERENCE_PRICE)

    def test_nan_reference_price_is_rejected(self):
        res = self.evaluate(order(reference_price=float("nan")))
        self.assertEqual(res.violation_type, ViolationType.INVALID_REFERENCE_PRICE)


class TestPriceCollar(ControlsTestBase):
    def test_breach_is_rejected(self):
        # 105 vs 100 reference is a 5% deviation against a 2.5% collar.
        res = self.evaluate(order(price=105.0))
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.REJECTED)
        self.assertEqual(res.violation_type, ViolationType.PRICE_COLLAR)

    def test_downside_breach_is_rejected(self):
        # The collar is two-sided: 95 is -5% from 100.
        self.assertEqual(
            self.evaluate(order(price=95.0)).violation_type, ViolationType.PRICE_COLLAR
        )

    def test_exactly_at_the_collar_passes(self):
        # 2.5% of 100 is exactly 2.5, so 102.5 sits on the limit, which is inclusive.
        self.assertTrue(self.evaluate(order(price=102.5)).is_compliant)

    def test_just_outside_the_collar_is_rejected(self):
        self.assertEqual(
            self.evaluate(order(price=102.5000001)).violation_type,
            ViolationType.PRICE_COLLAR,
        )

    def test_collar_is_relative_not_absolute(self):
        # 2.5% of 10 is 0.25, so 10.30 is outside while the same 0.30 gap at a
        # reference of 100 would be well inside.
        self.assertEqual(
            self.evaluate(order(price=10.30, reference_price=10.0)).violation_type,
            ViolationType.PRICE_COLLAR,
        )
        self.assertTrue(self.evaluate(order(price=100.30)).is_compliant)


class TestSizeCaps(ControlsTestBase):
    def test_max_order_value_breach(self):
        # 100 * 6,000 = 600,000 against a 500,000 cap.
        res = self.evaluate(order(quantity=6_000.0))
        self.assertEqual(res.violation_type, ViolationType.MAX_ORDER_VALUE)

    def test_exactly_at_the_value_cap_passes(self):
        # 100 * 5,000 = 500,000 exactly.
        self.assertTrue(self.evaluate(order(quantity=5_000.0)).is_compliant)

    def test_max_order_volume_breach(self):
        # 15,000 shares at 10.00 is only 150,000 notional, so only the volume cap bites.
        res = self.evaluate(order(price=10.0, quantity=15_000.0, reference_price=10.0))
        self.assertEqual(res.violation_type, ViolationType.MAX_ORDER_VOLUME)

    def test_exactly_at_the_volume_cap_passes(self):
        self.assertTrue(
            self.evaluate(
                order(price=10.0, quantity=10_000.0, reference_price=10.0)
            ).is_compliant
        )


class TestUnexecutedOrderRatio(ControlsTestBase):
    def test_rts9_number_terms_formula(self):
        # RTS 9 Art. 3: total orders / total transactions - 1.
        # 1,000 orders and 10 transactions -> 100 - 1 = 99.
        state = SystemCapacityState(100.0, 1_000.0, 1_000, 10)
        self.assertAlmostEqual(state.unexecuted_to_transaction_ratio, 99.0)

    def test_zero_transactions_falls_back_to_order_count(self):
        state = SystemCapacityState(100.0, 1_000.0, 42, 0)
        self.assertAlmostEqual(state.unexecuted_to_transaction_ratio, 42.0)

    def test_no_activity_yields_zero(self):
        self.assertAlmostEqual(
            SystemCapacityState(0.0, 1_000.0, 0, 0).unexecuted_to_transaction_ratio, 0.0
        )

    def test_breach_is_rejected(self):
        # 15,150 / 150 - 1 = 100.0 exactly at the limit; one more order breaches it.
        at_limit = SystemCapacityState(100.0, 1_000.0, 15_150, 150)
        self.assertTrue(self.evaluate(capacity=at_limit).is_compliant)
        over = SystemCapacityState(100.0, 1_000.0, 15_450, 150)  # 103 - 1 = 102
        res = self.evaluate(capacity=over)
        self.assertEqual(res.violation_type, ViolationType.ORDER_TO_TRADE_RATIO)


class TestMessageCapacity(ControlsTestBase):
    def test_throttled_above_kill_threshold(self):
        stressed = SystemCapacityState(980.0, 1_000.0, 100, 10)  # 98%
        res = self.evaluate(capacity=stressed)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, ControlStatus.THROTTLED)
        self.assertEqual(res.violation_type, ViolationType.CAPACITY_EXCEEDED)

    def test_exactly_at_the_kill_threshold_is_throttled(self):
        at_limit = SystemCapacityState(950.0, 1_000.0, 100, 10)  # exactly 95%
        self.assertEqual(
            self.evaluate(capacity=at_limit).violation_type, ViolationType.CAPACITY_EXCEEDED
        )

    def test_between_warn_and_kill_still_passes(self):
        warned = SystemCapacityState(850.0, 1_000.0, 100, 10)  # 85%
        self.assertTrue(self.evaluate(capacity=warned).is_compliant)

    def test_zero_ceiling_rejects_instead_of_reporting_zero_utilisation(self):
        # A ceiling of 0 previously reported 0% utilisation, silently disabling the
        # control while the gateway was saturated.
        broken = SystemCapacityState(10_000.0, 0.0, 100, 10)
        res = self.evaluate(capacity=broken)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.violation_type, ViolationType.INVALID_CAPACITY_STATE)

    def test_nan_message_rate_rejects(self):
        broken = SystemCapacityState(float("nan"), 1_000.0, 100, 10)
        self.assertEqual(
            self.evaluate(capacity=broken).violation_type,
            ViolationType.INVALID_CAPACITY_STATE,
        )

    def test_utilisation_property_raises_on_unusable_ceiling(self):
        with self.assertRaises(FCAControlError):
            SystemCapacityState(10.0, 0.0, 1, 1).capacity_utilization_pct


class TestCreditLimit(ControlsTestBase):
    def test_breach_is_rejected(self):
        # 700,000 used + 400,000 new = 1.1m against a 1.0m ceiling.
        res = self.evaluate(
            order(quantity=4_000.0), credit=CreditState(used_gbp=700_000.0)
        )
        self.assertEqual(res.violation_type, ViolationType.CREDIT_LIMIT_EXCEEDED)

    def test_exactly_at_the_ceiling_passes(self):
        self.assertTrue(
            self.evaluate(
                order(quantity=4_000.0), credit=CreditState(used_gbp=600_000.0)
            ).is_compliant
        )

    def test_limit_comes_from_config_not_from_the_order(self):
        # The strategy cannot widen its own ceiling: the limit lives on the firm config
        # and OrderIntent has no credit field at all.
        self.assertFalse(hasattr(order(), "max_credit_limit_gbp"))
        self.assertEqual(self.config.max_credit_limit_gbp, 1_000_000.0)

    def test_sell_side_consumes_credit_gross(self):
        # No netting: a SELL is checked against the same gross ceiling as a BUY.
        res = self.evaluate(
            order(side="SELL", quantity=4_000.0), credit=CreditState(used_gbp=700_000.0)
        )
        self.assertEqual(res.violation_type, ViolationType.CREDIT_LIMIT_EXCEEDED)


class TestKillSwitch(ControlsTestBase):
    def test_activation_blocks_orders_and_reset_restores_them(self):
        result = self.engine.trigger_kill_switch("ALGO-STATARB", "runaway algo")
        self.assertTrue(result.is_activated)
        self.assertEqual(result.scope, "ALGO-STATARB")

        res = self.evaluate()
        self.assertEqual(res.status, ControlStatus.KILL_SWITCH_ACTIVATED)
        self.assertEqual(res.violation_type, ViolationType.KILL_SWITCH_ACTIVE)

        self.assertTrue(
            self.engine.reset_kill_switch("ALGO-STATARB", "j.smith", "post-incident sign-off")
        )
        self.assertTrue(self.evaluate().is_compliant)

    def test_mass_cancel_handler_is_invoked_with_the_scope(self):
        result = self.engine.trigger_kill_switch("ALGO-STATARB", "runaway algo")
        self.assertEqual(self.cancelled_scopes, ["ALGO-STATARB"])
        self.assertTrue(result.mass_cancel_invoked)
        self.assertEqual(result.cancelled_orders_count, 7)
        self.assertIsNone(result.mass_cancel_error)

    def test_without_a_handler_no_cancellation_is_claimed(self):
        # The old engine reported a hardcoded 99 cancelled orders having cancelled none.
        engine = UKFCAAlgoControlsEngine()
        result = engine.trigger_kill_switch("ALGO-X", "halt")
        self.assertFalse(result.mass_cancel_invoked)
        self.assertIsNone(result.cancelled_orders_count)
        self.assertIn("no mass_cancel_handler", result.mass_cancel_error)

    def test_latch_survives_a_failing_mass_cancel_handler(self):
        def boom(scope):
            raise ConnectionError("venue gateway down")

        engine = UKFCAAlgoControlsEngine(mass_cancel_handler=boom)
        result = engine.trigger_kill_switch("ALGO-X", "halt")
        self.assertTrue(result.is_activated)
        self.assertFalse(result.mass_cancel_invoked)
        self.assertIn("ConnectionError", result.mass_cancel_error)
        # Critically, the halt is still in force despite the failure.
        self.assertTrue(engine.is_kill_switch_active("ALGO-X"))

    def test_firm_wide_halt_blocks_every_algo(self):
        self.engine.trigger_kill_switch(None, "market-wide disruption")
        self.assertTrue(self.engine.is_kill_switch_active("ANY-OTHER-ALGO"))
        self.assertEqual(self.cancelled_scopes, [None])

    def test_resetting_one_algo_does_not_lift_the_firm_wide_halt(self):
        self.engine.trigger_kill_switch(None, "market-wide disruption")
        self.engine.reset_kill_switch("ALGO-STATARB", "j.smith", "algo cleared")
        self.assertTrue(self.engine.is_kill_switch_active("ALGO-STATARB"))
        self.engine.reset_kill_switch(None, "j.smith", "market reopened")
        self.assertFalse(self.engine.is_kill_switch_active("ALGO-STATARB"))

    def test_blank_algo_id_is_not_treated_as_firm_wide(self):
        # An empty identifier from bad config must not halt the firm, nor lift a halt.
        with self.assertRaises(ValueError):
            self.engine.trigger_kill_switch("", "typo")
        self.engine.trigger_kill_switch(None, "market-wide disruption")
        with self.assertRaises(ValueError):
            self.engine.reset_kill_switch("   ", "j.smith", "typo")
        self.assertTrue(self.engine.is_kill_switch_active("ALGO-STATARB"))

    def test_reset_requires_an_authoriser_and_a_reason(self):
        self.engine.trigger_kill_switch("ALGO-STATARB", "runaway algo")
        with self.assertRaises(ValueError):
            self.engine.reset_kill_switch("ALGO-STATARB", "", "sign-off")
        with self.assertRaises(ValueError):
            self.engine.reset_kill_switch("ALGO-STATARB", "j.smith", "  ")
        self.assertTrue(self.engine.is_kill_switch_active("ALGO-STATARB"))

    def test_trigger_requires_a_reason(self):
        with self.assertRaises(ValueError):
            self.engine.trigger_kill_switch("ALGO-STATARB", "")

    def test_reset_of_an_unset_scope_returns_false(self):
        self.assertFalse(
            self.engine.reset_kill_switch("ALGO-NEVER-HALTED", "j.smith", "housekeeping")
        )

    def test_repeated_trigger_is_idempotent_for_the_block(self):
        self.engine.trigger_kill_switch("ALGO-STATARB", "first")
        self.engine.trigger_kill_switch("ALGO-STATARB", "second")
        self.assertTrue(self.engine.is_kill_switch_active("ALGO-STATARB"))
        # Each activation re-attempts venue cancellation, which is the safe direction.
        self.assertEqual(self.cancelled_scopes, ["ALGO-STATARB", "ALGO-STATARB"])

    def test_audit_trail_records_trigger_and_reset(self):
        self.engine.trigger_kill_switch("ALGO-STATARB", "runaway algo")
        self.engine.reset_kill_switch("ALGO-STATARB", "j.smith", "cleared by risk")
        actions = [(e.action, e.scope, e.authorised_by) for e in self.engine.kill_switch_events]
        self.assertEqual(
            actions, [("TRIGGER", "ALGO-STATARB", None), ("RESET", "ALGO-STATARB", "j.smith")]
        )
        self.assertIsNotNone(self.engine.kill_switch_events[0].timestamp.tzinfo)

    def test_global_scope_constant_is_the_stored_key(self):
        self.engine.trigger_kill_switch(None, "firm-wide")
        self.assertIn(GLOBAL_SCOPE, self.engine.active_kill_switches)


class TestRepeatedExecutionThrottle(ControlsTestBase):
    """RTS 6 Art. 15(3): disable automatically after a pre-determined number of
    repeated executions, until re-enabled by a designated staff member."""

    def setUp(self):
        super().setUp()
        self.throttled = RTS6ControlConfig(
            max_repeated_executions=3, repeated_execution_window_seconds=60.0
        )

    def test_disabled_when_unconfigured(self):
        for i in range(50):
            self.assertFalse(
                self.engine.record_execution("ALGO-STATARB", self.config, at=float(i))
            )
        self.assertFalse(self.engine.is_kill_switch_active("ALGO-STATARB"))

    def test_trips_at_the_configured_count_and_halts_the_algo(self):
        self.assertFalse(self.engine.record_execution("ALGO-STATARB", self.throttled, at=0.0))
        self.assertFalse(self.engine.record_execution("ALGO-STATARB", self.throttled, at=1.0))
        self.assertTrue(self.engine.record_execution("ALGO-STATARB", self.throttled, at=2.0))
        self.assertTrue(self.engine.is_kill_switch_active("ALGO-STATARB"))
        self.assertEqual(
            self.evaluate().violation_type, ViolationType.KILL_SWITCH_ACTIVE
        )

    def test_executions_outside_the_window_do_not_accumulate(self):
        # Three executions spaced 40s apart never put three inside a 60s window.
        for t in (0.0, 40.0, 80.0, 120.0, 160.0):
            self.assertFalse(
                self.engine.record_execution("ALGO-STATARB", self.throttled, at=t)
            )
        self.assertFalse(self.engine.is_kill_switch_active("ALGO-STATARB"))

    def test_counter_is_per_algo(self):
        self.engine.record_execution("ALGO-A", self.throttled, at=0.0)
        self.engine.record_execution("ALGO-A", self.throttled, at=1.0)
        self.assertFalse(self.engine.record_execution("ALGO-B", self.throttled, at=2.0))
        self.assertFalse(self.engine.is_kill_switch_active("ALGO-B"))

    def test_reset_clears_the_execution_counter(self):
        self.engine.record_execution("ALGO-STATARB", self.throttled, at=0.0)
        self.engine.record_execution("ALGO-STATARB", self.throttled, at=1.0)
        self.engine.record_execution("ALGO-STATARB", self.throttled, at=2.0)
        self.engine.reset_kill_switch("ALGO-STATARB", "j.smith", "investigated")
        # A stale counter would re-trip on the very next execution.
        self.assertFalse(self.engine.record_execution("ALGO-STATARB", self.throttled, at=3.0))
        self.assertFalse(self.engine.is_kill_switch_active("ALGO-STATARB"))

    def test_invalid_throttle_configuration_is_rejected_at_construction(self):
        for value in (0, -1, float("nan"), 3.0, True, "3"):
            with self.subTest(max_repeated_executions=value):
                with self.assertRaises(ValueError):
                    RTS6ControlConfig(max_repeated_executions=value)
        with self.assertRaises(ValueError):
            RTS6ControlConfig(
                max_repeated_executions=3, repeated_execution_window_seconds=0.0
            )

    def test_late_fill_after_a_trip_does_not_re_trigger(self):
        for t in (0.0, 1.0, 2.0):
            self.engine.record_execution("ALGO-STATARB", self.throttled, at=t)
        triggers_before = len(self.engine.kill_switch_events)
        cancels_before = len(self.cancelled_scopes)
        self.assertTrue(self.engine.record_execution("ALGO-STATARB", self.throttled, at=3.0))
        self.assertEqual(len(self.engine.kill_switch_events), triggers_before)
        self.assertEqual(len(self.cancelled_scopes), cancels_before)


class TestConfigValidation(ControlsTestBase):
    """A NaN limit compares False against every value, silently switching a mandatory
    Art. 15 control off. The config refuses to be built that way."""

    def test_nan_limit_is_rejected_at_construction(self):
        for field in (
            "max_price_collar_pct",
            "max_order_value_gbp",
            "max_order_volume",
            "max_credit_limit_gbp",
            "system_capacity_kill_pct",
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    RTS6ControlConfig(**{field: float("nan")})

    def test_non_positive_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            RTS6ControlConfig(max_order_value_gbp=0.0)
        with self.assertRaises(ValueError):
            RTS6ControlConfig(max_price_collar_pct=-1.0)

    def test_warn_threshold_above_kill_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            RTS6ControlConfig(system_capacity_warn_pct=99.0, system_capacity_kill_pct=95.0)

    def test_ratio_limit_of_zero_is_allowed(self):
        # A firm may legitimately require at least one fill per order.
        self.assertEqual(
            RTS6ControlConfig(max_unexecuted_to_transaction_ratio=0.0)
            .max_unexecuted_to_transaction_ratio,
            0.0,
        )


class TestOrderIdentity(ControlsTestBase):
    def test_blank_algo_id_is_rejected_not_raised(self):
        # A blank algo_id used to raise out of the gate; it must be a normal rejection
        # so the caller records it like any other blocked order.
        res = self.evaluate(order(algo_id="  "))
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.violation_type, ViolationType.INVALID_ORDER)

    def test_blank_order_id_is_rejected(self):
        self.assertEqual(
            self.evaluate(order(order_id="")).violation_type, ViolationType.INVALID_ORDER
        )


class TestOrderFlowCounters(ControlsTestBase):
    def test_nan_counters_reject_instead_of_yielding_a_nan_ratio(self):
        broken = SystemCapacityState(100.0, 1_000.0, float("nan"), float("nan"))
        res = self.evaluate(capacity=broken)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.violation_type, ViolationType.INVALID_CAPACITY_STATE)

    def test_negative_counters_reject(self):
        broken = SystemCapacityState(100.0, 1_000.0, -500, -5)
        self.assertEqual(
            self.evaluate(capacity=broken).violation_type,
            ViolationType.INVALID_CAPACITY_STATE,
        )

    def test_ratio_property_raises_on_unusable_counters(self):
        with self.assertRaises(FCAControlError):
            SystemCapacityState(100.0, 1_000.0, float("nan"), 1).unexecuted_to_transaction_ratio


class TestControlPrecedence(ControlsTestBase):
    def test_kill_switch_precedes_every_other_control(self):
        self.engine.trigger_kill_switch("ALGO-STATARB", "halt")
        # An order that is also malformed still reports the halt, not the malformation.
        res = self.evaluate(order(price=float("nan")))
        self.assertEqual(res.violation_type, ViolationType.KILL_SWITCH_ACTIVE)

    def test_validation_precedes_the_threshold_controls(self):
        # NaN quantity would otherwise slip past the value and volume caps.
        res = self.evaluate(order(quantity=float("nan")))
        self.assertEqual(res.violation_type, ViolationType.INVALID_ORDER)

    def test_order_intent_is_immutable(self):
        # A frozen intent cannot be edited between the check and the send.
        o = order()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            o.price = 1.0


class TestNumericHelpers(unittest.TestCase):
    def test_boolean_is_not_a_valid_price(self):
        # bool is a subclass of int; True must not be accepted as a price of 1.
        from uk_fca_algorithmic_trading_systems_controls import _is_finite_positive

        self.assertFalse(_is_finite_positive(True))
        self.assertFalse(_is_finite_positive(math.inf))
        self.assertTrue(_is_finite_positive(0.01))


if __name__ == "__main__":
    unittest.main()
