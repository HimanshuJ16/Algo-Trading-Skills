"""
Unit tests for kill-switch-and-drawdown-circuit-breakers skill.

Tests:
1. Position limit breach order veto.
2. Daily loss limit breach & automatic halt with force-flatten callback.
3. Max drawdown peak-equity high-water mark breach.
4. Broker position reconciliation desync detection.
5. Manual emergency kill switch trigger.
6. Mandatory human re-enable gate.
7. Backward compatibility with CircuitBreaker wrapper class.
8. Fail-closed behaviour on non-evaluable (NaN/Inf) risk inputs.
9. Reduce-only orders permitted while halted / over the position limit.
10. Constructor limit validation (percent-vs-fraction confusion, non-positive limits).
11. Alert-channel and force-flatten callback failure isolation and escalation.
12. Operator authorization, audit trail, and high-water-mark re-baselining on re-enable.
13. Capital-flow adjustment of the drawdown high-water mark.
14. Concurrency: a single halt response under simultaneous breach detection.
"""
import math
import threading
import unittest

from circuit_breaker import (
    BreachEventLog,
    CircuitBreaker,
    CircuitBreakerStatus,
    KillSwitchCircuitBreaker,
    OrderDecisionCode,
    is_risk_reducing,
)


class TestKillSwitchCircuitBreaker(unittest.TestCase):

    def setUp(self):
        self.alerts = []
        self.flatten_calls = 0

        def mock_alert(msg):
            self.alerts.append(msg)

        def mock_flatten():
            self.flatten_calls += 1

        self.mock_alert = mock_alert
        self.mock_flatten = mock_flatten

        self.engine = KillSwitchCircuitBreaker(
            max_position=100.0,
            max_daily_loss=5000.0,
            max_drawdown_pct=0.10,  # 10% max drawdown
            alert_fn=self.mock_alert,
            flatten_fn=self.mock_flatten,
        )

    # ---------------------------------------------------------------- baseline

    def test_position_limit_veto(self):
        # Current = 80, proposed = 30 -> 110 > 100 -> Veto!
        ok, reason = self.engine.check_proposed_order(
            proposed_position_delta=30.0, current_position_size=80.0
        )
        self.assertFalse(ok)
        self.assertIn("exceeds max limit", reason)
        self.assertTrue(reason.startswith(OrderDecisionCode.POSITION_LIMIT.value + ":"))

    def test_daily_loss_breach_and_flatten(self):
        is_halted, status = self.engine.check_pnl_and_drawdown(
            daily_pnl=-5500.0, current_equity=100_000.0
        )
        self.assertTrue(is_halted)
        self.assertTrue(self.engine.halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DAILY_LOSS)
        self.assertEqual(self.flatten_calls, 1)
        self.assertEqual(len(self.alerts), 1)
        self.assertTrue(self.engine.audit_log[-1].flatten_succeeded)

    def test_daily_loss_exact_threshold_triggers(self):
        # Boundary: the limit is inclusive (PnL == -max_daily_loss must halt).
        is_halted, _ = self.engine.check_pnl_and_drawdown(
            daily_pnl=-5000.0, current_equity=100_000.0
        )
        self.assertTrue(is_halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DAILY_LOSS)

    def test_daily_loss_just_inside_threshold_does_not_trigger(self):
        is_halted, _ = self.engine.check_pnl_and_drawdown(
            daily_pnl=-4999.99, current_equity=100_000.0
        )
        self.assertFalse(is_halted)
        self.assertFalse(self.engine.halted)
        self.assertEqual(self.flatten_calls, 0)

    def test_max_drawdown_high_watermark(self):
        # Establish peak equity at 100,000
        self.engine.check_pnl_and_drawdown(daily_pnl=0.0, current_equity=100_000.0)

        # Equity drops to 88,000 (12% drawdown > 10% limit)
        is_halted, status = self.engine.check_pnl_and_drawdown(
            daily_pnl=-1000.0, current_equity=88_000.0
        )
        self.assertTrue(is_halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DRAWDOWN)
        self.assertEqual(self.flatten_calls, 1)
        # Independently derived: (100000 - 88000) / 100000 == 0.12
        self.assertAlmostEqual(self.engine.audit_log[-1].drawdown_pct, 12_000 / 100_000, places=12)

    def test_broker_position_reconciliation_desync(self):
        internal_pos = {"NIFTY": 50.0}
        broker_pos = {"NIFTY": 0.0}  # Fill status desync!

        is_reconciled = self.engine.reconcile_broker_positions(internal_pos, broker_pos)
        self.assertFalse(is_reconciled)
        self.assertTrue(self.engine.halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DESYNC)

    def test_manual_kill_switch_and_human_reenable(self):
        self.engine.trigger_emergency_kill_switch("Unusual latency spike observed")
        self.assertTrue(self.engine.halted)

        # Attempt order while halted
        ok, reason = self.engine.check_proposed_order(10.0, 0.0)
        self.assertFalse(ok)

        # Human re-enable gate
        granted = self.engine.human_re_enable(
            authorized_user="risk_mgr_alice", reason="Latency issue resolved"
        )
        self.assertTrue(granted)
        self.assertFalse(self.engine.halted)

        # Order now passes
        ok2, reason2 = self.engine.check_proposed_order(10.0, 0.0)
        self.assertTrue(ok2)

    def test_backward_compatibility(self):
        cb = CircuitBreaker(
            max_position=100,
            max_daily_loss=1000,
            max_drawdown_pct=0.05,
            alert_fn=self.mock_alert,
        )
        ok, r = cb.check_order(50, 60)
        self.assertFalse(ok)
        self.assertEqual(r, "position_limit")

        halted = cb.check_pnl(-1200, 10000)
        self.assertTrue(halted)

    def test_wrapper_halt_cannot_be_cleared_by_assignment(self):
        # Regression: the wrapper used to expose a `halted` setter, so any caller could
        # clear a halt with no operator, no reason, and no audit row -- an unaudited
        # bypass of the human re-enable gate.
        cb = CircuitBreaker(
            max_position=100,
            max_daily_loss=1000,
            max_drawdown_pct=0.05,
            alert_fn=self.mock_alert,
        )
        self.assertTrue(cb.check_pnl(-1200, 10000))
        self.assertTrue(cb.halted)

        with self.assertRaises(AttributeError):
            cb.halted = False
        self.assertTrue(cb.halted)
        self.assertEqual(cb.engine.re_enable_log, [])

        # The audited path is the only way out, and it leaves evidence.
        self.assertTrue(
            cb.human_re_enable(
                authorized_user="risk_mgr_alice",
                reason="Loss limit breach investigated; cause understood",
                new_peak_equity=10000,
            )
        )
        self.assertFalse(cb.halted)
        self.assertEqual(len(cb.engine.re_enable_log), 1)
        entry = cb.engine.re_enable_log[0]
        self.assertTrue(entry.granted)
        self.assertEqual(entry.authorized_user, "risk_mgr_alice")
        self.assertIn("investigated", entry.reason)
        self.assertEqual(entry.cleared_status, CircuitBreakerStatus.HALTED_DAILY_LOSS)

    # ------------------------------------------------- fail-closed on bad input

    def test_nan_daily_pnl_halts_instead_of_failing_open(self):
        # Regression: NaN compares False against every threshold, so the old code
        # returned "ok" and silently left both the loss and drawdown limits inert.
        is_halted, status = self.engine.check_pnl_and_drawdown(
            daily_pnl=float("nan"), current_equity=100_000.0
        )
        self.assertTrue(is_halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_INVALID_INPUT)
        self.assertEqual(self.flatten_calls, 1)

    def test_nan_equity_halts_instead_of_failing_open(self):
        self.engine.check_pnl_and_drawdown(daily_pnl=0.0, current_equity=100_000.0)
        is_halted, _ = self.engine.check_pnl_and_drawdown(
            daily_pnl=-10.0, current_equity=float("nan")
        )
        self.assertTrue(is_halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_INVALID_INPUT)
        # The poisoned value must not become the high-water mark.
        self.assertFalse(math.isnan(self.engine.peak_equity))

    def test_infinite_equity_halts(self):
        is_halted, _ = self.engine.check_pnl_and_drawdown(
            daily_pnl=0.0, current_equity=float("inf")
        )
        self.assertTrue(is_halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_INVALID_INPUT)

    def test_non_positive_peak_equity_halts(self):
        # A wiped account leaves drawdown undefined; the old guard returned 0.0 drawdown
        # and therefore never tripped.
        is_halted, _ = self.engine.check_pnl_and_drawdown(daily_pnl=-10.0, current_equity=0.0)
        self.assertTrue(is_halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_INVALID_INPUT)

    def test_non_finite_order_size_rejected_without_halting(self):
        ok, reason = self.engine.check_proposed_order(float("nan"), 10.0, symbol="AAPL")
        self.assertFalse(ok)
        self.assertTrue(reason.startswith(OrderDecisionCode.INVALID_INPUT.value + ":"))
        # A malformed order request is a strategy bug, not a reason to liquidate the book.
        self.assertFalse(self.engine.halted)

    # ------------------------------------------------------------- reduce-only

    def test_is_risk_reducing_classification(self):
        self.assertTrue(is_risk_reducing(100.0, -100.0))   # full close
        self.assertTrue(is_risk_reducing(100.0, -40.0))    # partial close
        self.assertTrue(is_risk_reducing(-100.0, 40.0))    # partial close of a short
        self.assertFalse(is_risk_reducing(100.0, 10.0))    # adds exposure
        self.assertFalse(is_risk_reducing(0.0, -50.0))     # opening from flat
        self.assertFalse(is_risk_reducing(100.0, -180.0))  # reversal into a new short
        self.assertFalse(is_risk_reducing(100.0, -200.0))  # symmetric reversal
        self.assertFalse(is_risk_reducing(100.0, 0.0))     # no-op

    def test_reduce_only_order_permitted_while_halted(self):
        # Regression: the workflow routes every order through check_proposed_order, so a
        # blanket reject-while-halted made the breaker veto its own force-flatten.
        self.engine.trigger_emergency_kill_switch("test")
        ok, reason = self.engine.check_proposed_order(
            proposed_position_delta=-50.0, current_position_size=50.0, symbol="AAPL"
        )
        self.assertTrue(ok)
        self.assertTrue(reason.startswith(OrderDecisionCode.REDUCE_ONLY_ALLOWED.value + ":"))

    def test_exposure_increasing_order_still_blocked_while_halted(self):
        self.engine.trigger_emergency_kill_switch("test")
        ok, reason = self.engine.check_proposed_order(10.0, 50.0)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith(OrderDecisionCode.HALTED.value + ":"))

    def test_reversal_blocked_while_halted(self):
        self.engine.trigger_emergency_kill_switch("test")
        ok, reason = self.engine.check_proposed_order(-180.0, 100.0)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith(OrderDecisionCode.HALTED.value + ":"))

    def test_reduce_only_permitted_when_already_over_position_limit(self):
        # Position 150 already exceeds the 100 limit; trimming to 120 is still over the
        # limit but strictly de-risking and must not be vetoed.
        ok, reason = self.engine.check_proposed_order(
            proposed_position_delta=-30.0, current_position_size=150.0
        )
        self.assertTrue(ok)
        self.assertTrue(reason.startswith(OrderDecisionCode.REDUCE_ONLY_ALLOWED.value + ":"))

    def test_reduce_only_can_be_disabled(self):
        strict = KillSwitchCircuitBreaker(
            max_position=100.0,
            max_daily_loss=5000.0,
            max_drawdown_pct=0.10,
            alert_fn=self.mock_alert,
            flatten_fn=self.mock_flatten,
            allow_reduce_only_when_halted=False,
        )
        strict.trigger_emergency_kill_switch("test")
        ok, _ = strict.check_proposed_order(-50.0, 50.0)
        self.assertFalse(ok)

    # --------------------------------------------------- constructor validation

    def test_drawdown_pct_given_as_percent_is_rejected(self):
        # Regression: max_drawdown_pct=10 meaning "10%" silently disabled the drawdown
        # breaker for the lifetime of the process.
        with self.assertRaises(ValueError):
            KillSwitchCircuitBreaker(max_position=100.0, max_daily_loss=5000.0, max_drawdown_pct=10)

    def test_invalid_limits_rejected(self):
        for kwargs in (
            {"max_position": 0.0, "max_daily_loss": 5000.0, "max_drawdown_pct": 0.1},
            {"max_position": -1.0, "max_daily_loss": 5000.0, "max_drawdown_pct": 0.1},
            {"max_position": 100.0, "max_daily_loss": 0.0, "max_drawdown_pct": 0.1},
            {"max_position": 100.0, "max_daily_loss": 5000.0, "max_drawdown_pct": 0.0},
            {"max_position": 100.0, "max_daily_loss": 5000.0, "max_drawdown_pct": float("nan")},
            {"max_position": float("inf"), "max_daily_loss": 5000.0, "max_drawdown_pct": 0.1},
            {
                "max_position": 100.0,
                "max_daily_loss": 5000.0,
                "max_drawdown_pct": 0.1,
                "desync_tolerance_units": -0.1,
            },
            {
                "max_position": 100.0,
                "max_daily_loss": 5000.0,
                "max_drawdown_pct": 0.1,
                "max_consecutive_rejections": 0,
            },
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    KillSwitchCircuitBreaker(**kwargs)

    def test_negative_max_daily_loss_is_normalized(self):
        engine = KillSwitchCircuitBreaker(
            max_position=100.0, max_daily_loss=-5000.0, max_drawdown_pct=0.1
        )
        self.assertEqual(engine.max_daily_loss, 5000.0)

    # ------------------------------------------------------- callback isolation

    def test_failing_alert_channel_does_not_prevent_flatten(self):
        # Regression: an alert_fn raising (the common case when the network is the very
        # thing that is broken) propagated out of _trigger_halt and skipped the flatten.
        def exploding_alert(msg):
            raise ConnectionError("PagerDuty unreachable")

        engine = KillSwitchCircuitBreaker(
            max_position=100.0,
            max_daily_loss=5000.0,
            max_drawdown_pct=0.10,
            alert_fn=exploding_alert,
            flatten_fn=self.mock_flatten,
        )
        is_halted, _ = engine.check_pnl_and_drawdown(daily_pnl=-6000.0, current_equity=100_000.0)
        self.assertTrue(is_halted)
        self.assertEqual(self.flatten_calls, 1)
        self.assertIn("ConnectionError", engine.audit_log[-1].alert_error)

    def test_flatten_failure_is_escalated_out_of_band_and_recorded(self):
        def exploding_flatten():
            raise TimeoutError("broker liquidation endpoint timed out")

        engine = KillSwitchCircuitBreaker(
            max_position=100.0,
            max_daily_loss=5000.0,
            max_drawdown_pct=0.10,
            alert_fn=self.mock_alert,
            flatten_fn=exploding_flatten,
        )
        engine.check_pnl_and_drawdown(daily_pnl=-6000.0, current_equity=100_000.0)

        entry = engine.audit_log[-1]
        self.assertTrue(entry.flatten_attempted)
        self.assertFalse(entry.flatten_succeeded)
        self.assertIn("TimeoutError", entry.flatten_error)
        # Two alerts: the breach itself, then the far more urgent flatten failure.
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("FORCE-FLATTEN FAILED", self.alerts[1])
        self.assertIn("MANUAL INTERVENTION REQUIRED", self.alerts[1])

    # ------------------------------------------------------- human re-enable gate

    def test_re_enable_requires_operator_identity_and_reason(self):
        # Regression: human_re_enable previously cleared the halt unconditionally and
        # returned True for any input, including an empty operator string.
        self.engine.trigger_emergency_kill_switch("test")
        self.assertFalse(self.engine.human_re_enable("", "some reason"))
        self.assertFalse(self.engine.human_re_enable("   ", "some reason"))
        self.assertFalse(self.engine.human_re_enable("alice", ""))
        self.assertTrue(self.engine.halted)

    def test_re_enable_enforces_operator_allowlist(self):
        engine = KillSwitchCircuitBreaker(
            max_position=100.0,
            max_daily_loss=5000.0,
            max_drawdown_pct=0.10,
            alert_fn=self.mock_alert,
            flatten_fn=self.mock_flatten,
            authorized_operators=["risk_mgr_alice"],
        )
        engine.trigger_emergency_kill_switch("test")
        self.assertFalse(engine.human_re_enable("random_dev_bob", "looks fine to me"))
        self.assertTrue(engine.halted)
        self.assertTrue(engine.human_re_enable("risk_mgr_alice", "root cause confirmed"))
        self.assertFalse(engine.halted)

    def test_re_enable_when_not_halted_is_refused(self):
        self.assertFalse(self.engine.human_re_enable("risk_mgr_alice", "nothing wrong"))

    def test_every_re_enable_attempt_is_audited(self):
        self.engine.trigger_emergency_kill_switch("test")
        self.engine.human_re_enable("", "blank operator")
        self.engine.human_re_enable("risk_mgr_alice", "root cause confirmed")

        self.assertEqual(len(self.engine.re_enable_log), 2)
        refused, granted = self.engine.re_enable_log
        self.assertFalse(refused.granted)
        self.assertIsNotNone(refused.rejection_reason)
        self.assertTrue(granted.granted)
        self.assertEqual(granted.authorized_user, "risk_mgr_alice")
        self.assertEqual(granted.cleared_status, CircuitBreakerStatus.HALTED_MANUAL_KILL)

    def test_re_enable_after_drawdown_halt_needs_explicit_rebaseline(self):
        self.engine.check_pnl_and_drawdown(daily_pnl=0.0, current_equity=100_000.0)
        self.engine.check_pnl_and_drawdown(daily_pnl=-1000.0, current_equity=88_000.0)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DRAWDOWN)

        # Re-enabling without re-baselining leaves the breached high-water mark in place,
        # so the very next evaluation re-halts. That is correct, and must be visible.
        self.assertTrue(self.engine.human_re_enable("risk_mgr_alice", "reviewed"))
        is_halted, _ = self.engine.check_pnl_and_drawdown(daily_pnl=-1000.0, current_equity=88_000.0)
        self.assertTrue(is_halted)

        # With an explicit operator-chosen re-baseline, trading resumes.
        self.assertTrue(
            self.engine.human_re_enable("risk_mgr_alice", "reviewed", new_peak_equity=88_000.0)
        )
        is_halted2, _ = self.engine.check_pnl_and_drawdown(daily_pnl=-1000.0, current_equity=88_000.0)
        self.assertFalse(is_halted2)
        self.assertEqual(self.engine.peak_equity, 88_000.0)

    def test_re_enable_rejects_invalid_rebaseline(self):
        self.engine.trigger_emergency_kill_switch("test")
        self.assertFalse(self.engine.human_re_enable("alice", "reviewed", new_peak_equity=0.0))
        self.assertFalse(
            self.engine.human_re_enable("alice", "reviewed", new_peak_equity=float("nan"))
        )
        self.assertTrue(self.engine.halted)

    # ------------------------------------------------------------ capital flows

    def test_withdrawal_does_not_trip_drawdown_breaker(self):
        # Regression: a scheduled 15% withdrawal against a 10% drawdown limit tripped the
        # kill switch and flattened a perfectly healthy book.
        self.engine.check_pnl_and_drawdown(daily_pnl=0.0, current_equity=100_000.0)
        self.engine.record_capital_flow(-15_000.0)
        self.assertEqual(self.engine.peak_equity, 85_000.0)

        is_halted, _ = self.engine.check_pnl_and_drawdown(daily_pnl=0.0, current_equity=85_000.0)
        self.assertFalse(is_halted)
        self.assertEqual(self.flatten_calls, 0)

    def test_deposit_does_not_mask_subsequent_drawdown(self):
        self.engine.check_pnl_and_drawdown(daily_pnl=0.0, current_equity=100_000.0)
        self.engine.record_capital_flow(50_000.0)
        self.assertEqual(self.engine.peak_equity, 150_000.0)

        # 150,000 -> 133,000 is an 11.33% real drawdown and must still halt. daily_pnl is
        # kept inside the 5,000 daily limit so this asserts the DRAWDOWN path specifically.
        is_halted, _ = self.engine.check_pnl_and_drawdown(daily_pnl=-1_000.0, current_equity=133_000.0)
        self.assertTrue(is_halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DRAWDOWN)

    def test_capital_flow_before_first_equity_observation_is_a_noop(self):
        self.assertIsNone(self.engine.record_capital_flow(1_000.0))
        self.assertIsNone(self.engine.peak_equity)

    def test_capital_flow_rejects_non_finite_amount(self):
        with self.assertRaises(ValueError):
            self.engine.record_capital_flow(float("nan"))

    # --------------------------------------------------------- reconciliation

    def test_reconciliation_reports_deterministic_symbol(self):
        # Regression: iterating an unordered set made the symbol named in the audit log
        # vary across processes under hash randomization.
        internal = {"ZZZZ": 10.0, "AAAA": 10.0, "MMMM": 10.0}
        broker = {"ZZZZ": 0.0, "AAAA": 0.0, "MMMM": 0.0}
        self.assertFalse(self.engine.reconcile_broker_positions(internal, broker))
        self.assertIn("'AAAA'", self.engine.audit_log[-1].reason)

    def test_reconciliation_within_tolerance_passes(self):
        self.assertTrue(
            self.engine.reconcile_broker_positions({"AAPL": 10.0}, {"AAPL": 10.0005})
        )
        self.assertFalse(self.engine.halted)

    def test_reconciliation_of_clean_book_while_halted_returns_true(self):
        self.engine.trigger_emergency_kill_switch("test")
        self.assertTrue(self.engine.reconcile_broker_positions({"AAPL": 10.0}, {"AAPL": 10.0}))

    def test_reconciliation_desync_while_halted_does_not_retrigger_response(self):
        self.engine.trigger_emergency_kill_switch("test")
        entries_before = len(self.engine.audit_log)
        flattens_before = self.flatten_calls

        self.assertFalse(self.engine.reconcile_broker_positions({"AAPL": 10.0}, {"AAPL": 0.0}))
        self.assertEqual(len(self.engine.audit_log), entries_before)
        self.assertEqual(self.flatten_calls, flattens_before)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_MANUAL_KILL)

    def test_reconciliation_of_non_finite_quantity_halts(self):
        self.assertFalse(
            self.engine.reconcile_broker_positions({"AAPL": float("nan")}, {"AAPL": 10.0})
        )
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DESYNC)

    def test_reconciliation_detects_symbol_missing_from_broker(self):
        self.assertFalse(self.engine.reconcile_broker_positions({"AAPL": 5.0}, {}))
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DESYNC)

    # --------------------------------------------------------------- escalation

    def test_consecutive_rejection_escalation_halts(self):
        engine = KillSwitchCircuitBreaker(
            max_position=100.0,
            max_daily_loss=5000.0,
            max_drawdown_pct=0.10,
            alert_fn=self.mock_alert,
            flatten_fn=self.mock_flatten,
            max_consecutive_rejections=3,
        )
        for _ in range(2):
            engine.check_proposed_order(500.0, 0.0)
        self.assertFalse(engine.halted)

        engine.check_proposed_order(500.0, 0.0)
        self.assertTrue(engine.halted)
        self.assertEqual(engine.status, CircuitBreakerStatus.HALTED_POSITION_LIMIT)
        self.assertEqual(self.flatten_calls, 1)

    def test_escalation_counter_resets_on_accepted_order(self):
        engine = KillSwitchCircuitBreaker(
            max_position=100.0,
            max_daily_loss=5000.0,
            max_drawdown_pct=0.10,
            max_consecutive_rejections=3,
        )
        engine.check_proposed_order(500.0, 0.0)
        engine.check_proposed_order(500.0, 0.0)
        engine.check_proposed_order(10.0, 0.0)  # accepted -> counter resets
        engine.check_proposed_order(500.0, 0.0)
        engine.check_proposed_order(500.0, 0.0)
        self.assertFalse(engine.halted)

    def test_escalation_disabled_by_default(self):
        for _ in range(50):
            self.engine.check_proposed_order(500.0, 0.0)
        self.assertFalse(self.engine.halted)

    # -------------------------------------------------------------- halt status

    def test_status_retains_first_halt_cause(self):
        self.engine.check_pnl_and_drawdown(daily_pnl=-6000.0, current_equity=100_000.0)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DAILY_LOSS)

        self.engine.trigger_emergency_kill_switch("operator follow-up")
        # Root cause is preserved for the operator; the manual kill is still in the log
        # and still re-runs the liquidation response.
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DAILY_LOSS)
        self.assertEqual(self.engine.audit_log[-1].status, CircuitBreakerStatus.HALTED_MANUAL_KILL)
        self.assertEqual(self.flatten_calls, 2)

    def test_breach_audit_entry_captures_position_snapshot(self):
        self.engine.check_pnl_and_drawdown(
            daily_pnl=-6000.0, current_equity=100_000.0, active_positions={"AAPL": 12.0}
        )
        entry = self.engine.audit_log[-1]
        self.assertIsInstance(entry, BreachEventLog)
        self.assertEqual(entry.positions_snapshot, {"AAPL": 12.0})
        self.assertEqual(entry.current_equity, 100_000.0)

    # -------------------------------------------------------------- concurrency

    def test_simultaneous_breach_detection_flattens_once(self):
        counter_lock = threading.Lock()
        calls = []

        def counting_flatten():
            with counter_lock:
                calls.append(1)

        engine = KillSwitchCircuitBreaker(
            max_position=100.0,
            max_daily_loss=5000.0,
            max_drawdown_pct=0.10,
            alert_fn=self.mock_alert,
            flatten_fn=counting_flatten,
        )
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait()
            engine.check_pnl_and_drawdown(daily_pnl=-9999.0, current_equity=100_000.0)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(engine.halted)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(engine.audit_log), 1)

    # ------------------------------------------------ backward-compat wrapper

    def test_backward_compat_halt_is_not_misreported_as_position_limit(self):
        # Regression: the wrapper classified rejections by sniffing for the substring
        # "position", so a HALTED_POSITION_LIMIT halt was reported as "position_limit"
        # rather than "halted".
        cb = CircuitBreaker(
            max_position=100, max_daily_loss=1000, max_drawdown_pct=0.05, alert_fn=self.mock_alert
        )
        cb.engine.max_consecutive_rejections = 1
        ok, reason = cb.check_order(500, 0)
        self.assertFalse(ok)
        self.assertEqual(reason, "position_limit")
        self.assertEqual(cb.engine.status, CircuitBreakerStatus.HALTED_POSITION_LIMIT)

        ok2, reason2 = cb.check_order(10, 0)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "halted")

    def test_backward_compat_reports_invalid_input(self):
        cb = CircuitBreaker(
            max_position=100, max_daily_loss=1000, max_drawdown_pct=0.05, alert_fn=self.mock_alert
        )
        ok, reason = cb.check_order(float("inf"), 0)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_input")


if __name__ == "__main__":
    unittest.main()
