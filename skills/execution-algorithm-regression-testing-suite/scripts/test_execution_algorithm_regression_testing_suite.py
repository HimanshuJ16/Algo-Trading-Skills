"""Unit tests for execution-algorithm-regression-testing-suite."""
import logging
import unittest

from execution_algorithm_regression_testing_suite import (
    DEFAULT_REQUIRED_SCENARIOS,
    ExecutionAlgoRegressionTestSuite,
    RegressionTestSuiteAuditReport,
    ScenarioTestResult,
)

# The engine logs a CRITICAL line for every rejected build; these tests reject on
# purpose, so keep the expected noise out of the runner's output.
logging.getLogger("execution_algorithm_regression_testing_suite").addHandler(logging.NullHandler())
logging.getLogger("execution_algorithm_regression_testing_suite").propagate = False


def scenario(scenario_id: str, name: str, **overrides) -> ScenarioTestResult:
    """A clean, passing scenario; override one field per test to isolate one rule."""
    fields = dict(
        baseline_is_bps=12.0,
        candidate_is_bps=12.0,
        baseline_fill_rate=1.0,
        candidate_fill_rate=1.0,
        candidate_max_participation_rate=0.10,
    )
    fields.update(overrides)
    return ScenarioTestResult(scenario_id, name, **fields)


def full_coverage(**per_scenario) -> list:
    """One scenario of each required kind, all passing unless overridden by kind name."""
    return [
        scenario(f"SC_{i:02d}", name, **per_scenario.get(name, {}))
        for i, name in enumerate(DEFAULT_REQUIRED_SCENARIOS, start=1)
    ]


class TestGateVerdicts(unittest.TestCase):

    def setUp(self):
        self.suite = ExecutionAlgoRegressionTestSuite(
            max_allowed_is_degradation_bps=2.0,
            min_allowed_fill_ratio=0.98,
            max_allowed_participation_rate=0.20,
        )

    def test_passing_candidate_build(self):
        # Degradations +0.5, +1.0, -0.3 -> mean 1.2/3 = +0.4, worst +1.0 (derived by hand).
        scenarios = full_coverage(
            NORMAL_VOLATILITY=dict(baseline_is_bps=12.0, candidate_is_bps=12.5),
            VOLATILITY_SHOCK=dict(baseline_is_bps=25.0, candidate_is_bps=26.0,
                                  baseline_fill_rate=0.99, candidate_fill_rate=0.99,
                                  candidate_max_participation_rate=0.15),
            LIQUIDITY_CRUNCH=dict(baseline_is_bps=20.0, candidate_is_bps=19.7),
        )
        report = self.suite.run_regression_suite("VWAP_ALGO", "v2.4.1", scenarios)

        self.assertIsInstance(report, RegressionTestSuiteAuditReport)
        self.assertEqual(report.cicd_gate_status, "PASS_REGRESSION_APPROVED")
        self.assertEqual(report.scenarios_passed_count, 3)
        self.assertEqual(report.scenarios_failed_count, 0)
        self.assertEqual(report.avg_is_degradation_bps, 0.40)
        self.assertEqual(report.worst_is_degradation_bps, 1.00)
        self.assertTrue(report.coverage_satisfied)
        self.assertEqual(report.missing_required_scenarios, [])

    def test_regressed_candidate_build_rejected(self):
        # LIQUIDITY_CRUNCH degrades +4.5 bps (> +2.0 limit) and drops fills to 0.95.
        scenarios = full_coverage(
            NORMAL_VOLATILITY=dict(baseline_is_bps=12.0, candidate_is_bps=12.5),
            LIQUIDITY_CRUNCH=dict(baseline_is_bps=20.0, candidate_is_bps=24.5,
                                  candidate_fill_rate=0.95),
        )
        report = self.suite.run_regression_suite("VWAP_ALGO", "v2.4.2-BAD", scenarios)

        crunch = report.scenario_details[2]
        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")
        self.assertEqual(report.scenarios_failed_count, 1)
        self.assertFalse(crunch.passed)
        self.assertIn("IS degradation +4.5bps", crunch.failure_reason)
        self.assertIn("Fill rate ratio 0.95", crunch.failure_reason)
        self.assertEqual(crunch.is_degradation_bps, 4.5)
        self.assertEqual(crunch.fill_rate_ratio, 0.95)

    def test_fill_rate_collapse_alone_rejects_build(self):
        # Shortfall improves while the algo silently stops completing orders.
        scenarios = full_coverage(
            VOLATILITY_SHOCK=dict(candidate_is_bps=8.0, candidate_fill_rate=0.80))
        report = self.suite.run_regression_suite("POV_ALGO", "v3.0.0", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")
        self.assertIn("Fill rate ratio 0.8", report.scenario_details[1].failure_reason)

    def test_participation_breach_alone_rejects_build(self):
        scenarios = full_coverage(
            LIQUIDITY_CRUNCH=dict(candidate_max_participation_rate=0.35))
        report = self.suite.run_regression_suite("POV_ALGO", "v3.0.1", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")
        self.assertIn("Max participation 0.35 exceeds limit 0.2",
                      report.scenario_details[2].failure_reason)

    def test_participation_exactly_at_limit_passes(self):
        scenarios = full_coverage(
            LIQUIDITY_CRUNCH=dict(candidate_max_participation_rate=0.20))
        report = self.suite.run_regression_suite("POV_ALGO", "v3.0.2", scenarios)

        self.assertEqual(report.cicd_gate_status, "PASS_REGRESSION_APPROVED")


class TestThresholdBoundaries(unittest.TestCase):
    """The gate must compare exact values; display rounding must not decide a release."""

    def setUp(self):
        self.suite = ExecutionAlgoRegressionTestSuite()

    def test_degradation_exactly_at_limit_passes(self):
        scenarios = full_coverage(
            VOLATILITY_SHOCK=dict(baseline_is_bps=10.0, candidate_is_bps=12.0))
        report = self.suite.run_regression_suite("IS_ALGO", "v1.0.0", scenarios)

        self.assertEqual(report.cicd_gate_status, "PASS_REGRESSION_APPROVED")

    def test_degradation_just_above_limit_is_not_rounded_into_a_pass(self):
        # Regression test: the previous implementation rounded the degradation to 2 dp
        # before comparing, so +2.004 bps became +2.0 and shipped.
        scenarios = full_coverage(
            VOLATILITY_SHOCK=dict(baseline_is_bps=10.0, candidate_is_bps=12.004))
        report = self.suite.run_regression_suite("IS_ALGO", "v1.0.1", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")
        self.assertIn("+2.004bps", report.scenario_details[1].failure_reason)

    def test_fill_ratio_just_below_limit_rejects(self):
        # 0.9799 / 1.0 is below the 0.98 minimum by less than one display digit.
        scenarios = full_coverage(NORMAL_VOLATILITY=dict(candidate_fill_rate=0.9799))
        report = self.suite.run_regression_suite("IS_ALGO", "v1.0.2", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")


class TestNonFiniteMetricsFailClosed(unittest.TestCase):
    """
    Regression tests for the worst failure mode: a candidate build whose shortfall
    calculation broke produced NaN, every `NaN > threshold` comparison was False, and
    the broken build was APPROVED for deployment.
    """

    def setUp(self):
        self.suite = ExecutionAlgoRegressionTestSuite()

    def test_nan_candidate_shortfall_rejects_build(self):
        scenarios = full_coverage(
            VOLATILITY_SHOCK=dict(candidate_is_bps=float("nan")))
        report = self.suite.run_regression_suite("VWAP_ALGO", "v9.9.9", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")
        self.assertFalse(report.scenario_details[1].passed)
        self.assertIn("not a finite number", report.scenario_details[1].failure_reason)
        self.assertIsNone(report.scenario_details[1].is_degradation_bps)

    def test_nan_baseline_shortfall_rejects_build(self):
        scenarios = full_coverage(NORMAL_VOLATILITY=dict(baseline_is_bps=float("nan")))
        report = self.suite.run_regression_suite("VWAP_ALGO", "v9.9.8", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")

    def test_infinite_shortfall_rejects_build(self):
        scenarios = full_coverage(
            LIQUIDITY_CRUNCH=dict(candidate_is_bps=float("inf")))
        report = self.suite.run_regression_suite("VWAP_ALGO", "v9.9.7", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")

    def test_all_scenarios_non_evaluable_reports_no_average(self):
        scenarios = full_coverage(
            NORMAL_VOLATILITY=dict(candidate_is_bps=float("nan")),
            VOLATILITY_SHOCK=dict(candidate_is_bps=float("nan")),
            LIQUIDITY_CRUNCH=dict(candidate_is_bps=float("nan")),
        )
        report = self.suite.run_regression_suite("VWAP_ALGO", "v9.9.6", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")
        self.assertIsNone(report.avg_is_degradation_bps)
        self.assertIsNone(report.worst_is_degradation_bps)
        self.assertIn("n/a", report.audit_notes)


class TestScenarioCoverage(unittest.TestCase):
    """A green gate earned on quiet-market data only is the pitfall this skill names."""

    def test_missing_required_scenario_rejects_build(self):
        suite = ExecutionAlgoRegressionTestSuite()
        report = suite.run_regression_suite(
            "VWAP_ALGO", "v4.0.0", [scenario("SC_01", "NORMAL_VOLATILITY")])

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")
        self.assertFalse(report.coverage_satisfied)
        self.assertEqual(report.missing_required_scenarios,
                         ["VOLATILITY_SHOCK", "LIQUIDITY_CRUNCH"])
        # Coverage rejection is not a scenario failure - read the gate status, not the count.
        self.assertEqual(report.scenarios_failed_count, 0)
        self.assertEqual(report.scenarios_passed_count, 1)
        self.assertIn("VOLATILITY_SHOCK", report.audit_notes)

    def test_scenario_names_match_case_insensitively(self):
        suite = ExecutionAlgoRegressionTestSuite()
        scenarios = [scenario("SC_01", " normal_volatility "),
                     scenario("SC_02", "Volatility_Shock"),
                     scenario("SC_03", "liquidity_crunch")]
        report = suite.run_regression_suite("VWAP_ALGO", "v4.0.1", scenarios)

        self.assertEqual(report.cicd_gate_status, "PASS_REGRESSION_APPROVED")

    def test_custom_required_scenarios_are_enforced(self):
        suite = ExecutionAlgoRegressionTestSuite(
            required_scenario_names=("OPENING_AUCTION", "HALT_RESUME"))
        report = suite.run_regression_suite(
            "VWAP_ALGO", "v4.0.2", [scenario("SC_01", "OPENING_AUCTION")])

        self.assertEqual(report.missing_required_scenarios, ["HALT_RESUME"])
        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")

    def test_required_scenario_names_as_bare_string_raises(self):
        # Iterating a string yields characters, which would turn one scenario name into
        # a per-letter requirement no suite could ever satisfy.
        with self.assertRaises(TypeError):
            ExecutionAlgoRegressionTestSuite(required_scenario_names="VOLATILITY_SHOCK")

    def test_repeated_requirement_is_reported_once(self):
        suite = ExecutionAlgoRegressionTestSuite(
            required_scenario_names=("VOLATILITY_SHOCK", "volatility_shock", " VOLATILITY_SHOCK "))
        report = suite.run_regression_suite(
            "VWAP_ALGO", "v4.0.4", [scenario("SC_01", "NORMAL_VOLATILITY")])

        self.assertEqual(report.missing_required_scenarios, ["VOLATILITY_SHOCK"])

    def test_coverage_enforcement_can_be_disabled(self):
        suite = ExecutionAlgoRegressionTestSuite(required_scenario_names=())
        report = suite.run_regression_suite(
            "VWAP_ALGO", "v4.0.3", [scenario("SC_01", "NORMAL_VOLATILITY")])

        self.assertEqual(report.cicd_gate_status, "PASS_REGRESSION_APPROVED")
        self.assertTrue(report.coverage_satisfied)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.suite = ExecutionAlgoRegressionTestSuite(required_scenario_names=())

    def _run(self, sc):
        return self.suite.run_regression_suite("ALGO", "v1", [sc])

    def test_empty_suite_raises(self):
        with self.assertRaises(ValueError):
            self.suite.run_regression_suite("ALGO", "v1", [])

    def test_blank_build_identifiers_raise(self):
        # An unlabelled report cannot be traced back to the build it cleared.
        with self.assertRaises(ValueError):
            self.suite.run_regression_suite("", "v1", [scenario("SC_01", "NORMAL_VOLATILITY")])
        with self.assertRaises(ValueError):
            self.suite.run_regression_suite("ALGO", "   ", [scenario("SC_01", "NORMAL_VOLATILITY")])

    def test_zero_baseline_fill_rate_raises(self):
        # Previously guarded by max(1e-4, baseline): candidate 0.5 / 1e-4 = 5000 -> PASS.
        with self.assertRaises(ValueError) as ctx:
            self._run(scenario("SC_01", "NORMAL_VOLATILITY",
                               baseline_fill_rate=0.0, candidate_fill_rate=0.5))
        self.assertIn("undefined", str(ctx.exception))

    def test_fill_rate_above_one_raises(self):
        with self.assertRaises(ValueError):
            self._run(scenario("SC_01", "NORMAL_VOLATILITY", candidate_fill_rate=5.0))

    def test_negative_participation_rate_raises(self):
        with self.assertRaises(ValueError):
            self._run(scenario("SC_01", "NORMAL_VOLATILITY",
                               candidate_max_participation_rate=-0.5))

    def test_nan_fill_rate_raises(self):
        with self.assertRaises(ValueError):
            self._run(scenario("SC_01", "NORMAL_VOLATILITY",
                               candidate_fill_rate=float("nan")))

    def test_blank_scenario_id_raises(self):
        with self.assertRaises(ValueError):
            self._run(scenario("   ", "NORMAL_VOLATILITY"))

    def test_blank_scenario_name_raises(self):
        with self.assertRaises(ValueError):
            self._run(scenario("SC_01", ""))

    def test_non_numeric_shortfall_raises(self):
        with self.assertRaises(TypeError):
            self._run(scenario("SC_01", "NORMAL_VOLATILITY", candidate_is_bps="12.0"))

    def test_duplicate_scenario_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.suite.run_regression_suite(
                "ALGO", "v1",
                [scenario("SC_01", "NORMAL_VOLATILITY"),
                 scenario("SC_01", "VOLATILITY_SHOCK")])
        self.assertIn("Duplicate scenario_id", str(ctx.exception))

    def test_invalid_thresholds_raise(self):
        with self.assertRaises(ValueError):
            ExecutionAlgoRegressionTestSuite(min_allowed_fill_ratio=0.0)
        with self.assertRaises(ValueError):
            ExecutionAlgoRegressionTestSuite(max_allowed_participation_rate=1.5)
        with self.assertRaises(ValueError):
            ExecutionAlgoRegressionTestSuite(max_allowed_is_degradation_bps=float("nan"))
        with self.assertRaises(TypeError):
            ExecutionAlgoRegressionTestSuite(max_allowed_is_degradation_bps="2.0")


class TestReportIntegrity(unittest.TestCase):

    def test_engine_does_not_mutate_caller_scenarios(self):
        # The engine used to write its verdict back into the caller's objects and return
        # aliases, so a second run against different thresholds rewrote the first run's
        # recorded evidence.
        suite = ExecutionAlgoRegressionTestSuite()
        scenarios = full_coverage(
            VOLATILITY_SHOCK=dict(baseline_is_bps=10.0, candidate_is_bps=99.0))
        report = suite.run_regression_suite("ALGO", "v1", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")
        for original in scenarios:
            self.assertFalse(original.passed)
            self.assertIsNone(original.failure_reason)
            self.assertIsNone(original.is_degradation_bps)
            self.assertIsNone(original.fill_rate_ratio)
        for detail, original in zip(report.scenario_details, scenarios):
            self.assertIsNot(detail, original)

    def test_worst_degradation_is_reported_when_the_average_masks_it(self):
        # Mean is a comfortable -0.5 bps while one scenario degrades +6.0 bps.
        suite = ExecutionAlgoRegressionTestSuite()
        scenarios = full_coverage(
            NORMAL_VOLATILITY=dict(baseline_is_bps=12.0, candidate_is_bps=7.5),
            VOLATILITY_SHOCK=dict(baseline_is_bps=25.0, candidate_is_bps=31.0),
            LIQUIDITY_CRUNCH=dict(baseline_is_bps=20.0, candidate_is_bps=17.0),
        )
        report = suite.run_regression_suite("ALGO", "v1", scenarios)

        self.assertEqual(report.avg_is_degradation_bps, -0.50)
        self.assertEqual(report.worst_is_degradation_bps, 6.00)
        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")

    def test_pass_notes_do_not_claim_regulatory_conformance(self):
        # A green gate must not read as evidence of RTS 6 compliance.
        suite = ExecutionAlgoRegressionTestSuite()
        report = suite.run_regression_suite("ALGO", "v1", full_coverage())

        self.assertEqual(report.cicd_gate_status, "PASS_REGRESSION_APPROVED")
        self.assertNotIn("PASSED RTS 6", report.audit_notes)
        self.assertIn("disorderly trading conditions", report.audit_notes)


if __name__ == '__main__':
    unittest.main()
