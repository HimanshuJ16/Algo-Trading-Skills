"""Unit tests for backtest-parameter-sensitivity-analysis."""
import unittest

from sensitivity_analyzer import (
    GridPoint,
    ParameterSensitivityAnalyzer,
    RobustnessVerdict,
    SensitivityError,
)


def grid(param_name, pairs):
    """Builds grid points from (param_value, sharpe) pairs."""
    return [GridPoint(params={param_name: v}, sharpe_ratio=s) for v, s in pairs]


class TestParameterSensitivityAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = ParameterSensitivityAnalyzer(max_neighborhood_degradation_pct=0.15) # 15% max drop-off

    # ----------------------------------------------------------------- baseline

    def test_robust_parameter_plateau(self):
        # Sharpe is stable across the range and the optimum is bracketed on both sides.
        results = self.analyzer.run_grid_sweep(
            "lookback", [10, 20, 30, 40, 50],
            lambda x: 1.5 - abs(x - 30) * 0.0005  # Flat interior plateau peaking at 30
        )
        report = self.analyzer.analyze_sensitivity(results, "lookback")
        self.assertTrue(report.is_robust)
        self.assertIn("ROBUST PLATEAU", report.message)
        self.assertIs(report.verdict, RobustnessVerdict.ROBUST_PLATEAU)
        self.assertEqual(report.best_params, {"lookback": 30.0})

    def test_fragile_overfit_peak(self):
        # Sharpe spikes only at one point -> fragile peak
        results = self.analyzer.run_grid_sweep(
            "threshold", [0.01, 0.02, 0.03, 0.04, 0.05],
            lambda x: 5.0 if abs(x - 0.03) < 0.005 else 0.5  # Massive isolated spike at 0.03
        )
        report = self.analyzer.analyze_sensitivity(results, "threshold")
        self.assertFalse(report.is_robust)
        self.assertIn("FRAGILE PEAK", report.message)
        self.assertIs(report.verdict, RobustnessVerdict.FRAGILE_PEAK)

    # --------------------------------------------------- ordering (regression, critical)

    def test_verdict_is_independent_of_list_order(self):
        """Regression: neighbours must be taken in parameter order, not list order.

        This exact pair of orderings previously produced "FRAGILE PEAK" and
        "ROBUST PLATEAU: ... Safe to deploy." from an identical set of results.
        """
        pairs = [(10, 4.9), (20, 0.5), (30, 5.0), (40, 0.5), (50, 4.95)]
        shuffled = [(20, 0.5), (10, 4.9), (30, 5.0), (50, 4.95), (40, 0.5)]

        in_order = self.analyzer.analyze_sensitivity(grid("p", pairs), "p")
        out_of_order = self.analyzer.analyze_sensitivity(grid("p", shuffled), "p")

        self.assertIs(in_order.verdict, RobustnessVerdict.FRAGILE_PEAK)
        self.assertIs(out_of_order.verdict, RobustnessVerdict.FRAGILE_PEAK)
        self.assertEqual(in_order.max_gradient, out_of_order.max_gradient)
        self.assertEqual(in_order.best_params, out_of_order.best_params)

    def test_descending_parameter_order_is_handled(self):
        pairs = [(50, 0.5), (40, 0.5), (30, 5.0), (20, 0.5), (10, 0.5)]
        report = self.analyzer.analyze_sensitivity(grid("p", pairs), "p")
        self.assertIs(report.verdict, RobustnessVerdict.FRAGILE_PEAK)
        self.assertEqual(report.best_params, {"p": 30})

    # ------------------------------------------------- viability (regression, critical)

    def test_uniformly_losing_grid_is_not_robust(self):
        """Regression: an all-negative grid used to report "ROBUST PLATEAU ... Safe to deploy".

        The degradation term was only computed when the best Sharpe was positive, so it
        stayed at 0.0 and every losing strategy passed the plateau test.
        """
        report = self.analyzer.analyze_sensitivity(
            grid("p", [(1, -1.0), (2, -1.01), (3, -1.02), (4, -1.03), (5, -1.04)]), "p"
        )
        self.assertFalse(report.is_robust)
        self.assertIs(report.verdict, RobustnessVerdict.NOT_VIABLE)
        self.assertNotIn("ROBUST PLATEAU", report.message)

    def test_best_sharpe_of_exactly_zero_is_not_viable(self):
        report = self.analyzer.analyze_sensitivity(
            grid("p", [(1, -10.0), (2, 0.0), (3, -10.0)]), "p"
        )
        self.assertIs(report.verdict, RobustnessVerdict.NOT_VIABLE)

    def test_min_viable_sharpe_rejects_a_stable_but_worthless_grid(self):
        """A flat grid at Sharpe 0.001 is stable and worthless; the hurdle must catch it."""
        flat = grid("p", [(1, 0.001), (2, 0.001), (3, 0.001), (4, 0.001), (5, 0.001)])

        permissive = ParameterSensitivityAnalyzer()
        self.assertIs(
            permissive.analyze_sensitivity(flat, "p").verdict, RobustnessVerdict.ROBUST_PLATEAU
        )

        with_hurdle = ParameterSensitivityAnalyzer(min_viable_sharpe=0.5)
        self.assertIs(
            with_hurdle.analyze_sensitivity(flat, "p").verdict, RobustnessVerdict.NOT_VIABLE
        )

    # ------------------------------------------------- grid coverage (regression, critical)

    def test_single_grid_point_cannot_prove_robustness(self):
        """Regression: one point used to return "ROBUST PLATEAU ... Safe to deploy"."""
        report = self.analyzer.analyze_sensitivity(grid("p", [(1, 2.0)]), "p")
        self.assertFalse(report.is_robust)
        self.assertIs(report.verdict, RobustnessVerdict.INSUFFICIENT_GRID)

    def test_two_grid_points_cannot_prove_robustness(self):
        report = self.analyzer.analyze_sensitivity(grid("p", [(1, 2.0), (2, 1.99)]), "p")
        self.assertIs(report.verdict, RobustnessVerdict.INSUFFICIENT_GRID)

    def test_empty_grid(self):
        report = self.analyzer.analyze_sensitivity([], "p")
        self.assertFalse(report.is_robust)
        self.assertEqual(report.total_grid_points, 0)
        self.assertIs(report.verdict, RobustnessVerdict.INSUFFICIENT_GRID)

    def test_edge_optimum_is_not_certified_robust(self):
        """Regression: this is the skill's own original "robust plateau" example.

        Sharpe rises monotonically with the parameter, so the best point is the last
        one swept. Its neighbourhood was only observed on one side and the true optimum
        lies outside the grid, but it used to be reported as a robust plateau.
        """
        results = self.analyzer.run_grid_sweep(
            "lookback", [10, 20, 30, 40, 50], lambda x: 1.5 + (x - 30) * 0.001
        )
        report = self.analyzer.analyze_sensitivity(results, "lookback")

        self.assertTrue(report.best_at_grid_edge)
        self.assertFalse(report.is_robust)
        self.assertIs(report.verdict, RobustnessVerdict.EDGE_OPTIMUM)

    def test_flat_grid_ties_resolve_to_an_interior_optimum(self):
        """A perfectly flat grid is the ideal plateau, not an edge optimum.

        Every point ties for the maximum, so taking the first index would land on the
        boundary and wrongly report EDGE_OPTIMUM.
        """
        report = self.analyzer.analyze_sensitivity(
            grid("p", [(1, 2.0), (2, 2.0), (3, 2.0), (4, 2.0), (5, 2.0)]), "p"
        )
        self.assertFalse(report.best_at_grid_edge)
        self.assertIs(report.verdict, RobustnessVerdict.ROBUST_PLATEAU)
        self.assertEqual(report.best_params, {"p": 3})

    def test_interior_optimum_is_not_flagged_as_edge(self):
        report = self.analyzer.analyze_sensitivity(
            grid("p", [(1, 1.0), (2, 1.2), (3, 1.0)]), "p"
        )
        self.assertFalse(report.best_at_grid_edge)

    # ------------------------------------------------ degradation, verified by hand

    def test_degradation_threshold_boundary_is_inclusive(self):
        """Best 4.0 against worst neighbour 3.0 is exactly (4-3)/4 = 0.25 degradation.

        Values chosen as exact binary fractions so the boundary comparison is not
        decided by floating-point noise.
        """
        analyzer = ParameterSensitivityAnalyzer(max_neighborhood_degradation_pct=0.25)

        at_threshold = grid("p", [(1, 3.5), (2, 3.0), (3, 4.0), (4, 3.5), (5, 3.5)])
        report = analyzer.analyze_sensitivity(at_threshold, "p")
        self.assertEqual(report.neighborhood_degradation_pct, 0.25)
        self.assertIs(report.verdict, RobustnessVerdict.ROBUST_PLATEAU)

        just_over = grid("p", [(1, 3.5), (2, 2.9), (3, 4.0), (4, 3.5), (5, 3.5)])
        over_report = analyzer.analyze_sensitivity(just_over, "p")
        self.assertGreater(over_report.neighborhood_degradation_pct, 0.25)
        self.assertIs(over_report.verdict, RobustnessVerdict.FRAGILE_PEAK)

    def test_worst_of_the_two_neighbours_drives_the_verdict(self):
        # Left neighbour is fine, right neighbour collapses; must be judged fragile.
        report = self.analyzer.analyze_sensitivity(
            grid("p", [(1, 1.95), (2, 2.0), (3, 0.1)]), "p"
        )
        self.assertIs(report.verdict, RobustnessVerdict.FRAGILE_PEAK)

    def test_max_gradient_is_an_alias_of_neighborhood_degradation(self):
        report = self.analyzer.analyze_sensitivity(
            grid("p", [(1, 0.5), (2, 5.0), (3, 0.5)]), "p"
        )
        self.assertEqual(report.max_gradient, report.neighborhood_degradation_pct)

    def test_summary_statistics_use_population_standard_deviation(self):
        """Sharpes [1,2,3]: mean 2.0, population sd sqrt(2/3) = 0.8165 (sample sd is 1.0)."""
        report = self.analyzer.analyze_sensitivity(
            grid("p", [(1, 1.0), (2, 2.0), (3, 3.0)]), "p"
        )
        self.assertEqual(report.avg_sharpe, 2.0)
        self.assertEqual(report.sharpe_std, 0.8165)

    def test_total_grid_points_reports_the_trial_count(self):
        """The trial count is what a downstream Sharpe deflation needs."""
        results = self.analyzer.run_grid_sweep("p", list(range(20)), lambda x: 1.0)
        report = self.analyzer.analyze_sensitivity(results, "p")
        self.assertEqual(report.total_grid_points, 20)

    # --------------------------------------------------------------- input validation

    def test_non_finite_sharpe_from_backtest_is_rejected(self):
        """Regression: NaN made every comparison False, yielding a robust verdict."""
        with self.assertRaises(SensitivityError):
            self.analyzer.run_grid_sweep("p", [1, 2, 3], lambda x: float("nan"))
        with self.assertRaises(SensitivityError):
            self.analyzer.run_grid_sweep("p", [1, 2, 3], lambda x: float("inf"))

    def test_non_finite_sharpe_in_grid_points_is_rejected(self):
        with self.assertRaises(SensitivityError):
            self.analyzer.analyze_sensitivity(
                grid("p", [(1, 1.0), (2, float("nan")), (3, 1.0)]), "p"
            )

    def test_missing_parameter_key_is_rejected(self):
        with self.assertRaises(SensitivityError):
            self.analyzer.analyze_sensitivity(
                [GridPoint({"other": 1}, 1.0), GridPoint({"other": 2}, 1.0)], "p"
            )

    def test_duplicate_parameter_values_are_rejected(self):
        with self.assertRaises(SensitivityError):
            self.analyzer.run_grid_sweep("p", [1, 2, 2, 3], lambda x: 1.0)
        with self.assertRaises(SensitivityError):
            self.analyzer.analyze_sensitivity(grid("p", [(1, 1.0), (1, 2.0)]), "p")

    def test_non_numeric_grid_inputs_are_rejected(self):
        with self.assertRaises(SensitivityError):
            self.analyzer.run_grid_sweep("p", [1, "two", 3], lambda x: 1.0)
        with self.assertRaises(SensitivityError):
            self.analyzer.run_grid_sweep("p", [1, float("inf")], lambda x: 1.0)
        with self.assertRaises(SensitivityError):
            self.analyzer.run_grid_sweep("p", [1, 2], lambda x: "not a sharpe")
        with self.assertRaises(SensitivityError):
            self.analyzer.run_grid_sweep("", [1, 2], lambda x: 1.0)

    def test_invalid_analyzer_configuration_is_rejected(self):
        with self.assertRaises(SensitivityError):
            ParameterSensitivityAnalyzer(max_neighborhood_degradation_pct=-0.1)
        with self.assertRaises(SensitivityError):
            ParameterSensitivityAnalyzer(max_neighborhood_degradation_pct=float("nan"))
        with self.assertRaises(SensitivityError):
            ParameterSensitivityAnalyzer(min_grid_points=2)
        with self.assertRaises(SensitivityError):
            ParameterSensitivityAnalyzer(min_viable_sharpe=float("nan"))

    def test_negative_viability_hurdle_is_rejected(self):
        """A negative hurdle would readmit the original bug.

        The degradation ratio divides by the best Sharpe, so it is undefined for a
        non-positive optimum. Allowing min_viable_sharpe=-1.0 would let a grid whose
        best Sharpe is -0.5 clear the gate and then score 0% degradation, reproducing
        exactly the "stable loser reported as robust" defect.
        """
        with self.assertRaises(SensitivityError):
            ParameterSensitivityAnalyzer(min_viable_sharpe=-1.0)


if __name__ == "__main__":
    unittest.main()
