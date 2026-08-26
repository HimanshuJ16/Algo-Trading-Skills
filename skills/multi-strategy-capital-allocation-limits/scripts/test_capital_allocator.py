"""
Unit tests for multi-strategy-capital-allocation-limits skill.

Expected dollar values are derived by hand in the comments rather than
re-deriving them from the implementation's own arithmetic.
"""
import logging
import threading
import unittest

from capital_allocator import (
    AllocationError,
    MultiStrategyCapitalAllocator,
    RejectionCode,
)

# Keep logging's "last resort" stderr handler from printing expected breach
# warnings during the run; assertLogs still installs its own handler.
logging.getLogger("capital_allocator").addHandler(logging.NullHandler())

NAV = 100_000.0


class TestMultiStrategyCapitalAllocator(unittest.TestCase):

    def setUp(self):
        self.allocator = MultiStrategyCapitalAllocator(cash_reserve_pct=0.10)
        self.allocator.register_strategy("momentum", 0.40)
        self.allocator.register_strategy("mean_revert", 0.30)
        self.allocator.register_strategy("stat_arb", 0.20)
        self.allocator.validate_allocations()

    # ------------------------------------------------------------------
    # Core per-strategy cap behaviour
    # ------------------------------------------------------------------

    def test_order_within_allocation(self):
        """Order within cap should be approved."""
        self.allocator.update_exposure("momentum", 20000.0)
        result = self.allocator.check_order("momentum", 15000.0, NAV)
        # Max = 40000, current = 20000, order = 15000, projected = 35000 < 40000
        self.assertTrue(result.approved)
        self.assertAlmostEqual(result.max_allowed_usd, 40000.0)
        self.assertAlmostEqual(result.remaining_capacity_usd, 20000.0)

    def test_order_exceeds_allocation(self):
        """Order exceeding cap should be rejected."""
        self.allocator.update_exposure("mean_revert", 25000.0)
        result = self.allocator.check_order("mean_revert", 10000.0, NAV)
        # Max = 30000, current = 25000, order = 10000, projected = 35000 > 30000
        self.assertFalse(result.approved)
        self.assertIn("ALLOCATION BREACH", result.rejection_reason)
        self.assertEqual(result.rejection_code, RejectionCode.STRATEGY_CAP)

    def test_order_exactly_at_cap_is_approved(self):
        """Boundary: projected exposure landing exactly on the cap must pass."""
        self.allocator.update_exposure("stat_arb", 5000.0)
        # Max = 0.20 * 100000 = 20000; 5000 + 15000 == 20000 exactly.
        result = self.allocator.check_order("stat_arb", 15000.0, NAV)
        self.assertTrue(result.approved)

    def test_order_one_dollar_over_cap_is_rejected(self):
        """Boundary: a dollar past the cap must not slip through the cent tolerance."""
        self.allocator.update_exposure("stat_arb", 5000.0)
        result = self.allocator.check_order("stat_arb", 15001.0, NAV)
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_code, RejectionCode.STRATEGY_CAP)

    def test_exposure_reducing_order_never_vetoed(self):
        """A de-risking order is approved even when the strategy is over cap."""
        self.allocator.update_exposure("momentum", 55000.0)  # cap is 40000
        result = self.allocator.check_order("momentum", -10000.0, NAV)
        self.assertTrue(result.approved)
        self.assertIsNone(result.rejection_reason)

    def test_unknown_strategy_is_rejected_with_code(self):
        result = self.allocator.check_order("ghost", 1000.0, NAV)
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_code, RejectionCode.UNKNOWN_STRATEGY)

    # ------------------------------------------------------------------
    # Regression: fail closed on non-finite / non-positive inputs
    # ------------------------------------------------------------------

    def test_nan_order_value_is_rejected(self):
        """Regression: NaN made `projected > cap` false, silently APPROVING the order."""
        result = self.allocator.check_order("momentum", float("nan"), NAV)
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_code, RejectionCode.INVALID_INPUT)

    def test_infinite_order_value_is_rejected(self):
        result = self.allocator.check_order("momentum", float("inf"), NAV)
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_code, RejectionCode.INVALID_INPUT)

    def test_nan_nav_is_rejected(self):
        """Regression: a NaN NAV produced a NaN cap and approved every order."""
        result = self.allocator.check_order("momentum", 1000.0, float("nan"))
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_code, RejectionCode.INVALID_INPUT)

    def test_non_positive_nav_is_rejected(self):
        for bad_nav in (0.0, -100_000.0):
            with self.subTest(nav=bad_nav):
                result = self.allocator.check_order("momentum", 1000.0, bad_nav)
                self.assertFalse(result.approved)
                self.assertEqual(result.rejection_code, RejectionCode.INVALID_INPUT)

    def test_update_exposure_rejects_bad_values(self):
        with self.assertRaises(AllocationError):
            self.allocator.update_exposure("momentum", float("nan"))
        with self.assertRaises(AllocationError):
            # Gross notional only: a negative would manufacture fake headroom.
            self.allocator.update_exposure("momentum", -25000.0)
        with self.assertRaises(AllocationError):
            self.allocator.update_exposure("ghost", 1000.0)

    # ------------------------------------------------------------------
    # Regression: in-flight orders consume capacity
    # ------------------------------------------------------------------

    def test_second_reservation_cannot_reuse_in_flight_capacity(self):
        """Regression: two unfilled orders each fitting the cap jointly breached it."""
        self.allocator.update_exposure("momentum", 20000.0)  # cap 40000
        first = self.allocator.reserve("momentum", 15000.0, NAV, order_id="o-1")
        self.assertTrue(first.approved)
        # 20000 settled + 15000 in flight = 35000; a second 15000 would reach 50000.
        second = self.allocator.reserve("momentum", 15000.0, NAV, order_id="o-2")
        self.assertFalse(second.approved)
        self.assertEqual(second.rejection_code, RejectionCode.STRATEGY_CAP)
        self.assertAlmostEqual(second.pending_exposure_usd, 15000.0)

    def test_reserve_reports_post_reservation_headroom(self):
        """Sizing a follow-up order off a reserve() result must not re-spend the
        headroom that same call just claimed."""
        result = self.allocator.reserve("momentum", 30000.0, NAV, "h-1")
        # Cap 40000, nothing settled, 30000 now held -> 10000 left.
        self.assertTrue(result.approved)
        self.assertAlmostEqual(result.remaining_capacity_usd, 10000.0)
        self.assertAlmostEqual(result.pending_exposure_usd, 30000.0)
        retry = self.allocator.reserve("momentum", 30000.0, NAV, "h-1")
        self.assertAlmostEqual(retry.remaining_capacity_usd, 10000.0)
        self.assertAlmostEqual(retry.pending_exposure_usd, 30000.0)

    def test_rejected_reservation_consumes_no_capacity(self):
        self.allocator.update_exposure("mean_revert", 25000.0)  # cap 30000
        rejected = self.allocator.reserve("mean_revert", 10000.0, NAV, order_id="o-x")
        self.assertFalse(rejected.approved)
        report = self.allocator.get_utilization_report(NAV)
        row = next(r for r in report if r["strategy"] == "mean_revert")
        self.assertAlmostEqual(row["pending_exposure_usd"], 0.0)
        # The 5000 of genuine headroom must still be usable.
        self.assertTrue(
            self.allocator.reserve("mean_revert", 5000.0, NAV, order_id="o-y").approved
        )

    def test_release_restores_capacity_and_is_idempotent(self):
        self.allocator.update_exposure("momentum", 20000.0)
        self.assertTrue(self.allocator.reserve("momentum", 15000.0, NAV, "o-1").approved)
        self.assertFalse(self.allocator.reserve("momentum", 15000.0, NAV, "o-2").approved)

        self.assertTrue(self.allocator.release_reservation("o-1"))
        self.assertFalse(self.allocator.release_reservation("o-1"))  # idempotent
        self.assertFalse(self.allocator.release_reservation("never-existed"))
        # Capacity is back: 20000 settled + 15000 = 35000 <= 40000.
        self.assertTrue(self.allocator.reserve("momentum", 15000.0, NAV, "o-3").approved)

    def test_duplicate_order_id_does_not_double_count(self):
        """A retry after an ambiguous submission must not book the capital twice."""
        first = self.allocator.reserve("momentum", 30000.0, NAV, "dup-1")
        retry = self.allocator.reserve("momentum", 30000.0, NAV, "dup-1")
        self.assertTrue(first.approved)
        self.assertTrue(retry.approved)
        summary = self.allocator.get_portfolio_summary(NAV)
        self.assertAlmostEqual(summary["total_pending_usd"], 30000.0)
        self.assertEqual(summary["open_reservations"], 1)

    def test_conflicting_order_id_raises(self):
        self.allocator.reserve("momentum", 10000.0, NAV, "dup-2")
        with self.assertRaises(AllocationError):
            self.allocator.reserve("mean_revert", 10000.0, NAV, "dup-2")
        with self.assertRaises(AllocationError):
            self.allocator.reserve("momentum", 12000.0, NAV, "dup-2")

    def test_settle_converts_pending_into_exposure(self):
        self.assertTrue(self.allocator.reserve("stat_arb", 12000.0, NAV, "s-1").approved)
        self.assertTrue(self.allocator.settle_reservation("s-1"))
        row = next(
            r for r in self.allocator.get_utilization_report(NAV)
            if r["strategy"] == "stat_arb"
        )
        self.assertAlmostEqual(row["current_exposure_usd"], 12000.0)
        self.assertAlmostEqual(row["pending_exposure_usd"], 0.0)
        self.assertFalse(self.allocator.settle_reservation("s-1"))  # idempotent

    def test_partial_fill_keeps_remainder_reserved(self):
        self.assertTrue(self.allocator.reserve("stat_arb", 12000.0, NAV, "s-2").approved)
        self.allocator.settle_reservation("s-2", filled_usd=5000.0, close=False)
        row = next(
            r for r in self.allocator.get_utilization_report(NAV)
            if r["strategy"] == "stat_arb"
        )
        # 5000 filled, 7000 still working: committed is unchanged at 12000.
        self.assertAlmostEqual(row["current_exposure_usd"], 5000.0)
        self.assertAlmostEqual(row["pending_exposure_usd"], 7000.0)
        self.assertAlmostEqual(row["committed_exposure_usd"], 12000.0)

    def test_overfill_does_not_flip_reservation_sign(self):
        """A fill larger than its reservation must not leave a negative remainder
        that a later default settle would subtract from exposure."""
        self.assertTrue(self.allocator.reserve("stat_arb", 10000.0, NAV, "s-4").approved)
        # Price moved: 12000 of notional filled against a 10000 reservation.
        self.allocator.settle_reservation("s-4", filled_usd=12000.0, close=False)
        self.allocator.settle_reservation("s-4")  # remainder settles as 0, not -2000
        row = next(
            r for r in self.allocator.get_utilization_report(NAV)
            if r["strategy"] == "stat_arb"
        )
        self.assertAlmostEqual(row["current_exposure_usd"], 12000.0)
        self.assertAlmostEqual(row["pending_exposure_usd"], 0.0)

    def test_settle_rejects_non_finite_fill(self):
        self.allocator.reserve("stat_arb", 1000.0, NAV, "s-3")
        with self.assertRaises(AllocationError):
            self.allocator.settle_reservation("s-3", filled_usd=float("nan"))

    # ------------------------------------------------------------------
    # Account-level ceiling
    # ------------------------------------------------------------------

    def test_portfolio_cap_blocks_order_that_breaches_cash_reserve(self):
        """Mark-to-market drift can leave every strategy under its own cap while
        the account as a whole eats into the 10% cash reserve."""
        # Investable = 90% * 100000 = 90000. Caps are 40000/30000/20000.
        self.allocator.update_exposure("momentum", 40000.0)
        self.allocator.update_exposure("mean_revert", 25000.0)
        self.allocator.update_exposure("stat_arb", 20000.0)
        # Book = 85000. mean_revert has 5000 of its own headroom, and the
        # account has 5000 as well, so this order is fine.
        self.assertTrue(self.allocator.check_order("mean_revert", 1000.0, NAV).approved)

        # Now momentum's marks drift 5000 above its own cap (nothing was traded
        # -- prices moved). Book = 90000, exactly the investable ceiling.
        # mean_revert still shows 5000 of unused allocation, so the per-strategy
        # check alone would wave the same order through.
        self.allocator.update_exposure("momentum", 45000.0)
        result = self.allocator.check_order("mean_revert", 1000.0, NAV)
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_code, RejectionCode.PORTFOLIO_CAP)
        self.assertIn("PORTFOLIO ALLOCATION BREACH", result.rejection_reason)
        # The account, not the strategy, is the binding constraint: downsizing to
        # mean_revert's own 5000 of headroom would just be rejected again.
        self.assertAlmostEqual(result.remaining_capacity_usd, 0.0)

    def test_portfolio_summary_flags_breach(self):
        self.allocator.update_exposure("momentum", 40000.0)
        self.allocator.update_exposure("mean_revert", 30000.0)
        self.allocator.update_exposure("stat_arb", 20000.0)
        summary = self.allocator.get_portfolio_summary(NAV)
        # 90000 committed against a 90000 investable ceiling: at the limit, not over.
        self.assertAlmostEqual(summary["total_committed_usd"], 90000.0)
        self.assertAlmostEqual(summary["investable_usd"], 90000.0)
        self.assertFalse(summary["is_over_cap"])
        self.allocator.update_exposure("stat_arb", 21000.0)
        self.assertTrue(self.allocator.get_portfolio_summary(NAV)["is_over_cap"])

    # ------------------------------------------------------------------
    # Configuration integrity
    # ------------------------------------------------------------------

    def test_over_allocation_rejected_at_registration(self):
        """Total allocations exceeding investable capital should raise, and the
        rejected registration must not be applied."""
        allocator = MultiStrategyCapitalAllocator(cash_reserve_pct=0.10)
        allocator.register_strategy("s1", 0.50)
        with self.assertRaises(AllocationError):
            allocator.register_strategy("s2", 0.50)  # total = 100% > 90% investable
        self.assertNotIn("s2", allocator.strategies)
        allocator.validate_allocations()  # roster left in a valid state

    def test_validate_allocations_passes_on_valid_roster(self):
        self.allocator.validate_allocations()  # 40 + 30 + 20 == 90% exactly

    def test_duplicate_registration_raises_and_preserves_exposure(self):
        """Regression: re-registering silently reset tracked exposure to 0,
        unbinding the cap after a config reload."""
        self.allocator.update_exposure("momentum", 35000.0)
        with self.assertRaises(AllocationError):
            self.allocator.register_strategy("momentum", 0.40)
        self.assertAlmostEqual(
            self.allocator.strategies["momentum"].current_exposure_usd, 35000.0
        )

    def test_update_allocation_preserves_exposure(self):
        self.allocator.update_exposure("momentum", 35000.0)
        self.allocator.update_allocation("momentum", 0.20)
        alloc = self.allocator.strategies["momentum"]
        self.assertAlmostEqual(alloc.current_exposure_usd, 35000.0)
        self.assertAlmostEqual(alloc.max_allocation_pct, 0.20)
        # Now over its own cap: further exposure-increasing orders are blocked.
        self.assertFalse(self.allocator.check_order("momentum", 1.0, NAV).approved)

    def test_update_allocation_respects_budget(self):
        with self.assertRaises(AllocationError):
            self.allocator.update_allocation("stat_arb", 0.30)  # 40+30+30 = 100% > 90%
        with self.assertRaises(AllocationError):
            self.allocator.update_allocation("ghost", 0.10)

    def test_invalid_allocation_pct_rejected(self):
        allocator = MultiStrategyCapitalAllocator()
        for bad in (0.0, -0.1, 1.5, float("nan")):
            with self.subTest(pct=bad):
                with self.assertRaises(AllocationError):
                    allocator.register_strategy("bad", bad)

    def test_invalid_cash_reserve_rejected(self):
        for bad in (-0.1, 1.0, 1.5, float("nan")):
            with self.subTest(reserve=bad):
                with self.assertRaises(AllocationError):
                    MultiStrategyCapitalAllocator(cash_reserve_pct=bad)

    def test_zero_cash_reserve_allows_full_allocation(self):
        allocator = MultiStrategyCapitalAllocator(cash_reserve_pct=0.0)
        allocator.register_strategy("all_in", 1.0)
        allocator.validate_allocations()
        self.assertTrue(allocator.check_order("all_in", NAV, NAV).approved)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def test_utilization_report(self):
        """Utilization report should reflect current exposures."""
        self.allocator.update_exposure("momentum", 20000.0)
        self.allocator.update_exposure("mean_revert", 15000.0)
        report = self.allocator.get_utilization_report(NAV)
        self.assertEqual(len(report), 3)
        momentum = next(r for r in report if r["strategy"] == "momentum")
        self.assertAlmostEqual(momentum["utilization_pct"], 0.50)  # 20k / 40k
        self.assertFalse(momentum["is_over_cap"])

    def test_utilization_report_flags_over_cap_strategy(self):
        self.allocator.update_exposure("stat_arb", 25000.0)  # cap 20000
        row = next(
            r for r in self.allocator.get_utilization_report(NAV)
            if r["strategy"] == "stat_arb"
        )
        self.assertTrue(row["is_over_cap"])
        self.assertAlmostEqual(row["remaining_usd"], 0.0)

    def test_reports_reject_bad_nav(self):
        for bad_nav in (0.0, -1.0, float("nan")):
            with self.subTest(nav=bad_nav):
                with self.assertRaises(AllocationError):
                    self.allocator.get_utilization_report(bad_nav)
                with self.assertRaises(AllocationError):
                    self.allocator.get_portfolio_summary(bad_nav)

    # ------------------------------------------------------------------
    # Concurrency
    # ------------------------------------------------------------------

    def test_concurrent_reservations_do_not_overshoot_cap(self):
        """Regression: check-then-act let concurrent orders share the same headroom."""
        allocator = MultiStrategyCapitalAllocator(cash_reserve_pct=0.10)
        allocator.register_strategy("momentum", 0.40)  # cap = 40000
        approvals = []
        approvals_lock = threading.Lock()
        start = threading.Barrier(20)

        def worker(i):
            start.wait()
            result = allocator.reserve("momentum", 5000.0, NAV, order_id=f"c-{i}")
            with approvals_lock:
                approvals.append(result.approved)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 40000 cap / 5000 per order == exactly 8 orders may be reserved.
        self.assertEqual(sum(approvals), 8)
        self.assertAlmostEqual(
            allocator.strategies["momentum"].pending_exposure_usd, 40000.0
        )


if __name__ == "__main__":
    unittest.main()
