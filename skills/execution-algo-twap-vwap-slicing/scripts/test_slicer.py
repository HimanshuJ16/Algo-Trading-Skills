"""
Unit tests for execution-algo-twap-vwap-slicing.

Expected values are derived by hand from the benchmark definitions, not by re-running
the implementation's own arithmetic. Tests named `test_regression_*` fail against the
1.0.0 implementation and pass against 2.0.0.
"""
import logging
import math
import random
import unittest

from slicer import (
    CatchUpPolicy,
    ChildOrderSlice,
    ExecutionReport,
    ExecutionSlicer,
    OrderSide,
    SliceStatus,
    SlicerType,
    allocate_lots,
    twap_schedule,
    vwap_schedule,
)

# Keep expected-error logging out of the test output.
logging.getLogger("slicer").setLevel(logging.CRITICAL)


class TestAllocateLots(unittest.TestCase):
    """Apportionment must conserve quantity exactly and never emit a negative clip."""

    def test_largest_remainder_is_hand_checkable(self):
        # 10 lots over 3 equal weights: exact = 3.333 each, floors = 3 each (9 lots),
        # one lot left over, all remainders equal so the tie breaks to the lowest index.
        self.assertEqual(allocate_lots(10, [1.0, 1.0, 1.0]), [4.0, 3.0, 3.0])

    def test_weights_are_normalised_not_assumed_to_sum_to_one(self):
        # Weights 1:3 over 100 lots -> 25 / 75 regardless of their absolute scale.
        self.assertEqual(allocate_lots(100, [2.0, 6.0]), [25.0, 75.0])
        self.assertEqual(allocate_lots(100, [0.25, 0.75]), [25.0, 75.0])

    def test_regression_fractional_instrument_is_not_zeroed(self):
        # 1.0.0 rounded every child of a 0.5 BTC parent to 0, producing a schedule
        # that summed to 0 and silently executed nothing.
        schedule = allocate_lots(0.5, [1.0] * 5, lot_size=0.1)
        self.assertEqual(len(schedule), 5)
        self.assertAlmostEqual(math.fsum(schedule), 0.5, places=12)
        self.assertTrue(all(qty > 0 for qty in schedule))

    def test_regression_never_emits_a_negative_child_quantity(self):
        # 1.0.0 patched the rounding residual onto the last slice, which drove it
        # negative for ~14% of seeds at total_qty=5 over 10 intervals. A negative
        # child quantity reads downstream as an order on the opposite side.
        for seed in range(400):
            rng = random.Random(seed)
            schedule = twap_schedule(5, 10, jitter_pct=0.15, rng=rng)
            self.assertTrue(
                all(qty >= 0.0 for qty in schedule),
                f"seed {seed} produced a negative child quantity: {schedule}",
            )
            self.assertAlmostEqual(math.fsum(schedule), 5.0, places=12)

    def test_conservation_holds_across_a_parameter_sweep(self):
        for total, count, lot in [
            (1000, 10, 1.0), (7, 5, 1.0), (1, 4, 1.0),
            (2.5, 5, 0.5), (0.003, 3, 0.001), (999_999, 7, 1.0),
        ]:
            for seed in range(15):
                rng = random.Random(seed)
                schedule = twap_schedule(total, count, jitter_pct=0.3, lot_size=lot, rng=rng)
                self.assertEqual(len(schedule), count)
                self.assertTrue(all(q >= 0.0 for q in schedule))
                self.assertAlmostEqual(
                    math.fsum(schedule), total, delta=1e-9 * max(1.0, total),
                    msg=f"total={total} count={count} lot={lot} seed={seed}",
                )
                for qty in schedule:  # every clip is a whole number of lots
                    self.assertAlmostEqual(qty / lot, round(qty / lot), places=9)

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            allocate_lots(1000, [0.5, -0.2, 0.7])          # negative weight
        with self.assertRaises(ValueError):
            allocate_lots(1000, [0.0, 0.0])                # weights sum to zero
        with self.assertRaises(ValueError):
            allocate_lots(1000, [])                        # empty weights
        with self.assertRaises(ValueError):
            allocate_lots(-500, [1.0, 1.0])                # negative quantity
        with self.assertRaises(ValueError):
            allocate_lots(float("nan"), [1.0])             # non-finite quantity
        with self.assertRaises(ValueError):
            allocate_lots(1000, [1.0], jitter_pct=1.5)     # jitter could go negative
        with self.assertRaises(ValueError):
            allocate_lots(10.5, [1.0, 1.0], lot_size=1.0)  # not a whole lot multiple


class TestScheduleFunctions(unittest.TestCase):

    def test_twap_schedule_conservation(self):
        schedule = twap_schedule(1000, 10, jitter_pct=0.15, rng=random.Random(0))
        self.assertEqual(len(schedule), 10)
        self.assertAlmostEqual(math.fsum(schedule), 1000.0, places=9)

    def test_vwap_schedule_matches_the_volume_curve_exactly(self):
        # 10% / 80% / 10% of 10,000 shares is 1,000 / 8,000 / 1,000 by definition.
        self.assertEqual(vwap_schedule(10000, [0.10, 0.80, 0.10], jitter_pct=0.0),
                         [1000.0, 8000.0, 1000.0])

    def test_twap_rejects_zero_intervals(self):
        with self.assertRaises(ValueError):
            twap_schedule(1000, 0)


class TestScheduleConstruction(unittest.TestCase):

    def test_regression_zero_jitter_produces_exact_timestamps(self):
        # 1.0.0 hard-coded +/-0.15 timing jitter regardless of jitter_pct, and dropped
        # start_time=0.0 via a falsy `or` default. Both are visible here.
        slicer = ExecutionSlicer(
            total_qty=100, num_intervals=4, interval_seconds=60.0,
            jitter_pct=0.0, start_time=0.0,
        )
        self.assertEqual([s.target_time for s in slicer.slices], [0.0, 60.0, 120.0, 180.0])
        self.assertEqual([s.target_qty for s in slicer.slices], [25.0] * 4)

    def test_timestamps_stay_ordered_at_the_maximum_legal_jitter(self):
        for seed in range(200):
            slicer = ExecutionSlicer(
                total_qty=1000, num_intervals=8, interval_seconds=60.0,
                jitter_pct=0.49, start_time=0.0, seed=seed,
            )
            times = [s.target_time for s in slicer.slices]
            self.assertEqual(times, sorted(times), f"seed {seed} scheduled out of order")

    def test_jitter_at_or_above_half_an_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            ExecutionSlicer(total_qty=100, num_intervals=4, jitter_pct=0.5)

    def test_seed_makes_the_schedule_reproducible(self):
        def build(seed):
            s = ExecutionSlicer(total_qty=1000, num_intervals=10, jitter_pct=0.2,
                                start_time=0.0, seed=seed)
            return [(x.target_qty, x.target_time) for x in s.slices]

        self.assertEqual(build(42), build(42))
        self.assertNotEqual(build(42), build(43))

    def test_regression_does_not_consume_the_global_rng(self):
        # 1.0.0 drew from the module-level `random`, so building a schedule shifted
        # every other consumer's stream and made backtests irreproducible.
        random.seed(123)
        expected = [random.random() for _ in range(3)]
        random.seed(123)
        ExecutionSlicer(total_qty=1000, num_intervals=10, jitter_pct=0.15, seed=7)
        self.assertEqual([random.random() for _ in range(3)], expected)

    def test_vwap_requires_a_curve_of_matching_length(self):
        with self.assertRaises(ValueError):  # no curve at all
            ExecutionSlicer(total_qty=1000, algo_type=SlicerType.VWAP, num_intervals=3)
        with self.assertRaises(ValueError):  # curve silently overrode num_intervals
            ExecutionSlicer(total_qty=1000, algo_type=SlicerType.VWAP, num_intervals=10,
                            historical_volume_curve=[0.5, 0.5])

    def test_constructor_rejects_invalid_parameters(self):
        for kwargs in (
            {"total_qty": 100, "num_intervals": 0},
            {"total_qty": -500, "num_intervals": 3},
            {"total_qty": 0, "num_intervals": 3},
            {"total_qty": float("inf"), "num_intervals": 3},
            {"total_qty": 100, "num_intervals": 3, "interval_seconds": 0},
            {"total_qty": 100, "num_intervals": 3, "lot_size": 0},
            {"total_qty": 100, "num_intervals": 3, "start_time": float("nan")},
            {"total_qty": 100, "num_intervals": 3, "max_child_multiple": 0.5},
        ):
            with self.assertRaises(ValueError, msg=f"accepted {kwargs}"):
                ExecutionSlicer(**kwargs)


class TestFillLifecycle(unittest.TestCase):

    def _slicer(self, **kw):
        kw.setdefault("total_qty", 1000)
        kw.setdefault("num_intervals", 4)
        kw.setdefault("jitter_pct", 0.0)
        kw.setdefault("start_time", 0.0)
        return ExecutionSlicer(**kw)

    def test_quantity_weighted_average_price(self):
        slicer = self._slicer(total_qty=100, num_intervals=1)
        slicer.on_child_fill(0, filled_qty=30, fill_price=100.0)
        slicer.on_child_fill(0, filled_qty=70, fill_price=110.0)
        # (30*100 + 70*110) / 100 = 107.0
        self.assertAlmostEqual(slicer.slices[0].filled_avg_price, 107.0, places=9)
        self.assertEqual(slicer.slices[0].status, SliceStatus.FILLED)

    def test_regression_unknown_slice_id_raises_instead_of_dropping_the_fill(self):
        # 1.0.0 returned silently, erasing a fill that really happened at the broker.
        slicer = self._slicer()
        with self.assertRaises(KeyError):
            slicer.on_child_fill(99, filled_qty=50, fill_price=100.0)
        with self.assertRaises(KeyError):
            slicer.on_child_fill(-1, filled_qty=50, fill_price=100.0)

    def test_regression_invalid_fill_payloads_raise(self):
        slicer = self._slicer()
        for qty, price in [(-10, 100.0), (0, 100.0), (10, 0.0), (10, -5.0),
                           (float("nan"), 100.0), (10, float("inf"))]:
            with self.assertRaises(ValueError, msg=f"accepted qty={qty} price={price}"):
                slicer.on_child_fill(0, filled_qty=qty, fill_price=price)

    def test_partial_fill_alone_does_not_release_quantity(self):
        # A working child order may still fill more; the residual is released only
        # when the child order is actually closed.
        slicer = self._slicer(catch_up_policy=CatchUpPolicy.AGGRESSIVE_CATCHUP,
                              max_child_multiple=3.0)
        slicer.on_child_fill(0, filled_qty=50, fill_price=100.0)
        self.assertEqual(slicer.unassigned_qty, 0.0)
        self.assertEqual([s.target_qty for s in slicer.slices], [250.0] * 4)
        self.assertTrue(slicer.quantity_invariant_ok())

    def test_overfill_is_recorded_not_discarded(self):
        slicer = self._slicer(total_qty=100, num_intervals=2)
        slicer.on_child_fill(0, filled_qty=200, fill_price=100.0)
        report = slicer.get_execution_report(benchmark_price=100.0)
        self.assertAlmostEqual(report.overfill_qty, 100.0, places=9)
        self.assertAlmostEqual(report.total_filled, 200.0, places=9)


class TestCatchUpPolicies(unittest.TestCase):
    """All three enum members must produce distinct, documented behaviour."""

    def _slicer(self, policy, **kw):
        kw.setdefault("total_qty", 1000)
        kw.setdefault("num_intervals", 4)
        kw.setdefault("interval_seconds", 60.0)
        kw.setdefault("jitter_pct", 0.0)
        kw.setdefault("start_time", 0.0)
        return ExecutionSlicer(catch_up_policy=policy, **kw)

    def test_regression_catchup_conserves_the_parent_quantity(self):
        # 1.0.0 redistributed the residual to the pending slices WITHOUT truncating the
        # partly-filled slice, so the schedule summed to 1200 against a 1000 parent —
        # a caller driving orders off target_qty would have over-executed by 200.
        slicer = self._slicer(CatchUpPolicy.AGGRESSIVE_CATCHUP, max_child_multiple=3.0)
        slicer.on_child_fill(0, filled_qty=50, fill_price=100.0)
        slicer.on_child_expired(0)

        scheduled = math.fsum(s.target_qty for s in slicer.slices)
        self.assertAlmostEqual(scheduled + slicer.unassigned_qty, 1000.0, places=6)
        self.assertTrue(slicer.quantity_invariant_ok())
        self.assertEqual(slicer.slices[0].target_qty, 50.0)  # truncated to what filled
        self.assertEqual(slicer.slices[0].status, SliceStatus.PARTIAL)
        # 950 left over 3 open slices of 250 each -> 317 / 317 / 316 by largest remainder
        self.assertEqual([s.target_qty for s in slicer.slices[1:]], [317.0, 317.0, 316.0])

    def test_regression_catchup_preserves_the_vwap_volume_curve(self):
        # 1.0.0 flattened every pending slice to remaining/len(pending), silently
        # converting a VWAP schedule into a TWAP one at the first partial fill.
        slicer = ExecutionSlicer(
            total_qty=1000, algo_type=SlicerType.VWAP, num_intervals=4,
            historical_volume_curve=[0.1, 0.2, 0.3, 0.4], jitter_pct=0.0,
            start_time=0.0, catch_up_policy=CatchUpPolicy.AGGRESSIVE_CATCHUP,
        )
        self.assertEqual([s.target_qty for s in slicer.slices],
                         [100.0, 200.0, 300.0, 400.0])
        slicer.on_child_expired(0)  # nothing filled; 100 released

        remaining = [s.target_qty for s in slicer.slices[1:]]
        self.assertEqual(remaining, sorted(remaining))
        self.assertNotEqual(len(set(remaining)), 1, "curve was flattened to TWAP")
        # 1000 over weights 200:300:400 -> 222 / 333 / 445
        self.assertEqual(remaining, [222.0, 333.0, 445.0])
        self.assertTrue(slicer.quantity_invariant_ok())

    def test_regression_passive_continue_is_not_a_silent_no_op(self):
        # 1.0.0 implemented nothing for the DEFAULT policy: the shortfall vanished with
        # no record. It must now be surfaced as unassigned quantity.
        slicer = self._slicer(CatchUpPolicy.PASSIVE_CONTINUE)
        self.assertIs(slicer.catch_up_policy, CatchUpPolicy.PASSIVE_CONTINUE)
        slicer.on_child_fill(0, filled_qty=50, fill_price=100.0)
        slicer.on_child_expired(0)

        self.assertEqual([s.target_qty for s in slicer.slices[1:]], [250.0] * 3)
        self.assertAlmostEqual(slicer.unassigned_qty, 200.0, places=9)
        self.assertTrue(slicer.quantity_invariant_ok())

    def test_give_up_at_deadline_cancels_post_deadline_slices(self):
        slicer = self._slicer(CatchUpPolicy.GIVE_UP_AT_DEADLINE,
                              deadline=120.0, max_child_multiple=2.0)
        slicer.on_child_expired(0)  # releases 250

        self.assertEqual(slicer.slices[2].status, SliceStatus.CANCELLED)
        self.assertEqual(slicer.slices[3].status, SliceStatus.CANCELLED)
        # Slice 1 (t=60) is the only schedulable interval; its 250 original size caps
        # catch-up at 2x = 500. The other 500 is abandoned, not crammed into one clip.
        self.assertEqual(slicer.slices[1].target_qty, 500.0)
        self.assertAlmostEqual(slicer.unassigned_qty, 500.0, places=9)
        self.assertTrue(slicer.quantity_invariant_ok())

    def test_max_child_multiple_bounds_the_catch_up_clip(self):
        capped = self._slicer(CatchUpPolicy.AGGRESSIVE_CATCHUP, num_intervals=2,
                              max_child_multiple=1.5)
        capped.on_child_expired(0)  # releases 500 onto a single 500-lot slice
        self.assertEqual(capped.slices[1].target_qty, 750.0)  # 1.5 x 500
        self.assertAlmostEqual(capped.unassigned_qty, 250.0, places=9)

        uncapped = self._slicer(CatchUpPolicy.AGGRESSIVE_CATCHUP, num_intervals=2)
        uncapped.on_child_expired(0)
        self.assertEqual(uncapped.slices[1].target_qty, 1000.0)

    def test_regression_rejection_is_handled_and_recorded(self):
        # 1.0.0 documented rejection-driven rescheduling but shipped no reject handler.
        slicer = self._slicer(CatchUpPolicy.AGGRESSIVE_CATCHUP, max_child_multiple=3.0)
        slicer.on_child_reject(0, reason="MIN_NOTIONAL")

        self.assertEqual(slicer.slices[0].status, SliceStatus.REJECTED)
        self.assertEqual(slicer.slices[0].reject_reason, "MIN_NOTIONAL")
        self.assertEqual(slicer.slices[0].target_qty, 0.0)
        self.assertTrue(slicer.quantity_invariant_ok())

    def test_closing_a_slice_twice_is_ignored(self):
        slicer = self._slicer(CatchUpPolicy.AGGRESSIVE_CATCHUP, max_child_multiple=3.0)
        slicer.on_child_expired(0)
        before = [s.target_qty for s in slicer.slices]
        slicer.on_child_expired(0)
        slicer.on_child_reject(0, reason="dup")
        slicer.on_child_cancel(0)
        self.assertEqual([s.target_qty for s in slicer.slices], before)
        self.assertTrue(slicer.quantity_invariant_ok())

    def test_invariant_survives_a_randomised_lifecycle_sweep(self):
        for seed in range(60):
            rng = random.Random(seed)
            policy = rng.choice(list(CatchUpPolicy))
            slicer = ExecutionSlicer(
                total_qty=1000, num_intervals=8, interval_seconds=60.0,
                jitter_pct=0.2, start_time=0.0, seed=seed,
                catch_up_policy=policy, max_child_multiple=3.0, deadline=300.0,
            )
            for child in list(slicer.slices):
                if child.status not in (SliceStatus.PENDING,):
                    continue
                action = rng.random()
                if action < 0.4 and child.target_qty > 0:
                    slicer.on_child_fill(child.slice_id, child.target_qty, 100.0)
                elif action < 0.6 and child.target_qty > 1:
                    slicer.on_child_fill(child.slice_id, child.target_qty / 2, 100.0)
                    slicer.on_child_expired(child.slice_id)
                elif action < 0.8:
                    slicer.on_child_reject(child.slice_id, reason="TEST")
                else:
                    slicer.on_child_cancel(child.slice_id)
                self.assertTrue(
                    slicer.quantity_invariant_ok(),
                    f"invariant broken: seed={seed} policy={policy} slice={child.slice_id}",
                )
                self.assertTrue(all(s.target_qty >= 0.0 for s in slicer.slices))


class TestReweightPending(unittest.TestCase):

    def test_reweight_moves_quantity_onto_the_observed_curve(self):
        # SKILL.md requires recomputing the VWAP schedule when live volume diverges
        # from the historical curve; 1.0.0 documented it but shipped no such method.
        slicer = ExecutionSlicer(
            total_qty=1000, algo_type=SlicerType.VWAP, num_intervals=4,
            historical_volume_curve=[0.25, 0.25, 0.25, 0.25],
            jitter_pct=0.0, start_time=0.0,
        )
        self.assertEqual([s.target_qty for s in slicer.slices], [250.0] * 4)
        slicer.on_child_fill(0, filled_qty=250, fill_price=100.0)

        # Volume turned out to be back-loaded: reweight the three open slices 1:2:3.
        slicer.reweight_pending([0.25, 0.10, 0.20, 0.30])
        remaining = [s.target_qty for s in slicer.slices[1:]]
        self.assertAlmostEqual(math.fsum(remaining), 750.0, places=6)
        self.assertEqual(remaining, [125.0, 250.0, 375.0])
        self.assertTrue(slicer.quantity_invariant_ok())

    def test_reweight_rejects_a_mismatched_curve(self):
        slicer = ExecutionSlicer(total_qty=1000, num_intervals=4, jitter_pct=0.0)
        with self.assertRaises(ValueError):
            slicer.reweight_pending([0.5, 0.5])


class TestExecutionReport(unittest.TestCase):

    def test_buy_slippage_in_basis_points(self):
        slicer = ExecutionSlicer(total_qty=1000, num_intervals=2, jitter_pct=0.0,
                                 side=OrderSide.BUY, start_time=0.0)
        slicer.on_child_fill(0, filled_qty=500, fill_price=100.0)
        slicer.on_child_fill(1, filled_qty=500, fill_price=101.0)
        report = slicer.get_execution_report(benchmark_price=100.0)

        # Achieved VWAP = (500*100 + 500*101)/1000 = 100.50 -> +50 bps of cost on a buy.
        self.assertAlmostEqual(report.vwap_achieved_price, 100.50, places=6)
        self.assertAlmostEqual(report.slippage_bps, 50.0, places=6)
        self.assertEqual(report.completion_pct, 100.0)
        self.assertEqual(report.unfilled_qty, 0.0)
        self.assertIsNone(report.opportunity_cost_bps)

    def test_regression_sell_side_slippage_is_signed_as_a_cost(self):
        # 1.0.0 was side-blind: a sell filled ABOVE the benchmark — a 100 bps price
        # improvement — was reported as +100 bps of cost.
        slicer = ExecutionSlicer(total_qty=100, num_intervals=1, jitter_pct=0.0,
                                 side=OrderSide.SELL, start_time=0.0)
        slicer.on_child_fill(0, filled_qty=100, fill_price=101.0)
        self.assertAlmostEqual(
            slicer.get_execution_report(benchmark_price=100.0).slippage_bps,
            -100.0, places=6,
        )

        worse = ExecutionSlicer(total_qty=100, num_intervals=1, jitter_pct=0.0,
                                side=OrderSide.SELL, start_time=0.0)
        worse.on_child_fill(0, filled_qty=100, fill_price=99.0)
        self.assertAlmostEqual(
            worse.get_execution_report(benchmark_price=100.0).slippage_bps,
            100.0, places=6,
        )

    def test_implementation_shortfall_decomposition(self):
        # Perold (1988): the unfilled remainder carries an opportunity cost. Buy 1000,
        # benchmark 100, 600 filled at 100.50, market ends at 102.
        #   slippage        = (100.50 - 100)/100 * 1e4 =  50 bps on 60% of the order
        #   opportunity cost= (102.00 - 100)/100 * 1e4 = 200 bps on 40% of the order
        #   IS              = 0.6*50 + 0.4*200        = 110 bps
        slicer = ExecutionSlicer(total_qty=1000, num_intervals=2, jitter_pct=0.0,
                                 side=OrderSide.BUY, start_time=0.0)
        slicer.on_child_fill(0, filled_qty=500, fill_price=100.50)
        slicer.on_child_fill(1, filled_qty=100, fill_price=100.50)
        slicer.on_child_expired(1)

        report = slicer.get_execution_report(benchmark_price=100.0, final_price=102.0)
        self.assertAlmostEqual(report.total_filled, 600.0, places=9)
        self.assertAlmostEqual(report.unfilled_qty, 400.0, places=9)
        self.assertAlmostEqual(report.slippage_bps, 50.0, places=6)
        self.assertAlmostEqual(report.opportunity_cost_bps, 200.0, places=6)
        self.assertAlmostEqual(report.implementation_shortfall_bps, 110.0, places=6)

    def test_report_surfaces_slice_states_and_the_invariant(self):
        slicer = ExecutionSlicer(total_qty=1000, num_intervals=4, jitter_pct=0.0,
                                 start_time=0.0)
        slicer.on_child_fill(0, filled_qty=250, fill_price=100.0)
        slicer.on_child_reject(1, reason="THROTTLED")
        slicer.on_child_cancel(2)
        report = slicer.get_execution_report(benchmark_price=100.0)

        self.assertEqual(report.status_counts["FILLED"], 1)
        self.assertEqual(report.status_counts["REJECTED"], 1)
        self.assertEqual(report.status_counts["CANCELLED"], 1)
        self.assertEqual(report.status_counts["PENDING"], 1)
        self.assertTrue(report.quantity_invariant_ok)
        self.assertAlmostEqual(report.notional_filled, 25000.0, places=6)

    def test_report_rejects_a_non_positive_benchmark(self):
        slicer = ExecutionSlicer(total_qty=100, num_intervals=1, jitter_pct=0.0)
        for bad in (0.0, -100.0, float("nan")):
            with self.assertRaises(ValueError):
                slicer.get_execution_report(benchmark_price=bad)
        slicer.on_child_fill(0, 100, 100.0)
        with self.assertRaises(ValueError):
            slicer.get_execution_report(benchmark_price=100.0, final_price=0.0)

    def test_report_on_a_completely_unfilled_parent_reports_zero_not_a_crash(self):
        slicer = ExecutionSlicer(total_qty=100, num_intervals=2, jitter_pct=0.0)
        report = slicer.get_execution_report(benchmark_price=100.0, final_price=105.0)
        self.assertEqual(report.total_filled, 0.0)
        self.assertEqual(report.completion_pct, 0.0)
        self.assertEqual(report.vwap_achieved_price, 0.0)
        self.assertEqual(report.slippage_bps, 0.0)
        # The whole order is unfilled, so IS is entirely opportunity cost.
        self.assertAlmostEqual(report.opportunity_cost_bps, 500.0, places=6)
        self.assertAlmostEqual(report.implementation_shortfall_bps, 500.0, places=6)


class TestPublicSurface(unittest.TestCase):
    """Names SKILL.md and references/ tell an implementer to rely on."""

    def test_documented_symbols_exist(self):
        for enum_cls, members in (
            (SlicerType, ["TWAP", "VWAP"]),
            (OrderSide, ["BUY", "SELL"]),
            (CatchUpPolicy,
             ["AGGRESSIVE_CATCHUP", "PASSIVE_CONTINUE", "GIVE_UP_AT_DEADLINE"]),
            (SliceStatus,
             ["PENDING", "PARTIAL", "FILLED", "REJECTED", "CANCELLED"]),
        ):
            for member in members:
                self.assertIn(member, enum_cls.__members__)

        slicer = ExecutionSlicer(total_qty=100, num_intervals=2, jitter_pct=0.0)
        for method in ("on_child_fill", "on_child_expired", "on_child_reject",
                       "on_child_cancel", "reweight_pending", "get_execution_report",
                       "open_slices", "actionable_slices", "remaining_qty",
                       "total_filled", "quantity_invariant_ok"):
            self.assertTrue(callable(getattr(slicer, method)), method)
        self.assertIsInstance(slicer.slices[0], ChildOrderSlice)
        self.assertIsInstance(slicer.get_execution_report(100.0), ExecutionReport)

    def test_slice_status_compares_equal_to_its_string(self):
        # Callers written against 1.0.0 compared status to bare string literals.
        slicer = ExecutionSlicer(total_qty=100, num_intervals=2, jitter_pct=0.0)
        self.assertEqual(slicer.slices[0].status, "PENDING")


if __name__ == "__main__":
    unittest.main()
