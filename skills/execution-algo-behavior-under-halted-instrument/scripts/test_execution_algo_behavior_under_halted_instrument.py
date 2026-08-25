"""Unit tests for the instrument-halt execution algo state machine.

Expected re-benchmark values are derived by hand in each test's comments from
the schedule arithmetic, not by re-running the implementation's own expression.
"""

import logging
import unittest

from execution_algo_behavior_under_halted_instrument import (
    ACK_CANCELLED,
    ACK_CANCEL_REJECTED,
    ACK_FILLED,
    CHILD_CANCELLED,
    CHILD_FILLED,
    CHILD_PENDING_CANCEL,
    CHILD_RESTING,
    STATE_PAUSED_HALTED,
    STATE_REBALANCING,
    STATE_RUNNING,
    ActiveChildOrder,
    AlgoHaltAuditReport,
    ExecutionAlgoHaltEngine,
    HaltEngineConfig,
    HaltEngineError,
    ParentAlgoInstanceState,
)


def make_algo(**overrides) -> ParentAlgoInstanceState:
    """A TWAP parent with two resting child orders, unless overridden."""
    params = dict(
        parent_algo_id="PARENT_TWAP_01",
        symbol="TSLA",
        algo_type="TWAP",
        total_target_qty=10_000,
        executed_qty=4_000,
        algo_state=STATE_RUNNING,
        active_child_orders=[
            ActiveChildOrder("CHILD_01", "NASDAQ", "BUY", 250.0, 500),
            ActiveChildOrder("CHILD_02", "EDGX", "BUY", 249.9, 500),
        ],
    )
    params.update(overrides)
    return ParentAlgoInstanceState(**params)


class HaltTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionAlgoHaltEngine()
        # The engine logs at CRITICAL on every halt; keep test output readable.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)


class TestHaltDetectionAndCancellation(HaltTestBase):
    def test_halt_issues_cancel_requests_but_confirms_nothing(self):
        """Regression: the engine must not mark orders cancelled without a venue ack.

        The pre-fix implementation set status directly to CANCELLED and reported
        cancelled_child_orders_count=2, which told the desk it had no exposure
        while both orders were in fact still resting and eligible interest for
        the reopening auction.
        """
        algo = make_algo()
        report = self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_000.0)

        self.assertEqual(report.previous_algo_state, STATE_RUNNING)
        self.assertEqual(report.new_algo_state, STATE_PAUSED_HALTED)
        self.assertEqual(report.cancel_requests_issued, 2)
        self.assertEqual(report.cancelled_child_orders_count, 0)
        self.assertEqual(report.orders_still_live_count, 2)
        self.assertFalse(report.is_slicing_active)
        self.assertFalse(report.marketable_child_orders_permitted)
        self.assertTrue(report.cancel_permitted)
        for child in algo.active_child_orders:
            self.assertEqual(child.status, CHILD_PENDING_CANCEL)
            self.assertTrue(child.is_live())

    def test_confirmed_cancel_clears_exposure(self):
        algo = make_algo()
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_000.0)
        self.engine.apply_cancel_ack(algo, "CHILD_01", ACK_CANCELLED)
        self.engine.apply_cancel_ack(algo, "CHILD_02", ACK_CANCELLED)

        report = self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_001.0)
        self.assertEqual(report.cancelled_child_orders_count, 2)
        self.assertEqual(report.orders_still_live_count, 0)
        self.assertEqual(report.cancel_requests_issued, 0)

    def test_rejected_cancel_leaves_order_live_on_the_book(self):
        """A cancel reject returns the order to RESTING -- it is still working."""
        algo = make_algo()
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_000.0)
        order = self.engine.apply_cancel_ack(algo, "CHILD_01", ACK_CANCEL_REJECTED)

        self.assertEqual(order.status, CHILD_RESTING)
        self.assertTrue(order.is_live())
        report = self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_002.0)
        self.assertEqual(report.orders_still_live_count, 2)
        self.assertEqual(report.cancelled_child_orders_count, 0)
        # The re-request is legitimate here: the order went back to RESTING.
        self.assertEqual(report.cancel_requests_issued, 1)

    def test_fill_races_the_cancel_and_updates_executed_qty(self):
        algo = make_algo()
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_000.0)
        order = self.engine.apply_cancel_ack(algo, "CHILD_01", ACK_FILLED)

        self.assertEqual(order.status, CHILD_FILLED)
        self.assertFalse(order.is_live())
        self.assertEqual(order.filled_qty, 500)
        # 4,000 pre-halt + the 500 that printed before the cancel landed.
        self.assertEqual(algo.executed_qty, 4_500)
        self.assertEqual(algo.remaining_qty(), 5_500)

    def test_partial_fill_then_cancel(self):
        algo = make_algo()
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_000.0)
        order = self.engine.apply_cancel_ack(algo, "CHILD_02", ACK_CANCELLED, filled_qty=200)

        self.assertEqual(order.status, CHILD_CANCELLED)
        self.assertEqual(order.filled_qty, 200)
        self.assertEqual(algo.executed_qty, 4_200)

    def test_filled_ack_must_consume_the_whole_remainder(self):
        """A partial that raced the cancel is CANCELLED+filled_qty, never FILLED."""
        algo = make_algo()
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_000.0)
        with self.assertRaises(HaltEngineError):
            self.engine.apply_cancel_ack(algo, "CHILD_01", ACK_FILLED, filled_qty=200)
        # executed_qty must be untouched by the rejected acknowledgement.
        self.assertEqual(algo.executed_qty, 4_000)
        self.assertEqual(algo.active_child_orders[0].filled_qty, 0)

    def test_duplicate_ack_is_rejected(self):
        algo = make_algo()
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_000.0)
        self.engine.apply_cancel_ack(algo, "CHILD_01", ACK_CANCELLED)
        with self.assertRaises(HaltEngineError):
            self.engine.apply_cancel_ack(algo, "CHILD_01", ACK_CANCELLED)

    def test_ack_rejects_unknown_outcome_unknown_order_and_overfill(self):
        algo = make_algo()
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1_000.0)
        with self.assertRaises(HaltEngineError):
            self.engine.apply_cancel_ack(algo, "CHILD_01", "CANCELED")   # misspelling
        with self.assertRaises(HaltEngineError):
            self.engine.apply_cancel_ack(algo, "CHILD_99", ACK_CANCELLED)
        with self.assertRaises(HaltEngineError):
            self.engine.apply_cancel_ack(algo, "CHILD_01", ACK_CANCELLED, filled_qty=501)
        with self.assertRaises(HaltEngineError):
            self.engine.apply_cancel_ack(algo, "CHILD_01", ACK_CANCELLED, filled_qty=-1)

    def test_duplicate_halt_does_not_rerequest_or_restamp_the_halt_clock(self):
        algo = make_algo(
            schedule_start_ts=0.0, schedule_end_ts=10_000.0, total_target_qty=10_000,
            executed_qty=0,
        )
        first = self.engine.handle_trading_status_change(algo, "HALTED_LULD", 100.0)
        second = self.engine.handle_trading_status_change(algo, "HALTED_LULD", 150.0)

        self.assertEqual(first.cancel_requests_issued, 2)
        self.assertEqual(second.cancel_requests_issued, 0)
        self.assertEqual(algo.halt_started_ts, 100.0)

        resume = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 400.0)
        # Measured from the FIRST halt message (400 - 100), not the duplicate.
        self.assertEqual(resume.halt_duration_s, 300.0)


class TestNoCancelAndAuctionPhases(HaltTestBase):
    def test_no_cancel_phase_reports_orders_as_committed(self):
        """CME `Pre-Open - No Cancel` / Eurex extended-VI freeze: cancels are refused."""
        algo = make_algo()
        report = self.engine.handle_trading_status_change(
            algo, "PRE_OPEN_NO_CANCEL", 1_000.0
        )

        self.assertFalse(report.cancel_permitted)
        self.assertEqual(report.cancel_requests_issued, 0)
        self.assertEqual(report.orders_still_live_count, 2)
        self.assertFalse(report.is_slicing_active)
        for child in algo.active_child_orders:
            self.assertEqual(child.status, CHILD_RESTING)

    def test_reopening_auction_does_not_resume_continuous_slicing(self):
        """Regression: AUCTION_REOPENING previously went straight to RUNNING."""
        algo = make_algo(algo_state=STATE_PAUSED_HALTED, executed_qty=4_000)
        algo.halt_started_ts = 1_000.0
        report = self.engine.handle_trading_status_change(
            algo, "AUCTION_REOPENING", 1_300.0
        )

        self.assertEqual(report.new_algo_state, STATE_REBALANCING)
        self.assertFalse(report.is_slicing_active)
        self.assertFalse(report.marketable_child_orders_permitted)
        self.assertTrue(report.cancel_permitted)
        self.assertEqual(report.cancel_requests_issued, 2)

    def test_halt_clock_spans_the_reopening_auction(self):
        algo = make_algo(
            schedule_start_ts=0.0, schedule_end_ts=10_000.0, executed_qty=0,
            active_child_orders=[],
        )
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 100.0)
        self.engine.handle_trading_status_change(algo, "AUCTION_REOPENING", 200.0)
        resume = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 300.0)

        # Pause (100->200) plus auction (200->300): the instrument was not
        # continuously tradable for the whole 200s.
        self.assertEqual(resume.halt_duration_s, 200.0)
        self.assertEqual(resume.new_algo_state, STATE_RUNNING)

    def test_closed_session_reports_cancels_unavailable(self):
        algo = make_algo()
        report = self.engine.handle_trading_status_change(algo, "CLOSE_FINAL", 1_000.0)
        self.assertFalse(report.cancel_permitted)
        self.assertFalse(report.is_slicing_active)
        self.assertEqual(report.remaining_qty, 6_000)


class TestBandStressStates(HaltTestBase):
    def test_limit_and_straddle_states_block_marketable_child_orders(self):
        for status in ("LIMIT_STATE", "STRADDLE_STATE"):
            with self.subTest(status=status):
                algo = make_algo()
                report = self.engine.handle_trading_status_change(algo, status, 1_000.0)
                self.assertTrue(report.is_slicing_active)
                self.assertFalse(report.marketable_child_orders_permitted)
                self.assertEqual(report.new_algo_state, STATE_RUNNING)
                # Band stress is not a halt: nothing is pulled from the book.
                self.assertEqual(report.cancel_requests_issued, 0)


class TestRebenchmarking(HaltTestBase):
    def test_schedule_is_extended_by_the_halt_duration(self):
        # span = 4600 - 1000 = 3600s; 36,000 shares => original rate 10.0 sh/s.
        # 12,000 done => 24,000 remaining.
        # Halt 2000 -> 2300 => 300s. New end = 4600 + 300 = 4900.
        # Remaining window = 4900 - 2300 = 2600s => 24000/2600 = 9.230769 sh/s.
        # 9.23 <= 1.5 * 10.0 = 15.0, so the guard does not trip.
        algo = make_algo(
            total_target_qty=36_000, executed_qty=12_000,
            schedule_start_ts=1_000.0, schedule_end_ts=4_600.0,
            active_child_orders=[],
        )
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 2_000.0)
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 2_300.0)

        self.assertEqual(report.new_algo_state, STATE_RUNNING)
        self.assertTrue(report.is_slicing_active)
        self.assertTrue(report.rebenchmark_applied)
        self.assertFalse(report.rebenchmark_breach)
        self.assertEqual(report.halt_duration_s, 300.0)
        self.assertEqual(report.rebenchmarked_end_ts, 4_900.0)
        self.assertAlmostEqual(report.original_rate_qty_per_s, 10.0)
        self.assertAlmostEqual(report.required_rate_qty_per_s, 24_000 / 2_600)
        self.assertAlmostEqual(report.required_rate_qty_per_s, 9.230769, places=6)
        self.assertEqual(algo.schedule_end_ts, 4_900.0)
        self.assertIsNone(algo.halt_started_ts)

    def test_backlog_guard_trips_instead_of_dumping_the_residual(self):
        # span = 1000s, 10,000 shares => original rate 10.0 sh/s.
        # 1,000 done => 9,000 remaining. Halt 900 -> 950 => 50s.
        # New end = 1050; remaining window = 100s => 90.0 sh/s required,
        # which is 9x the schedule and far above the 1.5x cap.
        algo = make_algo(
            total_target_qty=10_000, executed_qty=1_000,
            schedule_start_ts=0.0, schedule_end_ts=1_000.0,
            active_child_orders=[],
        )
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 900.0)
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 950.0)

        self.assertTrue(report.rebenchmark_breach)
        self.assertFalse(report.is_slicing_active)
        self.assertEqual(report.new_algo_state, STATE_REBALANCING)
        self.assertAlmostEqual(report.required_rate_qty_per_s, 90.0)
        # The schedule is NOT silently extended when the guard trips.
        self.assertEqual(algo.schedule_end_ts, 1_000.0)

    def test_extension_is_clamped_at_the_hard_deadline(self):
        # span = 1000s, 10,000 shares => original rate 10.0 sh/s.
        # 9,900 done => 100 remaining. Halt 900 -> 1000 => 100s.
        # Uncapped new end would be 1100, but hard_end_ts=1020 clamps it.
        # Remaining window = 1020 - 1000 = 20s => 100/20 = 5.0 sh/s <= 15.0.
        algo = make_algo(
            total_target_qty=10_000, executed_qty=9_900,
            schedule_start_ts=0.0, schedule_end_ts=1_000.0, hard_end_ts=1_020.0,
            active_child_orders=[],
        )
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 900.0)
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 1_000.0)

        self.assertEqual(report.rebenchmarked_end_ts, 1_020.0)
        self.assertFalse(report.rebenchmark_breach)
        self.assertAlmostEqual(report.required_rate_qty_per_s, 5.0)

    def test_exhausted_horizon_escalates_rather_than_dividing_by_zero(self):
        # New end clamps to 1000.0 and the resume stamp is 1000.0, leaving a
        # 0s window -- below min_remaining_seconds, so no rate is computable.
        algo = make_algo(
            total_target_qty=1_000, executed_qty=500,
            schedule_start_ts=0.0, schedule_end_ts=1_000.0, hard_end_ts=1_000.0,
            active_child_orders=[],
        )
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 999.5)
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 1_000.0)

        self.assertTrue(report.rebenchmark_breach)
        self.assertIsNone(report.required_rate_qty_per_s)
        self.assertFalse(report.is_slicing_active)

    def test_rate_exactly_at_the_cap_does_not_trip_the_guard(self):
        # span = 1000s, 10,000 shares => original rate 10.0 sh/s; cap = 15.0 sh/s.
        # Halt 500 -> 500 (zero duration) leaves the window at 1000 - 500 = 500s.
        # Choose remaining = 7,500 so required = 7500/500 = 15.0 exactly.
        algo = make_algo(
            total_target_qty=10_000, executed_qty=2_500,
            schedule_start_ts=0.0, schedule_end_ts=1_000.0,
            active_child_orders=[],
        )
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 500.0)
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 500.0)

        self.assertAlmostEqual(report.required_rate_qty_per_s, 15.0)
        self.assertFalse(report.rebenchmark_breach)
        self.assertTrue(report.is_slicing_active)

    def test_rate_just_above_the_cap_trips_the_guard(self):
        # Same geometry, one extra unexecuted share: 7,501/500 = 15.002 > 15.0.
        algo = make_algo(
            total_target_qty=10_000, executed_qty=2_499,
            schedule_start_ts=0.0, schedule_end_ts=1_000.0,
            active_child_orders=[],
        )
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 500.0)
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 500.0)

        self.assertAlmostEqual(report.required_rate_qty_per_s, 15.002)
        self.assertTrue(report.rebenchmark_breach)
        self.assertFalse(report.is_slicing_active)

    def test_completed_parent_resumes_without_tripping_the_guard(self):
        algo = make_algo(
            total_target_qty=1_000, executed_qty=1_000,
            schedule_start_ts=0.0, schedule_end_ts=1_000.0,
            active_child_orders=[],
        )
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 900.0)
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 999.9)

        self.assertEqual(report.remaining_qty, 0)
        self.assertFalse(report.rebenchmark_breach)
        self.assertEqual(report.new_algo_state, STATE_RUNNING)

    def test_missing_schedule_disables_the_guard_and_says_so(self):
        algo = make_algo(algo_type="POV", active_child_orders=[])
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 900.0)
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 950.0)

        self.assertFalse(report.rebenchmark_applied)
        self.assertFalse(report.rebenchmark_breach)
        self.assertTrue(report.is_slicing_active)
        self.assertEqual(report.new_algo_state, STATE_RUNNING)
        self.assertIn("did NOT", report.audit_notes)

    def test_resumption_reports_live_orders_still_outstanding(self):
        algo = make_algo(active_child_orders=[
            ActiveChildOrder("CHILD_01", "NASDAQ", "BUY", 250.0, 500),
        ])
        self.engine.handle_trading_status_change(algo, "HALTED_LULD", 900.0)
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 950.0)
        self.assertEqual(report.orders_still_live_count, 1)


class TestFailSafeAndValidation(HaltTestBase):
    def test_unrecognised_status_suspends_slicing_without_cancelling(self):
        algo = make_algo()
        report = self.engine.handle_trading_status_change(algo, "HALT_PENDING_NEWS", 1_000.0)

        self.assertFalse(report.status_recognised)
        self.assertFalse(report.is_slicing_active)
        self.assertEqual(report.cancel_requests_issued, 0)
        self.assertFalse(report.cancel_permitted)
        for child in algo.active_child_orders:
            self.assertEqual(child.status, CHILD_RESTING)

    def test_status_is_normalised_for_case_and_whitespace(self):
        algo = make_algo()
        report = self.engine.handle_trading_status_change(algo, "  halted_luld ", 1_000.0)
        self.assertEqual(report.new_algo_state, STATE_PAUSED_HALTED)
        self.assertEqual(report.instrument_trading_status, "HALTED_LULD")

    def test_over_execution_raises_a_reconciliation_breach_and_stops_slicing(self):
        algo = make_algo(total_target_qty=1_000, executed_qty=1_200, active_child_orders=[])
        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS", 1_000.0)

        self.assertTrue(report.reconciliation_breach)
        self.assertFalse(report.is_slicing_active)
        self.assertEqual(report.remaining_qty, 0)

    def test_invalid_inputs_are_rejected(self):
        cases = {
            "empty status": (make_algo(), "   ", 1.0),
            "blank id": (make_algo(parent_algo_id=""), "HALTED_LULD", 1.0),
            "zero target": (make_algo(total_target_qty=0), "HALTED_LULD", 1.0),
            "negative executed": (make_algo(executed_qty=-1), "HALTED_LULD", 1.0),
            "nan timestamp": (make_algo(), "HALTED_LULD", float("nan")),
            "inf timestamp": (make_algo(), "HALTED_LULD", float("inf")),
            "inverted schedule": (
                make_algo(schedule_start_ts=100.0, schedule_end_ts=100.0),
                "HALTED_LULD", 1.0,
            ),
            "deadline before schedule end": (
                make_algo(schedule_start_ts=0.0, schedule_end_ts=100.0, hard_end_ts=99.0),
                "HALTED_LULD", 1.0,
            ),
            "zero-qty child": (
                make_algo(active_child_orders=[
                    ActiveChildOrder("C", "NASDAQ", "BUY", 1.0, 0),
                ]),
                "HALTED_LULD", 1.0,
            ),
            "child filled beyond order qty": (
                make_algo(active_child_orders=[
                    ActiveChildOrder("C", "NASDAQ", "BUY", 1.0, 100, CHILD_RESTING, 101),
                ]),
                "HALTED_LULD", 1.0,
            ),
        }
        for label, (algo, status, ts) in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(HaltEngineError):
                    self.engine.handle_trading_status_change(algo, status, ts)

    def test_config_rejects_nonsensical_parameters(self):
        with self.assertRaises(HaltEngineError):
            HaltEngineConfig(max_rate_multiple=0.5)
        with self.assertRaises(HaltEngineError):
            HaltEngineConfig(min_remaining_seconds=0.0)
        with self.assertRaises(HaltEngineError):
            HaltEngineConfig(max_rate_multiple=float("nan"))

    def test_report_is_a_report_dataclass(self):
        algo = make_algo()
        report = self.engine.handle_trading_status_change(algo, "HALTED_LULD", 1.0)
        self.assertIsInstance(report, AlgoHaltAuditReport)


if __name__ == "__main__":
    unittest.main()
