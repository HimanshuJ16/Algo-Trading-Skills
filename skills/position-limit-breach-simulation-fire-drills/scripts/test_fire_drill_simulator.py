"""
Behavioural tests for the position-limit breach fire drill harness.

The regression tests that matter here are the ones that would have PASSED against the
previous implementation and now FAIL: the previous harness derived "the gateway blocked
it" from "the position exceeded the limit", so it reported success whether or not the
gateway did anything. ``test_open_gateway_is_detected`` and
``test_over_blocking_gateway_is_detected`` pin that down.
"""
import logging
import math
import unittest

from fire_drill_simulator import (
    BreachType,
    ControlPhase,
    DrillEnvironment,
    DrillStatus,
    ExpectedOutcome,
    FireDrillResult,
    FireDrillScenario,
    FireDrillSimulator,
    FireDrillSimulatorConfig,
    FireDrillSuiteReport,
    ObservedControlResponse,
    ProductionEnvironmentError,
    RTS6_ALERT_LATENCY_SLA_MS,
    is_over_limit,
)


def breach_scenario(**overrides) -> FireDrillScenario:
    """A CME-style exchange-limit breach: 12,000 contracts against a 10,000 limit."""
    kwargs = dict(
        scenario_id="DRILL_EXCHANGE_001",
        breach_type=BreachType.EXCHANGE_LIMIT,
        target_symbol="CL_FUT",
        injected_position_qty=12_000.0,
        limit_threshold=10_000.0,
        environment=DrillEnvironment.STAGING,
        control_phase=ControlPhase.PRE_TRADE,
        expected_outcome=ExpectedOutcome.BLOCK_AND_HALT,
    )
    kwargs.update(overrides)
    return FireDrillScenario(**kwargs)


def correct_block(**overrides) -> ObservedControlResponse:
    """The response a healthy pre-trade gateway produces for a breach."""
    kwargs = dict(
        order_rejected=True,
        trading_halted=True,
        manual_reenable_required=True,
        compliance_alert_id="ALERT-0001",
        risk_latency_ms=1.2,
    )
    kwargs.update(overrides)
    return ObservedControlResponse(**kwargs)


class TestOverLimitPredicate(unittest.TestCase):

    def test_strictly_over_the_limit_is_a_breach(self):
        self.assertTrue(is_over_limit(10_001.0, 10_000.0))

    def test_exactly_at_the_limit_is_compliant(self):
        # CME Rule 562 deems violations for positions "in excess of" the limit, so the
        # boundary itself is compliant. A >= predicate would flag this incorrectly.
        self.assertFalse(is_over_limit(10_000.0, 10_000.0))

    def test_short_side_uses_absolute_size(self):
        self.assertTrue(is_over_limit(-12_000.0, 10_000.0))
        self.assertFalse(is_over_limit(-10_000.0, 10_000.0))

    def test_nan_quantity_raises_rather_than_reading_as_within_limit(self):
        with self.assertRaises(ValueError):
            is_over_limit(float("nan"), 10_000.0)

    def test_infinite_quantity_raises(self):
        with self.assertRaises(ValueError):
            is_over_limit(math.inf, 10_000.0)


class TestScenarioValidation(unittest.TestCase):

    def test_blank_scenario_id_raises(self):
        with self.assertRaises(ValueError):
            breach_scenario(scenario_id="   ")

    def test_blank_symbol_raises(self):
        with self.assertRaises(ValueError):
            breach_scenario(target_symbol="")

    def test_non_positive_limit_raises(self):
        with self.assertRaises(ValueError):
            breach_scenario(limit_threshold=0.0)
        with self.assertRaises(ValueError):
            breach_scenario(limit_threshold=-10.0)

    def test_nan_quantity_raises(self):
        with self.assertRaises(ValueError):
            breach_scenario(injected_position_qty=float("nan"))

    def test_unknown_breach_type_raises_with_allowed_values(self):
        with self.assertRaises(ValueError) as ctx:
            breach_scenario(breach_type="TYPO_LIMIT")
        self.assertIn("EXCHANGE_LIMIT", str(ctx.exception))

    def test_string_enum_values_are_accepted(self):
        scenario = breach_scenario(breach_type="ROGUE_ALGO", environment="SANDBOX")
        self.assertIs(scenario.breach_type, BreachType.ROGUE_ALGO)
        self.assertIs(scenario.environment, DrillEnvironment.SANDBOX)

    def test_over_limit_allow_scenario_requires_an_exemption_basis(self):
        # Allowing an over-limit position without recording why is indistinguishable
        # from a gateway failure.
        with self.assertRaises(ValueError) as ctx:
            breach_scenario(
                breach_type=BreachType.ASSIGNMENT_OVERAGE,
                expected_outcome=ExpectedOutcome.ALLOW,
            )
        self.assertIn("exemption_basis", str(ctx.exception))

    def test_over_limit_allow_scenario_accepted_with_a_basis(self):
        scenario = breach_scenario(
            breach_type=BreachType.ASSIGNMENT_OVERAGE,
            expected_outcome=ExpectedOutcome.ALLOW,
            exemption_basis="CME Rule 562: one business day to liquidate an assignment overage.",
        )
        self.assertTrue(scenario.over_limit)

    def test_within_limit_allow_scenario_needs_no_basis(self):
        scenario = breach_scenario(
            breach_type=BreachType.WITHIN_LIMIT,
            injected_position_qty=9_000.0,
            expected_outcome=ExpectedOutcome.ALLOW,
        )
        self.assertFalse(scenario.over_limit)

    def test_post_trade_scenario_cannot_expect_an_order_rejection(self):
        with self.assertRaises(ValueError) as ctx:
            breach_scenario(
                control_phase=ControlPhase.POST_TRADE,
                expected_outcome=ExpectedOutcome.BLOCK_AND_HALT,
            )
        self.assertIn("no order to reject", str(ctx.exception))


class TestObservationValidation(unittest.TestCase):

    def test_negative_latency_raises(self):
        with self.assertRaises(ValueError):
            ObservedControlResponse(risk_latency_ms=-0.1)

    def test_nan_latency_raises(self):
        with self.assertRaises(ValueError):
            ObservedControlResponse(risk_latency_ms=float("nan"))

    def test_absent_latency_is_permitted_at_construction(self):
        self.assertIsNone(ObservedControlResponse().risk_latency_ms)


class TestConfigValidation(unittest.TestCase):

    def test_non_positive_sla_raises(self):
        with self.assertRaises(ValueError):
            FireDrillSimulatorConfig(max_pre_trade_latency_ms=0.0)

    def test_alert_sla_defaults_to_the_rts6_five_second_requirement(self):
        # RTS 6 Art. 16(5): real-time alerts within five seconds of the relevant event.
        self.assertEqual(FireDrillSimulatorConfig().max_alert_latency_ms, 5_000.0)
        self.assertEqual(RTS6_ALERT_LATENCY_SLA_MS, 5_000.0)


class TestPreTradeDrills(unittest.TestCase):

    def setUp(self):
        self.sim = FireDrillSimulator(
            FireDrillSimulatorConfig(enabled=True, max_pre_trade_latency_ms=5.0))
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_healthy_gateway_passes(self):
        result = self.sim.run_fire_drill(breach_scenario(), correct_block())
        self.assertTrue(result.passed)
        self.assertEqual(result.status, DrillStatus.CONTROL_VERIFIED.value)
        self.assertEqual(result.findings, ())
        self.assertTrue(result.latency_sla_passed)
        self.assertTrue(result.over_limit)
        self.assertTrue(result.compliance_alert_logged)

    def test_open_gateway_is_detected(self):
        # REGRESSION: the previous harness set order_blocked = is_breach and reported
        # BREACH_BLOCKED_KILL_SWITCH_ENGAGED here, i.e. a clean pass for a gateway that
        # let the breach order straight through.
        observed = ObservedControlResponse(
            order_rejected=False,
            trading_halted=False,
            compliance_alert_id="",
            risk_latency_ms=1.2,
        )
        result = self.sim.run_fire_drill(breach_scenario(), observed)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, DrillStatus.CONTROL_FAILED.value)
        self.assertEqual(len(result.findings), 3)
        self.assertTrue(any("did not reject" in f for f in result.findings))
        self.assertTrue(any("Kill switch did not halt" in f for f in result.findings))
        self.assertTrue(any("compliance alert" in f for f in result.findings))

    def test_order_rejected_but_kill_switch_did_not_trip(self):
        observed = correct_block(trading_halted=False, manual_reenable_required=False)
        result = self.sim.run_fire_drill(breach_scenario(), observed)
        self.assertFalse(result.passed)
        self.assertTrue(any("Kill switch did not halt" in f for f in result.findings))

    def test_self_resuming_kill_switch_is_a_finding(self):
        # RTS 6 Art. 15(3): disabled "until re-enabled by a designated staff member".
        observed = correct_block(manual_reenable_required=False)
        result = self.sim.run_fire_drill(breach_scenario(), observed)
        self.assertFalse(result.passed)
        self.assertTrue(any("re-enable" in f for f in result.findings))

    def test_latency_sla_breach_is_reported_separately_from_behaviour_failure(self):
        observed = correct_block(risk_latency_ms=15.0)
        result = self.sim.run_fire_drill(breach_scenario(), observed)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, DrillStatus.LATENCY_SLA_BREACHED.value)
        self.assertFalse(result.latency_sla_passed)
        # Behaviour was correct, so no behavioural findings were raised.
        self.assertEqual(result.findings, ())

    def test_latency_exactly_at_the_sla_passes(self):
        result = self.sim.run_fire_drill(breach_scenario(), correct_block(risk_latency_ms=5.0))
        self.assertTrue(result.passed)
        self.assertTrue(result.latency_sla_passed)

    def test_latency_just_over_the_sla_fails(self):
        result = self.sim.run_fire_drill(
            breach_scenario(), correct_block(risk_latency_ms=5.0000001))
        self.assertFalse(result.passed)

    def test_unmeasured_latency_is_a_failure_not_a_silent_pass(self):
        observed = correct_block(risk_latency_ms=None)
        result = self.sim.run_fire_drill(breach_scenario(), observed)
        self.assertFalse(result.passed)
        self.assertIsNone(result.latency_sla_passed)
        self.assertTrue(any("latency was recorded" in f for f in result.findings))

    def test_pre_trade_drill_ignores_the_alert_latency_field(self):
        observed = correct_block(risk_latency_ms=1.0, alert_latency_ms=99_999.0)
        result = self.sim.run_fire_drill(breach_scenario(), observed)
        self.assertTrue(result.passed)
        self.assertEqual(result.measured_latency_ms, 1.0)
        self.assertEqual(result.latency_sla_ms, 5.0)


class TestNegativeControls(unittest.TestCase):

    def setUp(self):
        self.sim = FireDrillSimulator()
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_within_limit_order_allowed_passes(self):
        scenario = breach_scenario(
            scenario_id="DRILL_NEGATIVE_001",
            breach_type=BreachType.WITHIN_LIMIT,
            injected_position_qty=9_999.0,
            expected_outcome=ExpectedOutcome.ALLOW,
        )
        observed = ObservedControlResponse(order_rejected=False, risk_latency_ms=0.9)
        result = self.sim.run_fire_drill(scenario, observed)
        self.assertTrue(result.passed)
        self.assertFalse(result.over_limit)

    def test_over_blocking_gateway_is_detected(self):
        # REGRESSION: the previous harness classified every non-breach scenario as
        # BREACH_UNBLOCKED_CRITICAL_FAILURE regardless of what the gateway did, so a
        # gateway that wrongly rejected a compliant order was indistinguishable from one
        # that behaved correctly.
        scenario = breach_scenario(
            scenario_id="DRILL_NEGATIVE_002",
            breach_type=BreachType.WITHIN_LIMIT,
            injected_position_qty=9_000.0,
            expected_outcome=ExpectedOutcome.ALLOW,
        )
        observed = ObservedControlResponse(order_rejected=True, risk_latency_ms=0.9)
        result = self.sim.run_fire_drill(scenario, observed)
        self.assertFalse(result.passed)
        self.assertTrue(any("over-blocking" in f for f in result.findings))

    def test_assignment_grace_position_must_not_be_blocked(self):
        # CME Rule 562 allows one business day to liquidate an assignment-driven overage.
        scenario = breach_scenario(
            scenario_id="DRILL_ASSIGNMENT_001",
            breach_type=BreachType.ASSIGNMENT_OVERAGE,
            injected_position_qty=10_400.0,
            expected_outcome=ExpectedOutcome.ALLOW,
            exemption_basis="CME Rule 562: one business day to liquidate an assignment overage.",
        )
        observed = ObservedControlResponse(order_rejected=False, risk_latency_ms=1.1)
        result = self.sim.run_fire_drill(scenario, observed)
        self.assertTrue(result.passed)
        self.assertTrue(result.over_limit)

    def test_halting_an_exempt_position_is_a_finding(self):
        scenario = breach_scenario(
            scenario_id="DRILL_HEDGE_001",
            breach_type=BreachType.EXEMPT_HEDGE,
            injected_position_qty=14_000.0,
            expected_outcome=ExpectedOutcome.ALLOW,
            exemption_basis="CME Rule 559: bona fide hedge exemption granted 2026-07-01.",
        )
        observed = ObservedControlResponse(
            order_rejected=False,
            trading_halted=True,
            manual_reenable_required=True,
            risk_latency_ms=1.0,
        )
        result = self.sim.run_fire_drill(scenario, observed)
        self.assertFalse(result.passed)
        self.assertTrue(any("halted trading" in f for f in result.findings))


class TestPostTradeDrills(unittest.TestCase):

    def setUp(self):
        self.sim = FireDrillSimulator()
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def post_trade_scenario(self, **overrides) -> FireDrillScenario:
        kwargs = dict(
            scenario_id="DRILL_POSTTRADE_001",
            breach_type=BreachType.EXCHANGE_LIMIT,
            control_phase=ControlPhase.POST_TRADE,
            expected_outcome=ExpectedOutcome.ALERT_ONLY,
            injected_position_qty=10_600.0,
            limit_threshold=10_000.0,
            description="Spot-month limit steps down at the close; position unchanged.",
        )
        kwargs.update(overrides)
        return breach_scenario(**kwargs)

    def test_alert_plus_remediation_passes(self):
        observed = ObservedControlResponse(
            compliance_alert_id="ALERT-9001",
            remediation_action="Orderly liquidation of 600 lots scheduled.",
            alert_latency_ms=1_800.0,
        )
        result = self.sim.run_fire_drill(self.post_trade_scenario(), observed)
        self.assertTrue(result.passed)
        self.assertEqual(result.latency_sla_ms, RTS6_ALERT_LATENCY_SLA_MS)

    def test_silent_post_trade_breach_is_detected(self):
        observed = ObservedControlResponse(alert_latency_ms=100.0)
        result = self.sim.run_fire_drill(self.post_trade_scenario(), observed)
        self.assertFalse(result.passed)
        self.assertTrue(any("no alert" in f for f in result.findings))
        self.assertTrue(any("remediation action" in f for f in result.findings))

    def test_alert_without_remediation_is_a_finding(self):
        observed = ObservedControlResponse(
            compliance_alert_id="ALERT-9002", alert_latency_ms=900.0)
        result = self.sim.run_fire_drill(self.post_trade_scenario(), observed)
        self.assertFalse(result.passed)
        self.assertTrue(any("remediation action" in f for f in result.findings))

    def test_alert_slower_than_five_seconds_breaches_the_rts6_sla(self):
        observed = ObservedControlResponse(
            compliance_alert_id="ALERT-9003",
            remediation_action="Desk notified; unwind started.",
            alert_latency_ms=6_500.0,
        )
        result = self.sim.run_fire_drill(self.post_trade_scenario(), observed)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, DrillStatus.LATENCY_SLA_BREACHED.value)

    def test_post_trade_drill_does_not_use_the_pre_trade_sla(self):
        # A 1.8s alert would blow a 5ms pre-trade SLA; the post-trade SLA is 5s.
        observed = ObservedControlResponse(
            compliance_alert_id="ALERT-9004",
            remediation_action="Unwound.",
            risk_latency_ms=None,
            alert_latency_ms=1_800.0,
        )
        result = self.sim.run_fire_drill(self.post_trade_scenario(), observed)
        self.assertTrue(result.passed)


class TestEnvironmentIsolation(unittest.TestCase):

    def setUp(self):
        self.sim = FireDrillSimulator()
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_production_scenario_refuses_to_run(self):
        # RTS 6 Art. 10: tests must not affect the production environment.
        scenario = breach_scenario(environment=DrillEnvironment.PRODUCTION)
        with self.assertRaises(ProductionEnvironmentError):
            self.sim.run_fire_drill(scenario, correct_block())

    def test_each_non_production_environment_runs(self):
        for env in (DrillEnvironment.SANDBOX, DrillEnvironment.STAGING, DrillEnvironment.PAPER):
            with self.subTest(environment=env):
                result = self.sim.run_fire_drill(
                    breach_scenario(environment=env), correct_block())
                self.assertTrue(result.passed)
                self.assertEqual(result.environment, env.value)

    def test_production_guard_precedes_the_disabled_short_circuit(self):
        # A disabled simulator must not be a route around the production guard.
        sim = FireDrillSimulator(FireDrillSimulatorConfig(enabled=False))
        with self.assertRaises(ProductionEnvironmentError):
            sim.run_fire_drill(
                breach_scenario(environment=DrillEnvironment.PRODUCTION), correct_block())


class TestDisabledSimulator(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_disabled_simulator_never_reports_a_pass(self):
        sim = FireDrillSimulator(FireDrillSimulatorConfig(enabled=False))
        result = sim.run_fire_drill(breach_scenario(), correct_block())
        self.assertFalse(result.passed)
        self.assertEqual(result.status, DrillStatus.DRILL_SKIPPED_SIMULATOR_DISABLED.value)
        self.assertFalse(result.order_rejected)


class TestTypeGuards(unittest.TestCase):

    def setUp(self):
        self.sim = FireDrillSimulator()
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_missing_observation_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.sim.run_fire_drill(breach_scenario(), {"order_rejected": True})

    def test_scenario_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.sim.run_fire_drill("DRILL_1", correct_block())


class TestDrillSuite(unittest.TestCase):

    def setUp(self):
        self.sim = FireDrillSimulator()
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def full_suite(self):
        pre_trade = (breach_scenario(), correct_block())
        post_trade = (
            breach_scenario(
                scenario_id="DRILL_POSTTRADE_010",
                control_phase=ControlPhase.POST_TRADE,
                expected_outcome=ExpectedOutcome.ALERT_ONLY,
                injected_position_qty=10_600.0,
            ),
            ObservedControlResponse(
                compliance_alert_id="ALERT-1",
                remediation_action="Unwind scheduled.",
                alert_latency_ms=1_200.0,
            ),
        )
        negative = (
            breach_scenario(
                scenario_id="DRILL_NEGATIVE_010",
                breach_type=BreachType.WITHIN_LIMIT,
                injected_position_qty=9_500.0,
                expected_outcome=ExpectedOutcome.ALLOW,
            ),
            ObservedControlResponse(order_rejected=False, risk_latency_ms=0.8),
        )
        return [pre_trade, post_trade, negative]

    def test_complete_suite_passes(self):
        report = self.sim.run_drill_suite("Q3_2026_FIRE_DRILL", self.full_suite())
        self.assertIsInstance(report, FireDrillSuiteReport)
        self.assertTrue(report.all_passed)
        self.assertEqual(report.total_scenarios, 3)
        self.assertEqual(report.passed_count, 3)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.negative_control_count, 1)
        self.assertEqual(report.post_trade_scenario_count, 1)
        self.assertEqual(report.coverage_findings, ())
        self.assertTrue(all(isinstance(r, FireDrillResult) for r in report.results))

    def test_suite_without_a_negative_control_cannot_pass(self):
        cases = [c for c in self.full_suite()
                 if c[0].expected_outcome is not ExpectedOutcome.ALLOW]
        report = self.sim.run_drill_suite("NO_NEGATIVE", cases)
        self.assertEqual(report.passed_count, len(cases))
        self.assertFalse(report.all_passed)
        self.assertTrue(any("negative control" in f for f in report.coverage_findings))

    def test_suite_without_a_post_trade_scenario_cannot_pass(self):
        cases = [c for c in self.full_suite()
                 if c[0].control_phase is not ControlPhase.POST_TRADE]
        report = self.sim.run_drill_suite("NO_POST_TRADE", cases)
        self.assertFalse(report.all_passed)
        self.assertTrue(any("POST_TRADE" in f for f in report.coverage_findings))

    def test_coverage_gates_can_be_switched_off_deliberately(self):
        sim = FireDrillSimulator(FireDrillSimulatorConfig(
            require_negative_control=False, require_post_trade_scenario=False))
        report = sim.run_drill_suite("PRE_TRADE_ONLY", [(breach_scenario(), correct_block())])
        self.assertTrue(report.all_passed)
        self.assertEqual(report.coverage_findings, ())

    def test_one_failing_scenario_fails_the_suite(self):
        cases = self.full_suite()
        cases[0] = (breach_scenario(), ObservedControlResponse(risk_latency_ms=1.0))
        report = self.sim.run_drill_suite("ONE_BAD", cases)
        self.assertFalse(report.all_passed)
        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.passed_count, 2)

    def test_duplicate_scenario_ids_are_rejected(self):
        cases = self.full_suite()
        cases.append((breach_scenario(), correct_block()))
        with self.assertRaises(ValueError) as ctx:
            self.sim.run_drill_suite("DUPES", cases)
        self.assertIn("Duplicate scenario_id", str(ctx.exception))

    def test_empty_suite_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sim.run_drill_suite("EMPTY", [])

    def test_blank_suite_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sim.run_drill_suite("  ", self.full_suite())

    def test_disabled_simulator_suite_is_counted_as_skipped_not_passed(self):
        sim = FireDrillSimulator(FireDrillSimulatorConfig(enabled=False))
        report = sim.run_drill_suite("DISABLED", self.full_suite())
        self.assertFalse(report.all_passed)
        self.assertEqual(report.skipped_count, 3)
        self.assertEqual(report.passed_count, 0)
        self.assertEqual(report.failed_count, 0)


if __name__ == "__main__":
    unittest.main()
