"""Unit tests for walk-forward-hyperparameter-search-budget."""
import itertools
import math
import unittest
from statistics import NormalDist

from search_budgeter import (
    HyperparameterSearchBudgeter,
    SearchBudgetError,
    SearchBudgetReport,
    expected_max_sharpe,
    minimum_backtest_length_years,
)


class TestHyperparameterSearchBudgeter(unittest.TestCase):
    def setUp(self):
        self.budgeter = HyperparameterSearchBudgeter(max_trials_per_year=100)

    def test_grid_within_budget_passes(self):
        grid = {
            "fast_period": [5, 10, 15],
            "slow_period": [20, 30, 40],
        }  # 3x3 = 9 combinations
        combos, report = self.budgeter.audit_and_prune(grid, in_sample_days=252)

        self.assertFalse(report.is_budget_exceeded)
        self.assertEqual(len(combos), 9)
        self.assertEqual(report.overfitting_risk_level, "LOW")

    def test_overlarge_grid_pruned(self):
        grid = {
            "param1": list(range(20)),
            "param2": list(range(20)),
            "param3": list(range(10)),
        }  # 20x20x10 = 4,000 combinations!
        combos, report = self.budgeter.audit_and_prune(grid, in_sample_days=252)

        self.assertTrue(report.is_budget_exceeded)
        self.assertTrue(report.pruning_applied)
        self.assertLessEqual(len(combos), report.allowed_budget_max)
        self.assertEqual(report.overfitting_risk_level, "HIGH")


class TestBudgetArithmetic(unittest.TestCase):
    """Hand-computed budget values at the boundaries of the clamp."""

    def setUp(self):
        self.budgeter = HyperparameterSearchBudgeter(max_trials_per_year=100)

    def test_full_year_yields_exactly_one_hundred(self):
        # 252/252 * 100 = 100.0 exactly.
        self.assertEqual(self.budgeter.compute_max_budget(252), 100)

    def test_two_hundred_fifty_days_truncates_to_ninety_nine(self):
        # 250/252 * 100 = 99.206..., floored to 99. Documented explicitly because
        # SKILL.md previously claimed a 250-day window produced a budget of 100.
        self.assertEqual(self.budgeter.compute_max_budget(250), 99)

    def test_budget_is_capped_at_five_hundred(self):
        # 1260/252 * 100 = 500 exactly; 2520 days would be 1000 but clamps down.
        self.assertEqual(self.budgeter.compute_max_budget(1260), 500)
        self.assertEqual(self.budgeter.compute_max_budget(2520), 500)

    def test_budget_floor_is_ten(self):
        # 25/252 * 100 = 9.92, floored to 9, raised to the floor of 10.
        self.assertEqual(self.budgeter.compute_max_budget(25), 10)
        self.assertEqual(self.budgeter.compute_max_budget(1), 10)

    def test_max_trials_per_year_scales_the_budget(self):
        lean = HyperparameterSearchBudgeter(max_trials_per_year=50)
        self.assertEqual(lean.compute_max_budget(252), 50)

    def test_non_positive_window_is_rejected(self):
        # Previously returned the floor of 10 silently for any non-positive window.
        for days in (0, -1, -500):
            with self.assertRaises(SearchBudgetError):
                self.budgeter.compute_max_budget(days)


class TestPruningCoverage(unittest.TestCase):
    """Regression tests for the constant-stride aliasing defect.

    The previous implementation sampled `combinations[::ceil(raw/budget)]`. Under
    `itertools.product` ordering the last parameter varies fastest, so a stride
    sharing a factor with its cardinality freezes it. For this exact 20x20x10 grid
    the stride was 40, and the pruned search explored 1 of 10 values of `param3`
    and 5 of 20 values of `param2` while still reporting a bounded, budget-compliant
    result. These assertions fail against that behaviour and pass against the
    index-sampling replacement.
    """

    def setUp(self):
        self.budgeter = HyperparameterSearchBudgeter(max_trials_per_year=100)
        self.grid = {
            "param1": list(range(20)),
            "param2": list(range(20)),
            "param3": list(range(10)),
        }

    def test_every_parameter_is_actually_varied(self):
        combos, _ = self.budgeter.audit_and_prune(self.grid, in_sample_days=252)

        for name in self.grid:
            distinct = {c[name] for c in combos}
            self.assertGreater(
                len(distinct),
                1,
                f"pruned search never varied '{name}' - stride aliasing regression",
            )

    def test_pruned_sample_covers_each_axis_broadly(self):
        combos, _ = self.budgeter.audit_and_prune(self.grid, in_sample_days=252)

        # 100 draws from 4,000 points. Old behaviour: param3 = 1, param2 = 5.
        self.assertGreaterEqual(len({c["param3"] for c in combos}), 8)
        self.assertGreaterEqual(len({c["param2"] for c in combos}), 15)
        self.assertGreaterEqual(len({c["param1"] for c in combos}), 15)

    def test_pruned_combinations_are_distinct(self):
        combos, report = self.budgeter.audit_and_prune(self.grid, in_sample_days=252)

        keyed = {tuple(sorted(c.items())) for c in combos}
        self.assertEqual(len(keyed), len(combos))
        self.assertEqual(len(combos), report.allowed_budget_max)

    def test_pruned_combinations_are_members_of_the_grid(self):
        combos, _ = self.budgeter.audit_and_prune(self.grid, in_sample_days=252)

        for combo in combos:
            self.assertEqual(set(combo), set(self.grid))
            for name, value in combo.items():
                self.assertIn(value, self.grid[name])

    def test_sampling_is_deterministic_for_a_fixed_seed(self):
        first, _ = self.budgeter.audit_and_prune(self.grid, in_sample_days=252)
        second, _ = HyperparameterSearchBudgeter(
            max_trials_per_year=100
        ).audit_and_prune(self.grid, in_sample_days=252)

        self.assertEqual(first, second)

    def test_different_seed_draws_a_different_subset(self):
        first, _ = self.budgeter.audit_and_prune(self.grid, in_sample_days=252)
        second, _ = HyperparameterSearchBudgeter(
            max_trials_per_year=100, seed=987
        ).audit_and_prune(self.grid, in_sample_days=252)

        self.assertNotEqual(first, second)

    def test_index_decoding_matches_itertools_product_ordering(self):
        # Independently derived: the full product built by itertools, compared
        # position by position against the mixed-radix decoder.
        grid = {"a": [1, 2, 3], "b": ["x", "y"], "c": [0.5, 1.5, 2.5, 3.5]}
        keys, values = list(grid), list(grid.values())
        strides = HyperparameterSearchBudgeter._mixed_radix_strides(values)
        expected = [dict(zip(keys, c)) for c in itertools.product(*values)]

        for i, want in enumerate(expected):
            got = HyperparameterSearchBudgeter._decode_index(i, keys, values, strides)
            self.assertEqual(got, want, f"decode mismatch at flat index {i}")

    def test_enormous_grid_is_sampled_without_materialising_it(self):
        # 20^10 = 10,240,000,000,000 combinations. The previous implementation
        # enumerated the full Cartesian product before slicing it, so this grid
        # could not be pruned at all.
        grid = {f"p{i}": list(range(20)) for i in range(10)}
        combos, report = self.budgeter.audit_and_prune(grid, in_sample_days=1260)

        self.assertEqual(report.raw_combinations, 20 ** 10)
        self.assertEqual(len(combos), 500)
        self.assertEqual(len({tuple(sorted(c.items())) for c in combos}), 500)


class TestBudgetBoundaries(unittest.TestCase):
    def setUp(self):
        self.budgeter = HyperparameterSearchBudgeter(max_trials_per_year=100)

    def test_grid_exactly_at_budget_is_not_pruned(self):
        # 10x10 = 100 == budget for a 252-day window.
        grid = {"a": list(range(10)), "b": list(range(10))}
        combos, report = self.budgeter.audit_and_prune(grid, in_sample_days=252)

        self.assertEqual(report.raw_combinations, 100)
        self.assertEqual(report.allowed_budget_max, 100)
        self.assertFalse(report.is_budget_exceeded)
        self.assertFalse(report.pruning_applied)
        self.assertEqual(len(combos), 100)
        self.assertEqual(report.overfitting_risk_level, "LOW")

    def test_one_combination_over_budget_triggers_pruning(self):
        # 101 = budget + 1.
        grid = {"a": list(range(101))}
        combos, report = self.budgeter.audit_and_prune(grid, in_sample_days=252)

        self.assertTrue(report.is_budget_exceeded)
        self.assertTrue(report.pruning_applied)
        self.assertEqual(len(combos), 100)
        self.assertEqual(report.overfitting_risk_level, "MODERATE")

    def test_risk_grade_boundary_between_moderate_and_high(self):
        # HIGH requires raw > 5 * budget, so exactly 500 is still MODERATE.
        at_multiple = {"a": list(range(500))}
        _, moderate = self.budgeter.audit_and_prune(at_multiple, in_sample_days=252)
        self.assertEqual(moderate.raw_combinations, 500)
        self.assertEqual(moderate.overfitting_risk_level, "MODERATE")

        over_multiple = {"a": list(range(501))}
        _, high = self.budgeter.audit_and_prune(over_multiple, in_sample_days=252)
        self.assertEqual(high.overfitting_risk_level, "HIGH")

    def test_single_combination_grid_is_within_budget(self):
        combos, report = self.budgeter.audit_and_prune({"a": [1]}, in_sample_days=252)

        self.assertEqual(combos, [{"a": 1}])
        self.assertEqual(report.raw_combinations, 1)
        self.assertFalse(report.is_budget_exceeded)


class TestGridValidation(unittest.TestCase):
    def setUp(self):
        self.budgeter = HyperparameterSearchBudgeter(max_trials_per_year=100)

    def test_empty_grid_is_rejected(self):
        # Previously returned [{}] and reported a LOW-risk, budget-compliant search.
        with self.assertRaises(SearchBudgetError):
            self.budgeter.audit_and_prune({}, in_sample_days=252)

    def test_empty_axis_is_rejected(self):
        # Previously produced raw_combinations = 0, which compared as within budget
        # and reported LOW risk for a search that evaluated nothing at all.
        with self.assertRaises(SearchBudgetError):
            self.budgeter.audit_and_prune({"a": [1, 2], "b": []}, in_sample_days=252)

    def test_bare_string_axis_is_rejected(self):
        # "abc" is a sized sequence, so it would silently become three one-character
        # candidate values.
        with self.assertRaises(SearchBudgetError):
            self.budgeter.audit_and_prune({"mode": "abc"}, in_sample_days=252)

    def test_unsized_axis_is_rejected(self):
        with self.assertRaises(SearchBudgetError):
            self.budgeter.audit_and_prune({"a": iter([1, 2, 3])}, in_sample_days=252)

    def test_invalid_constructor_arguments_are_rejected(self):
        for bad in (0, -1):
            with self.assertRaises(SearchBudgetError):
                HyperparameterSearchBudgeter(max_trials_per_year=bad)
        for bad in (0.0, -1.0):
            with self.assertRaises(SearchBudgetError):
                HyperparameterSearchBudgeter(target_sharpe=bad)


class TestMinimumBacktestLength(unittest.TestCase):
    """Checked against the published worked example, not against this module's own
    arithmetic.

    Bailey, Borwein, Lopez de Prado and Zhu state that with only five years of data,
    no more than forty-five independent model configurations may be tried before the
    best of them is expected to show an in-sample annualised Sharpe of 1 with an
    expected out-of-sample Sharpe of zero.
    """

    def test_reproduces_published_five_year_forty_five_trial_example(self):
        self.assertAlmostEqual(
            minimum_backtest_length_years(45, target_sharpe=1.0), 5.00, places=2
        )

    def test_forty_five_is_the_largest_trial_count_five_years_supports(self):
        self.assertLessEqual(minimum_backtest_length_years(45), 5.0)
        self.assertGreater(minimum_backtest_length_years(46), 5.0)

    def test_expected_max_sharpe_lies_between_its_two_quantiles(self):
        # Structural property independent of the constant's value: E[max_N] is a
        # convex combination of Z^-1[1 - 1/N] and Z^-1[1 - 1/(N e)].
        normal = NormalDist()
        for n in (2, 10, 45, 100, 1000):
            lower = normal.inv_cdf(1.0 - 1.0 / n)
            upper = normal.inv_cdf(1.0 - 1.0 / (n * math.e))
            value = expected_max_sharpe(n)
            self.assertLess(lower, value)
            self.assertLess(value, upper)

    def test_requirement_grows_with_trial_count(self):
        lengths = [minimum_backtest_length_years(n) for n in (10, 45, 100, 500, 1000)]
        self.assertEqual(lengths, sorted(lengths))

    def test_gumbel_estimate_is_below_the_two_log_n_upper_bound(self):
        # The paper's closed-form bound MinBTL < 2 ln(N) / SR^2 must dominate.
        for n in (10, 45, 100, 1000):
            self.assertLess(minimum_backtest_length_years(n), 2.0 * math.log(n))

    def test_requirement_scales_with_the_inverse_square_of_target_sharpe(self):
        # MinBTL = (E[max_N] / SR)^2, so halving the target quadruples the span.
        base = minimum_backtest_length_years(100, target_sharpe=1.0)
        halved = minimum_backtest_length_years(100, target_sharpe=0.5)
        self.assertAlmostEqual(halved, 4.0 * base, places=9)

    def test_expected_max_sharpe_annualises_by_root_years(self):
        one_year = expected_max_sharpe(100, years=1.0)
        four_years = expected_max_sharpe(100, years=4.0)
        self.assertAlmostEqual(four_years, one_year / 2.0, places=9)

    def test_single_trial_needs_no_backtest_length(self):
        self.assertEqual(expected_max_sharpe(1), 0.0)
        self.assertEqual(minimum_backtest_length_years(1), 0.0)

    def test_invalid_arguments_are_rejected(self):
        with self.assertRaises(SearchBudgetError):
            expected_max_sharpe(0)
        with self.assertRaises(SearchBudgetError):
            expected_max_sharpe(10, years=0)
        with self.assertRaises(SearchBudgetError):
            minimum_backtest_length_years(10, target_sharpe=0)


class TestWalkForwardCumulativeAudit(unittest.TestCase):
    """The cumulative audit SKILL.md documents in Workflow step 4."""

    def setUp(self):
        self.budgeter = HyperparameterSearchBudgeter(max_trials_per_year=100)

    def _window(self, evaluations: int) -> SearchBudgetReport:
        return SearchBudgetReport(
            raw_combinations=evaluations,
            allowed_budget_max=100,
            sampled_evaluations=evaluations,
            is_budget_exceeded=False,
            pruning_applied=False,
            overfitting_risk_level="LOW",
            message="",
        )

    def test_per_window_compliance_does_not_imply_campaign_compliance(self):
        # The documented pitfall: ten windows each individually within a 100-trial
        # budget is a 1,000-trial selection process over a five-year span whose own
        # budget is 500.
        windows = [self._window(100) for _ in range(10)]
        audit = self.budgeter.audit_walk_forward(windows, total_span_days=1260)

        self.assertEqual(audit.windows, 10)
        self.assertEqual(audit.total_evaluations, 1000)
        self.assertEqual(audit.cumulative_budget_max, 500)
        self.assertTrue(audit.is_cumulative_budget_exceeded)
        self.assertIn("CUMULATIVE BUDGET OVERRUN", audit.message)

    def test_compliant_campaign_is_graded_low(self):
        windows = [self._window(40) for _ in range(5)]
        audit = self.budgeter.audit_walk_forward(windows, total_span_days=1260)

        self.assertEqual(audit.total_evaluations, 200)
        self.assertFalse(audit.is_cumulative_budget_exceeded)
        self.assertEqual(audit.overfitting_risk_level, "LOW")

    def test_budget_is_taken_from_the_distinct_span_not_the_window_sum(self):
        # Ten 252-day windows sum to 2,520 window-days but overlap onto a 1,260-day
        # span. Counting window-days would double the allowance.
        windows = [self._window(10) for _ in range(10)]
        audit = self.budgeter.audit_walk_forward(windows, total_span_days=1260)

        self.assertEqual(audit.cumulative_budget_max, 500)
        self.assertEqual(
            audit.cumulative_budget_max, self.budgeter.compute_max_budget(1260)
        )

    def test_cumulative_risk_grade_boundary(self):
        # HIGH requires total > 5 * cumulative budget (500), so 2,500 is MODERATE.
        at_multiple = [self._window(500) for _ in range(5)]
        self.assertEqual(
            self.budgeter.audit_walk_forward(
                at_multiple, total_span_days=1260
            ).overfitting_risk_level,
            "MODERATE",
        )

        over_multiple = [self._window(501) for _ in range(5)]
        self.assertEqual(
            self.budgeter.audit_walk_forward(
                over_multiple, total_span_days=1260
            ).overfitting_risk_level,
            "HIGH",
        )

    def test_minbtl_shortfall_is_reported_against_available_data(self):
        windows = [self._window(100) for _ in range(10)]
        audit = self.budgeter.audit_walk_forward(windows, total_span_days=1260)

        self.assertAlmostEqual(audit.available_years, 5.0, places=9)
        self.assertAlmostEqual(
            audit.min_backtest_length_years,
            minimum_backtest_length_years(1000),
            places=9,
        )
        self.assertFalse(audit.is_data_span_sufficient)
        self.assertIn("MinBTL SHORTFALL", audit.message)

    def test_sufficient_span_clears_the_minbtl_check(self):
        # 45 trials need 5.00 years; a 10-year span comfortably clears it.
        windows = [self._window(45)]
        audit = self.budgeter.audit_walk_forward(windows, total_span_days=2520)

        self.assertTrue(audit.is_data_span_sufficient)
        self.assertNotIn("MinBTL SHORTFALL", audit.message)

    def test_invalid_audit_arguments_are_rejected(self):
        with self.assertRaises(SearchBudgetError):
            self.budgeter.audit_walk_forward([], total_span_days=1260)
        with self.assertRaises(SearchBudgetError):
            self.budgeter.audit_walk_forward([self._window(10)], total_span_days=0)


class TestEndToEndCampaign(unittest.TestCase):
    """The scenario SKILL.md's Verification section describes."""

    def test_five_hundred_point_grid_on_one_year_is_pruned_to_budget(self):
        budgeter = HyperparameterSearchBudgeter(max_trials_per_year=100)
        grid = {
            "fast_period": list(range(5, 55, 5)),      # 10 values
            "slow_period": list(range(60, 160, 10)),   # 10 values
            "stop_loss_pct": [0.01, 0.02, 0.03, 0.04, 0.05],  # 5 values
        }  # 10 x 10 x 5 = 500

        combos, report = budgeter.audit_and_prune(grid, in_sample_days=252)

        self.assertEqual(report.raw_combinations, 500)
        self.assertEqual(report.allowed_budget_max, 100)
        self.assertTrue(report.is_budget_exceeded)
        self.assertEqual(len(combos), 100)
        self.assertEqual(report.sampled_evaluations, 100)
        self.assertEqual(report.overfitting_risk_level, "MODERATE")
        for name in grid:
            self.assertGreater(len({c[name] for c in combos}), 1)


if __name__ == "__main__":
    unittest.main()
