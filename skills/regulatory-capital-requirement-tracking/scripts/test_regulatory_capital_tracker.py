"""Unit tests for the regulatory capital adequacy engine.

Expected values are derived by hand from the regulatory text (see
``references/standards.md``), not by re-running the implementation's own
arithmetic. Several tests are explicit regressions against version 1 defects and
say so in their docstrings.
"""

import logging
import math
import unittest

from regulatory_capital_tracker import (
    AGGREGATION_GREATER_OF,
    AGGREGATION_SUM,
    STATUS_CAPITAL_DEFICIT,
    STATUS_COMPLIANT,
    STATUS_WARNING_BUFFER_BREACHED,
    CapitalComponents,
    CapitalInputError,
    CapitalRequirementSpec,
    RegulatoryCapitalTrackerEngine,
)


def _sec_spec(**overrides):
    """A carrying broker-dealer: 15c3-1(a)(2)(i) USD 250,000 dollar minimum and
    a hand-computed 15c3-1(a)(1)(i) ratio requirement of USD 300,000."""
    kwargs = dict(
        jurisdiction="SEC_15C3_1",
        requirement_components={
            "MINIMUM_DOLLAR_15c3-1(a)(2)(i)": 250_000.0,
            "RATIO_REQ_15c3-1(a)(1)(i)": 300_000.0,
        },
    )
    kwargs.update(overrides)
    return CapitalRequirementSpec(**kwargs)


class TestNetCapitalComputation(unittest.TestCase):
    """17 CFR 240.15c3-1(c)(2): net worth, adjusted."""

    def setUp(self):
        self.engine = RegulatoryCapitalTrackerEngine(_sec_spec())

    def test_net_capital_follows_15c3_1_c_2(self):
        # net worth      = 1,000,000 - 500,000 = 500,000
        # + sub debt     = 500,000 + 100,000   = 600,000   (c)(2)(ii)
        # - non-allowable= 600,000 -  50,000   = 550,000   (c)(2)(iv)
        # - haircuts     = 550,000 -  40,000   = 510,000   (c)(2)(vi)
        components = CapitalComponents(
            total_assets=1_000_000.0,
            total_liabilities=500_000.0,
            non_allowable_assets=50_000.0,
            securities_haircuts=40_000.0,
            qualifying_subordinated_debt=100_000.0,
        )
        self.assertEqual(self.engine.calculate_net_capital(components), 510_000.0)

    def test_haircuts_reduce_capital_rather_than_raise_the_requirement(self):
        """Regression: v1 had no haircut input and callers were told to add
        haircuts to the requirement instead. Under 15c3-1(c)(2)(vi) a haircut is
        a deduction from net capital, so it must move net capital, not the floor.
        """
        base = dict(total_assets=800_000.0, total_liabilities=400_000.0)
        without = self.engine.evaluate_capital_adequacy(CapitalComponents(**base))
        with_haircut = self.engine.evaluate_capital_adequacy(
            CapitalComponents(securities_haircuts=75_000.0, **base)
        )
        self.assertEqual(without.net_capital_available, 400_000.0)
        self.assertEqual(with_haircut.net_capital_available, 325_000.0)
        # The floor is untouched by the haircut.
        self.assertEqual(without.total_capital_required, with_haircut.total_capital_required)

    def test_net_capital_may_be_negative(self):
        """A firm can be deficient past zero; the size of the hole matters."""
        components = CapitalComponents(total_assets=100_000.0, total_liabilities=450_000.0)
        self.assertEqual(self.engine.calculate_net_capital(components), -350_000.0)


class TestRequirementAggregation(unittest.TestCase):
    """15c3-1(a) 'the greater of'; MIFIDPRU 4.3.2R 'the highest of'."""

    def test_greater_of_picks_the_binding_component_not_the_sum(self):
        """Regression: v1 computed base + variable. For a MIFIDPRU non-SNI firm
        with PMR 750,000, FOR 420,000 and KFR 310,000 the requirement is
        750,000 (4.3.2R), not 1,480,000."""
        engine = RegulatoryCapitalTrackerEngine(
            CapitalRequirementSpec(
                jurisdiction="FCA_MIFIDPRU",
                requirement_components={"PMR": 750_000.0, "FOR": 420_000.0, "KFR": 310_000.0},
            )
        )
        required, binding = engine.calculate_required_capital()
        self.assertEqual(required, 750_000.0)
        self.assertEqual(binding, "PMR")

    def test_sum_aggregation_is_available_but_opt_in(self):
        """Stacked regimes (a Basel minimum plus a conservation buffer) are
        additive; that behaviour must be asked for explicitly."""
        engine = RegulatoryCapitalTrackerEngine(
            CapitalRequirementSpec(
                jurisdiction="STACKED_EXAMPLE",
                requirement_components={"MINIMUM": 800_000.0, "BUFFER": 250_000.0},
                aggregation=AGGREGATION_SUM,
            )
        )
        required, binding = engine.calculate_required_capital()
        self.assertEqual(required, 1_050_000.0)
        self.assertEqual(binding, "SUM_OF_ALL_COMPONENTS")

    def test_summed_components_overflowing_to_infinity_are_rejected(self):
        """Each component is finite; their sum is not. An infinite floor would
        fail every firm for a reason unrelated to its balance sheet."""
        engine = RegulatoryCapitalTrackerEngine(
            CapitalRequirementSpec(
                jurisdiction="OVERFLOW",
                requirement_components={"A": 1e308, "B": 1e308},
                aggregation=AGGREGATION_SUM,
            )
        )
        with self.assertRaises(CapitalInputError):
            engine.calculate_required_capital()

    def test_greater_of_is_the_default(self):
        self.assertEqual(_sec_spec().aggregation, AGGREGATION_GREATER_OF)

    def test_tie_breaks_deterministically_by_component_name(self):
        engine = RegulatoryCapitalTrackerEngine(
            CapitalRequirementSpec(
                jurisdiction="TIE",
                requirement_components={"ZULU": 500_000.0, "ALPHA": 500_000.0},
            )
        )
        self.assertEqual(engine.calculate_required_capital(), (500_000.0, "ALPHA"))


class TestStatusClassification(unittest.TestCase):
    """Requirement 300,000; early-warning line 300,000 x 1.20 = 360,000."""

    def setUp(self):
        self.engine = RegulatoryCapitalTrackerEngine(_sec_spec())

    def _report_for_net_capital(self, net_capital):
        """Build a balance sheet yielding exactly `net_capital` in net worth."""
        return self.engine.evaluate_capital_adequacy(
            CapitalComponents(total_assets=1_000_000.0, total_liabilities=1_000_000.0 - net_capital)
        )

    def test_compliant_above_the_warning_line(self):
        report = self._report_for_net_capital(550_000.0)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertTrue(report.is_compliant)
        self.assertFalse(report.is_warning)
        self.assertEqual(report.capital_headroom, 250_000.0)
        self.assertEqual(report.early_warning_threshold, 360_000.0)
        self.assertAlmostEqual(report.capital_ratio, 550_000.0 / 300_000.0, places=12)
        self.assertIsNone(report.regulatory_notice)

    def test_warning_band_between_floor_and_120_percent(self):
        report = self._report_for_net_capital(320_000.0)
        self.assertEqual(report.status, STATUS_WARNING_BUFFER_BREACHED)
        self.assertTrue(report.is_compliant)
        self.assertTrue(report.is_warning)
        self.assertEqual(report.capital_headroom, 20_000.0)

    def test_deficit_below_the_floor(self):
        report = self._report_for_net_capital(150_000.0)
        self.assertEqual(report.status, STATUS_CAPITAL_DEFICIT)
        self.assertFalse(report.is_compliant)
        self.assertFalse(report.is_warning)
        self.assertEqual(report.capital_headroom, -150_000.0)

    def test_exactly_at_the_floor_is_a_warning_not_a_deficit(self):
        """15c3-1(a) requires net capital 'no less than' the requirement, so
        equality is compliant -- but it is below 120%, so it warns."""
        report = self._report_for_net_capital(300_000.0)
        self.assertEqual(report.status, STATUS_WARNING_BUFFER_BREACHED)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.capital_headroom, 0.0)

    def test_exactly_at_120_percent_is_compliant(self):
        """17a-11(b)(3) triggers when net capital 'is less than 120 percent',
        so exactly 360,000 does not trigger."""
        report = self._report_for_net_capital(360_000.0)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertFalse(report.is_warning)

    def test_one_cent_below_the_floor_is_a_deficit(self):
        """Regression: v1 rounded net capital to 2dp before comparing, so a
        sub-cent shortfall could round into compliance. Comparison is now exact
        and only the audit string is formatted."""
        report = self._report_for_net_capital(299_999.99)
        self.assertEqual(report.status, STATUS_CAPITAL_DEFICIT)
        self.assertLess(report.capital_headroom, 0.0)

    def test_binding_component_is_reported(self):
        report = self._report_for_net_capital(550_000.0)
        self.assertEqual(report.binding_component, "RATIO_REQ_15c3-1(a)(1)(i)")
        self.assertEqual(report.total_capital_required, 300_000.0)


class TestNotificationObligations(unittest.TestCase):
    """17 CFR 240.17a-11: same-day on deficiency, 24 hours at 120%."""

    def setUp(self):
        self.engine = RegulatoryCapitalTrackerEngine(_sec_spec())

    def test_deficit_surfaces_same_day_notice(self):
        report = self.engine.evaluate_capital_adequacy(
            CapitalComponents(total_assets=400_000.0, total_liabilities=300_000.0)
        )
        self.assertEqual(report.status, STATUS_CAPITAL_DEFICIT)
        self.assertIn("17a-11(a)(1)", report.regulatory_notice)
        self.assertIn("SAME DAY", report.regulatory_notice)

    def test_warning_surfaces_24_hour_notice(self):
        report = self.engine.evaluate_capital_adequacy(
            CapitalComponents(total_assets=1_000_000.0, total_liabilities=680_000.0)
        )
        self.assertEqual(report.status, STATUS_WARNING_BUFFER_BREACHED)
        self.assertIn("17a-11(b)(3)", report.regulatory_notice)
        self.assertIn("24 HOURS", report.regulatory_notice)

    def test_unmapped_jurisdiction_yields_no_notice_text(self):
        """None means 'this module has no mapping', not 'nothing is due'."""
        engine = RegulatoryCapitalTrackerEngine(
            CapitalRequirementSpec(
                jurisdiction="FCA_MIFIDPRU", requirement_components={"PMR": 750_000.0}
            )
        )
        report = engine.evaluate_capital_adequacy(
            CapitalComponents(total_assets=100_000.0, total_liabilities=0.0)
        )
        self.assertEqual(report.status, STATUS_CAPITAL_DEFICIT)
        self.assertIsNone(report.regulatory_notice)


class TestInputValidation(unittest.TestCase):

    def test_nan_balance_sheet_input_is_rejected(self):
        """NaN compares False against everything, so an unguarded NaN net
        capital would classify as a deficit by accident rather than by fact."""
        with self.assertRaises(CapitalInputError):
            CapitalComponents(total_assets=float("nan"), total_liabilities=0.0)

    def test_infinite_input_is_rejected(self):
        with self.assertRaises(CapitalInputError):
            CapitalComponents(total_assets=float("inf"), total_liabilities=0.0)

    def test_negative_deduction_is_rejected(self):
        """A negative deduction adds phantom capital."""
        with self.assertRaises(CapitalInputError):
            CapitalComponents(
                total_assets=100_000.0, total_liabilities=0.0, securities_haircuts=-10_000.0
            )

    def test_negative_liabilities_are_rejected(self):
        with self.assertRaises(CapitalInputError):
            CapitalComponents(total_assets=100_000.0, total_liabilities=-1.0)

    def test_non_allowable_assets_exceeding_total_assets_are_rejected(self):
        with self.assertRaises(CapitalInputError):
            CapitalComponents(
                total_assets=100_000.0, total_liabilities=0.0, non_allowable_assets=150_000.0
            )

    def test_numeric_string_input_is_rejected(self):
        """float("1e6") parses and float("1,000,000") does not; accepting either
        would make validation depend on CSV formatting."""
        with self.assertRaises(CapitalInputError):
            CapitalComponents(total_assets="1000000", total_liabilities=0.0)

    def test_bool_input_is_rejected(self):
        """True would otherwise be a valid currency amount of 1."""
        with self.assertRaises(CapitalInputError):
            CapitalComponents(total_assets=True, total_liabilities=0.0)

    def test_empty_requirement_components_are_rejected(self):
        with self.assertRaises(CapitalInputError):
            CapitalRequirementSpec(jurisdiction="SEC_15C3_1", requirement_components={})

    def test_zero_requirement_component_is_rejected(self):
        """v1 returned a 999.0 sentinel ratio for a zero requirement. A zero
        component almost always means the caller failed to compute it, and under
        greater-of it would vanish without trace."""
        with self.assertRaises(CapitalInputError):
            CapitalRequirementSpec(
                jurisdiction="SEC_15C3_1", requirement_components={"PMR": 0.0}
            )

    def test_negative_requirement_component_is_rejected(self):
        with self.assertRaises(CapitalInputError):
            CapitalRequirementSpec(
                jurisdiction="SEC_15C3_1", requirement_components={"PMR": -250_000.0}
            )

    def test_nan_requirement_component_is_rejected(self):
        with self.assertRaises(CapitalInputError):
            CapitalRequirementSpec(
                jurisdiction="SEC_15C3_1", requirement_components={"PMR": float("nan")}
            )

    def test_blank_jurisdiction_is_rejected(self):
        with self.assertRaises(CapitalInputError):
            CapitalRequirementSpec(jurisdiction="   ", requirement_components={"PMR": 1.0})

    def test_unknown_aggregation_is_rejected(self):
        with self.assertRaises(CapitalInputError):
            _sec_spec(aggregation="AVERAGE")

    def test_early_warning_below_one_is_rejected(self):
        """A warning line under the floor leaves an empty warning band, so the
        tool would silently never warn before a breach."""
        with self.assertRaises(CapitalInputError):
            _sec_spec(early_warning_pct=0.9)

    def test_early_warning_of_exactly_one_is_allowed(self):
        spec = _sec_spec(early_warning_pct=1.0)
        self.assertEqual(spec.early_warning_pct, 1.0)

    def test_engine_requires_an_explicit_spec(self):
        """Regression: v1 defaulted to the USD 250,000 carrying-broker minimum
        of 15c3-1(a)(2)(i) for every caller, including introducing brokers whose
        minimum is USD 50,000 under (a)(2)(iv)."""
        with self.assertRaises(TypeError):
            RegulatoryCapitalTrackerEngine()
        with self.assertRaises(CapitalInputError):
            RegulatoryCapitalTrackerEngine(spec=None)

    def test_engine_rejects_non_components_input(self):
        engine = RegulatoryCapitalTrackerEngine(_sec_spec())
        with self.assertRaises(CapitalInputError):
            engine.calculate_net_capital({"total_assets": 1.0})

    def test_requirement_components_are_defensively_copied(self):
        """Mutating the caller's dict after construction must not move the
        firm's regulatory floor."""
        source = {"PMR": 750_000.0}
        spec = CapitalRequirementSpec(jurisdiction="FCA_MIFIDPRU", requirement_components=source)
        source["PMR"] = 1.0
        engine = RegulatoryCapitalTrackerEngine(spec)
        self.assertEqual(engine.calculate_required_capital(), (750_000.0, "PMR"))


class TestReportingHygiene(unittest.TestCase):

    def test_report_values_are_unrounded(self):
        engine = RegulatoryCapitalTrackerEngine(_sec_spec())
        report = engine.evaluate_capital_adequacy(
            CapitalComponents(total_assets=550_000.126, total_liabilities=0.0)
        )
        self.assertEqual(report.net_capital_available, 550_000.126)
        # ...while the human-readable string is formatted to cents. Note that
        # this formatting is round-half-even, which is one more reason to round
        # amounts to your reporting precision before constructing.
        self.assertIn("550,000.13", report.audit_notes)

    def test_audit_notes_name_the_status_binding_component_and_warning_line(self):
        engine = RegulatoryCapitalTrackerEngine(_sec_spec())
        report = engine.evaluate_capital_adequacy(
            CapitalComponents(total_assets=550_000.0, total_liabilities=0.0)
        )
        self.assertIn(STATUS_COMPLIANT, report.audit_notes)
        self.assertIn("RATIO_REQ_15c3-1(a)(1)(i)", report.audit_notes)
        self.assertIn("360,000.00", report.audit_notes)

    def test_deficit_is_logged_at_critical(self):
        engine = RegulatoryCapitalTrackerEngine(_sec_spec())
        with self.assertLogs("regulatory_capital_tracker", level=logging.CRITICAL) as captured:
            engine.evaluate_capital_adequacy(
                CapitalComponents(total_assets=100_000.0, total_liabilities=0.0)
            )
        self.assertIn(STATUS_CAPITAL_DEFICIT, captured.output[0])

    def test_reports_are_immutable(self):
        engine = RegulatoryCapitalTrackerEngine(_sec_spec())
        components = CapitalComponents(total_assets=550_000.0, total_liabilities=0.0)
        with self.assertRaises(Exception):
            components.total_assets = 9_000_000.0

    def test_capital_ratio_is_finite_for_a_deeply_negative_net_capital(self):
        engine = RegulatoryCapitalTrackerEngine(_sec_spec())
        report = engine.evaluate_capital_adequacy(
            CapitalComponents(total_assets=0.0, total_liabilities=600_000.0)
        )
        self.assertTrue(math.isfinite(report.capital_ratio))
        self.assertEqual(report.capital_ratio, -2.0)


if __name__ == "__main__":
    unittest.main()
