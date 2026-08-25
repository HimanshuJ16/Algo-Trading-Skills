"""Unit tests for the execution-algorithm kill switch engine.

The tests that matter here are the ones that fail against a naive kill switch:
a NaN PnL that slips past a loss limit, a mass cancel the venue refuses, a
strategy-scoped kill mislabelled as FIX tag 530 = 1, a mistyped scope that
returns "normal operations", and a reset that re-arms order entry while orders
are still unconfirmed at the venue.
"""

import logging
import unittest
from datetime import datetime, timedelta, timezone

from execution_algorithm_kill_switch_integration import (
    ORDER_CANCELLED,
    ORDER_FILLED,
    ORDER_NEW,
    ORDER_PARTIALLY_FILLED,
    ORDER_PENDING_CANCEL,
    DISPATCH_ACCEPTED,
    DISPATCH_ERROR,
    DISPATCH_NO_GATEWAY,
    DISPATCH_REJECTED,
    MASS_CANCEL_ALL_ORDERS,
    SCOPE_GLOBAL,
    SCOPE_STRATEGY,
    STATUS_ENGAGED,
    STATUS_NORMAL,
    STATUS_REJECTED_KILL_SWITCH,
    STATUS_REJECTED_RISK_DATA,
    STATUS_RESET,
    TRIGGER_MAX_EXPOSURE_BREACH,
    TRIGGER_MAX_LOSS_BREACH,
    TRIGGER_RISK_DATA_INVALID,
    TRIGGER_RISK_DATA_STALE,
    TRIGGER_RUNAWAY_ORDER_RATE,
    ActiveStrategyOrder,
    ExecutionAlgoKillSwitchEngine,
    FirmRiskLimits,
    InMemoryKillSwitchGateway,
    KillSwitchError,
    MassCancelOutcome,
    NewOrderRequest,
    RiskSnapshot,
)

T0 = datetime(2026, 3, 10, 14, 30, 0, tzinfo=timezone.utc)


class FrozenClock:
    """Deterministic injectable clock."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def limits(**overrides):
    kwargs = dict(
        max_daily_loss_usd=10_000.0,
        max_order_rate_per_sec=100,
        max_net_exposure_usd=1_000_000.0,
    )
    kwargs.update(overrides)
    return FirmRiskLimits(**kwargs)


def order(cl_ord_id, strategy_id="STRAT_ALPHA", symbol="AAPL", venue="NYSE", **kw):
    kwargs = dict(side="BUY", order_qty=100, price=185.0, order_status=ORDER_NEW)
    kwargs.update(kw)
    return ActiveStrategyOrder(
        cl_ord_id=cl_ord_id, strategy_id=strategy_id, symbol=symbol, venue=venue, **kwargs
    )


def snapshot(pnl=0.0, exposure=0.0, rate=0, as_of=None):
    return RiskSnapshot(
        daily_pnl_usd=pnl,
        net_exposure_usd=exposure,
        order_rate_per_sec=rate,
        as_of=as_of,
    )


class KillSwitchTestCase(unittest.TestCase):
    """Silences the engine's (deliberately loud) CRITICAL logging under test."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.clock = FrozenClock()
        self.gateway = InMemoryKillSwitchGateway("NYSE")
        self.engine = ExecutionAlgoKillSwitchEngine(
            limits(), {"NYSE": self.gateway}, clock=self.clock
        )
        self.engine.register_active_order(order("ORD_01"))
        self.engine.register_active_order(order("ORD_02", symbol="MSFT", price=400.0))


# --------------------------------------------------------------- configuration


class TestConfigurationValidation(unittest.TestCase):
    def test_non_positive_loss_limit_rejected(self):
        with self.assertRaises(KillSwitchError):
            limits(max_daily_loss_usd=0.0)

    def test_nan_loss_limit_rejected(self):
        with self.assertRaises(KillSwitchError):
            limits(max_daily_loss_usd=float("nan"))

    def test_non_positive_rate_limit_rejected(self):
        with self.assertRaises(KillSwitchError):
            limits(max_order_rate_per_sec=0)

    def test_bool_is_not_an_int_rate_limit(self):
        with self.assertRaises(KillSwitchError):
            limits(max_order_rate_per_sec=True)

    def test_engine_rejects_non_limits_object(self):
        with self.assertRaises(KillSwitchError):
            ExecutionAlgoKillSwitchEngine({"max_daily_loss_usd": 10.0})

    def test_naive_clock_rejected(self):
        engine = ExecutionAlgoKillSwitchEngine(limits(), clock=lambda: datetime(2026, 1, 1))
        with self.assertRaises(KillSwitchError):
            engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")


class TestOrderValidation(unittest.TestCase):
    def test_zero_quantity_rejected(self):
        with self.assertRaises(KillSwitchError):
            order("ORD_X", order_qty=0)

    def test_unknown_side_rejected(self):
        with self.assertRaises(KillSwitchError):
            order("ORD_X", side="LONG")

    def test_filled_qty_above_order_qty_rejected(self):
        with self.assertRaises(KillSwitchError):
            order("ORD_X", order_qty=100, filled_qty=101)

    def test_blank_cl_ord_id_rejected(self):
        with self.assertRaises(KillSwitchError):
            order("   ")

    def test_remaining_qty(self):
        self.assertEqual(order("ORD_X", order_qty=100, filled_qty=40).remaining_qty, 60)

    def test_duplicate_live_cl_ord_id_rejected(self):
        engine = ExecutionAlgoKillSwitchEngine(limits())
        engine.register_active_order(order("ORD_01"))
        with self.assertRaises(KillSwitchError):
            engine.register_active_order(order("ORD_01", symbol="MSFT"))

    def test_cl_ord_id_reusable_after_terminal_state(self):
        engine = ExecutionAlgoKillSwitchEngine(limits())
        engine.register_active_order(order("ORD_01"))
        engine.apply_execution_report("ORD_01", ORDER_FILLED, filled_qty=100)
        engine.register_active_order(order("ORD_01", symbol="MSFT"))
        self.assertEqual(engine.active_orders["ORD_01"].symbol, "MSFT")


# --------------------------------------------------------------- global scope


class TestGlobalKillSwitch(KillSwitchTestCase):
    def test_engages_locks_entry_and_requests_mass_cancel(self):
        report = self.engine.trigger_kill_switch(
            SCOPE_GLOBAL, "MANUAL_OPERATOR_OVERRIDE", triggered_by="usr_risk_01"
        )

        self.assertTrue(report.is_kill_switch_active)
        self.assertEqual(report.status, STATUS_ENGAGED)
        self.assertEqual(report.trigger_scope, SCOPE_GLOBAL)
        self.assertEqual(report.cancel_requested_count, 2)
        self.assertEqual(report.fix_mass_cancel_tag_530, MASS_CANCEL_ALL_ORDERS)
        self.assertTrue(report.is_new_order_blocked)
        self.assertTrue(self.engine.is_global_kill_switch_engaged)
        self.assertEqual(report.timestamp_utc, "2026-03-10T14:30:00.000+00:00")
        self.assertEqual(report.triggered_by, "usr_risk_01")

    def test_mass_cancel_sent_as_tag_530_value_7(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.assertEqual(len(self.gateway.mass_cancel_requests), 1)
        sent = self.gateway.mass_cancel_requests[0]
        self.assertEqual(sent.mass_cancel_request_type, "7")
        self.assertEqual(sent.venue, "NYSE")
        self.assertEqual(sent.transact_time, T0)

    def test_orders_are_pending_cancel_not_cancelled_on_dispatch(self):
        """An accepted request is not a dead order. This is the whole point."""
        report = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        for cl_ord_id in ("ORD_01", "ORD_02"):
            self.assertEqual(
                self.engine.active_orders[cl_ord_id].order_status, ORDER_PENDING_CANCEL
            )
        self.assertEqual(report.pending_cancel_order_ids, ("ORD_01", "ORD_02"))
        self.assertEqual(report.uncancelled_order_ids, ())
        self.assertTrue(report.is_fully_dispatched)

    def test_execution_report_confirms_the_cancel(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.engine.apply_execution_report("ORD_01", ORDER_CANCELLED)
        self.assertEqual(self.engine.pending_cancel_order_ids, ("ORD_02",))

    def test_fill_after_cancel_request_is_surfaced(self):
        """The cancel/fill race: the order traded before the cancel landed."""
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        logging.disable(logging.NOTSET)
        with self.assertLogs(
            "execution_algorithm_kill_switch_integration", level="WARNING"
        ) as captured:
            self.engine.apply_execution_report("ORD_01", ORDER_FILLED, filled_qty=100)
        self.assertIn("post-kill exposure changed", "\n".join(captured.output))
        self.assertEqual(self.engine.active_orders["ORD_01"].order_status, ORDER_FILLED)

    def test_subsequent_orders_are_rejected(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        req = NewOrderRequest("ORD_NEW_01", "STRAT_ALPHA", "AAPL", "BUY", 100, 185.0)
        report = self.engine.audit_and_validate_new_order(req, snapshot())
        self.assertEqual(report.status, STATUS_REJECTED_KILL_SWITCH)
        self.assertTrue(report.is_new_order_blocked)

    def test_orders_of_every_strategy_are_rejected(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        req = NewOrderRequest("ORD_NEW_02", "STRAT_UNRELATED", "TSLA", "SELL", 10, 200.0)
        report = self.engine.audit_and_validate_new_order(req, snapshot())
        self.assertEqual(report.status, STATUS_REJECTED_KILL_SWITCH)

    def test_mass_cancel_goes_to_every_configured_gateway_even_with_no_local_orders(self):
        """The local book is a belief; a missed ack means orders you cannot see."""
        quiet = InMemoryKillSwitchGateway("CBOE")
        engine = ExecutionAlgoKillSwitchEngine(
            limits(), {"NYSE": self.gateway, "CBOE": quiet}, clock=self.clock
        )
        report = engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.assertEqual(len(quiet.mass_cancel_requests), 1)
        self.assertEqual({d.venue for d in report.dispatches}, {"NYSE", "CBOE"})

    def test_repeat_trigger_rechases_unconfirmed_orders(self):
        first = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        second = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL_AGAIN")
        self.assertFalse(first.is_repeat_trigger)
        self.assertTrue(second.is_repeat_trigger)
        self.assertEqual(second.cancel_requested_count, 2)
        self.assertEqual(len(self.gateway.mass_cancel_requests), 2)

    def test_confirmed_orders_are_not_rechased(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.engine.apply_execution_report("ORD_01", ORDER_CANCELLED)
        self.engine.apply_execution_report("ORD_02", ORDER_CANCELLED)
        second = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL_AGAIN")
        self.assertEqual(second.cancel_requested_count, 0)

    def test_event_ids_are_unique(self):
        a = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        b = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.assertNotEqual(a.event_id, b.event_id)


# ------------------------------------------------------- failed cancellation


class TestCancelDispatchFailures(KillSwitchTestCase):
    def test_venue_rejecting_mass_cancel_leaves_orders_live_and_escalates(self):
        """FIX tag 532 = 0 is 'Mass Cancel Not Supported' -- nothing was cancelled."""
        refusing = InMemoryKillSwitchGateway(
            "NYSE",
            outcome=MassCancelOutcome(
                accepted=False, mass_cancel_response="0", reject_reason="0"
            ),
        )
        engine = ExecutionAlgoKillSwitchEngine(
            limits(), {"NYSE": refusing}, clock=self.clock
        )
        engine.register_active_order(order("ORD_01"))
        report = engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")

        self.assertEqual(report.uncancelled_order_ids, ("ORD_01",))
        self.assertFalse(report.is_fully_dispatched)
        self.assertTrue(report.manual_intervention_required)
        self.assertEqual(report.dispatches[0].status, DISPATCH_REJECTED)
        self.assertEqual(report.dispatches[0].reject_reason, "0")
        # Order entry stays locked even though cancellation failed.
        self.assertTrue(report.is_new_order_blocked)
        self.assertEqual(engine.active_orders["ORD_01"].order_status, ORDER_NEW)

    def test_gateway_exception_is_contained_and_other_venues_still_cancel(self):
        broken = InMemoryKillSwitchGateway("NYSE", raises=ConnectionError("session down"))
        healthy = InMemoryKillSwitchGateway("CBOE")
        engine = ExecutionAlgoKillSwitchEngine(
            limits(), {"NYSE": broken, "CBOE": healthy}, clock=self.clock
        )
        engine.register_active_order(order("ORD_NY", venue="NYSE"))
        engine.register_active_order(order("ORD_CB", venue="CBOE"))
        report = engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")

        statuses = {d.venue: d.status for d in report.dispatches}
        self.assertEqual(statuses["NYSE"], DISPATCH_ERROR)
        self.assertEqual(statuses["CBOE"], DISPATCH_ACCEPTED)
        self.assertEqual(report.uncancelled_order_ids, ("ORD_NY",))
        self.assertEqual(engine.active_orders["ORD_CB"].order_status, ORDER_PENDING_CANCEL)
        self.assertTrue(engine.manual_intervention_required)

    def test_no_gateway_configured_still_locks_entry_but_reports_no_cancel(self):
        engine = ExecutionAlgoKillSwitchEngine(limits(), clock=self.clock)
        engine.register_active_order(order("ORD_01"))
        report = engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")

        self.assertTrue(engine.is_global_kill_switch_engaged)
        self.assertTrue(report.is_new_order_blocked)
        self.assertEqual(report.dispatches[0].status, DISPATCH_NO_GATEWAY)
        self.assertEqual(report.uncancelled_order_ids, ("ORD_01",))
        self.assertTrue(report.manual_intervention_required)

    def test_order_at_venue_without_a_gateway_is_reported_uncancelled(self):
        self.engine.register_active_order(order("ORD_DARK", venue="DARKPOOL"))
        report = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.assertIn("ORD_DARK", report.uncancelled_order_ids)
        self.assertTrue(report.manual_intervention_required)


# -------------------------------------------------------------- strategy scope


class TestStrategyKillSwitch(KillSwitchTestCase):
    def setUp(self):
        super().setUp()
        self.engine.register_active_order(order("ORD_03", strategy_id="STRAT_BETA"))

    def test_cancels_only_that_strategys_orders(self):
        report = self.engine.trigger_kill_switch(
            SCOPE_STRATEGY, "STRATEGY_DRAWDOWN", strategy_id="STRAT_BETA"
        )
        self.assertEqual(report.cancel_requested_count, 1)
        self.assertEqual(report.scope_target, "STRAT_BETA")
        self.assertEqual(self.gateway.cancelled_order_ids, ["ORD_03"])
        self.assertEqual(self.engine.active_orders["ORD_01"].order_status, ORDER_NEW)

    def test_does_not_claim_a_tag_530_mass_cancel(self):
        """530 = 1 is 'cancel orders for a security' -- the wrong scope entirely."""
        report = self.engine.trigger_kill_switch(
            SCOPE_STRATEGY, "STRATEGY_DRAWDOWN", strategy_id="STRAT_BETA"
        )
        self.assertIsNone(report.fix_mass_cancel_tag_530)
        self.assertEqual(self.gateway.mass_cancel_requests, [])

    def test_other_strategies_keep_trading(self):
        self.engine.trigger_kill_switch(
            SCOPE_STRATEGY, "STRATEGY_DRAWDOWN", strategy_id="STRAT_BETA"
        )
        blocked = self.engine.audit_and_validate_new_order(
            NewOrderRequest("N1", "STRAT_BETA", "AAPL", "BUY", 10, 1.0), snapshot()
        )
        allowed = self.engine.audit_and_validate_new_order(
            NewOrderRequest("N2", "STRAT_ALPHA", "AAPL", "BUY", 10, 1.0), snapshot()
        )
        self.assertEqual(blocked.status, STATUS_REJECTED_KILL_SWITCH)
        self.assertEqual(allowed.status, STATUS_NORMAL)

    def test_refused_individual_cancel_is_reported(self):
        gateway = InMemoryKillSwitchGateway("NYSE", unsupported_order_ids={"ORD_03"})
        engine = ExecutionAlgoKillSwitchEngine(
            limits(), {"NYSE": gateway}, clock=self.clock
        )
        engine.register_active_order(order("ORD_03", strategy_id="STRAT_BETA"))
        report = engine.trigger_kill_switch(
            SCOPE_STRATEGY, "STRATEGY_DRAWDOWN", strategy_id="STRAT_BETA"
        )
        self.assertEqual(report.uncancelled_order_ids, ("ORD_03",))
        self.assertTrue(report.manual_intervention_required)


class TestScopeMisuseFailsLoudly(KillSwitchTestCase):
    """A kill-switch call that is not understood must never look like success."""

    def test_unknown_scope_raises(self):
        with self.assertRaises(KillSwitchError):
            self.engine.trigger_kill_switch("PORTFOLIO", "MANUAL")
        self.assertFalse(self.engine.is_global_kill_switch_engaged)

    def test_strategy_scope_without_strategy_id_raises(self):
        with self.assertRaises(KillSwitchError):
            self.engine.trigger_kill_switch(SCOPE_STRATEGY, "MANUAL")

    def test_strategy_scope_with_blank_strategy_id_raises(self):
        with self.assertRaises(KillSwitchError):
            self.engine.trigger_kill_switch(SCOPE_STRATEGY, "MANUAL", strategy_id="  ")

    def test_lowercase_scope_is_accepted(self):
        report = self.engine.trigger_kill_switch("global", "MANUAL")
        self.assertEqual(report.trigger_scope, SCOPE_GLOBAL)

    def test_blank_reason_raises(self):
        with self.assertRaises(KillSwitchError):
            self.engine.trigger_kill_switch(SCOPE_GLOBAL, "")


# ------------------------------------------------------------- risk triggers


class TestRiskLimitTriggers(KillSwitchTestCase):
    def test_loss_breach_engages_on_signed_pnl(self):
        """PnL is signed: -12,500 is a 12,500 loss against a 10,000 limit."""
        report = self.engine.evaluate_risk_state(snapshot(pnl=-12_500.0))
        self.assertEqual(report.status, STATUS_ENGAGED)
        self.assertEqual(report.trigger_reason_code, TRIGGER_MAX_LOSS_BREACH)
        self.assertIn("MAX DAILY LOSS BREACH", report.trigger_reason)

    def test_loss_exactly_at_limit_breaches(self):
        report = self.engine.evaluate_risk_state(snapshot(pnl=-10_000.0))
        self.assertEqual(report.status, STATUS_ENGAGED)

    def test_one_cent_inside_the_limit_does_not_breach(self):
        report = self.engine.evaluate_risk_state(snapshot(pnl=-9_999.99))
        self.assertEqual(report.status, STATUS_NORMAL)
        self.assertFalse(self.engine.is_global_kill_switch_engaged)

    def test_profit_never_breaches_the_loss_limit(self):
        """Regression: a positive number passed as 'loss' used to fire the switch."""
        report = self.engine.evaluate_risk_state(snapshot(pnl=50_000.0))
        self.assertEqual(report.status, STATUS_NORMAL)

    def test_short_exposure_breaches_on_absolute_value(self):
        report = self.engine.evaluate_risk_state(snapshot(exposure=-1_000_000.0))
        self.assertEqual(report.status, STATUS_ENGAGED)
        self.assertEqual(report.trigger_reason_code, TRIGGER_MAX_EXPOSURE_BREACH)

    def test_long_exposure_breach(self):
        report = self.engine.evaluate_risk_state(snapshot(exposure=1_250_000.0))
        self.assertEqual(report.trigger_reason_code, TRIGGER_MAX_EXPOSURE_BREACH)

    def test_exposure_inside_limit_is_normal(self):
        report = self.engine.evaluate_risk_state(snapshot(exposure=999_999.99))
        self.assertEqual(report.status, STATUS_NORMAL)

    def test_reported_order_rate_breach(self):
        report = self.engine.evaluate_risk_state(snapshot(rate=100))
        self.assertEqual(report.trigger_reason_code, TRIGGER_RUNAWAY_ORDER_RATE)

    def test_loss_takes_precedence_over_rate_in_the_reason(self):
        report = self.engine.evaluate_risk_state(snapshot(pnl=-20_000.0, rate=500))
        self.assertEqual(report.trigger_reason_code, TRIGGER_MAX_LOSS_BREACH)

    def test_breach_on_the_order_path_engages_and_blocks_that_order(self):
        req = NewOrderRequest("ORD_NEW", "STRAT_BETA", "GOOGL", "BUY", 100, 150.0)
        report = self.engine.audit_and_validate_new_order(req, snapshot(pnl=-12_500.0))
        self.assertEqual(report.status, STATUS_ENGAGED)
        self.assertTrue(report.is_new_order_blocked)
        self.assertEqual(report.cancel_requested_count, 2)

    def test_supervisory_path_fires_with_no_order_flow(self):
        """A stuck algorithm stops submitting; the loss keeps growing anyway."""
        engine = ExecutionAlgoKillSwitchEngine(
            limits(), {"NYSE": self.gateway}, clock=self.clock
        )
        engine.register_active_order(order("ORD_09"))
        report = engine.evaluate_risk_state(snapshot(pnl=-11_000.0))
        self.assertEqual(report.status, STATUS_ENGAGED)
        self.assertEqual(report.triggered_by, "RISK_MONITOR")


class TestObservedOrderRate(KillSwitchTestCase):
    def test_engine_measures_the_rate_when_caller_supplies_none(self):
        """A looping algorithm cannot under-report a rate the gate counts itself."""
        engine = ExecutionAlgoKillSwitchEngine(
            limits(max_order_rate_per_sec=5), {"NYSE": self.gateway}, clock=self.clock
        )
        snap = RiskSnapshot(daily_pnl_usd=0.0, order_rate_per_sec=None)
        statuses = []
        for i in range(6):
            req = NewOrderRequest(f"N{i}", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0)
            statuses.append(engine.audit_and_validate_new_order(req, snap).status)
        self.assertEqual(statuses[:4], [STATUS_NORMAL] * 4)
        self.assertEqual(statuses[4], STATUS_ENGAGED)

    def test_attempts_age_out_of_the_one_second_window(self):
        engine = ExecutionAlgoKillSwitchEngine(
            limits(max_order_rate_per_sec=3), {"NYSE": self.gateway}, clock=self.clock
        )
        snap = RiskSnapshot(daily_pnl_usd=0.0, order_rate_per_sec=None)
        for i in range(2):
            engine.audit_and_validate_new_order(
                NewOrderRequest(f"A{i}", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0), snap
            )
        self.clock.advance(1.5)
        for i in range(2):
            report = engine.audit_and_validate_new_order(
                NewOrderRequest(f"B{i}", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0), snap
            )
        self.assertEqual(report.status, STATUS_NORMAL)

    def test_rejected_orders_still_count_toward_the_message_rate(self):
        """A rejection loop is still a message-rate problem (RTS 6 Art. 15(1))."""
        engine = ExecutionAlgoKillSwitchEngine(
            limits(max_order_rate_per_sec=3), {"NYSE": self.gateway}, clock=self.clock
        )
        engine.trigger_kill_switch(SCOPE_STRATEGY, "HALT", strategy_id="STRAT_ALPHA")
        snap = RiskSnapshot(daily_pnl_usd=0.0, order_rate_per_sec=None)
        for i in range(5):
            engine.audit_and_validate_new_order(
                NewOrderRequest(f"R{i}", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0), snap
            )
        other = engine.audit_and_validate_new_order(
            NewOrderRequest("OTHER", "STRAT_GAMMA", "AAPL", "BUY", 1, 1.0), snap
        )
        self.assertEqual(other.status, STATUS_ENGAGED)
        self.assertEqual(other.trigger_reason_code, TRIGGER_RUNAWAY_ORDER_RATE)


class TestUnusableRiskDataFailsClosed(KillSwitchTestCase):
    def test_nan_pnl_rejects_the_order_rather_than_passing_it(self):
        """NaN >= limit is False; a naive gate would wave this order through."""
        self.assertFalse(float("nan") >= 10_000.0)  # the trap, made explicit
        req = NewOrderRequest("ORD_NAN", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0)
        report = self.engine.audit_and_validate_new_order(
            req, RiskSnapshot(daily_pnl_usd=float("nan"))
        )
        self.assertEqual(report.status, STATUS_REJECTED_RISK_DATA)
        self.assertEqual(report.trigger_reason_code, TRIGGER_RISK_DATA_INVALID)
        self.assertTrue(report.is_new_order_blocked)

    def test_broken_feed_does_not_engage_a_firm_wide_kill(self):
        self.engine.audit_and_validate_new_order(
            NewOrderRequest("ORD_NAN", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0),
            RiskSnapshot(daily_pnl_usd=float("nan")),
        )
        self.assertFalse(self.engine.is_global_kill_switch_engaged)

    def test_infinite_exposure_rejected(self):
        report = self.engine.audit_and_validate_new_order(
            NewOrderRequest("ORD_INF", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0),
            RiskSnapshot(daily_pnl_usd=0.0, net_exposure_usd=float("inf")),
        )
        self.assertEqual(report.status, STATUS_REJECTED_RISK_DATA)

    def test_supervisory_path_raises_on_unusable_data(self):
        with self.assertRaises(KillSwitchError):
            self.engine.evaluate_risk_state(RiskSnapshot(daily_pnl_usd=float("nan")))

    def test_stale_snapshot_rejects_the_order(self):
        stale = snapshot(as_of=T0 - timedelta(seconds=30))
        report = self.engine.audit_and_validate_new_order(
            NewOrderRequest("ORD_STALE", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0), stale
        )
        self.assertEqual(report.status, STATUS_REJECTED_RISK_DATA)
        self.assertEqual(report.trigger_reason_code, TRIGGER_RISK_DATA_STALE)

    def test_fresh_snapshot_passes(self):
        fresh = snapshot(as_of=T0 - timedelta(seconds=1))
        report = self.engine.audit_and_validate_new_order(
            NewOrderRequest("ORD_FRESH", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0), fresh
        )
        self.assertEqual(report.status, STATUS_NORMAL)

    def test_staleness_gate_can_be_disabled(self):
        engine = ExecutionAlgoKillSwitchEngine(
            limits(max_snapshot_age_seconds=None), {"NYSE": self.gateway}, clock=self.clock
        )
        report = engine.audit_and_validate_new_order(
            NewOrderRequest("ORD_OLD", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0),
            snapshot(as_of=T0 - timedelta(hours=3)),
        )
        self.assertEqual(report.status, STATUS_NORMAL)

    def test_future_dated_snapshot_is_treated_as_clock_skew(self):
        """A fast risk-feed clock would otherwise make stale data look fresh."""
        skewed = snapshot(as_of=T0 + timedelta(seconds=30))
        report = self.engine.audit_and_validate_new_order(
            NewOrderRequest("ORD_SKEW", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0), skewed
        )
        self.assertEqual(report.status, STATUS_REJECTED_RISK_DATA)
        self.assertEqual(report.trigger_reason_code, TRIGGER_RISK_DATA_STALE)
        self.assertIn("CLOCK SKEW", report.trigger_reason)

    def test_small_forward_skew_inside_tolerance_passes(self):
        report = self.engine.audit_and_validate_new_order(
            NewOrderRequest("ORD_OK", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0),
            snapshot(as_of=T0 + timedelta(seconds=1)),
        )
        self.assertEqual(report.status, STATUS_NORMAL)

    def test_naive_snapshot_timestamp_rejected(self):
        with self.assertRaises(KillSwitchError):
            RiskSnapshot(daily_pnl_usd=0.0, as_of=datetime(2026, 3, 10, 14, 30)).validate()


# ---------------------------------------------------------------------- reset


class TestReset(KillSwitchTestCase):
    def test_reset_refuses_while_cancels_are_unconfirmed(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        with self.assertRaises(KillSwitchError):
            self.engine.reset(SCOPE_GLOBAL, authorized_by="usr_risk_01", reason="reviewed")
        self.assertTrue(self.engine.is_global_kill_switch_engaged)

    def test_reset_allowed_once_every_cancel_is_confirmed(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.engine.apply_execution_report("ORD_01", ORDER_CANCELLED)
        self.engine.apply_execution_report("ORD_02", ORDER_CANCELLED)
        report = self.engine.reset(
            SCOPE_GLOBAL, authorized_by="usr_risk_01", reason="bug patched"
        )
        self.assertEqual(report.status, STATUS_RESET)
        self.assertFalse(self.engine.is_global_kill_switch_engaged)
        self.assertFalse(report.is_new_order_blocked)

    def test_reset_can_be_forced_with_an_explicit_acknowledgement(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        report = self.engine.reset(
            SCOPE_GLOBAL,
            authorized_by="usr_risk_01",
            reason="venue book reconciled by hand",
            acknowledge_unconfirmed=True,
        )
        self.assertEqual(report.status, STATUS_RESET)
        self.assertEqual(report.pending_cancel_order_ids, ("ORD_01", "ORD_02"))

    def test_reset_records_who_and_why(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        report = self.engine.reset(
            SCOPE_GLOBAL,
            authorized_by="usr_risk_01",
            reason="bug patched",
            acknowledge_unconfirmed=True,
        )
        self.assertEqual(report.triggered_by, "usr_risk_01")
        self.assertIn("bug patched", report.audit_notes)
        self.assertIn("Venue-side kill switches are NOT lifted", report.audit_notes)

    def test_reset_requires_an_authoriser(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        with self.assertRaises(KillSwitchError):
            self.engine.reset(SCOPE_GLOBAL, authorized_by="  ", reason="x")

    def test_reset_refuses_after_a_failed_dispatch_even_with_no_pending_orders(self):
        engine = ExecutionAlgoKillSwitchEngine(limits(), clock=self.clock)
        engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")  # no gateway -> escalated
        with self.assertRaises(KillSwitchError):
            engine.reset(SCOPE_GLOBAL, authorized_by="usr_risk_01", reason="looks fine")

    def test_strategy_reset_only_clears_that_strategy(self):
        self.engine.trigger_kill_switch(SCOPE_STRATEGY, "HALT", strategy_id="STRAT_ALPHA")
        self.engine.trigger_kill_switch(SCOPE_STRATEGY, "HALT", strategy_id="STRAT_BETA")
        self.engine.reset(
            SCOPE_STRATEGY,
            authorized_by="usr_risk_01",
            reason="fixed",
            strategy_id="STRAT_ALPHA",
            acknowledge_unconfirmed=True,
        )
        self.assertFalse(self.engine.is_blocked("STRAT_ALPHA"))
        self.assertTrue(self.engine.is_blocked("STRAT_BETA"))

    def test_orders_flow_again_after_reset(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.engine.reset(
            SCOPE_GLOBAL,
            authorized_by="usr_risk_01",
            reason="cleared",
            acknowledge_unconfirmed=True,
        )
        report = self.engine.audit_and_validate_new_order(
            NewOrderRequest("N9", "STRAT_ALPHA", "AAPL", "BUY", 1, 1.0), snapshot()
        )
        self.assertEqual(report.status, STATUS_NORMAL)


# ------------------------------------------------------------------ audit log


class TestAuditTrail(KillSwitchTestCase):
    def test_triggers_and_resets_are_recorded(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.engine.reset(
            SCOPE_GLOBAL,
            authorized_by="usr_risk_01",
            reason="cleared",
            acknowledge_unconfirmed=True,
        )
        statuses = [r.status for r in self.engine.audit_trail]
        self.assertEqual(statuses, [STATUS_ENGAGED, STATUS_RESET])

    def test_strategy_attribution_is_preserved(self):
        """RTS 6 Art. 12(3): the firm must know which algorithm owns each order."""
        self.engine.trigger_kill_switch(SCOPE_STRATEGY, "HALT", strategy_id="STRAT_ALPHA")
        self.assertEqual(self.engine.audit_trail[-1].scope_target, "STRAT_ALPHA")

    def test_audit_trail_is_an_immutable_snapshot(self):
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        trail = self.engine.audit_trail
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.assertEqual(len(trail), 1)
        self.assertEqual(len(self.engine.audit_trail), 2)

    def test_active_orders_view_does_not_leak_the_internal_map(self):
        view = self.engine.active_orders
        view.pop("ORD_01")
        self.assertIn("ORD_01", self.engine.active_orders)


class TestPartialFillHandling(KillSwitchTestCase):
    def test_partially_filled_orders_are_in_scope_for_cancellation(self):
        self.engine.apply_execution_report("ORD_01", ORDER_PARTIALLY_FILLED, filled_qty=40)
        report = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.assertEqual(report.cancel_requested_count, 2)
        self.assertEqual(self.engine.active_orders["ORD_01"].remaining_qty, 60)

    def test_terminal_orders_are_not_chased(self):
        self.engine.apply_execution_report("ORD_01", ORDER_FILLED, filled_qty=100)
        report = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        self.assertEqual(report.cancel_requested_count, 1)

    def test_execution_report_for_unknown_order_raises(self):
        with self.assertRaises(KillSwitchError):
            self.engine.apply_execution_report("NOT_AN_ORDER", ORDER_CANCELLED)

    def test_overfill_rejected(self):
        with self.assertRaises(KillSwitchError):
            self.engine.apply_execution_report("ORD_01", ORDER_FILLED, filled_qty=101)

    def test_unknown_order_status_rejected(self):
        """An unmapped venue status would drop the order out of every sweep."""
        with self.assertRaises(KillSwitchError):
            self.engine.apply_execution_report("ORD_01", "REPLACED")
        self.assertEqual(self.engine.active_orders["ORD_01"].order_status, ORDER_NEW)

    def test_order_registered_while_engaged_is_flagged_and_rechased(self):
        """A late ack for an order that was in flight when the latch closed."""
        self.engine.trigger_kill_switch(SCOPE_GLOBAL, "MANUAL")
        logging.disable(logging.NOTSET)
        with self.assertLogs(
            "execution_algorithm_kill_switch_integration", level="CRITICAL"
        ) as captured:
            self.engine.register_active_order(order("ORD_LATE"))
        self.assertIn("NOT covered by the cancel sweep", "\n".join(captured.output))
        logging.disable(logging.CRITICAL)
        again = self.engine.trigger_kill_switch(SCOPE_GLOBAL, "RE_FIRE")
        self.assertIn("ORD_LATE", again.pending_cancel_order_ids)


if __name__ == "__main__":
    unittest.main()
