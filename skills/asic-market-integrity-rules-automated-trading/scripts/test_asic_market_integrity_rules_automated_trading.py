import dataclasses
import decimal
import math
import threading
import unittest
from asic_market_integrity_rules_automated_trading import (
    AopHaltRecord,
    AopHaltScope,
    AopRejectionCode,
    AopOrderRequest,
    AsicAopPreTradeFilter,
    AsicKillSwitchManager,
    AsicMarketIntegrityConfig,
    ComplianceResult,
    FilterParameterChange,
    KillSwitchAuditEntry,
    KillSwitchEvent,
    SeriesCancellationStatus,
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
        self.assertIsInstance(res, ComplianceResult)
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

    def test_bool_qty_rejected_not_read_as_one(self):
        # Regression: isinstance(True, int) is True, so an unguarded bool was
        # read as qty=1 and passed every limit on a safety-critical filter.
        order = AopOrderRequest(symbol="X.AX", price=45.0, qty=True, reference_price=44.5)
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.rejection_code, AopRejectionCode.NON_FINITE_INPUT)

    def test_decimal_price_rejected_fail_closed(self):
        # Documented input contract: Decimal must be converted at the boundary.
        # The filter fails closed rather than raising mid-check.
        order = AopOrderRequest(
            symbol="X.AX", price=decimal.Decimal("45.00"), qty=100, reference_price=44.5
        )
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

    def test_price_deviation_boundary_float_representable_case(self):
        # Regression: with the division form, abs(422.8245 - 402.69) / 402.69
        # evaluates to 0.05000000000000001 and rejects an order priced at
        # exactly the 5% limit, contradicting the documented contract that a
        # deviation equal to the limit is allowed. Independently derived:
        # 402.69 * 1.05 == 422.8245 exactly in IEEE-754 double.
        ref = 402.69
        price = 422.8245
        self.assertEqual(ref * 1.05, price)
        self.assertGreater(abs(price - ref) / ref, 0.05)  # the old form's error
        order = AopOrderRequest(symbol="X.AX", price=price, qty=10, reference_price=ref)
        res = self.filter.run_checks(order)
        self.assertTrue(res.is_compliant, msg=f"boundary failed: {res.reason}")

    def test_one_increment_past_deviation_limit_rejected(self):
        ref = 402.69
        order = AopOrderRequest(
            symbol="X.AX", price=math.nextafter(422.8245, math.inf), qty=10, reference_price=ref
        )
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.rejection_code, AopRejectionCode.PRICE_DEVIATION)

    def test_boundary_volume_and_value_exactly_at_limit_pass(self):
        # The configured limit is itself permitted; a breach requires exceeding it.
        order = AopOrderRequest(symbol="X.AX", price=50.0, qty=10000, reference_price=50.0)
        self.assertEqual(order.price * order.qty, self.config.max_order_value_aud)
        res = self.filter.run_checks(order)
        self.assertTrue(res.is_compliant, msg=res.reason)

    def test_kill_switch_audit_log_records_trigger_and_reset(self):
        self.kill_switch.trigger_kill_switch(reason="rogue algo", actor="ops")
        self.kill_switch.reset_kill_switch(reason="verified safe", actor="compliance")
        log = self.kill_switch.audit_log
        self.assertEqual(len(log), 2)
        self.assertIsInstance(log[0], KillSwitchAuditEntry)
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


class TestScopedHalts(unittest.TestCase):
    """Rule 5.6.3(1)(d) / RG 241.52: suspension must be available in respect of
    one or more authorised persons, clients, financial products or markets,
    not only as a single global halt."""

    def setUp(self):
        self.config = AsicMarketIntegrityConfig(500000.0, 10000, 0.05)
        self.kill_switch = AsicKillSwitchManager()
        self.filter = AsicAopPreTradeFilter(self.config, self.kill_switch)

    def _order(self, **kw):
        base = dict(symbol="BHP.AX", price=45.0, qty=100, reference_price=44.5)
        base.update(kw)
        return AopOrderRequest(**base)

    def test_algorithm_halt_blocks_only_that_algorithm(self):
        self.kill_switch.trigger_scoped_halt(
            AopHaltScope.ALGORITHM, "algo-7", reason="runaway", actor="ops"
        )
        blocked = self.filter.run_checks(self._order(algorithm_id="algo-7"))
        self.assertFalse(blocked.is_compliant)
        self.assertEqual(blocked.rejection_code, AopRejectionCode.AOP_SCOPE_HALTED)
        self.assertIn("ALGORITHM=algo-7", blocked.reason)

        allowed = self.filter.run_checks(self._order(algorithm_id="algo-8"))
        self.assertTrue(allowed.is_compliant, msg=allowed.reason)

    def test_client_product_market_and_person_scopes_each_block(self):
        cases = [
            (AopHaltScope.CLIENT, "cli-1", {"client_id": "cli-1"}),
            (AopHaltScope.FINANCIAL_PRODUCT, "BHP.AX", {}),
            (AopHaltScope.MARKET, "XASX", {"market": "XASX"}),
            (AopHaltScope.AUTHORISED_PERSON, "ap-9", {"authorised_person_id": "ap-9"}),
        ]
        for scope, value, order_kw in cases:
            with self.subTest(scope=scope):
                ks = AsicKillSwitchManager()
                flt = AsicAopPreTradeFilter(self.config, ks)
                self.assertTrue(flt.run_checks(self._order(**order_kw)).is_compliant)
                ks.trigger_scoped_halt(scope, value, reason="interference", actor="ops")
                res = flt.run_checks(self._order(**order_kw))
                self.assertFalse(res.is_compliant)
                self.assertEqual(res.rejection_code, AopRejectionCode.AOP_SCOPE_HALTED)

    def test_scoped_halt_does_not_set_global_is_halted(self):
        self.kill_switch.trigger_scoped_halt(
            AopHaltScope.CLIENT, "cli-1", reason="r", actor="a"
        )
        self.assertFalse(self.kill_switch.is_halted)
        self.assertIsNone(self.kill_switch.triggered_at)
        self.assertEqual(len(self.kill_switch.active_halts), 1)

    def test_global_halt_blocks_orders_carrying_unrelated_identities(self):
        self.kill_switch.trigger_kill_switch(reason="systemic", actor="ops")
        res = self.filter.run_checks(self._order(client_id="cli-2", algorithm_id="algo-3"))
        self.assertEqual(res.rejection_code, AopRejectionCode.KILL_SWITCH_ACTIVE)

    def test_blank_identity_on_order_never_matches_a_halt(self):
        # A halt keyed on "" must never be creatable, so an order with no
        # client_id cannot be caught by a client-scoped halt.
        with self.assertRaises(ValueError):
            self.kill_switch.trigger_scoped_halt(AopHaltScope.CLIENT, "  ", reason="r", actor="a")
        self.assertTrue(self.filter.run_checks(self._order()).is_compliant)

    def test_halt_matching_is_case_and_whitespace_insensitive(self):
        # A halt keyed "BHP.AX" that misses an order carrying "bhp.ax" is a
        # suspension control that reports itself active while letting the
        # messages through. Case-folding can only make a halt match more
        # orders, which is the fail-safe direction.
        self.kill_switch.trigger_scoped_halt(
            AopHaltScope.FINANCIAL_PRODUCT, "  BHP.AX  ", reason="anomalous", actor="ops"
        )
        res = self.filter.run_checks(self._order(symbol="bhp.ax"))
        self.assertEqual(res.rejection_code, AopRejectionCode.AOP_SCOPE_HALTED)
        # ... and the release must find it through the same normalisation.
        self.assertTrue(
            self.kill_switch.release_scoped_halt(
                AopHaltScope.FINANCIAL_PRODUCT, "bhp.ax", reason="cleared", actor="compliance"
            )
        )
        self.assertTrue(self.filter.run_checks(self._order(symbol="BHP.AX")).is_compliant)

    def test_audit_record_preserves_the_operator_entered_scope_value(self):
        self.kill_switch.trigger_scoped_halt(
            AopHaltScope.CLIENT, "Client-A1", reason="r", actor="ops"
        )
        self.assertEqual(self.kill_switch.audit_log[0].scope_value, "Client-A1")
        self.assertEqual(self.kill_switch.active_halts[0].scope_value, "Client-A1")

    def test_trigger_scoped_halt_with_all_aop_scope_halts_globally(self):
        self.kill_switch.trigger_scoped_halt(
            AopHaltScope.ALL_AOP, "ignored", reason="systemic", actor="ops"
        )
        self.assertTrue(self.kill_switch.is_halted)
        self.assertEqual(len(self.kill_switch.active_halts), 1)
        self.assertEqual(
            self.filter.run_checks(self._order()).rejection_code,
            AopRejectionCode.KILL_SWITCH_ACTIVE,
        )

    def test_release_scoped_halt_restores_flow(self):
        self.kill_switch.trigger_scoped_halt(
            AopHaltScope.ALGORITHM, "algo-7", reason="runaway", actor="ops"
        )
        released = self.kill_switch.release_scoped_halt(
            AopHaltScope.ALGORITHM, "algo-7", reason="root cause fixed", actor="compliance"
        )
        self.assertTrue(released)
        self.assertTrue(self.filter.run_checks(self._order(algorithm_id="algo-7")).is_compliant)

    def test_releasing_a_halt_that_is_not_active_returns_false(self):
        self.assertFalse(
            self.kill_switch.release_scoped_halt(
                AopHaltScope.ALGORITHM, "never-halted", reason="r", actor="a"
            )
        )

    def test_global_reset_does_not_release_scoped_halts(self):
        self.kill_switch.trigger_kill_switch(reason="systemic", actor="ops")
        self.kill_switch.trigger_scoped_halt(
            AopHaltScope.ALGORITHM, "algo-7", reason="runaway", actor="ops"
        )
        self.assertTrue(self.kill_switch.reset_kill_switch(reason="all clear", actor="compliance"))
        self.assertFalse(self.kill_switch.is_halted)
        # The algorithm-specific suspension must survive the global resume.
        res = self.filter.run_checks(self._order(algorithm_id="algo-7"))
        self.assertEqual(res.rejection_code, AopRejectionCode.AOP_SCOPE_HALTED)


class TestHaltAttributionGate(unittest.TestCase):
    """RG 241.44: administrator-level changes only after authorisation by a
    qualified person. Resuming AOP is the direction that puts messages back
    into the market, so the release is gated; raising a halt never is."""

    def setUp(self):
        self.kill_switch = AsicKillSwitchManager()

    def test_reset_with_blank_actor_is_refused_and_halt_remains(self):
        self.kill_switch.trigger_kill_switch(reason="rogue algo", actor="ops")
        self.assertFalse(self.kill_switch.reset_kill_switch(reason="looks fine", actor=""))
        self.assertTrue(self.kill_switch.is_halted)

    def test_reset_with_whitespace_reason_is_refused(self):
        self.kill_switch.trigger_kill_switch(reason="rogue algo", actor="ops")
        self.assertFalse(self.kill_switch.reset_kill_switch(reason="   ", actor="compliance"))
        self.assertTrue(self.kill_switch.is_halted)

    def test_refused_reset_is_itself_audited(self):
        self.kill_switch.trigger_kill_switch(reason="rogue algo", actor="ops")
        self.kill_switch.reset_kill_switch(reason="", actor="")
        events = [e.event for e in self.kill_switch.audit_log]
        self.assertIn(KillSwitchEvent.RESET_REFUSED, events)

    def test_successful_reset_returns_true(self):
        self.kill_switch.trigger_kill_switch(reason="rogue algo", actor="ops")
        self.assertTrue(self.kill_switch.reset_kill_switch(reason="cleared", actor="compliance"))
        self.assertFalse(self.kill_switch.is_halted)

    def test_trigger_is_never_refused_for_missing_attribution(self):
        # A halt must never be blocked by incomplete paperwork.
        self.kill_switch.trigger_kill_switch()
        self.assertTrue(self.kill_switch.is_halted)
        self.kill_switch.trigger_scoped_halt(AopHaltScope.CLIENT, "cli-1")
        self.assertEqual(len(self.kill_switch.active_halts), 2)


class TestSeriesCancellationHandoff(unittest.TestCase):
    """Rule 5.6.3(1)(e)(ii)/(iv), RG 241.55 and RG 241.58: messages in a series
    already entered in the market must be cancellable."""

    def test_no_callback_records_not_configured(self):
        ks = AsicKillSwitchManager()
        ks.trigger_kill_switch(reason="r", actor="a")
        entry = ks.audit_log[0]
        self.assertEqual(entry.cancellation_status, SeriesCancellationStatus.NOT_CONFIGURED)
        self.assertIsNone(entry.cancelled_message_count)

    def test_callback_receives_halt_record_and_count_is_audited(self):
        seen = []

        def cancel(record: AopHaltRecord) -> int:
            seen.append(record)
            return 4

        ks = AsicKillSwitchManager(cancel_series_callback=cancel)
        ks.trigger_scoped_halt(AopHaltScope.ALGORITHM, "algo-7", reason="runaway", actor="ops")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].scope, AopHaltScope.ALGORITHM)
        self.assertEqual(seen[0].scope_value, "algo-7")
        self.assertEqual(seen[0].actor, "ops")
        entry = ks.audit_log[0]
        self.assertEqual(entry.cancellation_status, SeriesCancellationStatus.COMPLETED)
        self.assertEqual(entry.cancelled_message_count, 4)

    def test_failing_callback_does_not_prevent_the_halt(self):
        def cancel(record: AopHaltRecord) -> int:
            raise ConnectionError("OMS unreachable")

        ks = AsicKillSwitchManager(cancel_series_callback=cancel)
        with self.assertLogs(
            "asic_market_integrity_rules_automated_trading", level="CRITICAL"
        ):
            ks.trigger_kill_switch(reason="rogue algo", actor="ops")
        # The halt is the control that stops the bleeding; it must still apply.
        self.assertTrue(ks.is_halted)
        entry = ks.audit_log[0]
        self.assertEqual(entry.cancellation_status, SeriesCancellationStatus.FAILED)
        self.assertIn("ConnectionError", entry.cancellation_error)
        self.assertIn("OMS unreachable", entry.cancellation_error)

    def test_pre_trade_gate_is_not_blocked_while_the_cancel_callback_runs(self):
        # Regression: the callback is an OMS network call. Holding the lock
        # across it stalled every concurrent halt_blocking() read from the
        # filter thread, so a scoped halt on one algorithm froze the pre-trade
        # gate for every unrelated order until the OMS replied. Checked from a
        # second thread: a re-entrant lock would hide this on the same thread.
        in_callback = threading.Event()
        release_callback = threading.Event()
        reader_finished = threading.Event()
        holder = {}

        def cancel(record: AopHaltRecord) -> int:
            in_callback.set()
            # Long enough that a reader blocked on the manager's lock cannot
            # finish inside the assertion window below.
            release_callback.wait(timeout=30)
            return 0

        ks = AsicKillSwitchManager(cancel_series_callback=cancel)
        holder["ks"] = ks
        order = AopOrderRequest(symbol="BHP.AX", price=45.0, qty=100, reference_price=44.5)

        def reader():
            holder["blocking"] = ks.halt_blocking(order)
            holder["halted"] = ks.is_halted
            holder["active"] = len(ks.active_halts)
            reader_finished.set()

        def trigger():
            ks.trigger_scoped_halt(
                AopHaltScope.ALGORITHM, "algo-7", reason="runaway", actor="ops"
            )

        # The main thread only orchestrates, so it is never itself blocked and
        # can assert while the callback is still in flight.
        t_trigger = threading.Thread(target=trigger, daemon=True)
        t_trigger.start()
        self.assertTrue(in_callback.wait(timeout=5), "cancel callback never ran")

        t_reader = threading.Thread(target=reader, daemon=True)
        t_reader.start()
        try:
            # This is the assertion: while the OMS call is still outstanding,
            # a filter-thread read must complete. Holding the lock across the
            # callback leaves this unset until the callback returns.
            self.assertTrue(
                reader_finished.wait(timeout=2),
                "halt_blocking() was blocked by the in-flight cancel callback",
            )
        finally:
            release_callback.set()
            t_reader.join(timeout=5)
            t_trigger.join(timeout=5)

        # The unrelated order is not caught by the algorithm-scoped halt...
        self.assertIsNone(holder["blocking"])
        self.assertFalse(holder["halted"])
        # ...and the halt itself was already in force during the callback.
        self.assertEqual(holder["active"], 1)

    def test_release_does_not_invoke_the_cancel_callback(self):
        calls = []
        ks = AsicKillSwitchManager(cancel_series_callback=lambda r: calls.append(r) or 0)
        ks.trigger_kill_switch(reason="r", actor="a")
        ks.reset_kill_switch(reason="cleared", actor="compliance")
        self.assertEqual(len(calls), 1)


class TestFilterParameterControl(unittest.TestCase):
    """Rule 5.6.3(1)(a) and RG 241.43-241.45: every change to a filter
    parameter must be recorded, and parameters must not be silently
    deactivatable."""

    def setUp(self):
        self.config = AsicMarketIntegrityConfig(500000.0, 10000, 0.05)
        self.filter = AsicAopPreTradeFilter(self.config, AsicKillSwitchManager())

    def test_config_is_immutable(self):
        # Regression: a mutable config allowed a mandatory control to be
        # widened after construction, bypassing validation and leaving no record.
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.config.max_order_value_aud = 1e18

    def test_filter_config_attribute_cannot_be_swapped_directly(self):
        with self.assertRaises(AttributeError):
            self.filter.config = AsicMarketIntegrityConfig(1e18, 10000, 0.05)

    def test_replace_config_requires_attribution(self):
        new = AsicMarketIntegrityConfig(600000.0, 10000, 0.05)
        with self.assertRaises(ValueError):
            self.filter.replace_config(new, authorised_by="", reason="widen")
        with self.assertRaises(ValueError):
            self.filter.replace_config(new, authorised_by="head-of-trading", reason="  ")
        # Refused changes must not take effect.
        self.assertEqual(self.filter.config.max_order_value_aud, 500000.0)

    def test_replace_config_applies_and_records_the_change(self):
        new = AsicMarketIntegrityConfig(600000.0, 10000, 0.05)
        self.filter.replace_config(
            new, authorised_by="head-of-trading", reason="approved uplift CR-42"
        )
        self.assertEqual(self.filter.config.max_order_value_aud, 600000.0)
        log = self.filter.parameter_audit_log
        self.assertEqual(len(log), 1)
        self.assertIsInstance(log[0], FilterParameterChange)
        self.assertEqual(log[0].authorised_by, "head-of-trading")
        self.assertEqual(log[0].previous.max_order_value_aud, 500000.0)
        self.assertEqual(log[0].replacement.max_order_value_aud, 600000.0)
        self.assertGreater(log[0].timestamp_unix, 0)

    def test_replaced_parameters_take_effect_on_the_next_check(self):
        order = AopOrderRequest(symbol="CBA.AX", price=100.0, qty=6000, reference_price=100.0)
        self.assertEqual(
            self.filter.run_checks(order).rejection_code, AopRejectionCode.VALUE_LIMIT
        )
        self.filter.replace_config(
            AsicMarketIntegrityConfig(700000.0, 10000, 0.05),
            authorised_by="head-of-trading",
            reason="approved uplift CR-42",
        )
        self.assertTrue(self.filter.run_checks(order).is_compliant)

    def test_replace_config_rejects_a_non_config_object(self):
        with self.assertRaises(TypeError):
            self.filter.replace_config(
                {"max_order_value_aud": 1e18}, authorised_by="x", reason="y"
            )

    def test_parameter_audit_log_returned_is_a_copy(self):
        self.filter.replace_config(
            AsicMarketIntegrityConfig(600000.0, 10000, 0.05),
            authorised_by="head-of-trading",
            reason="CR-42",
        )
        snapshot = self.filter.parameter_audit_log
        snapshot.clear()
        self.assertEqual(len(self.filter.parameter_audit_log), 1)

    def test_filter_rejects_a_non_config_object_at_construction(self):
        with self.assertRaises(TypeError):
            AsicAopPreTradeFilter({"max_order_value_aud": 1.0}, AsicKillSwitchManager())


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

    def test_inf_limit_rejected(self):
        with self.assertRaises(ValueError):
            AsicMarketIntegrityConfig(max_order_value_aud=math.inf, max_order_volume=100, max_price_deviation_pct=0.05)

    def test_non_int_volume_rejected(self):
        with self.assertRaises(ValueError):
            AsicMarketIntegrityConfig(max_order_value_aud=100.0, max_order_volume=100.5, max_price_deviation_pct=0.05)

    def test_bool_volume_rejected(self):
        # isinstance(True, int) is True; a bool limit means max_order_volume=1.
        with self.assertRaises(ValueError):
            AsicMarketIntegrityConfig(max_order_value_aud=100.0, max_order_volume=True, max_price_deviation_pct=0.05)

    def test_valid_config_accepted(self):
        cfg = AsicMarketIntegrityConfig(max_order_value_aud=1.0, max_order_volume=1, max_price_deviation_pct=1.0)
        self.assertEqual(cfg.max_order_volume, 1)


if __name__ == "__main__":
    unittest.main()
