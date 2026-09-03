import math
import unittest

from arrival_price_benchmark_execution_algo import (
    ArrivalPriceTrajectoryGenerator,
    ExecutionTrajectory,
    ImpactParameters,
    ShortfallForecast,
    UrgencyLevel,
    forecast_shortfall,
)


def reference_trade_list(total_size, num_bins, kappa):
    """
    Almgren-Chriss (2000) Eq. (17) differenced into a trade list, computed on an
    independent numerical path: ``math.exp`` applied to the analytic identity

        sinh(a)/sinh(b) = exp(a-b) * (1 - exp(-2a)) / (1 - exp(-2b))

    rather than the module's ``expm1``-based helper. Returns exact floats (no
    integer apportionment), so tests comparing against it must allow for
    largest-remainder rounding of at most one share per bin.
    """
    def ratio(a, b):
        if a <= 0.0:
            return 0.0
        return math.exp(a - b) * (1.0 - math.exp(-2.0 * a)) / (1.0 - math.exp(-2.0 * b))

    t_total = float(num_bins)
    remaining = [
        total_size * ratio(kappa * (t_total - t), kappa * t_total)
        for t in range(num_bins + 1)
    ]
    return [remaining[t] - remaining[t + 1] for t in range(num_bins)]


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


class TestLongHorizonNumerics(unittest.TestCase):
    """
    Regression tests for the sinh overflow defect.

    The previous implementation multiplied ``total_size`` by ``math.sinh(...)``
    before dividing, so the numerator overflowed to ``inf`` while the guard
    checked only ``kappa*T > 700``. Two distinct failures resulted:

      * ``kappa*T`` just under the guard (HIGH urgency, ~698-700 bins):
        ``inf - inf`` produced ``NaN`` and ``generate_schedule`` raised
        ``ValueError: cannot convert float NaN to integer``.
      * ``kappa*T`` past the guard (HIGH urgency, >700 bins): the schedule
        short-circuited to 100% of the parent in bin 0, versus the correct
        63.2%. For an execution algo that is the maximum-impact outcome.

    Both are asserted against Eq. (17) evaluated independently, not against the
    module's own helper.
    """

    def setUp(self):
        self.generator = ArrivalPriceTrajectoryGenerator()

    def test_horizon_at_former_nan_boundary_does_not_raise(self):
        # kappa*T in (697, 700] used to raise ValueError from a NaN size.
        for num_bins in (698, 699, 700):
            with self.subTest(num_bins=num_bins):
                trajectory = self.generator.generate_schedule(
                    1_000_000, num_bins, UrgencyLevel.HIGH
                )
                self.assertEqual(sum(trajectory.child_order_sizes), 1_000_000)
                for size in trajectory.child_order_sizes:
                    self.assertGreaterEqual(size, 0)

    def test_long_horizon_is_not_an_immediate_dump(self):
        # kappa*T > 700 used to place the whole parent in bin 0. The correct
        # first-bin share is 1 - sinh(k(T-1))/sinh(kT) -> 1 - e^-1 = 63.2% for
        # kappa = 1, independent of T (half-life is independent of horizon).
        expected_fraction = 1.0 - math.exp(-1.0)
        for num_bins in (701, 800, 1000, 5000):
            with self.subTest(num_bins=num_bins):
                sizes = self.generator.generate_schedule(
                    1_000_000, num_bins, UrgencyLevel.HIGH
                ).child_order_sizes
                self.assertEqual(sum(sizes), 1_000_000)
                self.assertAlmostEqual(sizes[0] / 1_000_000, expected_fraction, places=5)
                # Explicitly reject the old degenerate behaviour.
                self.assertLess(sizes[0], 1_000_000)
                self.assertGreater(sizes[1], 0)

    def test_medium_urgency_long_horizon(self):
        # kappa = 0.5 crosses the old guard at 1400 bins; correct first bin is
        # 1 - e^-0.5 = 39.3% of the parent regardless of horizon.
        expected_fraction = 1.0 - math.exp(-0.5)
        for num_bins in (1396, 1400, 1402, 2000):
            with self.subTest(num_bins=num_bins):
                sizes = self.generator.generate_schedule(
                    1_000_000, num_bins, UrgencyLevel.MEDIUM
                ).child_order_sizes
                self.assertEqual(sum(sizes), 1_000_000)
                self.assertAlmostEqual(sizes[0] / 1_000_000, expected_fraction, places=5)

    def test_matches_independent_closed_form(self):
        # Whole schedule, not just bin 0, against Eq. (17) on an independent
        # numerical path. Largest-remainder rounding allows <= 1 share drift.
        for num_bins, urgency, kappa in (
            (10, UrgencyLevel.HIGH, 1.0),
            (10, UrgencyLevel.MEDIUM, 0.5),
            (250, UrgencyLevel.HIGH, 1.0),
            (900, UrgencyLevel.HIGH, 1.0),
        ):
            with self.subTest(num_bins=num_bins, urgency=urgency):
                got = self.generator.generate_schedule(
                    1_000_000, num_bins, urgency
                ).child_order_sizes
                expected = reference_trade_list(1_000_000, num_bins, kappa)
                for i, (g, e) in enumerate(zip(got, expected)):
                    self.assertLessEqual(
                        abs(g - e), 1.0, msg=f"bin {i}: got {g}, closed form {e}"
                    )

    def test_extreme_horizon_stays_finite_and_monotone(self):
        sizes = self.generator.generate_schedule(
            10_000_000, 20_000, UrgencyLevel.HIGH
        ).child_order_sizes
        self.assertEqual(sum(sizes), 10_000_000)
        for i in range(len(sizes) - 1):
            self.assertGreaterEqual(sizes[i], sizes[i + 1])


class TestShortfallForecast(unittest.TestCase):
    """
    Cost-model tests.

    Expected values are derived independently of ``forecast_shortfall``: from
    the closed-form limiting cases Almgren & Chriss (2000) give for specific
    trajectories -- Eq. (10)/(11) for the uniform (TWAP) schedule and Eq. (13)
    for the single-bin dump -- and from Eq. (20) for the optimal trajectory.
    None of these share an evaluation path with the implementation, which sums
    Eqs. (5)/(8) bin by bin.
    """

    def setUp(self):
        self.sigma = 0.02
        self.eta = 1e-6
        self.gamma = 5e-8
        self.epsilon = 0.01
        self.tau = 1.0
        self.params = ImpactParameters(
            sigma=self.sigma,
            eta=self.eta,
            gamma=self.gamma,
            epsilon=self.epsilon,
            tau=self.tau,
        )
        self.total = 1_000_000

    def test_eta_tilde_matches_paper(self):
        # eta_tilde = eta - gamma*tau/2, stated following Eq. (8).
        self.assertAlmostEqual(
            self.params.eta_tilde, self.eta - 0.5 * self.gamma * self.tau, places=18
        )

    def test_uniform_schedule_matches_equation_10_and_11(self):
        # Eq. (10): E = gamma*X^2/2 + eps*X + (eta - gamma*tau/2) * X^2/T
        # Eq. (11): V = (1/3) sigma^2 X^2 T (1 - 1/N)(1 - 1/(2N))
        num_bins = 10
        horizon = num_bins * self.tau
        schedule = [self.total / num_bins] * num_bins

        expected_e = (
            0.5 * self.gamma * self.total ** 2
            + self.epsilon * self.total
            + (self.eta - 0.5 * self.gamma * self.tau) * self.total ** 2 / horizon
        )
        expected_v = (
            (1.0 / 3.0)
            * self.sigma ** 2
            * self.total ** 2
            * horizon
            * (1.0 - 1.0 / num_bins)
            * (1.0 - 1.0 / (2.0 * num_bins))
        )

        got = forecast_shortfall(schedule, self.params)
        self.assertAlmostEqual(got.expected_cost / expected_e, 1.0, places=12)
        self.assertAlmostEqual(got.variance / expected_v, 1.0, places=12)

    def test_single_bin_dump_matches_equation_13(self):
        # Eq. (13): E = eps*X + eta*X^2/tau, V = 0. Note this equals
        # gamma*X^2/2 + eps*X + eta_tilde*X^2/tau, so it is a genuine
        # cross-check of the gamma bookkeeping rather than a restatement.
        schedule = [self.total] + [0] * 9
        expected_e = self.epsilon * self.total + self.eta * self.total ** 2 / self.tau

        got = forecast_shortfall(schedule, self.params)
        self.assertAlmostEqual(got.expected_cost / expected_e, 1.0, places=12)
        self.assertEqual(got.variance, 0.0)
        self.assertEqual(got.stdev, 0.0)

    def test_optimal_trajectory_matches_equation_20(self):
        # Eq. (20), the closed-form E/V of the *optimal* trajectory:
        #   E = gamma X^2/2 + eps X
        #       + eta~ X^2 tanh(k tau/2)(tau sinh(2kT) + 2T sinh(k tau))
        #         / (2 tau^2 sinh^2(kT))
        #   V = sigma^2 X^2 (tau sinh(kT) cosh(k(T-tau)) - T sinh(k tau))
        #         / (2 sinh^2(kT) sinh(k tau))
        # Evaluated directly with math.sinh, which is well conditioned for the
        # moderate kappa*T used here.
        for num_bins, kappa in ((10, 1.0), (10, 0.5), (25, 0.5)):
            with self.subTest(num_bins=num_bins, kappa=kappa):
                horizon = num_bins * self.tau
                schedule = reference_trade_list(self.total, num_bins, kappa)

                kt = kappa * horizon
                ktau = kappa * self.tau
                expected_e = (
                    0.5 * self.gamma * self.total ** 2
                    + self.epsilon * self.total
                    + self.params.eta_tilde
                    * self.total ** 2
                    * math.tanh(0.5 * ktau)
                    * (self.tau * math.sinh(2 * kt) + 2 * horizon * math.sinh(ktau))
                    / (2 * self.tau ** 2 * math.sinh(kt) ** 2)
                )
                expected_v = (
                    0.5
                    * self.sigma ** 2
                    * self.total ** 2
                    * (
                        self.tau * math.sinh(kt) * math.cosh(kappa * (horizon - self.tau))
                        - horizon * math.sinh(ktau)
                    )
                    / (math.sinh(kt) ** 2 * math.sinh(ktau))
                )

                got = forecast_shortfall(schedule, self.params)
                self.assertAlmostEqual(got.expected_cost / expected_e, 1.0, places=10)
                self.assertAlmostEqual(got.variance / expected_v, 1.0, places=10)

    def test_expected_cost_is_never_negative_across_kappa(self):
        # The formula previously published in references/standards.md returned a
        # negative expected cost for small kappa. An impact cost cannot be
        # negative for a one-sided schedule with non-negative parameters.
        for num_bins in (5, 10, 100):
            for kappa in (2.0, 1.0, 0.5, 0.1, 0.01, 1e-4):
                with self.subTest(num_bins=num_bins, kappa=kappa):
                    schedule = reference_trade_list(self.total, num_bins, kappa)
                    got = forecast_shortfall(schedule, self.params)
                    self.assertGreater(got.expected_cost, 0.0)
                    self.assertGreater(got.variance, 0.0)

    def test_variance_does_not_collapse_as_kappa_falls(self):
        # Also from the standards.md defect: the published V(X) omitted a
        # 1/sinh(k tau) factor and collapsed toward zero as kappa -> 0, when it
        # should rise to the TWAP variance of Eq. (11).
        num_bins = 10
        horizon = num_bins * self.tau
        twap_variance = (
            (1.0 / 3.0)
            * self.sigma ** 2
            * self.total ** 2
            * horizon
            * (1.0 - 1.0 / num_bins)
            * (1.0 - 1.0 / (2.0 * num_bins))
        )
        previous = 0.0
        for kappa in (2.0, 1.0, 0.5, 0.1, 0.01):
            variance = forecast_shortfall(
                reference_trade_list(self.total, num_bins, kappa), self.params
            ).variance
            self.assertGreater(variance, previous)
            previous = variance
        self.assertAlmostEqual(
            forecast_shortfall(
                reference_trade_list(self.total, num_bins, 1e-6), self.params
            ).variance
            / twap_variance,
            1.0,
            places=6,
        )

    def test_front_loading_trades_impact_for_variance(self):
        # The core Almgren-Chriss tradeoff: a more urgent schedule costs more in
        # impact but carries less timing risk.
        generator = ArrivalPriceTrajectoryGenerator()
        forecasts = {
            urgency: forecast_shortfall(
                generator.generate_schedule(self.total, 20, urgency).child_order_sizes,
                self.params,
            )
            for urgency in UrgencyLevel
        }
        self.assertGreater(
            forecasts[UrgencyLevel.HIGH].expected_cost,
            forecasts[UrgencyLevel.MEDIUM].expected_cost,
        )
        self.assertGreater(
            forecasts[UrgencyLevel.MEDIUM].expected_cost,
            forecasts[UrgencyLevel.LOW].expected_cost,
        )
        self.assertLess(
            forecasts[UrgencyLevel.HIGH].variance,
            forecasts[UrgencyLevel.MEDIUM].variance,
        )
        self.assertLess(
            forecasts[UrgencyLevel.MEDIUM].variance,
            forecasts[UrgencyLevel.LOW].variance,
        )

    def test_objective_includes_risk_aversion_term(self):
        schedule = [self.total / 10] * 10
        neutral = forecast_shortfall(schedule, self.params, risk_aversion=0.0)
        averse = forecast_shortfall(schedule, self.params, risk_aversion=1e-6)
        self.assertEqual(neutral.objective, neutral.expected_cost)
        self.assertAlmostEqual(
            averse.objective,
            averse.expected_cost + 1e-6 * averse.variance,
            places=6,
        )
        self.assertGreater(averse.objective, neutral.objective)

    def test_stdev_is_sqrt_of_variance(self):
        got = forecast_shortfall([500, 300, 200], self.params)
        self.assertAlmostEqual(got.stdev, math.sqrt(got.variance), places=12)

    def test_forecast_is_immutable(self):
        got = forecast_shortfall([500, 300, 200], self.params)
        self.assertIsInstance(got, ShortfallForecast)
        with self.assertRaises(AttributeError):
            got.expected_cost = 0.0

    # --- cost-model input validation ----------------------------------------

    def test_non_convex_parameters_rejected(self):
        # eta_tilde = eta - gamma*tau/2 <= 0 leaves the objective non-convex, so
        # the "optimal" trajectory is not a minimiser (A&C 2000, after Eq. 8).
        with self.assertRaises(ValueError):
            ImpactParameters(sigma=0.02, eta=1e-6, gamma=4e-6, tau=1.0)

    def test_non_positive_eta_rejected(self):
        with self.assertRaises(ValueError):
            ImpactParameters(sigma=0.02, eta=0.0)

    def test_negative_sigma_rejected(self):
        with self.assertRaises(ValueError):
            ImpactParameters(sigma=-0.01, eta=1e-6)

    def test_non_positive_tau_rejected(self):
        with self.assertRaises(ValueError):
            ImpactParameters(sigma=0.02, eta=1e-6, tau=0.0)

    def test_non_finite_parameter_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ImpactParameters(sigma=bad, eta=1e-6)

    def test_empty_schedule_rejected(self):
        with self.assertRaises(ValueError):
            forecast_shortfall([], self.params)

    def test_zero_quantity_schedule_rejected(self):
        with self.assertRaises(ValueError):
            forecast_shortfall([0, 0, 0], self.params)

    def test_negative_child_size_rejected(self):
        with self.assertRaises(ValueError):
            forecast_shortfall([500, -100, 200], self.params)

    def test_non_finite_child_size_rejected(self):
        with self.assertRaises(ValueError):
            forecast_shortfall([500, float("nan"), 200], self.params)

    def test_negative_risk_aversion_rejected(self):
        with self.assertRaises(ValueError):
            forecast_shortfall([500, 300], self.params, risk_aversion=-1.0)

    def test_wrong_params_type_rejected(self):
        with self.assertRaises(TypeError):
            forecast_shortfall([500, 300], {"sigma": 0.02, "eta": 1e-6})

    def test_string_schedule_rejected(self):
        # A bare string is a sequence; silently iterating it would be nonsense.
        with self.assertRaises(TypeError):
            forecast_shortfall("1000", self.params)


if __name__ == "__main__":
    unittest.main()
