"""Behavioural tests for the capital-preservation kill switch.

Time-dependent behaviour is driven by an injected fake clock rather than
``time.sleep``, so the rolling-window and staleness boundaries are asserted
exactly instead of approximately.
"""

import json
import logging
import threading
import unittest

from capital_preservation_engine import (
    ORDER_RATE_WINDOW_SECONDS,
    RESET_TOKEN_ENV_VAR,
    CapitalPreservationEngine,
    EngineState,
    HaltRecord,
    PreservationLimits,
    ResetAuthorizer,
)

logging.disable(logging.CRITICAL)

TEST_TOKEN = "unit-test-reset-token"


class FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_engine(clock=None, on_halt=None, **limit_overrides):
    limits = PreservationLimits(
        **{
            "max_daily_drawdown_usd": 10000.0,
            "max_orders_per_minute": 5,
            "max_consecutive_errors": 3,
            **limit_overrides,
        }
    )
    return CapitalPreservationEngine(
        limits,
        authorizer=ResetAuthorizer(expected_token=TEST_TOKEN),
        clock=clock or FakeClock(),
        on_halt=on_halt,
    )


class TestLimitsValidation(unittest.TestCase):
    """A misconfigured limit must fail loudly at construction, not silently at runtime."""

    def test_nan_drawdown_limit_rejected(self):
        # nan >= limit is always False, so a NaN limit would disable the
        # drawdown control entirely rather than tightening it.
        with self.assertRaises(ValueError):
            PreservationLimits(max_daily_drawdown_usd=float("nan"))

    def test_infinite_drawdown_limit_rejected(self):
        with self.assertRaises(ValueError):
            PreservationLimits(max_daily_drawdown_usd=float("inf"))

    def test_non_positive_limits_rejected(self):
        with self.assertRaises(ValueError):
            PreservationLimits(max_daily_drawdown_usd=0.0)
        with self.assertRaises(ValueError):
            PreservationLimits(max_daily_drawdown_usd=-1.0)
        with self.assertRaises(ValueError):
            PreservationLimits(max_orders_per_minute=0)
        with self.assertRaises(ValueError):
            PreservationLimits(max_consecutive_errors=0)

    def test_non_integer_count_limits_rejected(self):
        with self.assertRaises(TypeError):
            PreservationLimits(max_orders_per_minute=5.5)

    def test_optional_limits_default_to_disabled(self):
        limits = PreservationLimits()
        self.assertIsNone(limits.max_daily_loss_usd)
        self.assertIsNone(limits.max_pnl_staleness_seconds)

    def test_optional_limits_still_validated_when_supplied(self):
        with self.assertRaises(ValueError):
            PreservationLimits(max_daily_loss_usd=-5.0)
        with self.assertRaises(ValueError):
            PreservationLimits(max_pnl_staleness_seconds=float("nan"))


class TestNormalOperation(unittest.TestCase):
    def test_profitable_session_does_not_halt(self):
        engine = build_engine()
        self.assertTrue(engine.check_order_allowed())
        engine.update_pnl(5000.0, -1000.0)  # net +4000
        self.assertTrue(engine.check_order_allowed())
        self.assertEqual(engine.state, EngineState.ACTIVE)
        self.assertEqual(engine.current_drawdown, 0.0)

    def test_staleness_check_disabled_by_default(self):
        engine = build_engine()
        # No update_pnl has ever been called; with no staleness limit set the
        # gate must not block.
        self.assertTrue(engine.check_order_allowed())


class TestDrawdownAccounting(unittest.TestCase):
    """Drawdown is peak-to-trough, seeded at flat."""

    def test_straight_line_loss_from_flat_halts(self):
        engine = build_engine()
        engine.update_pnl(-2000.0, -9000.0)  # net -11000, peak 0 -> drawdown 11000
        self.assertEqual(engine.current_drawdown, 11000.0)
        self.assertEqual(engine.state, EngineState.HALTED)
        self.assertFalse(engine.check_order_allowed())
        self.assertIn("drawdown", engine.halt_reason.lower())

    def test_giveback_from_intraday_peak_halts_while_still_profitable(self):
        # Regression test for the defect this skill previously had: drawdown
        # was computed as abs(total P&L) only when total P&L was negative, so a
        # +40,000 -> +29,000 give-back reported a drawdown of 0.0 and never
        # tripped. Peak-to-trough is 11,000 > the 10,000 limit, so it must halt
        # even though the session is up on the day.
        engine = build_engine()
        engine.update_pnl(40000.0, 0.0)
        self.assertEqual(engine.state, EngineState.ACTIVE)
        engine.update_pnl(29000.0, 0.0)
        self.assertEqual(engine.peak_session_pnl, 40000.0)
        self.assertEqual(engine.current_drawdown, 11000.0)
        self.assertEqual(engine.state, EngineState.HALTED)

    def test_drawdown_exactly_at_limit_halts(self):
        engine = build_engine()
        engine.update_pnl(-10000.0, 0.0)
        self.assertEqual(engine.state, EngineState.HALTED)

    def test_drawdown_one_cent_below_limit_does_not_halt(self):
        engine = build_engine()
        engine.update_pnl(-9999.99, 0.0)
        self.assertEqual(engine.state, EngineState.ACTIVE)

    def test_peak_never_ratchets_down(self):
        engine = build_engine()
        engine.update_pnl(8000.0, 0.0)
        engine.update_pnl(3000.0, 0.0)
        engine.update_pnl(4000.0, 0.0)
        self.assertEqual(engine.peak_session_pnl, 8000.0)
        self.assertEqual(engine.current_drawdown, 4000.0)

    def test_absolute_session_loss_limit_catches_what_drawdown_allows(self):
        # A 100k peak makes any loss look like a modest give-back to a drawdown
        # limit; the absolute loss limit is the control that still fires.
        engine = build_engine(max_daily_drawdown_usd=1_000_000.0, max_daily_loss_usd=20000.0)
        engine.update_pnl(100000.0, 0.0)
        engine.update_pnl(-20000.0, 0.0)
        self.assertEqual(engine.state, EngineState.HALTED)
        self.assertIn("Session loss", engine.halt_reason)


class TestNonFinitePnl(unittest.TestCase):
    """A NaN P&L must fail closed, not silently disable the drawdown control."""

    def test_nan_pnl_blocks_orders_via_degraded_state(self):
        engine = build_engine()
        engine.update_pnl(float("nan"), 0.0)
        self.assertEqual(engine.state, EngineState.DEGRADED_WARNING)
        self.assertFalse(engine.check_order_allowed())

    def test_infinite_pnl_blocks_orders(self):
        engine = build_engine()
        engine.update_pnl(float("-inf"), 0.0)
        self.assertEqual(engine.state, EngineState.DEGRADED_WARNING)
        self.assertFalse(engine.check_order_allowed())

    def test_nan_pnl_does_not_corrupt_the_high_water_mark(self):
        engine = build_engine()
        engine.update_pnl(5000.0, 0.0)
        engine.update_pnl(float("nan"), 0.0)
        self.assertEqual(engine.peak_session_pnl, 5000.0)
        self.assertEqual(engine.current_session_pnl, 5000.0)

    def test_degraded_state_is_not_cleared_by_the_order_gate_alone(self):
        # Regression guard: recovery must require a valid P&L update, not merely
        # the next order check.
        engine = build_engine()
        engine.update_pnl(float("nan"), 0.0)
        self.assertFalse(engine.check_order_allowed())
        self.assertEqual(engine.state, EngineState.DEGRADED_WARNING)

    def test_valid_update_clears_the_degraded_state(self):
        engine = build_engine()
        engine.update_pnl(float("nan"), 0.0)
        engine.update_pnl(100.0, 0.0)
        self.assertEqual(engine.state, EngineState.ACTIVE)
        self.assertTrue(engine.check_order_allowed())

    def test_non_numeric_pnl_degrades_rather_than_raising(self):
        engine = build_engine()
        engine.update_pnl("not-a-number", 0.0)  # type: ignore[arg-type]
        self.assertEqual(engine.state, EngineState.DEGRADED_WARNING)


class TestPnlStaleness(unittest.TestCase):
    def test_orders_blocked_before_the_first_pnl_update(self):
        clock = FakeClock()
        engine = build_engine(clock=clock, max_pnl_staleness_seconds=30.0)
        self.assertFalse(engine.check_order_allowed())
        self.assertEqual(engine.state, EngineState.DEGRADED_WARNING)

    def test_stale_feed_blocks_and_fresh_feed_recovers(self):
        clock = FakeClock()
        engine = build_engine(clock=clock, max_pnl_staleness_seconds=30.0)
        engine.update_pnl(0.0, 0.0)
        self.assertTrue(engine.check_order_allowed())

        clock.advance(30.0)  # exactly at the limit: still fresh
        self.assertTrue(engine.check_order_allowed())

        clock.advance(0.01)  # just past it
        self.assertFalse(engine.check_order_allowed())
        self.assertEqual(engine.state, EngineState.DEGRADED_WARNING)

        engine.update_pnl(0.0, 0.0)
        self.assertEqual(engine.state, EngineState.ACTIVE)
        self.assertTrue(engine.check_order_allowed())

    def test_staleness_is_recoverable_not_a_halt(self):
        clock = FakeClock()
        engine = build_engine(clock=clock, max_pnl_staleness_seconds=5.0)
        engine.update_pnl(0.0, 0.0)
        clock.advance(60.0)
        engine.check_order_allowed()
        self.assertNotEqual(engine.state, EngineState.HALTED)
        self.assertEqual(engine.halt_reason, "")

    def test_stale_feed_does_not_consume_rate_budget(self):
        clock = FakeClock()
        engine = build_engine(clock=clock, max_pnl_staleness_seconds=5.0)
        engine.update_pnl(0.0, 0.0)
        clock.advance(10.0)
        for _ in range(20):
            self.assertFalse(engine.check_order_allowed())
        engine.update_pnl(0.0, 0.0)
        # Full budget must still be available once the feed recovers.
        for _ in range(5):
            self.assertTrue(engine.check_order_allowed())


class TestOrderRateLimit(unittest.TestCase):
    def test_budget_is_exactly_the_configured_limit(self):
        engine = build_engine()
        for _ in range(5):
            self.assertTrue(engine.check_order_allowed())
        self.assertFalse(engine.check_order_allowed())
        self.assertEqual(engine.state, EngineState.HALTED)
        self.assertIn("Runaway Algo", engine.halt_reason)

    def test_window_rolls_so_a_sustained_legal_rate_never_halts(self):
        clock = FakeClock()
        engine = build_engine(clock=clock)
        # 5 orders per 60s, spaced 12s apart, for 10 minutes.
        for _ in range(50):
            self.assertTrue(engine.check_order_allowed())
            clock.advance(12.0)
        self.assertEqual(engine.state, EngineState.ACTIVE)

    def test_window_is_half_open_at_exactly_the_window_width(self):
        # An order exactly ORDER_RATE_WINDOW_SECONDS old has left the window, so
        # traffic at exactly the configured rate is legal. A closed window would
        # make the effective limit one below the configured one.
        clock = FakeClock()
        engine = build_engine(clock=clock)
        for _ in range(5):
            self.assertTrue(engine.check_order_allowed())
        clock.advance(ORDER_RATE_WINDOW_SECONDS)
        self.assertTrue(engine.check_order_allowed())
        self.assertEqual(engine.state, EngineState.ACTIVE)

    def test_one_tick_inside_the_window_still_trips(self):
        clock = FakeClock()
        engine = build_engine(clock=clock)
        for _ in range(5):
            self.assertTrue(engine.check_order_allowed())
        clock.advance(ORDER_RATE_WINDOW_SECONDS - 0.001)
        self.assertFalse(engine.check_order_allowed())
        self.assertEqual(engine.state, EngineState.HALTED)

    def test_monotonic_clock_is_the_default(self):
        # Regression guard for the wall-clock defect: an NTP step forward on a
        # wall clock silently empties the window and lets a runaway algo
        # through. time.monotonic is immune.
        import time as _time

        engine = CapitalPreservationEngine(PreservationLimits())
        self.assertIs(engine._clock, _time.monotonic)


class TestErrorTracking(unittest.TestCase):
    def test_consecutive_errors_halt_at_the_limit(self):
        engine = build_engine()
        engine.register_error()
        engine.register_error()
        self.assertEqual(engine.state, EngineState.ACTIVE)
        engine.register_error()
        self.assertEqual(engine.state, EngineState.HALTED)
        self.assertFalse(engine.check_order_allowed())

    def test_success_resets_the_counter(self):
        engine = build_engine()
        engine.register_error()
        engine.register_error()
        engine.register_success()
        engine.register_error()
        self.assertEqual(engine.state, EngineState.ACTIVE)
        self.assertEqual(engine.consecutive_errors, 1)


class TestHaltLatching(unittest.TestCase):
    def test_first_halt_reason_is_retained(self):
        engine = build_engine()
        for _ in range(3):
            engine.register_error()
        first_reason = engine.halt_reason
        engine.update_pnl(-50000.0, 0.0)
        self.assertEqual(engine.halt_reason, first_reason)
        self.assertEqual(len([r for r in engine.audit_log if r.event == "halt"]), 1)

    def test_halt_callback_fires_once_with_the_record(self):
        seen = []
        engine = build_engine(on_halt=seen.append)
        for _ in range(3):
            engine.register_error()
        engine.register_error()  # already halted; must not re-fire
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], HaltRecord)
        self.assertEqual(seen[0].event, "halt")

    def test_halt_callback_may_call_back_into_the_engine(self):
        # The callback fires outside the lock, so a cancel-all hook that
        # re-checks the gate must not deadlock.
        observed = {}

        def hook(record):
            observed["allowed"] = engine.check_order_allowed()
            observed["snapshot_state"] = engine.snapshot()["state"]

        engine = build_engine(on_halt=hook)
        for _ in range(3):
            engine.register_error()
        self.assertFalse(observed["allowed"])
        self.assertEqual(observed["snapshot_state"], "HALTED")

    def test_broken_callback_does_not_unlatch_the_halt(self):
        def hook(_record):
            raise RuntimeError("pager is down")

        engine = build_engine(on_halt=hook)
        for _ in range(3):
            engine.register_error()
        self.assertEqual(engine.state, EngineState.HALTED)
        self.assertFalse(engine.check_order_allowed())


class TestManualReset(unittest.TestCase):
    def test_wrong_token_is_rejected(self):
        engine = build_engine()
        for _ in range(3):
            engine.register_error()
        with self.assertRaises(PermissionError):
            engine.manual_reset("INVALID_TOKEN")
        self.assertEqual(engine.state, EngineState.HALTED)

    def test_correct_token_clears_a_connectivity_halt(self):
        engine = build_engine()
        for _ in range(3):
            engine.register_error()
        engine.manual_reset(TEST_TOKEN, operator="head-of-trading")
        self.assertEqual(engine.state, EngineState.ACTIVE)
        self.assertTrue(engine.check_order_allowed())

    def test_reset_is_recorded_with_the_operator_and_cleared_reason(self):
        engine = build_engine()
        for _ in range(3):
            engine.register_error()
        engine.manual_reset(TEST_TOKEN, operator="head-of-trading")
        resets = [r for r in engine.audit_log if r.event == "reset"]
        self.assertEqual(len(resets), 1)
        self.assertEqual(resets[0].operator, "head-of-trading")
        self.assertIn("consecutive API errors", resets[0].reason)

    def test_reset_does_not_grant_a_second_drawdown_budget(self):
        # Regression test: a reset that forgot the session's losses would let
        # the strategy lose a full drawdown limit again.
        engine = build_engine()
        engine.update_pnl(-11000.0, 0.0)
        self.assertEqual(engine.state, EngineState.HALTED)
        engine.manual_reset(TEST_TOKEN, operator="head-of-trading")
        self.assertEqual(engine.state, EngineState.HALTED)
        self.assertFalse(engine.check_order_allowed())
        self.assertIn("still exceeds limit", engine.halt_reason)

    def test_explicit_rebaseline_grants_a_new_session(self):
        engine = build_engine()
        engine.update_pnl(-11000.0, 0.0)
        engine.manual_reset(TEST_TOKEN, operator="head-of-trading", rebaseline_session_pnl=True)
        self.assertEqual(engine.state, EngineState.ACTIVE)
        self.assertEqual(engine.current_drawdown, 0.0)
        self.assertEqual(engine.peak_session_pnl, -11000.0)
        self.assertTrue(engine.check_order_allowed())

    def test_rebaseline_is_recorded_in_the_audit_trail(self):
        engine = build_engine()
        engine.update_pnl(-11000.0, 0.0)
        engine.manual_reset(TEST_TOKEN, operator="cro", rebaseline_session_pnl=True)
        resets = [r for r in engine.audit_log if r.event == "reset"]
        self.assertIn("re-baselined", resets[-1].reason)

    def test_rebaseline_cannot_defeat_the_absolute_session_loss_limit(self):
        engine = build_engine(max_daily_loss_usd=5000.0)
        engine.update_pnl(-6000.0, 0.0)
        self.assertEqual(engine.state, EngineState.HALTED)
        engine.manual_reset(TEST_TOKEN, operator="cro", rebaseline_session_pnl=True)
        self.assertEqual(engine.state, EngineState.HALTED)

    def test_reset_clears_the_rate_window(self):
        clock = FakeClock()
        engine = build_engine(clock=clock)
        for _ in range(5):
            engine.check_order_allowed()
        engine.check_order_allowed()  # trips the runaway halt
        engine.manual_reset(TEST_TOKEN, operator="head-of-trading")
        for _ in range(5):
            self.assertTrue(engine.check_order_allowed())


class TestResetAuthorizer(unittest.TestCase):
    def test_unconfigured_authorizer_denies_every_token(self):
        # An engine deployed without a configured secret must be unresettable,
        # never resettable with a default baked into this repository.
        authorizer = ResetAuthorizer(expected_token=None, env_var="__NEVER_SET_IN_TESTS__")
        self.assertFalse(authorizer.is_configured)
        with self.assertRaises(PermissionError):
            authorizer.authorize("")
        with self.assertRaises(PermissionError):
            authorizer.authorize("SECURE_ADMIN_TOKEN")

    def test_no_hard_coded_token_remains_in_the_source(self):
        import capital_preservation_engine as module

        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("SECURE_ADMIN_TOKEN", source)

    def test_env_var_name_is_exported_for_deployment_docs(self):
        self.assertEqual(RESET_TOKEN_ENV_VAR, "CAPITAL_PRESERVATION_RESET_TOKEN")

    def test_non_string_token_is_rejected_without_raising_typeerror(self):
        authorizer = ResetAuthorizer(expected_token=TEST_TOKEN)
        with self.assertRaises(PermissionError):
            authorizer.authorize(None)  # type: ignore[arg-type]


class TestPersistence(unittest.TestCase):
    def test_halt_survives_a_process_restart(self):
        engine = build_engine()
        engine.update_pnl(-11000.0, 0.0)
        blob = json.dumps(engine.snapshot())

        restarted = build_engine()
        self.assertEqual(restarted.state, EngineState.ACTIVE)
        restarted.restore(json.loads(blob))

        self.assertEqual(restarted.state, EngineState.HALTED)
        self.assertFalse(restarted.check_order_allowed())
        self.assertEqual(restarted.current_drawdown, 11000.0)

    def test_snapshot_is_json_serialisable(self):
        engine = build_engine()
        engine.update_pnl(-11000.0, 0.0)
        engine.manual_reset(TEST_TOKEN, operator="cro", rebaseline_session_pnl=True)
        round_tripped = json.loads(json.dumps(engine.snapshot()))
        self.assertEqual(len(round_tripped["audit_log"]), 2)

    def test_corrupt_snapshot_fails_closed(self):
        engine = build_engine()
        engine.restore({"state": "TOTALLY_BOGUS"})
        self.assertEqual(engine.state, EngineState.HALTED)
        self.assertFalse(engine.check_order_allowed())

    def test_empty_snapshot_fails_closed(self):
        engine = build_engine()
        engine.restore({})
        self.assertEqual(engine.state, EngineState.HALTED)

    def test_malformed_field_does_not_leave_a_half_restored_active_engine(self):
        # The state name parses fine and says ACTIVE, but a later field is
        # garbage. Assigning fields as they parse would leave the engine ACTIVE
        # holding partially restored risk state.
        engine = build_engine()
        engine.restore({"state": "ACTIVE", "current_session_pnl": "not-a-number"})
        self.assertEqual(engine.state, EngineState.HALTED)
        self.assertFalse(engine.check_order_allowed())

    def test_non_finite_pnl_in_snapshot_fails_closed(self):
        engine = build_engine()
        engine.restore({"state": "ACTIVE", "peak_session_pnl": float("nan")})
        self.assertEqual(engine.state, EngineState.HALTED)

    def test_malformed_audit_log_fails_closed(self):
        engine = build_engine()
        engine.restore({"state": "ACTIVE", "audit_log": ["not-a-record"]})
        self.assertEqual(engine.state, EngineState.HALTED)

    def test_restore_rearms_the_staleness_gate(self):
        clock = FakeClock()
        engine = build_engine(clock=clock, max_pnl_staleness_seconds=30.0)
        engine.update_pnl(0.0, 0.0)
        blob = engine.snapshot()

        restarted = build_engine(clock=FakeClock(start=5.0), max_pnl_staleness_seconds=30.0)
        restarted.restore(blob)
        # The monotonic epoch changed across the restart, so a fresh P&L update
        # is required before trading resumes.
        self.assertFalse(restarted.check_order_allowed())


class TestConcurrency(unittest.TestCase):
    """Multi-threaded invariants for an engine used as OMS middleware.

    Caveat on what these prove: under CPython the GIL already serialises most of
    the read-modify-write in ``check_order_allowed``, so removing the lock does
    not reliably fail these tests on this interpreter (it shows up as an
    occasional *lost* admission rather than an extra one). They assert the
    invariants that must hold - exact budget accounting, a single halt, no
    deadlock - rather than proving the absence of a race on every runtime. The
    lock is still required: the interleaving is unsafe by construction and a
    free-threaded build removes the accidental protection.
    """

    def test_rate_budget_is_not_exceeded_under_concurrent_callers(self):
        engine = build_engine(max_orders_per_minute=50, max_consecutive_errors=1000)
        approvals = []
        approvals_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            local = 0
            for _ in range(50):
                if engine.check_order_allowed():
                    local += 1
            with approvals_lock:
                approvals.append(local)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(approvals), 50)
        self.assertEqual(engine.state, EngineState.HALTED)

    def test_concurrent_error_registration_halts_exactly_once(self):
        seen = []
        engine = build_engine(max_consecutive_errors=10, on_halt=seen.append)
        threads = [threading.Thread(target=engine.register_error) for _ in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(engine.consecutive_errors, 40)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
