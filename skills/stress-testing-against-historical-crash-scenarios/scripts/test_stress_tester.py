"""
Unit tests for stress-testing-against-historical-crash-scenarios.

Expected P&L figures are derived by hand in the comment beside each assertion rather
than re-derived from the implementation, so a test failing means the arithmetic moved,
not that the code disagrees with itself.

The tests in TestRegressionsAgainstV1 each fail against version 1.0.0 and pass here.
"""
import logging
import unittest

from stress_tester import (
    BUILTIN_SCENARIOS,
    FALLBACK_SYMBOL_KEY,
    STATUS_COMPLETE,
    STATUS_INCOMPLETE_COVERAGE,
    CrashScenario,
    HistoricalStressTester,
)

# The engine logs coverage and breach warnings at WARNING; silence them for test output.
logging.getLogger("stress_tester").setLevel(logging.CRITICAL)

NAN = float("nan")
INF = float("inf")


class TestStressPnlArithmetic(unittest.TestCase):
    """Normal-case revaluation, worst-case selection, and the loss percentage."""

    def test_stress_pnl_matches_hand_calculation(self):
        scenario = CrashScenario(
            name="TEST_CRASH",
            description="Test scenario",
            asset_returns={"AAPL": -0.20, "MSFT": -0.10, FALLBACK_SYMBOL_KEY: -0.15},
        )
        tester = HistoricalStressTester(max_stressed_loss_pct=0.50, scenarios=[scenario])

        # AAPL: 100 * 150 = 15,000 -> 15,000 * -0.20 = -3,000
        # MSFT: 200 * 300 = 60,000 -> 60,000 * -0.10 = -6,000
        # Total -9,000 over a 75,000 NAV = -12.00%
        report = tester.run_stress_test(
            {"AAPL": 100, "MSFT": 200}, {"AAPL": 150.0, "MSFT": 300.0}, 75000.0
        )

        self.assertEqual(len(report.results), 1)
        self.assertAlmostEqual(report.results[0].per_asset_impact["AAPL"], -3000.0, places=6)
        self.assertAlmostEqual(report.results[0].per_asset_impact["MSFT"], -6000.0, places=6)
        self.assertAlmostEqual(report.results[0].stressed_pnl_usd, -9000.0, places=6)
        self.assertAlmostEqual(report.worst_loss_pct, 0.12, places=6)
        self.assertAlmostEqual(report.worst_loss_usd, 9000.0, places=6)
        self.assertAlmostEqual(report.worst_pnl_usd, -9000.0, places=6)
        self.assertFalse(report.threshold_breached)
        self.assertIsNone(report.breach_reason)
        self.assertEqual(report.status, STATUS_COMPLETE)

    def test_short_position_loses_when_the_market_rallies(self):
        """A signed short quantity flips the sign of the impact."""
        scenario = CrashScenario("RALLY", "Melt-up", {"SPY": 0.25})
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15, scenarios=[scenario])

        # -1,000 * 100 = -100,000 exposure -> -100,000 * +0.25 = -25,000, over 100,000 NAV
        report = tester.run_stress_test({"SPY": -1000}, {"SPY": 100.0}, 100000.0)

        self.assertAlmostEqual(report.worst_pnl_usd, -25000.0, places=6)
        self.assertAlmostEqual(report.worst_loss_pct, 0.25, places=6)
        self.assertTrue(report.threshold_breached)

    def test_worst_scenario_is_the_most_adverse_not_the_largest_magnitude(self):
        scenarios = [
            CrashScenario("MILD", "Mild", {"AAPL": -0.05, FALLBACK_SYMBOL_KEY: -0.05}),
            CrashScenario("SEVERE", "Severe", {"AAPL": -0.40, FALLBACK_SYMBOL_KEY: -0.40}),
            CrashScenario("MODERATE", "Moderate", {"AAPL": -0.15, FALLBACK_SYMBOL_KEY: -0.15}),
        ]
        tester = HistoricalStressTester(max_stressed_loss_pct=0.50, scenarios=scenarios)

        # 100 * 100 = 10,000, fully invested against a 10,000 NAV, so pct == shock.
        report = tester.run_stress_test({"AAPL": 100}, {"AAPL": 100.0}, 10000.0)

        self.assertEqual(report.worst_scenario, "SEVERE")
        self.assertAlmostEqual(report.worst_loss_pct, 0.40, places=6)
        self.assertEqual(len(report.results), 3)

    def test_hedge_leg_offsets_the_equity_leg(self):
        """A scenario in which one leg gains nets against the other."""
        scenario = CrashScenario("CRASH", "Crash", {"SPY": -0.30, "TLT": 0.15})
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15, scenarios=[scenario])

        # SPY: 800 * 100 = 80,000 -> -24,000 ; TLT: 200 * 100 = 20,000 -> +3,000
        # Net -21,000 over 100,000 NAV = -21.00%
        report = tester.run_stress_test(
            {"SPY": 800, "TLT": 200}, {"SPY": 100.0, "TLT": 100.0}, 100000.0
        )

        self.assertAlmostEqual(report.results[0].per_asset_impact["TLT"], 3000.0, places=6)
        self.assertAlmostEqual(report.worst_pnl_usd, -21000.0, places=6)
        self.assertAlmostEqual(report.worst_loss_pct, 0.21, places=6)
        self.assertTrue(report.threshold_breached)

    def test_shocked_value_reports_gross_not_net_exposure(self):
        scenario = CrashScenario("CRASH", "Crash", {"SPY": -0.30, "QQQ": -0.30})
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15, scenarios=[scenario])

        # Long 50,000 SPY and short 20,000 QQQ -> gross shocked value 70,000.
        report = tester.run_stress_test(
            {"SPY": 500, "QQQ": -200}, {"SPY": 100.0, "QQQ": 100.0}, 100000.0
        )
        self.assertAlmostEqual(report.results[0].shocked_value_usd, 70000.0, places=6)


class TestGateThreshold(unittest.TestCase):
    """The stressed-loss gate: exact boundary, direction, and the breach message."""

    def _tester(self, shock, limit):
        return HistoricalStressTester(
            max_stressed_loss_pct=limit,
            scenarios=[CrashScenario("S", "S", {"SPY": shock})],
        )

    def test_breach_detected_and_reported(self):
        # 1,000 * 100 = 100,000 -> -50,000 over 100,000 NAV = -50.00% against a 15% limit.
        report = self._tester(-0.50, 0.15).run_stress_test(
            {"SPY": 1000}, {"SPY": 100.0}, 100000.0
        )
        self.assertTrue(report.threshold_breached)
        self.assertIn("STRESS TEST BREACH", report.breach_reason)
        self.assertIn("50.00% loss", report.breach_reason)
        self.assertIn("$50,000.00", report.breach_reason)
        self.assertAlmostEqual(report.worst_loss_pct, 0.50, places=6)

    def test_loss_exactly_at_the_threshold_breaches(self):
        """The comparison is `>=`, so at-limit is a breach. Pinned deliberately."""
        report = self._tester(-0.15, 0.15).run_stress_test(
            {"SPY": 1000}, {"SPY": 100.0}, 100000.0
        )
        self.assertTrue(report.threshold_breached)

    def test_loss_just_inside_the_threshold_passes(self):
        report = self._tester(-0.1499, 0.15).run_stress_test(
            {"SPY": 1000}, {"SPY": 100.0}, 100000.0
        )
        self.assertFalse(report.threshold_breached)
        self.assertIsNone(report.breach_reason)


class TestCoverageReporting(unittest.TestCase):
    """Positions the replay could not reach must never be invisible."""

    def test_unpriced_position_is_reported_not_silently_dropped(self):
        tester = HistoricalStressTester(
            max_stressed_loss_pct=0.15,
            scenarios=[CrashScenario("S", "S", {FALLBACK_SYMBOL_KEY: -0.30})],
        )
        report = tester.run_stress_test(
            {"SPY": 1000, "ILLIQ": 5000}, {"SPY": 100.0}, 100000.0
        )
        self.assertEqual(report.unpriced_symbols, ["ILLIQ"])
        self.assertEqual(report.status, STATUS_INCOMPLETE_COVERAGE)
        self.assertNotIn("ILLIQ", report.results[0].per_asset_impact)

    def test_symbol_no_scenario_names_is_unshocked_not_assigned_a_default(self):
        """With no DEFAULT entry the symbol contributes nothing and is named."""
        tester = HistoricalStressTester(
            max_stressed_loss_pct=0.15,
            scenarios=[CrashScenario("S", "S", {"SPY": -0.50})],
        )
        # Only SPY is shocked: 1,000 * 100 * -0.50 = -50,000. FOO contributes nothing.
        report = tester.run_stress_test(
            {"SPY": 1000, "FOO": 100}, {"SPY": 100.0, "FOO": 50.0}, 100000.0
        )
        self.assertEqual(report.unshocked_symbols, ["FOO"])
        self.assertEqual(report.results[0].unshocked_symbols, ["FOO"])
        self.assertEqual(report.status, STATUS_INCOMPLETE_COVERAGE)
        self.assertAlmostEqual(report.results[0].stressed_pnl_usd, -50000.0, places=6)

    def test_default_priced_symbols_are_flagged_as_fallback(self):
        tester = HistoricalStressTester(
            max_stressed_loss_pct=0.15,
            scenarios=[CrashScenario("S", "S", {"SPY": -0.50, FALLBACK_SYMBOL_KEY: -0.30})],
        )
        report = tester.run_stress_test(
            {"SPY": 100, "OBSCURE": 100}, {"SPY": 100.0, "OBSCURE": 100.0}, 100000.0
        )
        self.assertEqual(report.fallback_symbols, ["OBSCURE"])
        self.assertEqual(report.results[0].fallback_symbols, ["OBSCURE"])
        self.assertEqual(report.status, STATUS_INCOMPLETE_COVERAGE)

    def test_fully_covered_book_reports_complete(self):
        tester = HistoricalStressTester(
            max_stressed_loss_pct=0.15,
            scenarios=[CrashScenario("S", "S", {"SPY": -0.10})],
        )
        report = tester.run_stress_test({"SPY": 100}, {"SPY": 100.0}, 100000.0)
        self.assertEqual(report.status, STATUS_COMPLETE)
        self.assertEqual(report.unpriced_symbols, [])
        self.assertEqual(report.unshocked_symbols, [])
        self.assertEqual(report.fallback_symbols, [])

    def test_book_with_no_priceable_position_raises(self):
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15)
        with self.assertRaises(ValueError):
            tester.run_stress_test({"ILLIQ": 100, "OTHER": 50}, {}, 100000.0)

    def test_flat_positions_are_skipped_without_breaking_coverage(self):
        tester = HistoricalStressTester(
            max_stressed_loss_pct=0.15,
            scenarios=[CrashScenario("S", "S", {"SPY": -0.10})],
        )
        report = tester.run_stress_test(
            {"SPY": 100, "FLAT": 0.0}, {"SPY": 100.0}, 100000.0
        )
        # FLAT holds nothing, so its absent price is not a coverage gap.
        self.assertEqual(report.unpriced_symbols, [])
        self.assertEqual(report.status, STATUS_COMPLETE)


class TestInputValidation(unittest.TestCase):
    """Corrupt input must raise, never produce a report that reads as an all-clear."""

    def setUp(self):
        self.tester = HistoricalStressTester(
            max_stressed_loss_pct=0.15,
            scenarios=[CrashScenario("S", "S", {FALLBACK_SYMBOL_KEY: -0.30})],
        )

    def test_non_finite_price_raises(self):
        for bad in (NAN, INF, -INF):
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    self.tester.run_stress_test({"SPY": 100}, {"SPY": bad}, 100000.0)

    def test_non_finite_quantity_raises(self):
        for bad in (NAN, INF, -INF):
            with self.subTest(quantity=bad):
                with self.assertRaises(ValueError):
                    self.tester.run_stress_test({"SPY": bad}, {"SPY": 100.0}, 100000.0)

    def test_non_numeric_and_bool_inputs_raise(self):
        for bad in ("100", b"100", None, True, [1], {"a": 1}, object()):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.tester.run_stress_test({"SPY": bad}, {"SPY": 100.0}, 100000.0)
                with self.assertRaises(ValueError):
                    self.tester.run_stress_test({"SPY": 100}, {"SPY": bad}, 100000.0)

    def test_decimal_and_other_float_convertible_numerics_are_accepted(self):
        """
        Quantities and prices routinely arrive as Decimal or numpy scalars from the data
        layer; rejecting them would force a conversion at every call site.
        """
        from decimal import Decimal

        # 100 * 150 = 15,000 -> -30% -> -4,500 over a 100,000 NAV.
        report = self.tester.run_stress_test(
            {"SPY": Decimal("100")}, {"SPY": Decimal("150")}, 100000.0
        )
        self.assertAlmostEqual(report.worst_pnl_usd, -4500.0, places=6)

    def test_pnl_overflowing_to_infinity_raises(self):
        """
        Individually finite inputs can still overflow. An infinite loss would otherwise
        format as "inf% loss ($inf)" in the breach reason.
        """
        with self.assertRaises(ValueError):
            self.tester.run_stress_test({"SPY": 1e308}, {"SPY": 1e308}, 1e6)

    def test_non_positive_or_non_finite_nav_raises(self):
        for bad in (0.0, -1.0, NAN, INF):
            with self.subTest(nav=bad):
                with self.assertRaises(ValueError):
                    self.tester.run_stress_test({"SPY": 100}, {"SPY": 100.0}, bad)

    def test_empty_positions_raises(self):
        with self.assertRaises(ValueError):
            self.tester.run_stress_test({}, {}, 100000.0)

    def test_blank_and_reserved_position_keys_raise(self):
        for bad_key in ("", "   ", FALLBACK_SYMBOL_KEY):
            with self.subTest(key=bad_key):
                with self.assertRaises(ValueError):
                    self.tester.run_stress_test({bad_key: 100}, {bad_key: 100.0}, 100000.0)

    def test_non_dict_positions_or_prices_raise(self):
        with self.assertRaises(ValueError):
            self.tester.run_stress_test([("SPY", 100)], {"SPY": 100.0}, 100000.0)
        with self.assertRaises(ValueError):
            self.tester.run_stress_test({"SPY": 100}, [("SPY", 100.0)], 100000.0)


class TestConstructorValidation(unittest.TestCase):
    """A malformed scenario library must be rejected at construction."""

    def test_non_positive_or_non_finite_threshold_raises(self):
        for bad in (0.0, -0.1, NAN, INF):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    HistoricalStressTester(max_stressed_loss_pct=bad)

    def test_empty_scenario_library_raises(self):
        with self.assertRaises(ValueError):
            HistoricalStressTester(max_stressed_loss_pct=0.15, scenarios=[])

    def test_duplicate_scenario_name_raises(self):
        with self.assertRaises(ValueError):
            HistoricalStressTester(
                max_stressed_loss_pct=0.15,
                scenarios=[
                    CrashScenario("S", "a", {"SPY": -0.10}),
                    CrashScenario("S", "b", {"SPY": -0.40}),
                ],
            )

    def test_scenario_with_no_shocks_raises(self):
        with self.assertRaises(ValueError):
            HistoricalStressTester(
                max_stressed_loss_pct=0.15, scenarios=[CrashScenario("S", "S", {})]
            )

    def test_non_finite_shock_raises(self):
        for bad in (NAN, INF, -INF):
            with self.subTest(shock=bad):
                with self.assertRaises(ValueError):
                    HistoricalStressTester(
                        max_stressed_loss_pct=0.15,
                        scenarios=[CrashScenario("S", "S", {"SPY": bad})],
                    )

    def test_shock_below_minus_one_raises(self):
        """A return shock cannot take a price through zero."""
        with self.assertRaises(ValueError):
            HistoricalStressTester(
                max_stressed_loss_pct=0.15,
                scenarios=[CrashScenario("S", "S", {"CL": -1.5})],
            )
        # -1.0 (a total wipeout) is legitimate and must be accepted.
        HistoricalStressTester(
            max_stressed_loss_pct=0.15,
            scenarios=[CrashScenario("S", "S", {"XYZ": -1.0})],
        )

    def test_wrongly_typed_scenario_raises(self):
        with self.assertRaises(ValueError):
            HistoricalStressTester(
                max_stressed_loss_pct=0.15,
                scenarios=[{"name": "S", "asset_returns": {"SPY": -0.1}}],
            )

    def test_blank_scenario_name_raises(self):
        with self.assertRaises(ValueError):
            HistoricalStressTester(
                max_stressed_loss_pct=0.15,
                scenarios=[CrashScenario("  ", "S", {"SPY": -0.1})],
            )

    def test_caller_scenario_list_is_copied(self):
        """Mutating the caller's list after construction must not change the library."""
        library = [CrashScenario("S", "S", {"SPY": -0.10})]
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15, scenarios=library)
        library.append(CrashScenario("T", "T", {"SPY": -0.90}))
        report = tester.run_stress_test({"SPY": 100}, {"SPY": 100.0}, 100000.0)
        self.assertEqual(len(report.results), 1)


class TestBuiltinScenarioLibrary(unittest.TestCase):
    """Provenance and historical plausibility of the shipped scenarios."""

    def test_all_builtins_carry_a_dated_window_and_a_basis(self):
        for scenario in BUILTIN_SCENARIOS:
            with self.subTest(scenario=scenario.name):
                self.assertTrue(scenario.window_start, "window_start must be recorded")
                self.assertTrue(scenario.window_end, "window_end must be recorded")
                self.assertTrue(scenario.basis, "price basis must be recorded")
                self.assertTrue(scenario.calibration_note)

    def test_no_builtin_scenario_shocks_a_security_that_did_not_yet_trade(self):
        """
        Tesla listed 2010-06-29 and Meta (as Facebook) 2012-05-18. Version 1.0.0's
        2008_GFC scenario assigned TSLA -80% and META -50% -- returns for securities
        that did not exist in the window.
        """
        listing_dates = {"TSLA": "2010-06-29", "META": "2012-05-18"}
        for scenario in BUILTIN_SCENARIOS:
            for symbol, listed_on in listing_dates.items():
                if symbol in scenario.asset_returns:
                    with self.subTest(scenario=scenario.name, symbol=symbol):
                        self.assertGreaterEqual(
                            scenario.window_start, listed_on,
                            f"{scenario.name} shocks {symbol}, which did not trade until {listed_on}",
                        )

    def test_builtin_windows_are_ordered_and_shocks_are_in_range(self):
        for scenario in BUILTIN_SCENARIOS:
            with self.subTest(scenario=scenario.name):
                self.assertLessEqual(scenario.window_start, scenario.window_end)
                for symbol, shock in scenario.asset_returns.items():
                    self.assertGreaterEqual(shock, -1.0, f"{scenario.name}/{symbol}")

    def test_builtin_library_constructs_and_runs(self):
        tester = HistoricalStressTester()
        # 1,000 * 100 = 100,000 in SPY against a 100,000 NAV: worst builtin SPY shock is
        # the 2008 -0.5190, so the loss is exactly 51.90%.
        report = tester.run_stress_test({"SPY": 1000}, {"SPY": 100.0}, 100000.0)
        self.assertEqual(report.worst_scenario, "2008_GFC")
        self.assertAlmostEqual(report.worst_loss_pct, 0.5190, places=6)
        self.assertTrue(report.threshold_breached)


class TestRegressionsAgainstV1(unittest.TestCase):
    """Each of these fails against version 1.0.0 and passes here."""

    def test_scenario_gain_is_never_reported_as_a_loss(self):
        """
        1.0.0 computed worst_loss_pct = abs(worst.stressed_pnl_pct). A book short SPY
        gains in every crash scenario; 1.0.0 reported the smallest gain as a loss of the
        same size and, above the limit, fired the gate on a profitable book.
        """
        scenarios = [
            CrashScenario("GFC", "GFC", {"SPY": -0.52}),
            CrashScenario("COVID", "COVID", {"SPY": -0.34}),
        ]
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15, scenarios=scenarios)

        # Short 1,000 * 100 = -100,000 exposure. COVID: -100,000 * -0.34 = +34,000.
        # Every scenario is a gain, so the worst-case loss is zero.
        report = tester.run_stress_test({"SPY": -1000}, {"SPY": 100.0}, 100000.0)

        self.assertAlmostEqual(report.worst_pnl_usd, 34000.0, places=6)   # 1.0.0: +34,000
        self.assertEqual(report.worst_loss_pct, 0.0)                      # 1.0.0: 0.34
        self.assertEqual(report.worst_loss_usd, 0.0)                      # 1.0.0: 34,000
        self.assertFalse(report.threshold_breached)                       # 1.0.0: True
        self.assertIsNone(report.breach_reason)

    def test_nan_price_raises_instead_of_clearing_the_gate(self):
        """
        1.0.0 propagated NaN into worst_loss_pct; `nan >= 0.15` is False, so the report
        read threshold_breached=False with breach_reason=None on corrupt input.
        """
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15)
        with self.assertRaises(ValueError):
            tester.run_stress_test({"SPY": 1000}, {"SPY": NAN}, 100000.0)

    def test_unpriced_position_no_longer_produces_a_silent_understatement(self):
        """1.0.0 skipped unpriced positions with `continue` and reported nothing."""
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15)
        report = tester.run_stress_test(
            {"SPY": 100, "ILLIQ": 100000}, {"SPY": 100.0}, 100000.0
        )
        self.assertEqual(report.unpriced_symbols, ["ILLIQ"])   # 1.0.0: no such field
        self.assertEqual(report.status, STATUS_INCOMPLETE_COVERAGE)

    def test_missing_symbol_is_not_assigned_a_hard_coded_minus_30_percent(self):
        """
        1.0.0's _get_scenario_return fell back to a literal -0.30 when the scenario had
        no DEFAULT, putting an unsourced magnitude into a report labelled as history.
        """
        tester = HistoricalStressTester(
            max_stressed_loss_pct=0.15,
            scenarios=[CrashScenario("S", "S", {"SPY": -0.10})],
        )
        # 1.0.0: OBSCURE 1,000 * 100 * -0.30 = -30,000 and a breach. Now: unshocked, $0.
        report = tester.run_stress_test(
            {"OBSCURE": 1000}, {"OBSCURE": 100.0}, 100000.0
        )
        self.assertEqual(report.unshocked_symbols, ["OBSCURE"])
        self.assertAlmostEqual(report.worst_pnl_usd, 0.0, places=6)
        self.assertFalse(report.threshold_breached)
        self.assertEqual(report.status, STATUS_INCOMPLETE_COVERAGE)

    def test_empty_book_raises_instead_of_returning_a_vacuous_all_clear(self):
        """1.0.0 returned worst_scenario=<first scenario>, 0.0% loss, no breach."""
        tester = HistoricalStressTester(max_stressed_loss_pct=0.15)
        with self.assertRaises(ValueError):
            tester.run_stress_test({}, {}, 100000.0)

    def test_2008_scenario_does_not_shock_securities_that_had_not_listed(self):
        """1.0.0's 2008_GFC carried TSLA -0.80 and META -0.50."""
        gfc = next(s for s in BUILTIN_SCENARIOS if s.name == "2008_GFC")
        self.assertNotIn("TSLA", gfc.asset_returns)
        self.assertNotIn("META", gfc.asset_returns)


if __name__ == "__main__":
    unittest.main()
