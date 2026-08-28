"""
Unit tests for the new-strategy-onboarding gatekeeper.

Run from this directory:  python test_new_strategy_onboarding_checklist.py

The tests are grouped by what they protect:

* ``TestGateVerdicts``      -- the conjunctive pass/reject decision.
* ``TestThresholdBoundaries`` -- exact-threshold behaviour, one criterion at a time.
* ``TestAttestationTypeSafety`` -- the fail-open regressions. Every test here passed
  a strategy through the gate before validation existed.
* ``TestNumericIntegrity``  -- NaN/Inf and negative-count inputs.
* ``TestPolicyAuditability`` -- the thresholds actually applied are in the record.
"""
import math
import unittest

from new_strategy_onboarding_checklist import (
    DEFAULT_MIN_BACKTEST_SHARPE,
    DEFAULT_MIN_PAPER_TRADING_DAYS,
    DEFAULT_MIN_REGIMES_COVERED,
    DEFAULT_MIN_WALK_FORWARD_SCORE,
    NewStrategyOnboardingEngine,
    OnboardingPolicyConfig,
    StrategyOnboardingPayload,
)

# A package that clears every default threshold with room to spare. Tests override
# single fields so that exactly one criterion is under examination at a time.
COMPLIANT_FIELDS = {
    "strategy_id": "STAT_ARB_v2",
    "strategy_name": "Statistical Arbitrage Equity",
    "author": "Quant Team A",
    "walk_forward_score": 0.82,
    "regimes_covered": 4,
    "backtest_sharpe": 2.1,
    "paper_trading_days": 20,
    "paper_trading_errors": 0,
    "kill_switch_integrated": True,
    "model_card_completed": True,
    "compliance_approved": True,
}


def payload(**overrides) -> StrategyOnboardingPayload:
    fields = dict(COMPLIANT_FIELDS)
    fields.update(overrides)
    return StrategyOnboardingPayload(**fields)


class TestGateVerdicts(unittest.TestCase):

    def setUp(self):
        self.engine = NewStrategyOnboardingEngine()

    def test_fully_compliant_strategy_onboarding_passed(self):
        report = self.engine.audit_strategy_onboarding(payload())

        self.assertTrue(report.is_onboarding_approved)
        self.assertEqual(report.status, "ONBOARDING_PASSED")
        self.assertEqual(report.total_gates_passed, 4)
        self.assertEqual(report.total_gates_count, 4)
        self.assertEqual(report.failed_gates, [])
        self.assertNotIn("Failed Gates", report.audit_notes)
        for gate in report.gates_evaluated:
            self.assertEqual(gate.failed_criteria, [])

    def test_non_compliant_strategy_onboarding_rejected(self):
        # Paper trading 5 < 14 days and compliance sign-off missing.
        report = self.engine.audit_strategy_onboarding(
            payload(
                strategy_id="TREND_CTA_v1",
                strategy_name="CTA Trend Following",
                author="Quant Team B",
                walk_forward_score=0.75,
                regimes_covered=3,
                backtest_sharpe=1.8,
                paper_trading_days=5,
                compliance_approved=False,
            )
        )

        self.assertFalse(report.is_onboarding_approved)
        self.assertEqual(report.status, "ONBOARDING_REJECTED")
        self.assertEqual(report.total_gates_passed, 2)
        self.assertEqual(report.failed_gates, ["OPERATIONAL_GATE", "COMPLIANCE_GATE"])
        self.assertIn("OPERATIONAL_GATE", report.audit_notes)
        self.assertIn("COMPLIANCE_GATE", report.audit_notes)
        self.assertIn("paper_trading_days: 5 < 14", report.audit_notes)

    def test_gates_are_conjunctive_not_scored(self):
        """Three of four gates passing is exactly as rejected as none passing."""
        report = self.engine.audit_strategy_onboarding(
            payload(kill_switch_integrated=False))

        self.assertEqual(report.total_gates_passed, 3)
        self.assertFalse(report.is_onboarding_approved)
        self.assertEqual(report.status, "ONBOARDING_REJECTED")
        self.assertEqual(report.failed_gates, ["OPERATIONAL_GATE"])

    def test_every_gate_can_fail_independently(self):
        cases = [
            ("BACKTEST_GATE", {"walk_forward_score": 0.10}),
            ("OPERATIONAL_GATE", {"paper_trading_days": 1}),
            ("MODEL_RISK_GATE", {"model_card_completed": False}),
            ("COMPLIANCE_GATE", {"compliance_approved": False}),
        ]
        for gate_name, override in cases:
            with self.subTest(gate=gate_name):
                report = self.engine.audit_strategy_onboarding(payload(**override))
                self.assertEqual(report.failed_gates, [gate_name])
                self.assertEqual(report.total_gates_passed, 3)

    def test_failed_criteria_names_each_failing_condition(self):
        """A gate bundles three criteria; the report must say which ones failed."""
        report = self.engine.audit_strategy_onboarding(
            payload(walk_forward_score=0.10, regimes_covered=1, backtest_sharpe=0.2))

        backtest = report.gates_evaluated[0]
        self.assertEqual(backtest.gate_name, "BACKTEST_GATE")
        self.assertFalse(backtest.passed)
        self.assertEqual(len(backtest.failed_criteria), 3)
        joined = " ".join(backtest.failed_criteria)
        self.assertIn("walk_forward_score", joined)
        self.assertIn("regimes_covered", joined)
        self.assertIn("backtest_sharpe", joined)

    def test_passed_flag_is_a_real_bool_on_every_gate(self):
        """``passed`` is serialised into audit records; a truthy non-bool corrupts them."""
        report = self.engine.audit_strategy_onboarding(payload())
        for gate in report.gates_evaluated:
            with self.subTest(gate=gate.gate_name):
                self.assertIs(type(gate.passed), bool)

    def test_audit_is_deterministic(self):
        first = self.engine.audit_strategy_onboarding(payload())
        second = self.engine.audit_strategy_onboarding(payload())
        self.assertEqual(first, second)


class TestThresholdBoundaries(unittest.TestCase):
    """Thresholds are inclusive floors: `>= min` passes, one step below fails."""

    def setUp(self):
        self.engine = NewStrategyOnboardingEngine()

    def test_exactly_at_every_threshold_passes(self):
        report = self.engine.audit_strategy_onboarding(
            payload(
                walk_forward_score=DEFAULT_MIN_WALK_FORWARD_SCORE,
                regimes_covered=DEFAULT_MIN_REGIMES_COVERED,
                backtest_sharpe=DEFAULT_MIN_BACKTEST_SHARPE,
                paper_trading_days=DEFAULT_MIN_PAPER_TRADING_DAYS,
                paper_trading_errors=0,
            )
        )
        self.assertTrue(report.is_onboarding_approved)

    def test_one_step_below_each_threshold_fails_that_gate_only(self):
        cases = [
            ("BACKTEST_GATE", {"walk_forward_score": 0.69}),
            ("BACKTEST_GATE", {"regimes_covered": DEFAULT_MIN_REGIMES_COVERED - 1}),
            ("BACKTEST_GATE", {"backtest_sharpe": 1.49}),
            ("OPERATIONAL_GATE",
             {"paper_trading_days": DEFAULT_MIN_PAPER_TRADING_DAYS - 1}),
            ("OPERATIONAL_GATE", {"paper_trading_errors": 1}),
        ]
        for gate_name, override in cases:
            with self.subTest(override=override):
                report = self.engine.audit_strategy_onboarding(payload(**override))
                self.assertFalse(report.is_onboarding_approved)
                self.assertEqual(report.failed_gates, [gate_name])

    def test_a_single_paper_trading_error_fails_the_operational_gate(self):
        """`max_paper_trading_errors` defaults to 0: one critical error is a rejection."""
        report = self.engine.audit_strategy_onboarding(payload(paper_trading_errors=1))
        self.assertFalse(report.is_onboarding_approved)
        self.assertIn("paper_trading_errors: 1 > 0",
                      " ".join(report.gates_evaluated[1].failed_criteria))


class TestAttestationTypeSafety(unittest.TestCase):
    """
    Regression tests for the fail-open defect.

    Gate outcomes were previously derived from raw truthiness. Verified against the
    pre-fix module: ``compliance_approved="false"`` with ``model_card_completed="NO"``
    returned ``ONBOARDING_PASSED``, with the string ``'NO'`` sitting in the audit
    record's boolean ``passed`` field. A blank ``strategy_id`` also produced a
    passing report keyed by an empty identifier.

    Not every case here was fail-open -- ``paper_trading_days=True`` failed closed as
    "one day" -- but silent type confusion in a promotion gate is a defect whichever
    way it happens to fall.
    """

    def setUp(self):
        self.engine = NewStrategyOnboardingEngine()

    def test_string_false_is_rejected_not_treated_as_true(self):
        for value in ("false", "False", "NO", "pending", "0"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    payload(compliance_approved=value)

    def test_every_attestation_flag_requires_a_real_bool(self):
        for flag in ("kill_switch_integrated", "model_card_completed",
                     "compliance_approved"):
            for value in ("yes", 1, 0, 1.0, None, [], {"ok": True}):
                with self.subTest(flag=flag, value=value):
                    with self.assertRaises(ValueError):
                        payload(**{flag: value})

    def test_mutation_after_construction_is_caught_at_audit_time(self):
        """The gate, not the constructor, is the enforcement point."""
        pkg = payload()
        pkg.compliance_approved = "signed"          # truthy, would have passed Gate 4
        with self.assertRaises(ValueError):
            self.engine.audit_strategy_onboarding(pkg)

    def test_bool_is_not_accepted_where_a_count_is_expected(self):
        """``True`` is an int in Python and would silently mean 'one day'."""
        with self.assertRaises(ValueError):
            payload(paper_trading_days=True)
        with self.assertRaises(ValueError):
            payload(regimes_covered=True)

    def test_blank_identifiers_are_rejected(self):
        for field_name in ("strategy_id", "strategy_name", "author"):
            for value in ("", "   ", None, 42):
                with self.subTest(field=field_name, value=value):
                    with self.assertRaises(ValueError):
                        payload(**{field_name: value})

    def test_engine_rejects_a_non_payload_object(self):
        with self.assertRaises(ValueError):
            self.engine.audit_strategy_onboarding({"strategy_id": "X"})


class TestNumericIntegrity(unittest.TestCase):

    def test_nan_metrics_raise_rather_than_reading_as_a_failed_strategy(self):
        """
        Every gate is a comparison and every comparison against NaN is False, so a
        corrupt metric used to surface as ``ONBOARDING_REJECTED`` -- indistinguishable
        from a genuinely weak strategy, and routed to the wrong team.
        """
        for field_name in ("walk_forward_score", "backtest_sharpe"):
            with self.subTest(field=field_name):
                with self.assertRaises(ValueError):
                    payload(**{field_name: float("nan")})

    def test_infinite_sharpe_raises_instead_of_clearing_the_floor(self):
        """An infinite Sharpe means zero return variance: a broken backtest, not a great one."""
        with self.assertRaises(ValueError):
            payload(backtest_sharpe=math.inf)
        with self.assertRaises(ValueError):
            payload(walk_forward_score=-math.inf)

    def test_negative_counts_are_rejected(self):
        for field_name in ("regimes_covered", "paper_trading_days",
                           "paper_trading_errors"):
            with self.subTest(field=field_name):
                with self.assertRaises(ValueError):
                    payload(**{field_name: -1})

    def test_non_numeric_metrics_are_rejected(self):
        with self.assertRaises(ValueError):
            payload(backtest_sharpe="2.1")
        with self.assertRaises(ValueError):
            payload(paper_trading_days="20")

    def test_integer_metrics_are_accepted_and_normalised_to_float(self):
        pkg = payload(backtest_sharpe=2, walk_forward_score=1)
        self.assertIsInstance(pkg.backtest_sharpe, float)
        self.assertIsInstance(pkg.walk_forward_score, float)


class TestPolicyAuditability(unittest.TestCase):
    """
    The verdict is meaningless without the thresholds behind it: a config of zeros
    emits the same ``ONBOARDING_PASSED`` string as the strict default.
    """

    def test_report_records_the_thresholds_actually_applied(self):
        config = OnboardingPolicyConfig(min_paper_trading_days=30,
                                        min_backtest_sharpe=2.0)
        report = NewStrategyOnboardingEngine(config).audit_strategy_onboarding(payload())

        self.assertEqual(report.policy_applied["min_paper_trading_days"], 30)
        self.assertEqual(report.policy_applied["min_backtest_sharpe"], 2.0)
        self.assertEqual(report.policy_applied["min_regimes_covered"],
                         DEFAULT_MIN_REGIMES_COVERED)

    def test_default_policy_reports_no_weakening(self):
        report = NewStrategyOnboardingEngine().audit_strategy_onboarding(payload())
        self.assertEqual(report.policy_weakened, [])

    def test_a_disabled_policy_still_passes_but_is_flagged_in_the_record(self):
        """
        This is the audit trail's whole job. The strategy below is unfit by every
        default, the verdict is still ONBOARDING_PASSED, and the only evidence of
        that is ``policy_applied`` / ``policy_weakened``.
        """
        config = OnboardingPolicyConfig(
            min_walk_forward_score=0.0,
            min_regimes_covered=0,
            min_backtest_sharpe=0.0,
            min_paper_trading_days=0,
            max_paper_trading_errors=99,
        )
        report = NewStrategyOnboardingEngine(config).audit_strategy_onboarding(
            payload(walk_forward_score=0.01, regimes_covered=0, backtest_sharpe=0.0,
                    paper_trading_days=0, paper_trading_errors=50))

        self.assertTrue(report.is_onboarding_approved)
        weakened = " ".join(report.policy_weakened)
        for threshold in ("min_walk_forward_score", "min_regimes_covered",
                          "min_backtest_sharpe", "min_paper_trading_days",
                          "max_paper_trading_errors"):
            self.assertIn(threshold, weakened)

    def test_tightened_policy_is_not_reported_as_weakened(self):
        config = OnboardingPolicyConfig(min_paper_trading_days=60,
                                        min_walk_forward_score=0.9)
        self.assertEqual(config.weakened_thresholds(), [])

    def test_invalid_policy_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            OnboardingPolicyConfig(min_paper_trading_days=-1)
        with self.assertRaises(ValueError):
            OnboardingPolicyConfig(max_paper_trading_errors=-1)
        with self.assertRaises(ValueError):
            OnboardingPolicyConfig(min_backtest_sharpe=float("nan"))
        with self.assertRaises(ValueError):
            OnboardingPolicyConfig(min_regimes_covered=1.5)

    def test_engine_rejects_a_non_config_object(self):
        with self.assertRaises(ValueError):
            NewStrategyOnboardingEngine({"min_paper_trading_days": 0})


if __name__ == '__main__':
    unittest.main()
