"""
Unit tests for multi-account-same-strategy-fan-out.

Expected quantities are derived by hand from the largest-remainder definition, not by
re-running the implementation's own arithmetic. Several tests are explicit regressions
against the two defects the previous per-account `max(min_order_qty, int(round(q*w)))`
allocator had:

  * independent per-account rounding, which let the fan-out under- or over-execute the
    master signal (4 equal accounts x 6 shares dispatched 8 shares); and
  * an unconditional minimum-quantity floor, which handed sub-entitlement accounts
    quantity the master signal never authorised.

Each such test is marked REGRESSION and states the quantity the old allocator produced.
"""
import random
import threading
import unittest
from fractions import Fraction

from fanout_engine import (
    ALLOCATION_METHOD_NAV,
    ALLOCATION_METHOD_WEIGHT,
    EXCLUDED_BELOW_MIN_ORDER_QTY,
    EXCLUDED_ZERO_ENTITLEMENT,
    MultiAccountStrategyFanOut,
    apportion_largest_remainder,
)


def quantities(report):
    return {o.account_id: o.allocated_quantity for o in report.account_orders}


class TestApportionLargestRemainder(unittest.TestCase):
    """Direct tests of the apportionment primitive, against hand-computed values."""

    def test_exact_division_needs_no_remainder(self):
        # 1000 x {0.5, 0.3, 0.2} = {500, 300, 200}; nothing is stranded by flooring.
        allocations, exact, winners = apportion_largest_remainder(
            {"A": 500000.0, "B": 300000.0, "C": 200000.0}, 1000)
        self.assertEqual(allocations, {"A": 500, "B": 300, "C": 200})
        self.assertEqual(winners, [])
        self.assertAlmostEqual(exact["A"], 500.0)

    def test_remainders_go_to_largest_fractional_parts(self):
        # exact = 3.5, 2.1, 1.4 -> floors 3, 2, 1 = 6, one share left.
        # Fractional parts 0.5 > 0.4 > 0.1, so A takes it.
        allocations, _, winners = apportion_largest_remainder(
            {"A": 50.0, "B": 30.0, "C": 20.0}, 7)
        self.assertEqual(allocations, {"A": 4, "B": 2, "C": 1})
        self.assertEqual(winners, ["A"])
        self.assertEqual(sum(allocations.values()), 7)

    def test_ties_broken_deterministically_by_account_id(self):
        # All fractional parts are 1/3; the tie-break must be reproducible, not
        # dict-insertion-order dependent, so an auditor can re-derive the split.
        first, _, _ = apportion_largest_remainder({"C": 1.0, "A": 1.0, "B": 1.0}, 10)
        second, _, _ = apportion_largest_remainder({"A": 1.0, "B": 1.0, "C": 1.0}, 10)
        self.assertEqual(first, second)
        self.assertEqual(first, {"A": 4, "B": 3, "C": 3})

    def test_sum_invariance_over_random_inputs(self):
        rng = random.Random(20240517)
        for _ in range(2000):
            n = rng.randint(1, 15)
            bases = {f"A{i:02d}": rng.uniform(1.0, 1e7) for i in range(n)}
            total = rng.randint(0, 10000)
            allocations, _, _ = apportion_largest_remainder(bases, total)
            self.assertEqual(sum(allocations.values()), total)
            self.assertTrue(all(q >= 0 for q in allocations.values()))

    def test_integer_entitlements_are_not_misfloored(self):
        # REGRESSION (float flooring): bases 1/20/29 over 100 shares entitle the three
        # accounts to exactly 2, 40 and 58 shares. Computed in floating point,
        # 100 * (29.0 / 50.0) evaluates to 57.99999999999999 and floors to 57, so the
        # third account was short a share and then had it handed back by the remainder
        # pass -- which also mislabelled it as a remainder award. Exact rational
        # arithmetic over the float bases removes both effects.
        allocations, _, winners = apportion_largest_remainder(
            {"A": 1.0, "B": 20.0, "C": 29.0}, 100)
        self.assertEqual(allocations, {"A": 2, "B": 40, "C": 58})
        self.assertEqual(winners, [], "an exact split must award no remainder shares")

    def test_matches_exact_rational_apportionment(self):
        # Cross-check against an independently written exact-rational implementation
        # rather than against the engine's own arithmetic.
        rng = random.Random(31337)
        for _ in range(1500):
            counts = [rng.randint(1, 500) for _ in range(rng.randint(1, 8))]
            bases = {f"A{i:02d}": float(c) for i, c in enumerate(counts)}
            total = rng.randint(0, 3000)

            denominator = Fraction(sum(counts))
            entitlement = {
                f"A{i:02d}": Fraction(total) * Fraction(c) / denominator
                for i, c in enumerate(counts)
            }
            expected = {k: v.numerator // v.denominator for k, v in entitlement.items()}
            leftover = total - sum(expected.values())
            ranked = sorted(
                expected, key=lambda k: (-(entitlement[k] - expected[k]), k))
            for account in ranked[:leftover]:
                expected[account] += 1

            actual, _, _ = apportion_largest_remainder(bases, total)
            self.assertEqual(actual, expected)

    def test_empty_and_invalid_inputs(self):
        self.assertEqual(apportion_largest_remainder({}, 100), ({}, {}, []))
        with self.assertRaises(ValueError):
            apportion_largest_remainder({"A": 1.0}, -1)
        for bad_basis in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(basis=bad_basis):
                with self.assertRaises(ValueError):
                    apportion_largest_remainder({"A": 1.0, "B": bad_basis}, 10)


class TestProRataAllocation(unittest.TestCase):

    def setUp(self):
        self.engine = MultiAccountStrategyFanOut(min_order_qty=1)
        self.engine.register_account("ACC_ALPHA", 500000.0)
        self.engine.register_account("ACC_BETA", 300000.0)
        self.engine.register_account("ACC_GAMMA", 200000.0)
        # Total NAV = $1,000,000

    def test_pro_rata_quantity_allocation(self):
        report = self.engine.calculate_fanout_orders(
            symbol="AAPL", action="BUY", total_target_quantity=1000)

        self.assertEqual(len(report.account_orders), 3)
        self.assertEqual(report.total_allocated_qty, 1000)
        self.assertTrue(report.is_fully_allocated)
        self.assertEqual(
            quantities(report), {"ACC_ALPHA": 500, "ACC_BETA": 300, "ACC_GAMMA": 200})
        self.assertEqual(report.allocation_method, ALLOCATION_METHOD_NAV)

    def test_audit_fields_are_populated(self):
        # 17 CFR 1.35(b)(5)(iv)(C) requires the allocation be independently verifiable;
        # the report must therefore carry the basis each quantity was derived from.
        report = self.engine.calculate_fanout_orders("AAPL", "BUY", 1000)
        self.assertEqual(report.total_basis, 1000000.0)
        alpha = next(o for o in report.account_orders if o.account_id == "ACC_ALPHA")
        self.assertEqual(alpha.allocation_basis, 500000.0)
        self.assertAlmostEqual(alpha.allocation_weight, 0.5)
        self.assertAlmostEqual(alpha.exact_quantity, 500.0)
        self.assertAlmostEqual(
            sum(o.allocation_weight for o in report.account_orders), 1.0)
        self.assertGreater(report.allocated_at_ms, 0)

    def test_no_active_accounts_allocates_nothing(self):
        empty = MultiAccountStrategyFanOut()
        report = empty.calculate_fanout_orders("AAPL", "BUY", 100)
        self.assertEqual(report.account_orders, [])
        self.assertEqual(report.total_allocated_qty, 0)
        self.assertFalse(report.is_fully_allocated)


class TestSumInvarianceRegressions(unittest.TestCase):
    """REGRESSION: independent per-account rounding lost or invented quantity."""

    @staticmethod
    def _equal_accounts(count):
        engine = MultiAccountStrategyFanOut(min_order_qty=1)
        for i in range(count):
            engine.register_account(f"ACC_{i:02d}", 100000.0)
        return engine

    def test_three_equal_accounts_ten_shares_allocates_all_ten(self):
        # exact = 3.333 each -> floors 3,3,3 = 9, one share left -> lowest id takes it.
        # REGRESSION: round(3.333) = 3 per account allocated only 9 of 10 shares; the
        # tenth was never traded and nothing in the report said so.
        report = self._equal_accounts(3).calculate_fanout_orders("X", "BUY", 10)
        self.assertEqual(report.total_allocated_qty, 10)
        self.assertTrue(report.is_fully_allocated)
        self.assertEqual(quantities(report), {"ACC_00": 4, "ACC_01": 3, "ACC_02": 3})

    def test_seven_equal_accounts_ten_shares_allocates_all_ten(self):
        # exact = 1.4286 each -> floors 1 x 7 = 7, three shares left to the three
        # lowest ids (all remainders equal).
        # REGRESSION: round(1.4286) = 1 per account allocated 7 of 10 -- 30% of the
        # master signal silently dropped.
        report = self._equal_accounts(7).calculate_fanout_orders("X", "BUY", 10)
        self.assertEqual(report.total_allocated_qty, 10)
        self.assertEqual(
            quantities(report),
            {"ACC_00": 2, "ACC_01": 2, "ACC_02": 2,
             "ACC_03": 1, "ACC_04": 1, "ACC_05": 1, "ACC_06": 1})

    def test_four_equal_accounts_six_shares_does_not_over_execute(self):
        # exact = 1.5 each -> floors 1 x 4 = 4, two shares left to the two lowest ids.
        # REGRESSION: Python's round() is banker's rounding, so round(1.5) = 2 for
        # every account and the old allocator dispatched 8 shares for a 6-share
        # signal -- a 33% over-execution of the master order.
        report = self._equal_accounts(4).calculate_fanout_orders("X", "BUY", 6)
        self.assertEqual(report.total_allocated_qty, 6)
        self.assertEqual(
            quantities(report),
            {"ACC_00": 2, "ACC_01": 2, "ACC_02": 1, "ACC_03": 1})

    def test_two_equal_accounts_five_shares_allocates_all_five(self):
        # exact = 2.5 each. REGRESSION: round(2.5) = 2 (round-half-to-even) for both,
        # allocating 4 of 5 shares.
        report = self._equal_accounts(2).calculate_fanout_orders("X", "BUY", 5)
        self.assertEqual(quantities(report), {"ACC_00": 3, "ACC_01": 2})
        self.assertEqual(report.total_allocated_qty, 5)

    def test_sum_invariance_across_engine_level_random_inputs(self):
        rng = random.Random(7)
        for _ in range(1000):
            engine = MultiAccountStrategyFanOut(min_order_qty=1)
            for i in range(rng.randint(1, 12)):
                engine.register_account(f"ACC_{i:02d}", rng.uniform(1.0, 5e6))
            quantity = rng.randint(1, 5000)
            report = engine.calculate_fanout_orders("X", "BUY", quantity)
            self.assertEqual(report.total_allocated_qty, quantity)
            self.assertTrue(report.is_fully_allocated)


class TestMinimumOrderQuantity(unittest.TestCase):
    """REGRESSION: the floor used to raise allocations instead of excluding accounts."""

    def test_sub_entitlement_account_receives_nothing_not_a_floor_order(self):
        # ACC_TINY holds 0.01% of NAV; its entitlement out of 10 shares is 0.001.
        # REGRESSION: `max(min_order_qty, round(q*w))` gave ACC_TINY 1 share -- 1000x
        # its pro-rata entitlement -- and dispatched 11 shares for a 10-share signal.
        # 17 CFR 1.35(b)(5)(iv)(B) forbids an account consistently receiving
        # favorable treatment, which a guaranteed floor share is.
        engine = MultiAccountStrategyFanOut(min_order_qty=1)
        engine.register_account("ACC_HUGE", 999900.0)
        engine.register_account("ACC_TINY", 100.0)

        report = engine.calculate_fanout_orders("MSFT", "BUY", 10)

        self.assertEqual(quantities(report), {"ACC_HUGE": 10})
        self.assertEqual(report.total_allocated_qty, 10)
        self.assertTrue(report.is_fully_allocated)
        excluded = {e.account_id: e.reason for e in report.excluded_accounts}
        self.assertEqual(excluded, {"ACC_TINY": EXCLUDED_ZERO_ENTITLEMENT})

    def test_sell_side_does_not_open_a_short_in_a_zero_entitlement_account(self):
        # REGRESSION: on a SELL the old floor issued ACC_TINY a 1-share sell order.
        # An account whose fair share of the exit is zero has nothing to sell, so the
        # order would have opened an unintended short position.
        engine = MultiAccountStrategyFanOut(min_order_qty=1)
        engine.register_account("ACC_HUGE", 999900.0)
        engine.register_account("ACC_TINY", 100.0)

        report = engine.calculate_fanout_orders("MSFT", "SELL", 10)

        self.assertNotIn("ACC_TINY", quantities(report))
        self.assertTrue(all(o.allocated_quantity > 0 for o in report.account_orders))

    def test_below_floor_account_is_dropped_and_its_shares_redistributed(self):
        # exact = 50.0, 49.0, 1.0. With a 5-share floor, C's single share is below the
        # floor, so C is dropped and the 100 shares are re-apportioned over A and B
        # (basis 50/49): exact 50.505 and 49.495 -> floors 50, 49 = 99, one left to A.
        engine = MultiAccountStrategyFanOut(min_order_qty=5)
        engine.register_account("A", 50.0)
        engine.register_account("B", 49.0)
        engine.register_account("C", 1.0)

        report = engine.calculate_fanout_orders("X", "BUY", 100)

        self.assertEqual(quantities(report), {"A": 51, "B": 49})
        self.assertEqual(report.total_allocated_qty, 100)
        self.assertTrue(report.is_fully_allocated)
        self.assertEqual(
            [(e.account_id, e.reason) for e in report.excluded_accounts],
            [("C", EXCLUDED_BELOW_MIN_ORDER_QTY)])

    def test_every_account_below_floor_allocates_nothing_and_flags_it(self):
        engine = MultiAccountStrategyFanOut(min_order_qty=100)
        engine.register_account("A", 100.0)
        engine.register_account("B", 100.0)

        report = engine.calculate_fanout_orders("X", "BUY", 10)

        self.assertEqual(report.account_orders, [])
        self.assertEqual(report.total_allocated_qty, 0)
        self.assertFalse(report.is_fully_allocated)
        self.assertEqual(len(report.excluded_accounts), 2)

    def test_every_dispatched_order_clears_the_floor(self):
        rng = random.Random(99)
        for _ in range(500):
            floor = rng.choice([1, 2, 5, 25])
            engine = MultiAccountStrategyFanOut(min_order_qty=floor)
            for i in range(rng.randint(1, 10)):
                engine.register_account(f"ACC_{i:02d}", rng.uniform(1.0, 1e6))
            report = engine.calculate_fanout_orders("X", "BUY", rng.randint(1, 2000))
            for order in report.account_orders:
                self.assertGreaterEqual(order.allocated_quantity, floor)


class TestClientOrderIds(unittest.TestCase):

    def setUp(self):
        self.engine = MultiAccountStrategyFanOut()
        self.engine.register_account("ACC_ALPHA", 500000.0)
        self.engine.register_account("ACC_BETA", 300000.0)
        self.engine.register_account("ACC_GAMMA", 200000.0)

    def test_ids_unique_within_a_batch_and_prefixed(self):
        report = self.engine.calculate_fanout_orders("TSLA", "SELL", 500)
        order_ids = [o.client_order_id for o in report.account_orders]
        self.assertEqual(len(order_ids), len(set(order_ids)))
        for order_id in order_ids:
            self.assertTrue(order_id.startswith("CLORD_"))

    def test_ids_are_deterministic_for_a_replayed_batch(self):
        # An ambiguous timeout must be retried with the SAME client order ID so the
        # broker rejects the duplicate rather than filling the order twice.
        first = self.engine.calculate_fanout_orders("TSLA", "SELL", 500, batch_id="b-001")
        replay = self.engine.calculate_fanout_orders("TSLA", "SELL", 500, batch_id="b-001")
        self.assertEqual(
            [o.client_order_id for o in first.account_orders],
            [o.client_order_id for o in replay.account_orders])
        self.assertEqual(
            [o.client_order_id for o in first.account_orders],
            ["CLORD_b-001_ACC_ALPHA", "CLORD_b-001_ACC_BETA", "CLORD_b-001_ACC_GAMMA"])

    def test_ids_differ_across_batches(self):
        first = self.engine.calculate_fanout_orders("TSLA", "SELL", 500)
        second = self.engine.calculate_fanout_orders("TSLA", "SELL", 500)
        self.assertTrue(set(o.client_order_id for o in first.account_orders).isdisjoint(
            o.client_order_id for o in second.account_orders))

    def test_batch_id_with_separator_is_rejected(self):
        # `_` separates the id fields; permitting it would make CLORD_{batch}_{account}
        # ambiguous, which is how two different orders end up with one id.
        with self.assertRaises(ValueError):
            self.engine.calculate_fanout_orders("X", "BUY", 10, batch_id="b_001")

    def test_ids_are_unique_under_concurrent_fan_out(self):
        # The skill's stated dispatch model is concurrent. The previous shared
        # `self._seq_counter += 1` was an unguarded read-modify-write, so two threads
        # could mint the same sequence number and therefore the same client order id.
        collected = []
        errors = []
        lock = threading.Lock()

        def worker(index):
            try:
                report = self.engine.calculate_fanout_orders(
                    "X", "BUY", 1000, batch_id=f"batch-{index:03d}")
                with lock:
                    collected.extend(o.client_order_id for o in report.account_orders)
                    self.assertEqual(report.total_allocated_qty, 1000)
            except Exception as exc:  # surfaced below; assertions cannot cross threads
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(collected), 32 * 3)
        self.assertEqual(len(collected), len(set(collected)))

    def test_registry_mutation_during_fan_out_does_not_corrupt_a_batch(self):
        errors = []

        def register(index):
            try:
                self.engine.register_account(f"ACC_NEW_{index:03d}", 1000.0 + index)
            except Exception as exc:
                errors.append(exc)

        def fan_out(index):
            try:
                report = self.engine.calculate_fanout_orders(
                    "X", "BUY", 997, batch_id=f"race-{index:03d}")
                if report.account_orders:
                    assert report.total_allocated_qty == 997, report.total_allocated_qty
            except Exception as exc:
                errors.append(exc)

        threads = (
            [threading.Thread(target=register, args=(i,)) for i in range(20)]
            + [threading.Thread(target=fan_out, args=(i,)) for i in range(20)]
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])


class TestAccountRegistry(unittest.TestCase):

    def setUp(self):
        self.engine = MultiAccountStrategyFanOut()
        self.engine.register_account("ACC_A", 600000.0)
        self.engine.register_account("ACC_B", 400000.0)

    def test_duplicate_registration_raises_rather_than_overwriting(self):
        with self.assertRaises(ValueError):
            self.engine.register_account("ACC_A", 999.0)
        self.assertEqual(self.engine.accounts["ACC_A"].nav_usd, 600000.0)

    def test_update_account_nav_changes_the_split(self):
        self.engine.update_account_nav("ACC_A", 200000.0)
        report = self.engine.calculate_fanout_orders("X", "BUY", 600)
        self.assertEqual(quantities(report), {"ACC_A": 200, "ACC_B": 400})

    def test_update_unknown_account_raises(self):
        with self.assertRaises(KeyError):
            self.engine.update_account_nav("ACC_MISSING", 100.0)

    def test_suspended_account_is_excluded_and_quantity_redistributed(self):
        # A suspended account leaves the denominator entirely, so the survivor takes
        # the whole master quantity rather than the batch under-filling.
        self.engine.set_account_active("ACC_B", False)
        report = self.engine.calculate_fanout_orders("X", "BUY", 500)
        self.assertEqual(quantities(report), {"ACC_A": 500})
        self.assertTrue(report.is_fully_allocated)
        self.assertEqual(report.excluded_accounts, [])

    def test_resuming_an_account_restores_it(self):
        self.engine.set_account_active("ACC_B", False)
        self.engine.set_account_active("ACC_B", True)
        report = self.engine.calculate_fanout_orders("X", "BUY", 1000)
        self.assertEqual(quantities(report), {"ACC_A": 600, "ACC_B": 400})

    def test_suspending_unknown_account_raises(self):
        with self.assertRaises(KeyError):
            self.engine.set_account_active("ACC_MISSING", False)

    def test_invalid_nav_is_rejected(self):
        for bad_nav in (0.0, -1.0, float("nan"), float("inf"), "500000"):
            with self.subTest(nav=bad_nav):
                with self.assertRaises(ValueError):
                    self.engine.register_account("ACC_NEW", bad_nav)

    def test_invalid_account_id_is_rejected(self):
        for bad_id in ("", "   ", "ACC A", "ACC\tA"):
            with self.subTest(account_id=bad_id):
                with self.assertRaises(ValueError):
                    self.engine.register_account(bad_id, 100.0)


class TestExplicitWeightAllocation(unittest.TestCase):

    def test_weights_override_nav(self):
        # Unwinding by held quantity: NAV would give ACC_B 90% of the exit, but ACC_A
        # actually holds 40 of the 100 shares being closed.
        engine = MultiAccountStrategyFanOut(allocation_method=ALLOCATION_METHOD_WEIGHT)
        engine.register_account("ACC_A", 100000.0, allocation_weight=40.0)
        engine.register_account("ACC_B", 900000.0, allocation_weight=60.0)

        report = engine.calculate_fanout_orders("X", "SELL", 100)

        self.assertEqual(quantities(report), {"ACC_A": 40, "ACC_B": 60})
        self.assertEqual(report.allocation_method, ALLOCATION_METHOD_WEIGHT)

    def test_missing_weight_raises_rather_than_falling_back_to_nav(self):
        # A silent NAV fallback would unwind the wrong quantities from every account.
        engine = MultiAccountStrategyFanOut(allocation_method=ALLOCATION_METHOD_WEIGHT)
        engine.register_account("ACC_A", 100000.0, allocation_weight=40.0)
        engine.register_account("ACC_B", 900000.0)
        with self.assertRaises(ValueError):
            engine.calculate_fanout_orders("X", "SELL", 100)

    def test_unknown_allocation_method_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            MultiAccountStrategyFanOut(allocation_method="PRO_RATA_VIBES")


class TestOrderValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MultiAccountStrategyFanOut()
        self.engine.register_account("ACC_A", 600000.0)
        self.engine.register_account("ACC_B", 400000.0)

    def test_non_positive_quantity_is_rejected(self):
        for bad_qty in (0, -100):
            with self.subTest(quantity=bad_qty):
                with self.assertRaises(ValueError):
                    self.engine.calculate_fanout_orders("X", "BUY", bad_qty)

    def test_non_integer_quantity_is_rejected(self):
        # 10.5 shares silently floored per account is how a fan-out under-executes.
        for bad_qty in (10.5, "100", True):
            with self.subTest(quantity=bad_qty):
                with self.assertRaises(ValueError):
                    self.engine.calculate_fanout_orders("X", "BUY", bad_qty)

    def test_unknown_action_is_rejected(self):
        # A typo'd side previously fanned out unchanged to every client account.
        for bad_action in ("BUYY", "buy", "SHORT", ""):
            with self.subTest(action=bad_action):
                with self.assertRaises(ValueError):
                    self.engine.calculate_fanout_orders("X", bad_action, 100)

    def test_limit_order_requires_a_limit_price(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_fanout_orders("X", "BUY", 100, order_type="LIMIT")
        for bad_price in (0.0, -5.0, float("nan")):
            with self.subTest(price=bad_price):
                with self.assertRaises(ValueError):
                    self.engine.calculate_fanout_orders(
                        "X", "BUY", 100, order_type="LIMIT", limit_price=bad_price)

    def test_market_order_must_not_carry_a_limit_price(self):
        # A limit price silently dropped on a MARKET order is an unbounded fill.
        with self.assertRaises(ValueError):
            self.engine.calculate_fanout_orders(
                "X", "BUY", 100, order_type="MARKET", limit_price=101.5)

    def test_limit_price_propagates_to_every_account_order(self):
        report = self.engine.calculate_fanout_orders(
            "X", "BUY", 100, order_type="LIMIT", limit_price=101.5)
        self.assertTrue(all(o.order_type == "LIMIT" for o in report.account_orders))
        self.assertTrue(all(o.limit_price == 101.5 for o in report.account_orders))

    def test_unknown_order_type_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_fanout_orders("X", "BUY", 100, order_type="STOP")

    def test_invalid_symbol_is_rejected(self):
        for bad_symbol in ("", "  ", "AA PL"):
            with self.subTest(symbol=bad_symbol):
                with self.assertRaises(ValueError):
                    self.engine.calculate_fanout_orders(bad_symbol, "BUY", 100)

    def test_invalid_min_order_qty_is_rejected(self):
        for bad_floor in (0, -1, 1.5):
            with self.subTest(floor=bad_floor):
                with self.assertRaises(ValueError):
                    MultiAccountStrategyFanOut(min_order_qty=bad_floor)


class TestRemainderAuditTrail(unittest.TestCase):

    def test_remainder_recipients_are_flagged(self):
        # Largest-remainder is fair within a batch but not across batches: with stable
        # NAVs the same accounts keep winning the leftover share. The flag exists so
        # that bias is auditable against 17 CFR 1.35(b)(5)(iv)(B).
        engine = MultiAccountStrategyFanOut()
        for i in range(3):
            engine.register_account(f"ACC_{i:02d}", 100000.0)

        report = engine.calculate_fanout_orders("X", "BUY", 10)

        winners = [o.account_id for o in report.account_orders if o.received_remainder_share]
        self.assertEqual(winners, ["ACC_00"])
        self.assertEqual(
            report.total_allocated_qty - sum(
                int(o.exact_quantity) for o in report.account_orders),
            len(winners))


if __name__ == "__main__":
    unittest.main()
