import logging
import math
import unittest

from custom_scenario_stress_tester import (
    STATUS_COMPLETE,
    STATUS_INCOMPLETE_COVERAGE,
    AssetPosition,
    CustomScenarioStressTester,
    FactorShock,
    ScenarioResult,
    ShockType,
    StressScenarioCategory,
    StressScenarioDefinition,
    StressTestReport,
)


def custom(scenario_id, shocks, name=None):
    """Builds a one-off hypothetical scenario."""
    return StressScenarioDefinition(
        scenario_id=scenario_id,
        scenario_name=name or scenario_id,
        category=StressScenarioCategory.CUSTOM_HYPOTHETICAL,
        shocks=shocks,
    )


def result_for(report, scenario_id):
    return next(r for r in report.results if r.scenario_id == scenario_id)


class TestCustomScenarioStressTesterLegacy(unittest.TestCase):
    def test_scenarios(self):
        scenarios = {"Crash": -0.2, "Rally": 0.1}
        tester = CustomScenarioStressTester(scenarios)
        results = tester.run_stress_test(1000)

        self.assertEqual(len(results), 2)
        res_map = {r.scenario_name: r.pnl for r in results}
        self.assertEqual(res_map["Crash"], -200)
        self.assertEqual(res_map["Rally"], 100)

    def test_default_legacy_scenarios(self):
        results = CustomScenarioStressTester().run_stress_test(1000)
        self.assertEqual({r.scenario_name for r in results}, {"Crash", "Rally"})
        self.assertIsInstance(results[0], ScenarioResult)

    def test_legacy_rejects_non_finite_value(self):
        with self.assertRaises(ValueError):
            CustomScenarioStressTester().run_stress_test(float("nan"))


class TestEquityScenarioPnl(unittest.TestCase):
    """A $1M long-only equity book. Expected values derived by hand, not from the code."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tester = CustomScenarioStressTester(max_allowed_drawdown_pct=20.0)
        self.positions = [
            AssetPosition("AAPL", "EQUITY_SPOT", 500000.0, beta_to_factor=1.1),
            AssetPosition("MSFT", "EQUITY_SPOT", 500000.0, beta_to_factor=0.9),
        ]

    def test_predefined_historical_scenarios(self):
        report = self.tester.run_multi_factor_stress_test(self.positions)
        self.assertIsInstance(report, StressTestReport)
        self.assertEqual(report.total_scenarios_evaluated, 3)
        self.assertEqual(report.capital_base_usd, 1000000.0)

        # 500k * -0.35 * 1.1 = -192,500 and 500k * -0.35 * 0.9 = -157,500 -> -350,000.
        lehman = result_for(report, "SCEN_2008_LEHMAN")
        self.assertEqual(lehman.simulated_pnl_usd, -350000.0)
        self.assertEqual(lehman.percentage_loss_pct, -35.0)
        self.assertEqual(lehman.ending_portfolio_value_usd, 650000.0)
        self.assertTrue(lehman.is_drawdown_limit_breached)

        # 500k * -0.30 * 1.1 = -165,000 and 500k * -0.30 * 0.9 = -135,000 -> -300,000.
        covid = result_for(report, "SCEN_2020_COVID")
        self.assertEqual(covid.simulated_pnl_usd, -300000.0)
        self.assertEqual(covid.percentage_loss_pct, -30.0)
        self.assertTrue(covid.is_drawdown_limit_breached)

        self.assertEqual(report.worst_case_scenario, "2008 Lehman Financial Crisis")
        self.assertEqual(report.max_loss_usd, -350000.0)
        self.assertEqual(report.max_percentage_loss_pct, -35.0)
        self.assertEqual(
            report.breached_scenario_ids, ["SCEN_2008_LEHMAN", "SCEN_2020_COVID"]
        )

    def test_scenario_that_touches_nothing_reports_zero_and_names_the_positions(self):
        # The 2022 scenario shocks INTEREST_RATE_BPS and TECH_GROWTH_SPOT; an equity-spot
        # book holds neither, so the honest answer is "$0, and here is what I skipped".
        report = self.tester.run_multi_factor_stress_test(self.positions)
        rates = result_for(report, "SCEN_2022_RATES")
        self.assertEqual(rates.simulated_pnl_usd, 0.0)
        self.assertEqual(rates.unshocked_asset_ids, ["AAPL", "MSFT"])
        self.assertEqual(rates.unshocked_value_usd, 1000000.0)
        self.assertEqual(rates.shocked_value_usd, 0.0)
        # EQUITY_SPOT is covered by other scenarios, so the run as a whole is complete.
        self.assertEqual(report.status, STATUS_COMPLETE)
        self.assertEqual(report.factors_never_shocked, [])

    def test_custom_hypothetical_scenario(self):
        report = self.tester.run_multi_factor_stress_test(
            self.positions,
            custom_scenarios=[custom("SCEN_CUSTOM_TECH", [FactorShock("EQUITY_SPOT", -0.15)])],
        )
        self.assertEqual(report.total_scenarios_evaluated, 4)

        res = result_for(report, "SCEN_CUSTOM_TECH")
        self.assertEqual(res.simulated_pnl_usd, -150000.0)
        self.assertEqual(res.percentage_loss_pct, -15.0)
        self.assertFalse(res.is_drawdown_limit_breached)

    def test_total_wipeout_shock_is_allowed(self):
        report = self.tester.run_multi_factor_stress_test(
            self.positions, custom_scenarios=[custom("ZERO", [FactorShock("EQUITY_SPOT", -1.0)])]
        )
        res = result_for(report, "ZERO")
        self.assertEqual(res.simulated_pnl_usd, -1000000.0)
        self.assertEqual(res.percentage_loss_pct, -100.0)


class TestYieldShockSignConvention(unittest.TestCase):
    """
    Regression cover for the two 1.0.0 sign/unit errors. Both of these assertions fail
    against 1.0.0, which returned +140,000 and +3,000,000 respectively.
    """

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tester = CustomScenarioStressTester(max_allowed_drawdown_pct=20.0)

    def test_rate_hike_is_a_loss_for_a_long_bond_book(self):
        # dP/P = -D * dy = -7 * 0.0200 = -14.00% of $1,000,000 = -$140,000.
        report = self.tester.run_multi_factor_stress_test(
            [AssetPosition("UST10Y", "INTEREST_RATE_BPS", 1000000.0, beta_to_factor=7.0)]
        )
        res = result_for(report, "SCEN_2022_RATES")
        self.assertEqual(res.simulated_pnl_usd, -140000.0)
        self.assertEqual(res.percentage_loss_pct, -14.0)
        self.assertFalse(res.is_drawdown_limit_breached)

    def test_rate_cut_is_a_gain_for_a_long_bond_book(self):
        # dP/P = -5 * (-0.0150) = +7.50% of $2,000,000 = +$150,000.
        report = self.tester.run_multi_factor_stress_test(
            [AssetPosition("UST5Y", "INTEREST_RATE_BPS", 2000000.0, beta_to_factor=5.0)],
            custom_scenarios=[
                custom(
                    "CUT",
                    [FactorShock("INTEREST_RATE_BPS", -150.0, shock_type=ShockType.YIELD_BPS)],
                )
            ],
        )
        res = result_for(report, "CUT")
        self.assertEqual(res.simulated_pnl_usd, 150000.0)
        self.assertEqual(res.percentage_loss_pct, 7.5)
        self.assertFalse(res.is_drawdown_limit_breached)

    def test_credit_spread_widening_is_a_loss_sized_in_basis_points(self):
        # dP/P = -4 * 0.0300 = -12.00% of $2,000,000 = -$240,000.
        report = self.tester.run_multi_factor_stress_test(
            [AssetPosition("HYFUND", "CREDIT_SPREAD", 2000000.0, beta_to_factor=4.0)]
        )
        res = result_for(report, "SCEN_2008_LEHMAN")
        self.assertEqual(res.simulated_pnl_usd, -240000.0)
        self.assertEqual(res.percentage_loss_pct, -12.0)

    def test_legacy_is_absolute_change_flag_selects_the_yield_convention(self):
        shock = FactorShock("INTEREST_RATE_BPS", 100.0, is_absolute_change=True)
        self.assertIs(shock.shock_type, ShockType.YIELD_BPS)

        # dP/P = -5 * 0.0100 = -5.00% of $1,000,000 = -$50,000.
        report = self.tester.run_multi_factor_stress_test(
            [AssetPosition("BOND", "INTEREST_RATE_BPS", 1000000.0, beta_to_factor=5.0)],
            custom_scenarios=[custom("LEGACY", [shock])],
        )
        self.assertEqual(result_for(report, "LEGACY").simulated_pnl_usd, -50000.0)

    def test_default_shock_type_is_relative(self):
        shock = FactorShock("EQUITY_SPOT", -0.35)
        self.assertIs(shock.shock_type, ShockType.RELATIVE_RETURN)
        self.assertFalse(shock.is_absolute_change)

    def test_conflicting_shock_type_and_legacy_flag_raise(self):
        with self.assertRaises(ValueError):
            FactorShock(
                "INTEREST_RATE_BPS",
                100.0,
                is_absolute_change=True,
                shock_type=ShockType.RELATIVE_RETURN,
            )


class TestSignAndDenominatorConventions(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tester = CustomScenarioStressTester(max_allowed_drawdown_pct=20.0)

    def test_short_position_gains_on_a_crash(self):
        # A short vol book: -$100,000 * 1.50 = -$150,000 when vol trebles.
        report = self.tester.run_multi_factor_stress_test(
            [AssetPosition("SHORT_VOL", "IMPLIED_VOL", -100000.0, beta_to_factor=1.0)],
            capital_base_usd=1000000.0,
        )
        self.assertEqual(result_for(report, "SCEN_2008_LEHMAN").simulated_pnl_usd, -150000.0)

        # The mirror long gains the same amount.
        report = self.tester.run_multi_factor_stress_test(
            [AssetPosition("LONG_VOL", "IMPLIED_VOL", 100000.0, beta_to_factor=1.0)]
        )
        self.assertEqual(result_for(report, "SCEN_2008_LEHMAN").simulated_pnl_usd, 150000.0)

    def test_short_book_without_an_explicit_capital_base_raises(self):
        positions = [
            AssetPosition("LONG", "EQUITY_SPOT", 1000000.0),
            AssetPosition("SHORT", "EQUITY_SPOT", -999000.0),
        ]
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(positions)

        # With a stated base the hedged book nets to a trivial loss against real capital.
        report = self.tester.run_multi_factor_stress_test(positions, capital_base_usd=100000.0)
        res = result_for(report, "SCEN_2008_LEHMAN")
        self.assertEqual(res.simulated_pnl_usd, -350.0)
        self.assertEqual(res.percentage_loss_pct, -0.35)
        self.assertFalse(res.is_drawdown_limit_breached)

    def test_capital_base_overrides_the_position_sum(self):
        report = self.tester.run_multi_factor_stress_test(
            [AssetPosition("A", "EQUITY_SPOT", 1000000.0)], capital_base_usd=2000000.0
        )
        res = result_for(report, "SCEN_2008_LEHMAN")
        self.assertEqual(res.simulated_pnl_usd, -350000.0)
        self.assertEqual(res.percentage_loss_pct, -17.5)
        self.assertFalse(res.is_drawdown_limit_breached)

    def test_multi_factor_book_sums_across_factors(self):
        # Equity: 600k * -0.35 = -210,000. Credit: -5 * 0.03 * 400k = -60,000.
        report = self.tester.run_multi_factor_stress_test([
            AssetPosition("SPX", "EQUITY_SPOT", 600000.0),
            AssetPosition("HY", "CREDIT_SPREAD", 400000.0, beta_to_factor=5.0),
        ])
        res = result_for(report, "SCEN_2008_LEHMAN")
        self.assertEqual(res.simulated_pnl_usd, -270000.0)
        self.assertEqual(res.percentage_loss_pct, -27.0)
        self.assertEqual(res.shocked_value_usd, 1000000.0)
        self.assertEqual(res.unshocked_asset_ids, [])

    def test_one_instrument_may_carry_several_factor_rows(self):
        # A convertible: equity delta plus credit spread duration, both shocked.
        report = self.tester.run_multi_factor_stress_test(
            [
                AssetPosition("CB", "EQUITY_SPOT", 500000.0, beta_to_factor=0.5),
                AssetPosition("CB", "CREDIT_SPREAD", 500000.0, beta_to_factor=3.0),
            ],
        )
        # 500k * -0.35 * 0.5 = -87,500 ; -3 * 0.03 * 500k = -45,000.
        self.assertEqual(result_for(report, "SCEN_2008_LEHMAN").simulated_pnl_usd, -132500.0)


class TestDrawdownLimitThreshold(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tester = CustomScenarioStressTester(max_allowed_drawdown_pct=20.0)
        self.positions = [AssetPosition("A", "EQUITY_SPOT", 1000000.0)]

    def test_loss_exactly_at_the_limit_passes(self):
        report = self.tester.run_multi_factor_stress_test(
            self.positions, custom_scenarios=[custom("AT", [FactorShock("EQUITY_SPOT", -0.20)])]
        )
        res = result_for(report, "AT")
        self.assertEqual(res.percentage_loss_pct, -20.0)
        self.assertFalse(res.is_drawdown_limit_breached)
        self.assertNotIn("AT", report.breached_scenario_ids)

    def test_breach_is_decided_before_display_rounding(self):
        # -20.004% rounds to -20.00 for display. 1.0.0 compared the rounded figure and
        # cleared the limit; the breach must be decided on the unrounded loss.
        report = self.tester.run_multi_factor_stress_test(
            self.positions,
            custom_scenarios=[custom("JUST_OVER", [FactorShock("EQUITY_SPOT", -0.20004)])],
        )
        res = result_for(report, "JUST_OVER")
        self.assertEqual(res.percentage_loss_pct, -20.0)
        self.assertTrue(res.is_drawdown_limit_breached)
        self.assertIn("JUST_OVER", report.breached_scenario_ids)

    def test_a_gain_never_breaches(self):
        report = self.tester.run_multi_factor_stress_test(
            self.positions, custom_scenarios=[custom("UP", [FactorShock("EQUITY_SPOT", 0.50)])]
        )
        res = result_for(report, "UP")
        self.assertEqual(res.simulated_pnl_usd, 500000.0)
        self.assertFalse(res.is_drawdown_limit_breached)

    def test_all_gain_run_still_names_a_worst_case(self):
        report = CustomScenarioStressTester().run_multi_factor_stress_test(
            [AssetPosition("V", "IMPLIED_VOL", 1000000.0)],
            custom_scenarios=[custom("VOL_UP", [FactorShock("IMPLIED_VOL", 0.5)])],
        )
        self.assertNotEqual(report.worst_case_scenario, "")
        self.assertEqual(report.breached_scenario_ids, [])


class TestFactorCoverageReporting(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tester = CustomScenarioStressTester(max_allowed_drawdown_pct=20.0)

    def test_partially_covered_book_is_flagged_not_silently_zeroed(self):
        report = self.tester.run_multi_factor_stress_test([
            AssetPosition("SPX", "EQUITY_SPOT", 800000.0),
            AssetPosition("GLD", "GOLD", 200000.0),
        ])
        self.assertEqual(report.status, STATUS_INCOMPLETE_COVERAGE)
        self.assertEqual(report.factors_never_shocked, ["GOLD"])
        self.assertEqual(report.value_never_shocked_usd, 200000.0)

        lehman = result_for(report, "SCEN_2008_LEHMAN")
        self.assertEqual(lehman.simulated_pnl_usd, -280000.0)  # 800k * -0.35
        self.assertEqual(lehman.unshocked_asset_ids, ["GLD"])
        self.assertEqual(lehman.unshocked_value_usd, 200000.0)
        self.assertIn("GOLD", report.audit_notes)

    def test_a_book_no_scenario_touches_raises_instead_of_reporting_all_clear(self):
        # 1.0.0 reported $0 loss, no breach and an empty worst-case scenario here, which
        # reads as a pass on a book that was never stressed. A typo in factor_name was
        # enough to trigger it.
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(
                [AssetPosition("AAPL", "EQUITY_SPOTT", 1000000.0)]
            )

    def test_fully_covered_book_reports_complete(self):
        report = self.tester.run_multi_factor_stress_test(
            [AssetPosition("SPX", "EQUITY_SPOT", 1000000.0)]
        )
        self.assertEqual(report.status, STATUS_COMPLETE)
        self.assertEqual(report.factors_never_shocked, [])
        self.assertEqual(report.value_never_shocked_usd, 0.0)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tester = CustomScenarioStressTester(max_allowed_drawdown_pct=20.0)

    def test_empty_positions_raises_error(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test([])

    def test_non_finite_position_inputs_raise(self):
        # Every comparison against NaN is False, so 1.0.0 propagated NaN into the report
        # and set is_drawdown_limit_breached to False.
        for bad in (float("nan"), float("inf"), float("-inf"), "1000", True, None):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.tester.run_multi_factor_stress_test(
                        [AssetPosition("A", "EQUITY_SPOT", bad)]
                    )
            with self.subTest(beta=bad):
                with self.assertRaises(ValueError):
                    self.tester.run_multi_factor_stress_test(
                        [AssetPosition("A", "EQUITY_SPOT", 1000.0, beta_to_factor=bad)]
                    )

    def test_nan_shock_raises(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(
                [AssetPosition("A", "EQUITY_SPOT", 1000.0)],
                custom_scenarios=[custom("NAN", [FactorShock("EQUITY_SPOT", float("nan"))])],
            )

    def test_blank_identifiers_raise(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test([AssetPosition("  ", "EQUITY_SPOT", 1000.0)])
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test([AssetPosition("A", "", 1000.0)])

    def test_zero_or_negative_capital_base_raises(self):
        for base in (0.0, -1.0, float("nan")):
            with self.subTest(base=base):
                with self.assertRaises(ValueError):
                    self.tester.run_multi_factor_stress_test(
                        [AssetPosition("A", "EQUITY_SPOT", 1000.0)], capital_base_usd=base
                    )

    def test_zero_value_long_only_book_raises(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test([AssetPosition("A", "EQUITY_SPOT", 0.0)])

    def test_invalid_drawdown_limit_raises(self):
        for limit in (0.0, -5.0, float("inf")):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    CustomScenarioStressTester(max_allowed_drawdown_pct=limit)

    def test_duplicate_scenario_id_raises(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(
                [AssetPosition("A", "EQUITY_SPOT", 1000.0)],
                custom_scenarios=[custom("SCEN_2008_LEHMAN", [FactorShock("EQUITY_SPOT", -0.1)])],
            )

    def test_duplicate_factor_within_one_scenario_raises(self):
        # A dict comprehension silently kept the last of the pair in 1.0.0, so a -30%
        # shock sitting beside a -5% typo applied the -5%.
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(
                [AssetPosition("A", "EQUITY_SPOT", 1000.0)],
                custom_scenarios=[
                    custom(
                        "DUP",
                        [FactorShock("EQUITY_SPOT", -0.30), FactorShock("EQUITY_SPOT", -0.05)],
                    )
                ],
            )

    def test_empty_shock_list_raises(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(
                [AssetPosition("A", "EQUITY_SPOT", 1000.0)], custom_scenarios=[custom("EMPTY", [])]
            )

    def test_relative_shock_below_minus_one_raises(self):
        # A multiplicative shock cannot take a price through zero; WTI did exactly that
        # on 20 April 2020, and that episode is outside this model.
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(
                [AssetPosition("CL", "CRUDE_OIL", 1000.0)],
                custom_scenarios=[custom("NEGOIL", [FactorShock("CRUDE_OIL", -1.5)])],
            )

    def test_wrong_types_raise(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(["not-a-position"])
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(
                [AssetPosition("A", "EQUITY_SPOT", 1000.0)], custom_scenarios=["nope"]
            )

    def test_a_bare_scenario_is_rejected_not_iterated(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(
                [AssetPosition("A", "EQUITY_SPOT", 1000.0)],
                custom_scenarios=custom("SOLO", [FactorShock("EQUITY_SPOT", -0.1)]),
            )

    def test_positions_given_as_a_generator_are_not_exhausted(self):
        # An exhausted generator would leave every scenario after the first with a $0
        # all-clear on a book that had already been consumed.
        report = self.tester.run_multi_factor_stress_test(
            (AssetPosition(sym, "EQUITY_SPOT", 500000.0) for sym in ("A", "B"))
        )
        self.assertEqual(report.capital_base_usd, 1000000.0)
        self.assertEqual(result_for(report, "SCEN_2008_LEHMAN").simulated_pnl_usd, -350000.0)
        self.assertEqual(result_for(report, "SCEN_2020_COVID").simulated_pnl_usd, -300000.0)

    def test_overflowing_pnl_raises_rather_than_reporting_infinity(self):
        with self.assertRaises(ValueError):
            self.tester.run_multi_factor_stress_test(
                [AssetPosition("A", "EQUITY_SPOT", 1e308, beta_to_factor=1e10)]
            )

    def test_no_nan_reaches_the_report(self):
        report = self.tester.run_multi_factor_stress_test(
            [AssetPosition("A", "EQUITY_SPOT", 1000000.0)]
        )
        for res in report.results:
            self.assertFalse(math.isnan(res.simulated_pnl_usd))
            self.assertFalse(math.isnan(res.percentage_loss_pct))


if __name__ == "__main__":
    unittest.main()
