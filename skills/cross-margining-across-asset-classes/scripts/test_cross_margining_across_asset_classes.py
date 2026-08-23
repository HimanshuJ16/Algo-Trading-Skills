import math
import unittest

from cross_margining_across_asset_classes import (
    AssetClassMarginComponent,
    CrossMarginAuditReport,
    CrossMarginInputError,
    CrossMarginingCalculator,
    InconsistentCorrelationError,
)


class TestCrossMarginingCalculator(unittest.TestCase):

    def setUp(self):
        self.calculator = CrossMarginingCalculator(minimum_floor_pct=0.20)

        # Register correlation offset between CME Equity Futures (ES) and OCC
        # Index Options (SPX). Strong negative offset (-0.80) for a
        # delta-hedged position under the CME-OCC arrangement.
        self.calculator.register_correlation_offset(
            "EQUITY_FUTURES", "INDEX_OPTIONS", -0.80, program="CME-OCC"
        )

        self.components = [
            AssetClassMarginComponent("EQUITY_FUTURES", "CME", 500_000.0),
            AssetClassMarginComponent("INDEX_OPTIONS", "OCC", 400_000.0),
        ]

    def test_cross_margining_calculation_and_savings(self):
        # Expected values derived independently of the implementation:
        #   Standalone = 500,000 + 400,000                = 900,000
        #   M_cross^2  = 500,000^2 + 400,000^2
        #                + 2 * (-0.80) * 500,000 * 400,000
        #              = 250e9 + 160e9 - 320e9            = 90e9
        #   M_cross    = sqrt(90e9)                       = 300,000 exactly
        #   Savings    = 900,000 - 300,000                = 600,000
        #   Efficiency = 600,000 / 900,000                = 66.67%
        report = self.calculator.calculate_cross_margin(self.components)

        self.assertEqual(report.total_standalone_margin_usd, 900_000.0)
        self.assertAlmostEqual(
            report.total_cross_margined_requirement_usd, 300_000.0, delta=0.01
        )
        self.assertAlmostEqual(report.margin_savings_usd, 600_000.0, delta=0.01)
        self.assertAlmostEqual(report.capital_efficiency_gain_pct, 66.67, places=2)
        self.assertFalse(report.is_floor_applied)

    def test_audit_trail_records_applied_offset_and_floor(self):
        report = self.calculator.calculate_cross_margin(self.components)
        self.assertEqual(
            report.applied_offsets, [("EQUITY_FUTURES", "INDEX_OPTIONS", -0.80)]
        )
        self.assertEqual(report.unregistered_pairs, [])
        self.assertEqual(report.minimum_floor_pct, 0.20)

    def test_minimum_risk_floor_enforcement(self):
        # Perfect negative correlation -1.0 with equal amounts ($500k & $500k)
        # Raw cross margin = sqrt(500k^2 + 500k^2 - 2*500k*500k) = 0.0
        # Risk floor = 20% of $1,000,000 = $200,000
        self.calculator.register_correlation_offset(
            "EQUITY_FUTURES", "INDEX_OPTIONS", -1.0
        )
        components = [
            AssetClassMarginComponent("EQUITY_FUTURES", "CME", 500_000.0),
            AssetClassMarginComponent("INDEX_OPTIONS", "OCC", 500_000.0),
        ]
        report = self.calculator.calculate_cross_margin(components)

        self.assertEqual(report.total_cross_margined_requirement_usd, 200_000.0)
        self.assertTrue(report.is_floor_applied)
        self.assertEqual(report.capital_efficiency_gain_pct, 80.0)

    def test_floor_can_be_disabled(self):
        calc = CrossMarginingCalculator(minimum_floor_pct=0.0)
        calc.register_correlation_offset("A", "B", -1.0)
        report = calc.calculate_cross_margin(
            [
                AssetClassMarginComponent("A", "CME", 500_000.0),
                AssetClassMarginComponent("B", "OCC", 500_000.0),
            ]
        )
        self.assertEqual(report.total_cross_margined_requirement_usd, 0.0)
        self.assertFalse(report.is_floor_applied)
        self.assertEqual(report.capital_efficiency_gain_pct, 100.0)

    # --- fail-closed behaviour for unregistered pairs -------------------

    def test_unregistered_pair_receives_no_offset(self):
        # Regression: the previous implementation defaulted a missing pair to
        # rho = 0.0, granting an unearned diversification benefit
        # (sqrt(500k^2 + 400k^2) = 640,312) to a pair with no cross-margin
        # agreement. Fail-closed default rho = 1.0 must return the standalone
        # sum, 900,000.
        calc = CrossMarginingCalculator(minimum_floor_pct=0.20)
        report = calc.calculate_cross_margin(self.components)

        self.assertAlmostEqual(
            report.total_cross_margined_requirement_usd, 900_000.0, delta=0.01
        )
        self.assertAlmostEqual(report.margin_savings_usd, 0.0, delta=0.01)
        self.assertEqual(report.capital_efficiency_gain_pct, 0.0)
        self.assertEqual(
            report.unregistered_pairs, [("EQUITY_FUTURES", "INDEX_OPTIONS")]
        )
        self.assertEqual(report.applied_offsets, [])

    def test_explicit_zero_correlation_is_distinct_from_unregistered(self):
        calc = CrossMarginingCalculator(minimum_floor_pct=0.20)
        calc.register_correlation_offset("EQUITY_FUTURES", "INDEX_OPTIONS", 0.0)
        report = calc.calculate_cross_margin(self.components)

        # sqrt(500k^2 + 400k^2) = 640,312.42
        self.assertAlmostEqual(
            report.total_cross_margined_requirement_usd,
            math.sqrt(500_000.0 ** 2 + 400_000.0 ** 2),
            delta=0.01,
        )
        self.assertEqual(report.unregistered_pairs, [])

    def test_offsets_are_symmetric(self):
        calc = CrossMarginingCalculator(minimum_floor_pct=0.0)
        calc.register_correlation_offset("B", "A", -0.5)
        report = calc.calculate_cross_margin(
            [
                AssetClassMarginComponent("A", "CME", 300_000.0),
                AssetClassMarginComponent("B", "OCC", 200_000.0),
            ]
        )
        # sqrt(300k^2 + 200k^2 + 2*(-0.5)*300k*200k) = sqrt(70e9) = 264,575.13
        self.assertAlmostEqual(
            report.total_cross_margined_requirement_usd, math.sqrt(70e9), delta=0.01
        )

    # --- correlation consistency ----------------------------------------

    def test_jointly_inconsistent_offsets_are_rejected(self):
        # Three equal legs pairwise at rho = -0.9 is not positive
        # semi-definite: 3*M^2 + 2*3*(-0.9)*M^2 = -2.4*M^2 < 0. The previous
        # implementation clamped this to zero and reported a 100% saving.
        calc = CrossMarginingCalculator(minimum_floor_pct=0.0)
        for a, b in (("A", "B"), ("B", "C"), ("A", "C")):
            calc.register_correlation_offset(a, b, -0.9)
        components = [
            AssetClassMarginComponent("A", "CME", 100_000.0),
            AssetClassMarginComponent("B", "OCC", 100_000.0),
            AssetClassMarginComponent("C", "FICC", 100_000.0),
        ]
        with self.assertRaises(InconsistentCorrelationError):
            calc.calculate_cross_margin(components)

    def test_exactly_degenerate_offsets_are_allowed(self):
        # rho = -0.5 pairwise across three equal legs gives exactly zero
        # variance (3*M^2 - 3*M^2 = 0) -- the PSD boundary, which must NOT be
        # rejected by the tolerance check.
        calc = CrossMarginingCalculator(minimum_floor_pct=0.0)
        for a, b in (("A", "B"), ("B", "C"), ("A", "C")):
            calc.register_correlation_offset(a, b, -0.5)
        report = calc.calculate_cross_margin(
            [
                AssetClassMarginComponent("A", "CME", 100_000.0),
                AssetClassMarginComponent("B", "OCC", 100_000.0),
                AssetClassMarginComponent("C", "FICC", 100_000.0),
            ]
        )
        self.assertAlmostEqual(
            report.total_cross_margined_requirement_usd, 0.0, delta=0.01
        )

    def test_three_leg_offset_matches_independent_calculation(self):
        calc = CrossMarginingCalculator(minimum_floor_pct=0.0)
        calc.register_correlation_offset("A", "B", -0.6)
        calc.register_correlation_offset("A", "C", 0.3)
        calc.register_correlation_offset("B", "C", -0.2)
        components = [
            AssetClassMarginComponent("A", "CME", 400_000.0),
            AssetClassMarginComponent("B", "OCC", 300_000.0),
            AssetClassMarginComponent("C", "FICC", 200_000.0),
        ]
        # Hand-computed:
        #   squares  = 160e9 + 90e9 + 40e9 = 290e9
        #   AB: 2*(-0.6)*400k*300k = -144e9
        #   AC: 2*( 0.3)*400k*200k =  +48e9
        #   BC: 2*(-0.2)*300k*200k =  -24e9
        #   total    = 170e9 -> sqrt = 412,310.5626...
        expected = math.sqrt(170e9)
        report = calc.calculate_cross_margin(components)
        self.assertAlmostEqual(
            report.total_cross_margined_requirement_usd, expected, delta=0.01
        )
        self.assertEqual(len(report.applied_offsets), 3)

    def test_cross_margin_never_exceeds_standalone_sum(self):
        calc = CrossMarginingCalculator(minimum_floor_pct=0.0, default_correlation=1.0)
        report = calc.calculate_cross_margin(
            [
                AssetClassMarginComponent("A", "CME", 250_000.0),
                AssetClassMarginComponent("B", "OCC", 750_000.0),
            ]
        )
        self.assertAlmostEqual(
            report.total_cross_margined_requirement_usd, 1_000_000.0, delta=0.01
        )
        self.assertGreaterEqual(report.margin_savings_usd, 0.0)

    # --- input validation -------------------------------------------------

    def test_rejects_negative_standalone_margin(self):
        with self.assertRaises(CrossMarginInputError):
            self.calculator.calculate_cross_margin(
                [
                    AssetClassMarginComponent("EQUITY_FUTURES", "CME", -1.0),
                    AssetClassMarginComponent("INDEX_OPTIONS", "OCC", 400_000.0),
                ]
            )

    def test_rejects_non_finite_standalone_margin(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(CrossMarginInputError):
                    self.calculator.calculate_cross_margin(
                        [
                            AssetClassMarginComponent("EQUITY_FUTURES", "CME", bad),
                            AssetClassMarginComponent("INDEX_OPTIONS", "OCC", 400_000.0),
                        ]
                    )

    def test_rejects_duplicate_asset_class_id(self):
        # Two rows with the same id would look up a self-pair, take the
        # default correlation instead of 1.0, and misprice the portfolio.
        with self.assertRaises(CrossMarginInputError):
            self.calculator.calculate_cross_margin(
                [
                    AssetClassMarginComponent("EQUITY_FUTURES", "CME", 500_000.0),
                    AssetClassMarginComponent("equity_futures", "CME", 300_000.0),
                ]
            )

    def test_rejects_out_of_range_correlation(self):
        for bad in (-1.5, 1.5, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(CrossMarginInputError):
                    self.calculator.register_correlation_offset("A", "B", bad)

    def test_rejects_self_pair_offset_registration(self):
        with self.assertRaises(CrossMarginInputError):
            self.calculator.register_correlation_offset("A", "A", -0.9)

    def test_rejects_invalid_floor_pct(self):
        for bad in (-0.1, 1.1, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(CrossMarginInputError):
                    CrossMarginingCalculator(minimum_floor_pct=bad)

    def test_rejects_invalid_default_correlation(self):
        with self.assertRaises(CrossMarginInputError):
            CrossMarginingCalculator(default_correlation=2.0)

    # --- degenerate inputs -------------------------------------------------

    def test_empty_portfolio_returns_zero_report(self):
        report = self.calculator.calculate_cross_margin([])
        self.assertIsInstance(report, CrossMarginAuditReport)
        self.assertEqual(report.total_standalone_margin_usd, 0.0)
        self.assertEqual(report.total_cross_margined_requirement_usd, 0.0)
        self.assertEqual(report.capital_efficiency_gain_pct, 0.0)
        self.assertFalse(report.is_floor_applied)

    def test_all_zero_margins_return_zero_report_without_dividing_by_zero(self):
        report = self.calculator.calculate_cross_margin(
            [
                AssetClassMarginComponent("EQUITY_FUTURES", "CME", 0.0),
                AssetClassMarginComponent("INDEX_OPTIONS", "OCC", 0.0),
            ]
        )
        self.assertEqual(report.total_standalone_margin_usd, 0.0)
        self.assertEqual(report.capital_efficiency_gain_pct, 0.0)

    def test_single_component_returns_its_own_margin(self):
        report = self.calculator.calculate_cross_margin(
            [AssetClassMarginComponent("EQUITY_FUTURES", "CME", 500_000.0)]
        )
        self.assertEqual(report.total_cross_margined_requirement_usd, 500_000.0)
        self.assertEqual(report.margin_savings_usd, 0.0)
        self.assertEqual(report.unregistered_pairs, [])


if __name__ == '__main__':
    unittest.main()
