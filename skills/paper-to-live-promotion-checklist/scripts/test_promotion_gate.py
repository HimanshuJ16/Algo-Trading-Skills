"""
Unit tests for the paper-to-live-promotion-checklist skill.

Coverage groups
---------------
1.  Happy path, conjunctivity, and per-check isolation (a failing criterion fails only
    its own check).
2.  Inclusive-threshold boundaries on every numeric criterion.
3.  Tolerance semantics: ``slippage_tolerance_pct`` is RELATIVE, ``accuracy_tolerance_pct``
    is ABSOLUTE. The two share a suffix and not a meaning, so each is pinned by a case
    that only one interpretation can satisfy.
4.  Input validation: missing keys, NaN/Inf, bool-as-int, percent-vs-fraction unit errors.
5.  Regressions against specific 1.x defects, each of which is asserted to be impossible
    now rather than merely improved:
      - a zero / negative / NaN ``modeled_slippage`` PASSED ``slippage_alignment``
        unconditionally (division guarded by ``if bt_slip > 0 else 0.0``);
      - absent keys were silently defaulted, fabricating observations;
      - a drawdown supplied as a negative magnitude silently suppressed every rollback;
      - the rollback message quoted "2x paper baseline" even when the absolute floor was
        the binding threshold.
6.  Sampling-noise advisory: at the gate's own ``min_trades_count`` the accuracy tolerance
    is narrower than the 95% binomial half-width. Expected values are derived here from
    ``sqrt(p(1-p)/n)`` independently of the implementation's own formula path.
7.  Sign-off: the discrete human step, and ``is_authorised`` requiring it.
8.  Deprecated ``evaluate_promotion_gate``: still functional, still weaker, and no longer
    able to pass on corrupt input.
"""
import logging
import math
import unittest

from promotion_gate import (
    DEFAULT_MIN_PAPER_DAYS,
    DEFAULT_MIN_TRADES_COUNT,
    PaperToLivePromotionGate,
    PromotionDecisionReport,
    evaluate_promotion_gate,
)

# Keep the deprecation warning and the CRITICAL rollback lines out of test output.
_module_logger = logging.getLogger("promotion_gate")
_module_logger.addHandler(logging.NullHandler())
_module_logger.propagate = False


def _binomial_half_width_95(p: float, n: int) -> float:
    """Independent restatement of the 95% half-width the module should be reporting."""
    return 1.959963984540054 * math.sqrt(p * (1.0 - p) / n)


class PromotionGateTestBase(unittest.TestCase):

    def setUp(self):
        self.gate = PaperToLivePromotionGate()
        self.paper = {
            "days_run": 25,
            "trades_count": 45,
            "avg_slippage": 0.00105,
            "signal_accuracy": 0.58,
            "risk_controls_triggered": 2,
            "reauth_cycles_survived": 3,
        }
        self.backtest = {
            "modeled_slippage": 0.00100,
            "walk_forward_accuracy": 0.56,
        }

    def paper_with(self, **overrides):
        stats = dict(self.paper)
        stats.update(overrides)
        return stats

    def backtest_with(self, **overrides):
        stats = dict(self.backtest)
        stats.update(overrides)
        return stats

    def check(self, report, name):
        return next(c for c in report.checks if c.check_name == name)


class TestGateVerdict(PromotionGateTestBase):

    def test_all_criteria_pass(self):
        report = self.gate.evaluate_gate(self.paper, self.backtest)
        self.assertTrue(report.approved)
        self.assertIn("APPROVED", report.summary)
        self.assertEqual(len(report.checks), 6)
        self.assertEqual(report.failed_checks, [])
        self.assertEqual(report.policy_weakened, [])
        for c in report.checks:
            self.assertIs(type(c.passed), bool, c.check_name)

    def test_approved_is_not_authorised_until_signed_off(self):
        """The central premise: a green gate is not a promotion decision."""
        report = self.gate.evaluate_gate(self.paper, self.backtest)
        self.assertTrue(report.approved)
        self.assertFalse(report.is_authorised)
        self.assertIsNone(report.reviewer_id)
        self.assertIsNone(report.initial_live_sizing_pct)

    def test_policy_applied_is_embedded_in_every_report(self):
        report = self.gate.evaluate_gate(self.paper, self.backtest)
        self.assertEqual(report.policy_applied["min_days"], DEFAULT_MIN_PAPER_DAYS)
        self.assertEqual(
            report.policy_applied["min_trades_count"], DEFAULT_MIN_TRADES_COUNT)
        self.assertEqual(report.policy_applied["accuracy_tolerance_absolute"], 0.10)
        self.assertEqual(report.policy_applied["slippage_tolerance_pct_relative"], 0.15)

    def test_zeroed_policy_still_reports_approved_but_names_the_relaxations(self):
        """A permissive gate must not be indistinguishable from a strict one."""
        loose = PaperToLivePromotionGate(min_days=0, min_trades_count=0)
        report = loose.evaluate_gate(
            self.paper_with(days_run=1, trades_count=1), self.backtest)
        self.assertTrue(report.approved)
        weakened = " ".join(report.policy_weakened)
        self.assertIn("min_days", weakened)
        self.assertIn("min_trades_count", weakened)

    def test_each_failure_fails_only_its_own_check(self):
        cases = {
            "min_paper_duration": self.paper_with(days_run=10),
            "min_trades_count": self.paper_with(trades_count=5),
            "risk_controls_exercised": self.paper_with(risk_controls_triggered=0),
            "auth_reauth_survived": self.paper_with(reauth_cycles_survived=0),
            "accuracy_alignment": self.paper_with(signal_accuracy=0.20),
            "slippage_alignment": self.paper_with(avg_slippage=0.005),
        }
        for expected_failure, paper in cases.items():
            with self.subTest(check=expected_failure):
                report = self.gate.evaluate_gate(paper, self.backtest)
                self.assertFalse(report.approved)
                self.assertEqual(report.failed_checks, [expected_failure])

    def test_gate_is_conjunctive_not_scored(self):
        """5/6 is exactly as rejected as 0/6; there is no partial credit."""
        report = self.gate.evaluate_gate(
            self.paper_with(reauth_cycles_survived=0), self.backtest)
        self.assertEqual(len(report.failed_checks), 1)
        self.assertFalse(report.approved)
        self.assertIn("REJECTED", report.summary)


class TestBoundaries(PromotionGateTestBase):

    def test_duration_and_trade_count_floors_are_inclusive(self):
        exactly = self.gate.evaluate_gate(
            self.paper_with(days_run=20, trades_count=30), self.backtest)
        self.assertTrue(exactly.approved)
        for field, value, failing in (
            ("days_run", 19, "min_paper_duration"),
            ("trades_count", 29, "min_trades_count"),
        ):
            with self.subTest(field=field):
                report = self.gate.evaluate_gate(
                    self.paper_with(**{field: value}), self.backtest)
                self.assertEqual(report.failed_checks, [failing])

    def test_slippage_relative_tolerance_boundary_is_inclusive(self):
        # Binary-exact values so the boundary is not decided by float representation:
        # |0.75 - 0.5| / 0.5 == 0.5 exactly.
        gate = PaperToLivePromotionGate(slippage_tolerance_pct=0.5)
        at = gate.evaluate_gate(
            self.paper_with(avg_slippage=0.75), self.backtest_with(modeled_slippage=0.5))
        self.assertTrue(self.check(at, "slippage_alignment").passed)
        over = gate.evaluate_gate(
            self.paper_with(avg_slippage=0.8), self.backtest_with(modeled_slippage=0.5))
        self.assertFalse(self.check(over, "slippage_alignment").passed)

    def test_accuracy_absolute_tolerance_boundary_is_inclusive(self):
        # |0.75 - 0.5| == 0.25 exactly.
        gate = PaperToLivePromotionGate(accuracy_tolerance_pct=0.25)
        at = gate.evaluate_gate(
            self.paper_with(signal_accuracy=0.75),
            self.backtest_with(walk_forward_accuracy=0.5))
        self.assertTrue(self.check(at, "accuracy_alignment").passed)
        over = gate.evaluate_gate(
            self.paper_with(signal_accuracy=0.8),
            self.backtest_with(walk_forward_accuracy=0.5))
        self.assertFalse(self.check(over, "accuracy_alignment").passed)

    def test_risk_control_and_reauth_floors(self):
        for field, check_name in (
            ("risk_controls_triggered", "risk_controls_exercised"),
            ("reauth_cycles_survived", "auth_reauth_survived"),
        ):
            with self.subTest(field=field):
                one = self.gate.evaluate_gate(
                    self.paper_with(**{field: 1}), self.backtest)
                self.assertTrue(self.check(one, check_name).passed)
                zero = self.gate.evaluate_gate(
                    self.paper_with(**{field: 0}), self.backtest)
                self.assertFalse(self.check(zero, check_name).passed)


class TestToleranceSemantics(PromotionGateTestBase):
    """
    The two tolerance parameters share a ``_pct`` suffix and not a meaning. Each test picks
    a case that only one interpretation can satisfy, so a future refactor that unifies them
    breaks here rather than in production.
    """

    def test_accuracy_tolerance_is_absolute_percentage_points(self):
        # 0.20 -> 0.28 is 8pp absolute (inside the 10pp band) but 40% relative
        # (far outside a 10% relative band). Passing pins the ABSOLUTE reading.
        report = self.gate.evaluate_gate(
            self.paper_with(signal_accuracy=0.28),
            self.backtest_with(walk_forward_accuracy=0.20))
        self.assertTrue(self.check(report, "accuracy_alignment").passed)
        self.assertIn("pp", self.check(report, "accuracy_alignment").expected_value)

    def test_slippage_tolerance_is_relative_not_absolute(self):
        # A tiny ABSOLUTE difference (0.0001) that is a 100% RELATIVE miss must fail.
        tiny_but_double = self.gate.evaluate_gate(
            self.paper_with(avg_slippage=0.0002),
            self.backtest_with(modeled_slippage=0.0001))
        self.assertFalse(self.check(tiny_but_double, "slippage_alignment").passed)
        # A large ABSOLUTE difference (0.10) that is only a 10% RELATIVE miss must pass.
        large_but_close = self.gate.evaluate_gate(
            self.paper_with(avg_slippage=1.10),
            self.backtest_with(modeled_slippage=1.0))
        self.assertTrue(self.check(large_but_close, "slippage_alignment").passed)

    def test_slippage_check_is_two_sided(self):
        """Paper slippage far BELOW the model is a model failure too, not a bonus."""
        report = self.gate.evaluate_gate(
            self.paper_with(avg_slippage=0.0001), self.backtest)
        self.assertFalse(self.check(report, "slippage_alignment").passed)

    def test_negative_paper_slippage_raises_an_advisory(self):
        report = self.gate.evaluate_gate(
            self.paper_with(avg_slippage=-0.001), self.backtest)
        self.assertTrue(
            any("price improvement" in a for a in report.advisories), report.advisories)


class TestSamplingNoiseAdvisory(PromotionGateTestBase):

    def test_advisory_fires_when_tolerance_is_inside_the_sampling_noise(self):
        """
        At n=30 and p=0.56 the 95% half-width is ~17.8pp, so the default 10pp band cannot
        distinguish a real 10pp degradation from noise. Expected value derived here.
        """
        half_width = _binomial_half_width_95(0.56, 30)
        self.assertGreater(half_width, 0.10)  # sanity: the premise of the advisory holds
        report = self.gate.evaluate_gate(
            self.paper_with(trades_count=30), self.backtest)
        advisory = next(
            (a for a in report.advisories if "sampling half-width" in a), None)
        self.assertIsNotNone(advisory, report.advisories)
        self.assertIn("n=30", advisory)
        self.assertIn(f"{half_width * 100:.1f}pp", advisory)

    def test_no_advisory_once_the_sample_is_large_enough(self):
        half_width = _binomial_half_width_95(0.56, 400)
        self.assertLess(half_width, 0.10)
        gate = PaperToLivePromotionGate(min_trades_count=400)
        report = gate.evaluate_gate(self.paper_with(trades_count=400), self.backtest)
        self.assertEqual(
            [a for a in report.advisories if "sampling half-width" in a], [])

    def test_accuracy_sample_size_overrides_trades_count(self):
        """Accuracy measured per-bar, not per-trade, must be annotated against its own n."""
        report = self.gate.evaluate_gate(
            self.paper_with(trades_count=45, accuracy_sample_size=5000), self.backtest)
        self.assertEqual(
            [a for a in report.advisories if "sampling half-width" in a], [])

    def test_zero_sample_size_is_reported_as_not_evaluable(self):
        report = self.gate.evaluate_gate(
            self.paper_with(trades_count=0), self.backtest)
        self.assertTrue(
            any("not evaluable" in a for a in report.advisories), report.advisories)

    def test_advisory_never_changes_the_verdict(self):
        report = self.gate.evaluate_gate(
            self.paper_with(trades_count=30), self.backtest)
        self.assertTrue(report.approved)
        self.assertTrue(report.advisories)


class TestInputValidation(PromotionGateTestBase):

    def test_missing_paper_key_raises_and_names_the_key(self):
        for key in (
            "days_run", "trades_count", "avg_slippage", "signal_accuracy",
            "risk_controls_triggered", "reauth_cycles_survived",
        ):
            with self.subTest(key=key):
                stats = dict(self.paper)
                del stats[key]
                with self.assertRaises(ValueError) as ctx:
                    self.gate.evaluate_gate(stats, self.backtest)
                self.assertIn(key, str(ctx.exception))

    def test_missing_backtest_key_raises(self):
        for key in ("modeled_slippage", "walk_forward_accuracy"):
            with self.subTest(key=key):
                stats = dict(self.backtest)
                del stats[key]
                with self.assertRaises(ValueError) as ctx:
                    self.gate.evaluate_gate(self.paper, stats)
                self.assertIn(key, str(ctx.exception))

    def test_non_finite_metrics_raise_rather_than_rejecting_the_strategy(self):
        """A corrupt metric is a data failure routed to a different team than a weak one."""
        for field, value in (
            ("avg_slippage", float("nan")),
            ("avg_slippage", float("inf")),
            ("signal_accuracy", float("nan")),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    self.gate.evaluate_gate(
                        self.paper_with(**{field: value}), self.backtest)

    def test_bools_are_not_accepted_as_counts(self):
        for field in ("days_run", "trades_count", "risk_controls_triggered",
                      "reauth_cycles_survived"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.gate.evaluate_gate(
                        self.paper_with(**{field: True}), self.backtest)

    def test_negative_counts_raise(self):
        with self.assertRaises(ValueError):
            self.gate.evaluate_gate(
                self.paper_with(risk_controls_triggered=-1), self.backtest)

    def test_percent_instead_of_fraction_raises(self):
        """``signal_accuracy=58`` is a unit error, not a 5700pp divergence."""
        with self.assertRaises(ValueError) as ctx:
            self.gate.evaluate_gate(
                self.paper_with(signal_accuracy=58), self.backtest)
        self.assertIn("0.58", str(ctx.exception))
        with self.assertRaises(ValueError):
            self.gate.evaluate_gate(
                self.paper, self.backtest_with(walk_forward_accuracy=56))

    def test_string_payload_raises(self):
        with self.assertRaises(ValueError):
            self.gate.evaluate_gate(self.paper_with(days_run="25"), self.backtest)

    def test_non_dict_payload_raises(self):
        with self.assertRaises(ValueError):
            self.gate.evaluate_gate([], self.backtest)

    def test_invalid_thresholds_are_rejected_at_construction(self):
        for kwargs in (
            {"min_days": -1},
            {"min_days": 20.5},
            {"slippage_tolerance_pct": float("nan")},
            {"slippage_tolerance_pct": -0.1},
            {"accuracy_tolerance_pct": 1.5},
            {"rollback_drawdown_multiple": 0.5},
            {"rollback_drawdown_floor": -0.05},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    PaperToLivePromotionGate(**kwargs)


class TestZeroSlippageRegression(PromotionGateTestBase):
    """
    Regression for the most dangerous 1.x defect.

    ``slip_diff = abs(paper - bt) / bt if bt > 0 else 0.0`` meant a backtest that modelled
    zero, negative or NaN execution cost PASSED ``slippage_alignment`` unconditionally --
    the free-execution backtest this gate exists to catch was the one input guaranteed to
    clear the check, whatever the paper period actually observed.
    """

    def test_zero_modeled_slippage_raises_instead_of_passing(self):
        with self.assertRaises(ValueError) as ctx:
            self.gate.evaluate_gate(
                self.paper_with(avg_slippage=0.05),
                self.backtest_with(modeled_slippage=0.0))
        self.assertIn("modeled_slippage", str(ctx.exception))

    def test_negative_modeled_slippage_raises_instead_of_passing(self):
        with self.assertRaises(ValueError):
            self.gate.evaluate_gate(
                self.paper_with(avg_slippage=0.05),
                self.backtest_with(modeled_slippage=-0.001))

    def test_nan_modeled_slippage_raises_instead_of_passing(self):
        with self.assertRaises(ValueError):
            self.gate.evaluate_gate(
                self.paper_with(avg_slippage=0.05),
                self.backtest_with(modeled_slippage=float("nan")))


class TestRollbackTrigger(PromotionGateTestBase):

    def setUp(self):
        super().setUp()
        self.baseline = {"max_drawdown_pct": 0.02, "avg_slippage": 0.001}

    def test_drawdown_breach_triggers(self):
        triggered, msg = self.gate.check_rollback_trigger(
            {"max_drawdown_pct": 0.08, "avg_slippage": 0.001}, self.baseline)
        self.assertTrue(triggered)
        self.assertIn("ROLLBACK TRIGGERED", msg)

    def test_message_names_the_threshold_actually_applied(self):
        """
        Regression: 1.x reported ``paper_dd * 2`` as "the threshold" even when the absolute
        floor was binding, putting a number in the audit trail that was never used. With a
        2% paper baseline the applied threshold is max(5%, 4%) = 5%, not 4%.
        """
        _, msg = self.gate.check_rollback_trigger(
            {"max_drawdown_pct": 0.08, "avg_slippage": 0.001}, self.baseline)
        self.assertIn("5.00%", msg)
        self.assertIn("absolute floor", msg)
        self.assertNotIn("applied threshold 4.00%", msg)

    def test_message_names_the_multiple_when_the_multiple_binds(self):
        _, msg = self.gate.check_rollback_trigger(
            {"max_drawdown_pct": 0.21, "avg_slippage": 0.001},
            {"max_drawdown_pct": 0.10, "avg_slippage": 0.001})
        self.assertIn("20.00%", msg)
        self.assertIn("2x paper baseline", msg)

    def test_threshold_is_inclusive_and_below_it_is_quiet(self):
        at, _ = self.gate.check_rollback_trigger(
            {"max_drawdown_pct": 0.05, "avg_slippage": 0.001}, self.baseline)
        self.assertTrue(at)
        below, msg = self.gate.check_rollback_trigger(
            {"max_drawdown_pct": 0.0499, "avg_slippage": 0.001}, self.baseline)
        self.assertFalse(below)
        self.assertIn("within acceptable bounds", msg)

    def test_slippage_breach_triggers_independently(self):
        triggered, msg = self.gate.check_rollback_trigger(
            {"max_drawdown_pct": 0.01, "avg_slippage": 0.02}, self.baseline)
        self.assertTrue(triggered)
        self.assertIn("live slippage", msg)
        self.assertNotIn("live drawdown", msg)

    def test_simultaneous_breaches_report_both_reasons(self):
        """1.x returned on the first breach, hiding the second from the audit trail."""
        _, msg = self.gate.check_rollback_trigger(
            {"max_drawdown_pct": 0.30, "avg_slippage": 0.02}, self.baseline)
        self.assertIn("live drawdown", msg)
        self.assertIn("live slippage", msg)

    def test_negative_drawdown_raises_instead_of_silently_suppressing(self):
        """
        Regression: an 8% drawdown supplied as ``-0.08`` failed every ``>=`` comparison in
        1.x, so the worse the loss the more certainly the trigger stayed quiet.
        """
        with self.assertRaises(ValueError) as ctx:
            self.gate.check_rollback_trigger(
                {"max_drawdown_pct": -0.08, "avg_slippage": 0.001}, self.baseline)
        self.assertIn("0.08", str(ctx.exception))

    def test_drawdown_as_whole_number_raises(self):
        with self.assertRaises(ValueError):
            self.gate.check_rollback_trigger(
                {"max_drawdown_pct": 8, "avg_slippage": 0.001}, self.baseline)

    def test_missing_rollback_keys_raise(self):
        with self.assertRaises(ValueError):
            self.gate.check_rollback_trigger({"avg_slippage": 0.001}, self.baseline)
        with self.assertRaises(ValueError):
            self.gate.check_rollback_trigger(
                {"max_drawdown_pct": 0.01, "avg_slippage": 0.001},
                {"max_drawdown_pct": 0.02})

    def test_configured_floors_are_honoured(self):
        strict = PaperToLivePromotionGate(rollback_drawdown_floor=0.01)
        triggered, msg = strict.check_rollback_trigger(
            {"max_drawdown_pct": 0.045, "avg_slippage": 0.001}, self.baseline)
        self.assertTrue(triggered)
        self.assertIn("4.00%", msg)  # 2x the 2% baseline now binds, not the 1% floor


class TestSignOff(PromotionGateTestBase):

    def approved_report(self) -> PromotionDecisionReport:
        return self.gate.evaluate_gate(self.paper, self.backtest)

    def test_sign_off_authorises(self):
        report = self.approved_report().record_sign_off(
            reviewer_id="risk.officer@example.com",
            initial_live_sizing_pct=0.10,
            rollback_drawdown_pct=0.05,
            decided_at="2026-08-27T09:30:00+00:00",
        )
        self.assertTrue(report.is_authorised)
        self.assertEqual(report.reviewer_id, "risk.officer@example.com")
        self.assertEqual(report.initial_live_sizing_pct, 0.10)
        self.assertEqual(report.decided_at, "2026-08-27T09:30:00+00:00")

    def test_sign_off_refused_on_a_rejected_report(self):
        rejected = self.gate.evaluate_gate(
            self.paper_with(days_run=1), self.backtest)
        with self.assertRaises(ValueError) as ctx:
            rejected.record_sign_off("reviewer", 0.10, 0.05, "2026-08-27")
        self.assertIn("min_paper_duration", str(ctx.exception))

    def test_blank_reviewer_is_not_a_sign_off(self):
        for bad in ("", "   ", None, 42):
            with self.subTest(reviewer=bad):
                with self.assertRaises(ValueError):
                    self.approved_report().record_sign_off(bad, 0.10, 0.05, "2026-08-27")

    def test_sizing_must_be_a_fraction_in_the_open_unit_interval(self):
        for bad in (0.0, -0.1, 1.5, 25):
            with self.subTest(sizing=bad):
                with self.assertRaises(ValueError):
                    self.approved_report().record_sign_off(
                        "reviewer", bad, 0.05, "2026-08-27")

    def test_full_size_go_live_is_flagged(self):
        report = self.approved_report().record_sign_off(
            "reviewer", 1.0, 0.05, "2026-08-27")
        self.assertTrue(report.is_authorised)
        self.assertTrue(
            any("no reduced-size window" in a for a in report.advisories),
            report.advisories)

    def test_blank_decision_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            self.approved_report().record_sign_off("reviewer", 0.10, 0.05, "  ")

    def test_sizing_default_is_not_asserted_by_the_engine(self):
        """
        1.x shipped ``initial_live_sizing_pct=0.25`` as a dataclass default that nothing
        read, and which contradicts the 10% seed tier of
        ``incremental-capital-deployment-for-new-strategies``. The engine must record the
        reviewer's number, never supply one.
        """
        self.assertIsNone(self.approved_report().initial_live_sizing_pct)
        self.assertIsNone(self.approved_report().rollback_drawdown_pct)


class TestDeprecatedHelper(PromotionGateTestBase):

    def test_still_returns_the_1x_shape(self):
        result = evaluate_promotion_gate(self.paper, self.backtest)
        self.assertTrue(result["all_pass"])
        self.assertTrue(result["min_duration_met"])
        self.assertEqual(
            set(result),
            {"min_duration_met", "slippage_within_tolerance",
             "accuracy_within_tolerance", "risk_controls_exercised", "all_pass"},
        )

    def test_it_is_a_weaker_gate_than_evaluate_gate(self):
        """
        Documented trap: the short name checks 4 of 6 criteria. A strategy with 2 trades and
        no surviving reauth cycle passes here and is rejected by the full gate.
        """
        thin = self.paper_with(trades_count=2, reauth_cycles_survived=0)
        self.assertTrue(evaluate_promotion_gate(thin, self.backtest)["all_pass"])
        self.assertFalse(self.gate.evaluate_gate(thin, self.backtest).approved)

    def test_it_no_longer_passes_on_corrupt_input(self):
        with self.assertRaises(ValueError):
            evaluate_promotion_gate(
                self.paper, self.backtest_with(modeled_slippage=0.0))
        with self.assertRaises(ValueError):
            evaluate_promotion_gate(
                self.paper_with(signal_accuracy=float("nan")), self.backtest)

    def test_missing_key_raises_rather_than_defaulting(self):
        stats = dict(self.paper)
        del stats["signal_accuracy"]
        with self.assertRaises(ValueError):
            evaluate_promotion_gate(stats, self.backtest)


if __name__ == "__main__":
    unittest.main()
