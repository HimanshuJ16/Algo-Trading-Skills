import itertools
import logging
import unittest

from capital_reallocation_engine import (
    CapitalReallocationEngine,
    ReallocationInstruction,
    StrategyMetrics,
)

# Keep expected-warning noise out of the test output.
logging.getLogger("capital_reallocation_engine").setLevel(logging.CRITICAL)


class TestKellyWeight(unittest.TestCase):
    """Kelly weight f* = W - (1 - W) / R, with R = avg_win / avg_loss.

    Expected values below are computed by hand, not by re-running the
    implementation's own expression.
    """

    def setUp(self):
        self.engine = CapitalReallocationEngine(total_fund_capital=1_000_000.0, kelly_fraction=0.5)

    def test_kelly_calculation(self):
        # W = 0.60, R = 200/100 = 2.0 -> 0.60 - (0.40 / 2.0) = 0.40
        strat = StrategyMetrics("S1", 0, 1_000_000.0, 0.60, 200, 100)
        self.assertAlmostEqual(self.engine._calculate_kelly_weight(strat), 0.40)

    def test_kelly_calculation_asymmetric_payoff(self):
        # W = 0.55, R = 150/100 = 1.5 -> 0.55 - (0.45 / 1.5) = 0.55 - 0.30 = 0.25
        strat = StrategyMetrics("S1", 0, 1_000_000.0, 0.55, 150, 100)
        self.assertAlmostEqual(self.engine._calculate_kelly_weight(strat), 0.25)

    def test_negative_edge_floors_at_zero(self):
        # W = 0.40, R = 1.0 -> 0.40 - 0.60 = -0.20, floored to 0 (never short a strategy)
        strat = StrategyMetrics("S1", 0, 1_000_000.0, 0.40, 100, 100)
        self.assertEqual(self.engine._calculate_kelly_weight(strat), 0.0)

    def test_zero_avg_loss_is_unestimable_not_infinite_edge(self):
        # A strategy with no observed losing trade has an undefined R. The engine
        # must refuse to size it rather than infer an unbounded edge.
        strat = StrategyMetrics("S1", 0, 1_000_000.0, 1.0, 200, 0)
        self.assertEqual(self.engine._calculate_kelly_weight(strat), 0.0)

    def test_zero_avg_win_has_no_edge(self):
        strat = StrategyMetrics("S1", 0, 1_000_000.0, 0.60, 0, 100)
        self.assertEqual(self.engine._calculate_kelly_weight(strat), 0.0)


class TestKellyFractionGovernsExposure(unittest.TestCase):
    """Regression: kelly_fraction must scale deployed capital.

    Previously the fraction was applied to every raw weight and then divided out
    again by normalisation, so Full-Kelly and Quarter-Kelly produced identical
    allocations and the fund was always 100% deployed. These tests fail against
    that behaviour.
    """

    def _portfolio(self):
        # S1: W=0.60, R=2.0 -> k = 0.40
        # S2: W=0.55, R=1.5 -> k = 0.25
        # Capacities are far above any target, so only Kelly binds.
        return {
            "S1": StrategyMetrics("S1", 0, 1e9, 0.60, 200, 100),
            "S2": StrategyMetrics("S2", 0, 1e9, 0.55, 150, 100),
        }

    def test_full_kelly_deploys_sum_of_kelly_fractions(self):
        result = CapitalReallocationEngine(1_000_000.0, kelly_fraction=1.0).reallocate(self._portfolio())
        # 0.40 * 1M = 400,000 and 0.25 * 1M = 250,000; 350,000 stays in cash.
        self.assertAlmostEqual(result["S1"].new_target_capital, 400_000.0)
        self.assertAlmostEqual(result["S2"].new_target_capital, 250_000.0)

    def test_half_kelly_deploys_exactly_half_of_full_kelly(self):
        result = CapitalReallocationEngine(1_000_000.0, kelly_fraction=0.5).reallocate(self._portfolio())
        self.assertAlmostEqual(result["S1"].new_target_capital, 200_000.0)
        self.assertAlmostEqual(result["S2"].new_target_capital, 125_000.0)

    def test_quarter_kelly_deploys_less_than_half_kelly(self):
        portfolio = self._portfolio()
        half = sum(i.new_target_capital for i in
                   CapitalReallocationEngine(1e6, 0.5).reallocate(portfolio).values())
        quarter = sum(i.new_target_capital for i in
                      CapitalReallocationEngine(1e6, 0.25).reallocate(portfolio).values())
        self.assertAlmostEqual(half, 325_000.0)
        self.assertAlmostEqual(quarter, 162_500.0)
        self.assertLess(quarter, half)

    def test_undeployed_capital_is_held_as_cash_not_forced_out(self):
        result = CapitalReallocationEngine(1_000_000.0, 0.25).reallocate(self._portfolio())
        deployed = sum(i.new_target_capital for i in result.values())
        # Quarter-Kelly on a 0.65 gross-Kelly portfolio deploys 16.25% of the fund.
        self.assertAlmostEqual(deployed, 162_500.0)
        self.assertAlmostEqual(1_000_000.0 - deployed, 837_500.0)


class TestCapacityConstraints(unittest.TestCase):

    def test_capacity_cap_does_not_overfund_the_survivor(self):
        # Both strategies: W=0.60, R=2.0 -> k=0.40; at Half-Kelly each targets
        # 0.20 * 1M = 200,000. S1's capacity of 100,000 binds.
        # S2 must stop at its OWN Kelly target of 200,000 -- the 100,000 freed by
        # S1 is cash, not extra risk for S2.
        strategies = {
            "S1": StrategyMetrics("S1", 0, 100_000, 0.60, 200, 100),
            "S2": StrategyMetrics("S2", 0, 10_000_000, 0.60, 200, 100),
        }
        result = CapitalReallocationEngine(1_000_000.0, kelly_fraction=0.5).reallocate(strategies)

        self.assertAlmostEqual(result["S1"].new_target_capital, 100_000.0)
        self.assertAlmostEqual(result["S2"].new_target_capital, 200_000.0)

    def test_no_strategy_exceeds_its_capacity(self):
        strategies = {
            "S1": StrategyMetrics("S1", 0, 50_000, 0.80, 300, 100),
            "S2": StrategyMetrics("S2", 0, 75_000, 0.75, 250, 100),
        }
        result = CapitalReallocationEngine(1_000_000.0, kelly_fraction=1.0).reallocate(strategies)
        for sid, metrics in strategies.items():
            self.assertLessEqual(result[sid].new_target_capital, metrics.max_capacity)

    def test_allocation_is_independent_of_dict_ordering(self):
        # Regression: the old first pass mutated the running weight total while
        # iterating, so the same three strategies allocated differently depending
        # on insertion order -- and one ordering silently stranded 96,551.72.
        #
        # Full Kelly: A = 0.70 - 0.30/2 = 0.55, B = 0.40, C = 0.55 - 0.45/2 = 0.325.
        # Gross 1.275 > 1, so budget = 1M. A caps at 100,000; B then caps at its
        # capacity 400,000; C caps at its own Kelly target 325,000.
        built = {
            "A": StrategyMetrics("A", 0, 100_000, 0.70, 200, 100),
            "B": StrategyMetrics("B", 0, 400_000, 0.60, 200, 100),
            "C": StrategyMetrics("C", 0, 10_000_000, 0.55, 200, 100),
        }
        expected = {"A": 100_000.0, "B": 400_000.0, "C": 325_000.0}

        for order in itertools.permutations(["A", "B", "C"]):
            ordered = {sid: built[sid] for sid in order}
            result = CapitalReallocationEngine(1_000_000.0, kelly_fraction=1.0).reallocate(ordered)
            actual = {sid: round(result[sid].new_target_capital, 6) for sid in expected}
            self.assertEqual(
                actual, expected,
                msg=f"allocation changed with input ordering {order}")


class TestFundLevelConstraints(unittest.TestCase):

    def test_never_levers_beyond_fund_capital(self):
        # Each: W=0.80, R=3.0 -> k = 0.80 - 0.20/3 = 0.7333...; gross = 1.4667 > 1.
        strategies = {
            "S1": StrategyMetrics("S1", 0, 1e9, 0.80, 300, 100),
            "S2": StrategyMetrics("S2", 0, 1e9, 0.80, 300, 100),
        }
        result = CapitalReallocationEngine(1_000_000.0, kelly_fraction=1.0).reallocate(strategies)
        deployed = sum(i.new_target_capital for i in result.values())

        self.assertAlmostEqual(deployed, 1_000_000.0)
        # Identical strategies split the fund evenly once scaled back.
        self.assertAlmostEqual(result["S1"].new_target_capital, 500_000.0)
        self.assertAlmostEqual(result["S2"].new_target_capital, 500_000.0)

    def test_total_allocation_never_exceeds_fund_across_many_shapes(self):
        for win_rate, avg_win, capacity, kelly_fraction in itertools.product(
            (0.30, 0.55, 0.75, 0.95), (50, 100, 400), (10_000, 5_000_000), (0.25, 0.5, 1.0)
        ):
            strategies = {
                f"S{i}": StrategyMetrics(f"S{i}", 0, capacity, win_rate, avg_win, 100)
                for i in range(5)
            }
            result = CapitalReallocationEngine(1_000_000.0, kelly_fraction).reallocate(strategies)
            deployed = sum(i.new_target_capital for i in result.values())
            self.assertLessEqual(deployed, 1_000_000.0 + 1e-6)
            for instruction in result.values():
                self.assertGreaterEqual(instruction.new_target_capital, 0.0)

    def test_zero_edge_moves_to_cash(self):
        # W = 0.40, R = 1.0 -> Kelly < 0 for every strategy.
        strategies = {"S1": StrategyMetrics("S1", 100_000, 500_000, 0.40, 100, 100)}
        result = CapitalReallocationEngine(1_000_000.0, 0.5).reallocate(strategies)

        self.assertEqual(result["S1"].new_target_capital, 0.0)
        self.assertEqual(result["S1"].delta_capital, -100_000.0)

    def test_delta_is_measured_against_current_capital(self):
        # S1: k=0.40 at Half-Kelly -> target 200,000, currently funded 50,000.
        strategies = {"S1": StrategyMetrics("S1", 50_000, 1e9, 0.60, 200, 100)}
        result = CapitalReallocationEngine(1_000_000.0, 0.5).reallocate(strategies)

        self.assertAlmostEqual(result["S1"].new_target_capital, 200_000.0)
        self.assertAlmostEqual(result["S1"].delta_capital, 150_000.0)

    def test_empty_portfolio_returns_no_instructions(self):
        self.assertEqual(CapitalReallocationEngine(1_000_000.0, 0.5).reallocate({}), {})

    def test_instruction_is_returned_for_every_strategy(self):
        strategies = {
            "S1": StrategyMetrics("S1", 0, 1e9, 0.60, 200, 100),
            "S2": StrategyMetrics("S2", 0, 1e9, 0.20, 100, 100),  # no edge
        }
        result = CapitalReallocationEngine(1_000_000.0, 0.5).reallocate(strategies)
        self.assertEqual(set(result), {"S1", "S2"})
        self.assertIsInstance(result["S2"], ReallocationInstruction)
        self.assertEqual(result["S2"].new_target_capital, 0.0)


class TestInputValidation(unittest.TestCase):
    """An allocation engine must reject bad inputs loudly, not size on them."""

    def test_win_rate_must_be_a_probability(self):
        # Previously win_rate=1.5 yielded a Kelly weight of 1.75.
        for bad in (1.5, -0.1):
            with self.assertRaises(ValueError):
                StrategyMetrics("S1", 0, 1e9, bad, 200, 100)

    def test_non_finite_metrics_are_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                StrategyMetrics("S1", 0, 1e9, bad, 200, 100)
            with self.assertRaises(ValueError):
                StrategyMetrics("S1", 0, bad, 0.6, 200, 100)

    def test_negative_capital_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            StrategyMetrics("S1", 0, -1.0, 0.6, 200, 100)
        with self.assertRaises(ValueError):
            StrategyMetrics("S1", -1.0, 1e9, 0.6, 200, 100)

    def test_empty_strategy_id_is_rejected(self):
        with self.assertRaises(ValueError):
            StrategyMetrics("", 0, 1e9, 0.6, 200, 100)

    def test_kelly_fraction_must_be_within_full_kelly(self):
        # Betting above full Kelly reduces growth; beyond 2x it turns negative.
        for bad in (1.5, 2.0, 0.0, -0.5):
            with self.assertRaises(ValueError):
                CapitalReallocationEngine(1_000_000.0, bad)

    def test_negative_fund_capital_is_rejected(self):
        with self.assertRaises(ValueError):
            CapitalReallocationEngine(-500.0, 0.5)

    def test_mismatched_key_and_strategy_id_is_rejected(self):
        # Guards against a silent mapping bug funding the wrong strategy.
        strategies = {"WRONG": StrategyMetrics("S1", 0, 1e9, 0.60, 200, 100)}
        with self.assertRaises(ValueError):
            CapitalReallocationEngine(1_000_000.0, 0.5).reallocate(strategies)


if __name__ == '__main__':
    unittest.main()
