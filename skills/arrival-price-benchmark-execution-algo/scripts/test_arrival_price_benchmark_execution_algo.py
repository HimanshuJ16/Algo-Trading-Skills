import unittest

from arrival_price_benchmark_execution_algo import (
    ArrivalPriceTrajectoryGenerator,
    ExecutionTrajectory,
    UrgencyLevel,
)


class TestArrivalPriceTrajectoryGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = ArrivalPriceTrajectoryGenerator()
        self.total_size = 10000
        self.num_bins = 10

    # --- invariants shared by every urgency level ---------------------------

    def _assert_invariants(self, trajectory, total_size, num_bins, urgency):
        self.assertIsInstance(trajectory, ExecutionTrajectory)
        self.assertEqual(trajectory.total_size, total_size)
        self.assertEqual(trajectory.num_bins, num_bins)
        self.assertEqual(trajectory.urgency, urgency)
        self.assertEqual(len(trajectory.child_order_sizes), num_bins)
        # Sum invariant: child orders must exactly reconstruct the parent.
        self.assertEqual(sum(trajectory.child_order_sizes), total_size)
        # No negative child sizes are ever permitted.
        for size in trajectory.child_order_sizes:
            self.assertGreaterEqual(size, 0)

    # --- LOW urgency --------------------------------------------------------

    def test_low_urgency_is_uniform_twap(self):
        trajectory = self.generator.generate_schedule(
            self.total_size, self.num_bins, UrgencyLevel.LOW
        )
        self._assert_invariants(trajectory, self.total_size, self.num_bins, UrgencyLevel.LOW)
        # 10000 / 10 divides evenly -> every bin must be exactly 1000.
        for size in trajectory.child_order_sizes:
            self.assertEqual(size, 1000)

    def test_low_urgency_residual_goes_to_first_bin(self):
        # 10003 / 10 does not divide evenly; base=1000, residual=3 to bin 0.
        trajectory = self.generator.generate_schedule(10003, 10, UrgencyLevel.LOW)
        self.assertEqual(sum(trajectory.child_order_sizes), 10003)
        self.assertEqual(trajectory.child_order_sizes[0], 1003)
        for size in trajectory.child_order_sizes[1:]:
            self.assertEqual(size, 1000)

    # --- HIGH urgency -------------------------------------------------------

    def test_high_urgency_front_loading(self):
        trajectory = self.generator.generate_schedule(
            self.total_size, self.num_bins, UrgencyLevel.HIGH
        )
        self._assert_invariants(trajectory, self.total_size, self.num_bins, UrgencyLevel.HIGH)
        # First half must dominate the second half (heavily front-loaded).
        first_half_sum = sum(trajectory.child_order_sizes[:5])
        second_half_sum = sum(trajectory.child_order_sizes[5:])
        self.assertGreater(first_half_sum, second_half_sum * 2)
        # First bin is the largest.
        self.assertEqual(trajectory.child_order_sizes[0], max(trajectory.child_order_sizes))

    def test_high_urgency_monotonically_non_increasing(self):
        trajectory = self.generator.generate_schedule(
            self.total_size, self.num_bins, UrgencyLevel.HIGH
        )
        sizes = trajectory.child_order_sizes
        for i in range(len(sizes) - 1):
            self.assertGreaterEqual(
                sizes[i],
                sizes[i + 1],
                msg=f"Bin {i} ({sizes[i]}) must be >= bin {i + 1} ({sizes[i + 1]}) for HIGH urgency.",
            )

    # --- MEDIUM urgency -----------------------------------------------------

    def test_medium_urgency_curve(self):
        trajectory = self.generator.generate_schedule(
            self.total_size, self.num_bins, UrgencyLevel.MEDIUM
        )
        self._assert_invariants(trajectory, self.total_size, self.num_bins, UrgencyLevel.MEDIUM)
        # Medium must be front-loaded but less extreme than High.
        high_traj = self.generator.generate_schedule(
            self.total_size, self.num_bins, UrgencyLevel.HIGH
        )
        self.assertLess(trajectory.child_order_sizes[0], high_traj.child_order_sizes[0])
        # ... but more front-loaded than Low.
        low_traj = self.generator.generate_schedule(
            self.total_size, self.num_bins, UrgencyLevel.LOW
        )
        self.assertGreater(trajectory.child_order_sizes[0], low_traj.child_order_sizes[0])
        # Monotonically non-increasing as well.
        sizes = trajectory.child_order_sizes
        for i in range(len(sizes) - 1):
            self.assertGreaterEqual(sizes[i], sizes[i + 1])

    def test_urgency_ordering_front_loading(self):
        # Across urgency levels, front-loading must strictly increase:
        # LOW[0] < MEDIUM[0] < HIGH[0].
        low = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.LOW)
        med = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.MEDIUM)
        high = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.HIGH)
        self.assertLess(low.child_order_sizes[0], med.child_order_sizes[0])
        self.assertLess(med.child_order_sizes[0], high.child_order_sizes[0])

    # --- boundaries ---------------------------------------------------------

    def test_single_bin_executes_entire_parent(self):
        for urgency in UrgencyLevel:
            with self.subTest(urgency=urgency):
                trajectory = self.generator.generate_schedule(500, 1, urgency)
                self.assertEqual(trajectory.child_order_sizes, [500])

    def test_total_size_one_across_many_bins(self):
        # One share across 10 bins: must remain non-negative and sum to 1.
        for urgency in UrgencyLevel:
            with self.subTest(urgency=urgency):
                trajectory = self.generator.generate_schedule(1, 10, urgency)
                self.assertEqual(sum(trajectory.child_order_sizes), 1)
                for size in trajectory.child_order_sizes:
                    self.assertIn(size, (0, 1))

    def test_total_size_smaller_than_num_bins(self):
        # 3 shares across 10 bins: only 3 bins can be non-zero; no negatives.
        for urgency in UrgencyLevel:
            with self.subTest(urgency=urgency):
                trajectory = self.generator.generate_schedule(3, 10, urgency)
                self.assertEqual(sum(trajectory.child_order_sizes), 3)
                for size in trajectory.child_order_sizes:
                    self.assertGreaterEqual(size, 0)

    def test_large_horizon_does_not_overflow(self):
        # A long horizon must not trigger math overflow / NaN in the sinh path.
        trajectory = self.generator.generate_schedule(1_000_000, 1000, UrgencyLevel.HIGH)
        self.assertEqual(sum(trajectory.child_order_sizes), 1_000_000)
        for size in trajectory.child_order_sizes:
            self.assertGreaterEqual(size, 0)

    # --- determinism --------------------------------------------------------

    def test_schedule_is_deterministic(self):
        a = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.HIGH)
        b = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.HIGH)
        self.assertEqual(a.child_order_sizes, b.child_order_sizes)

    def test_immutable_trajectory(self):
        import dataclasses

        trajectory = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.MEDIUM)
        # frozen dataclass: assignment must raise. FrozenInstanceError is a
        # subclass of AttributeError on every supported Python version.
        with self.assertRaises(AttributeError):
            trajectory.total_size = 999

    # --- input validation ---------------------------------------------------

    def test_negative_total_size_raises(self):
        with self.assertRaises(ValueError):
            self.generator.generate_schedule(-500, 10, UrgencyLevel.MEDIUM)

    def test_zero_total_size_raises(self):
        with self.assertRaises(ValueError):
            self.generator.generate_schedule(0, 10, UrgencyLevel.MEDIUM)

    def test_zero_num_bins_raises(self):
        with self.assertRaises(ValueError):
            self.generator.generate_schedule(1000, 0, UrgencyLevel.MEDIUM)

    def test_negative_num_bins_raises(self):
        with self.assertRaises(ValueError):
            self.generator.generate_schedule(1000, -3, UrgencyLevel.MEDIUM)

    def test_non_int_total_size_raises(self):
        with self.assertRaises(TypeError):
            self.generator.generate_schedule(1000.0, 10, UrgencyLevel.MEDIUM)

    def test_bool_total_size_raises(self):
        # bool is a subclass of int and must be rejected explicitly.
        with self.assertRaises(TypeError):
            self.generator.generate_schedule(True, 10, UrgencyLevel.MEDIUM)

    def test_non_urgency_value_raises(self):
        with self.assertRaises(TypeError):
            self.generator.generate_schedule(1000, 10, "HIGH")


if __name__ == "__main__":
    unittest.main()
