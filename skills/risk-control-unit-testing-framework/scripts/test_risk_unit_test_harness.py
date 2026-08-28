import logging
import math
import unittest

from risk_unit_test_harness import (
    DEFAULT_REQUIRED_RULE_COVERAGE,
    PreTradeRiskEngine,
    ProposedOrder,
    RiskCheckResult,
    RiskControlUnitTestFrameworkEngine,
    RiskRuleConfig,
    RiskTestCase,
    RULE_DAILY_LOSS_LIMIT,
    RULE_FAT_FINGER_PRICE_COLLAR,
    RULE_INVALID_ORDER,
    RULE_MAX_ORDER_SIZE,
    RULE_POSITION_CAP,
    RULE_REFERENCE_PRICE_UNAVAILABLE,
    STATUS_COVERAGE_INCOMPLETE,
    STATUS_FAILURES,
    STATUS_LATENCY_BREACH,
    STATUS_PASSED,
    STATUS_SUITE_EMPTY,
)

# Keep the suite's own gate-status warnings off the CI console. assertLogs still
# works: it installs its own handler and toggles propagation for the duration.
logging.getLogger("risk_unit_test_harness").addHandler(logging.NullHandler())
logging.getLogger("risk_unit_test_harness").propagate = False

DEFAULT_CONFIG = RiskRuleConfig(
    rule_id="TEST_RULESET",
    max_order_size=1000.0,
    max_position_size=5000.0,
    max_daily_loss_usd=10000.0,
    max_price_collar_pct=0.05,
)


def order(**overrides):
    """A valid order under DEFAULT_CONFIG, with named fields overridden."""
    base = dict(
        order_id="O", symbol="AAPL", side="BUY", quantity=100.0, price=150.0,
        current_mid_price=150.0,
    )
    base.update(overrides)
    return ProposedOrder(**base)


class TestRiskRuleConfigValidation(unittest.TestCase):
    """A mis-configured ruleset must raise, never silently behave as 'unlimited'."""

    def test_blank_rule_id_rejected(self):
        with self.assertRaises(ValueError):
            RiskRuleConfig(rule_id="   ")

    def test_zero_max_order_size_rejected(self):
        # FIA (Mar 2015) 1.1: no order-size limit set must not mean no limit applied.
        with self.assertRaises(ValueError):
            RiskRuleConfig(rule_id="R", max_order_size=0.0)

    def test_infinite_max_position_size_rejected(self):
        with self.assertRaises(ValueError):
            RiskRuleConfig(rule_id="R", max_position_size=float("inf"))

    def test_nan_collar_rejected(self):
        with self.assertRaises(ValueError):
            RiskRuleConfig(rule_id="R", max_price_collar_pct=float("nan"))

    def test_negative_daily_loss_limit_rejected(self):
        # The limit is a positive magnitude; a negative value inverts the comparison.
        with self.assertRaises(ValueError):
            RiskRuleConfig(rule_id="R", max_daily_loss_usd=-10000.0)

    def test_config_is_frozen(self):
        cfg = RiskRuleConfig(rule_id="R")
        with self.assertRaises(Exception):
            cfg.max_order_size = 10.0


class TestPreTradeRiskEngineThresholds(unittest.TestCase):
    """Expected values are derived from the documented convention, not from the code:
    the configured limit value is permitted; a breach requires exceeding it."""

    def setUp(self):
        self.engine = PreTradeRiskEngine(DEFAULT_CONFIG)

    def evaluate(self, **overrides) -> RiskCheckResult:
        return self.engine.evaluate_order(order(**overrides))

    def test_valid_order_allowed_with_no_rules(self):
        res = self.evaluate()
        self.assertTrue(res.is_allowed)
        self.assertEqual(res.triggered_rules, [])
        self.assertEqual(res.rejection_reasons, [])

    def test_order_size_at_exact_limit_allowed(self):
        self.assertTrue(self.evaluate(quantity=1000.0).is_allowed)

    def test_order_size_one_cent_over_limit_rejected(self):
        res = self.evaluate(quantity=1000.01)
        self.assertFalse(res.is_allowed)
        self.assertEqual(res.triggered_rules, [RULE_MAX_ORDER_SIZE])

    def test_position_cap_at_exact_limit_allowed(self):
        # 4000 + 1000 == 5000 == the cap.
        self.assertTrue(self.evaluate(quantity=1000.0, current_position=4000.0).is_allowed)

    def test_position_cap_one_increment_past_limit_rejected(self):
        # 4000.01 + 1000 == 5000.01. Kept under max_order_size so exactly one rule fires.
        res = self.evaluate(quantity=1000.0, current_position=4000.01)
        self.assertEqual(res.triggered_rules, [RULE_POSITION_CAP])

    def test_position_cap_long_breach(self):
        res = self.evaluate(quantity=900.0, current_position=4500.0)
        self.assertEqual(res.triggered_rules, [RULE_POSITION_CAP])

    def test_position_cap_short_breach(self):
        res = self.evaluate(side="SELL", quantity=900.0, current_position=-4500.0)
        self.assertEqual(res.triggered_rules, [RULE_POSITION_CAP])

    def test_short_sell_does_not_breach_long_cap(self):
        # Regression: a SELL that reduces a long position must not be projected long.
        self.assertTrue(self.evaluate(side="SELL", quantity=900.0,
                                      current_position=4500.0).is_allowed)

    def test_daily_loss_at_exact_limit_allowed(self):
        self.assertTrue(self.evaluate(accumulated_daily_pnl_usd=-10000.0).is_allowed)

    def test_daily_loss_one_dollar_past_limit_rejected(self):
        res = self.evaluate(accumulated_daily_pnl_usd=-10000.01)
        self.assertEqual(res.triggered_rules, [RULE_DAILY_LOSS_LIMIT])

    def test_profit_never_triggers_loss_limit(self):
        self.assertTrue(self.evaluate(accumulated_daily_pnl_usd=50000.0).is_allowed)

    def test_multiple_simultaneous_breaches_all_reported(self):
        res = self.evaluate(quantity=6000.0, price=300.0,
                            accumulated_daily_pnl_usd=-20000.0)
        self.assertEqual(
            set(res.triggered_rules),
            {RULE_MAX_ORDER_SIZE, RULE_POSITION_CAP,
             RULE_FAT_FINGER_PRICE_COLLAR, RULE_DAILY_LOSS_LIMIT},
        )

    def test_disabled_ruleset_allows_everything(self):
        disabled = PreTradeRiskEngine(RiskRuleConfig(rule_id="OFF", enabled=False))
        with self.assertLogs("risk_unit_test_harness", level=logging.WARNING):
            res = disabled.evaluate_order(order(quantity=1e9))
        self.assertTrue(res.is_allowed)


class TestPriceCollarNumerics(unittest.TestCase):
    def setUp(self):
        self.engine = PreTradeRiskEngine(DEFAULT_CONFIG)

    def test_price_at_exact_collar_allowed_for_awkward_reference_price(self):
        # Regression: abs(422.8245 - 402.69) / 402.69 == 0.05000000000000001 > 0.05,
        # so the division form spuriously rejects an order priced at exactly the
        # collar. The multiplication form (dev > collar * mid) does not.
        res = self.engine.evaluate_order(
            order(price=422.8245, current_mid_price=402.69))
        self.assertTrue(res.is_allowed, res.rejection_reasons)

    def test_price_just_past_collar_rejected(self):
        res = self.engine.evaluate_order(
            order(price=422.83, current_mid_price=402.69))
        self.assertEqual(res.triggered_rules, [RULE_FAT_FINGER_PRICE_COLLAR])

    def test_collar_symmetric_below_mid(self):
        res = self.engine.evaluate_order(order(price=100.0, current_mid_price=150.0))
        self.assertEqual(res.triggered_rules, [RULE_FAT_FINGER_PRICE_COLLAR])

    def test_zero_collar_allows_only_the_reference_price(self):
        engine = PreTradeRiskEngine(
            RiskRuleConfig(rule_id="TIGHT", max_price_collar_pct=0.0))
        self.assertTrue(
            engine.evaluate_order(order(price=150.0, current_mid_price=150.0)).is_allowed)
        self.assertIn(
            RULE_FAT_FINGER_PRICE_COLLAR,
            engine.evaluate_order(
                order(price=150.01, current_mid_price=150.0)).triggered_rules)


class TestFailClosedBehaviour(unittest.TestCase):
    """Every check compares with `>`; `NaN > limit` is False, so unvalidated
    non-finite input would be allowed by every rule. These must all reject."""

    def setUp(self):
        self.engine = PreTradeRiskEngine(DEFAULT_CONFIG)

    def assertInvalid(self, **overrides):
        res = self.engine.evaluate_order(order(**overrides))
        self.assertFalse(res.is_allowed)
        self.assertEqual(res.triggered_rules, [RULE_INVALID_ORDER])
        self.assertTrue(res.rejection_reasons)
        return res

    def test_nan_quantity_rejected(self):
        self.assertInvalid(quantity=float("nan"))

    def test_infinite_quantity_rejected(self):
        self.assertInvalid(quantity=float("inf"))

    def test_zero_quantity_rejected(self):
        self.assertInvalid(quantity=0.0)

    def test_negative_quantity_rejected(self):
        # A negative quantity passes `quantity > max_order_size` and subtracts from
        # the projected position, so it would pass both size and cap checks.
        self.assertInvalid(quantity=-5000.0)

    def test_nan_price_rejected(self):
        self.assertInvalid(price=float("nan"))

    def test_non_positive_price_rejected(self):
        self.assertInvalid(price=0.0)

    def test_nan_position_rejected(self):
        self.assertInvalid(current_position=float("nan"))

    def test_nan_daily_pnl_rejected(self):
        self.assertInvalid(accumulated_daily_pnl_usd=float("nan"))

    def test_negative_working_quantity_rejected(self):
        self.assertInvalid(working_buy_quantity=-100.0)

    def test_unrecognised_side_rejected(self):
        # Regression: `side.upper() == "BUY"` treated every other value as a SELL,
        # flipping the sign of the position projection.
        self.assertInvalid(side="BUYY")

    def test_empty_side_rejected(self):
        self.assertInvalid(side="")

    def test_non_string_side_rejected(self):
        self.assertInvalid(side=None)

    def test_lowercase_and_padded_side_accepted(self):
        self.assertTrue(self.engine.evaluate_order(order(side=" buy ")).is_allowed)

    def test_invalid_order_short_circuits_other_rules(self):
        # A NaN quantity makes every limit comparison meaningless, so only
        # INVALID_ORDER is reported rather than a misleading rule list.
        res = self.engine.evaluate_order(
            order(quantity=float("nan"), price=1000.0, current_mid_price=150.0))
        self.assertEqual(res.triggered_rules, [RULE_INVALID_ORDER])

    def test_zero_reference_price_blocks_instead_of_skipping_collar(self):
        # Regression: `if mid > 0` silently disabled the collar when the reference
        # feed was stale/absent — exactly when a fat finger is most likely.
        res = self.engine.evaluate_order(
            order(price=999999.0, current_mid_price=0.0))
        self.assertFalse(res.is_allowed)
        self.assertEqual(res.triggered_rules, [RULE_REFERENCE_PRICE_UNAVAILABLE])

    def test_nan_reference_price_blocks(self):
        res = self.engine.evaluate_order(order(current_mid_price=float("nan")))
        self.assertEqual(res.triggered_rules, [RULE_REFERENCE_PRICE_UNAVAILABLE])

    def test_unusable_reference_price_does_not_mask_other_breaches(self):
        res = self.engine.evaluate_order(order(quantity=2000.0, current_mid_price=0.0))
        self.assertEqual(
            set(res.triggered_rules),
            {RULE_MAX_ORDER_SIZE, RULE_REFERENCE_PRICE_UNAVAILABLE},
        )


class TestWorkingOrderPositionProjection(unittest.TestCase):
    """FIA (Mar 2015) 1.2: working orders must be included in the projection, else
    N individually-compliant orders can jointly breach the cap."""

    def setUp(self):
        self.engine = PreTradeRiskEngine(DEFAULT_CONFIG)

    def test_working_buys_counted_towards_long_cap(self):
        # 4000 position + 900 resting buys + 200 new = 5100 > 5000. Position-only
        # projection would see 4200 and allow it.
        res = self.engine.evaluate_order(
            order(quantity=200.0, current_position=4000.0, working_buy_quantity=900.0))
        self.assertEqual(res.triggered_rules, [RULE_POSITION_CAP])

    def test_working_sells_counted_towards_short_cap(self):
        res = self.engine.evaluate_order(
            order(side="SELL", quantity=200.0, current_position=-4000.0,
                  working_sell_quantity=900.0))
        self.assertEqual(res.triggered_rules, [RULE_POSITION_CAP])

    def test_working_orders_at_exact_cap_allowed(self):
        res = self.engine.evaluate_order(
            order(quantity=100.0, current_position=4000.0, working_buy_quantity=900.0))
        self.assertTrue(res.is_allowed, res.rejection_reasons)

    def test_working_sells_do_not_offset_the_long_projection(self):
        # Netting resting sells against a long projection would understate the
        # worst case: the buys can fill while the sells do not.
        res = self.engine.evaluate_order(
            order(quantity=200.0, current_position=4000.0,
                  working_buy_quantity=900.0, working_sell_quantity=5000.0))
        self.assertIn(RULE_POSITION_CAP, res.triggered_rules)


class TestAssertionStrength(unittest.TestCase):
    def setUp(self):
        self.framework = RiskControlUnitTestFrameworkEngine(
            PreTradeRiskEngine(DEFAULT_CONFIG))

    def test_exact_rule_match_required(self):
        # This order breaches BOTH size and collar. A membership assertion on
        # MAX_ORDER_SIZE alone would pass and hide the spurious second rejection.
        res = self.framework.run_test_case(
            "size breach only", order(quantity=2000.0, price=300.0),
            expected_allowed=False, expected_triggered_rule=RULE_MAX_ORDER_SIZE)
        self.assertFalse(res.passed)
        self.assertIn(RULE_FAT_FINGER_PRICE_COLLAR, res.actual_triggered_rules)

    def test_exact_rule_set_match_passes(self):
        res = self.framework.run_test_case(
            "size and collar", order(quantity=2000.0, price=300.0),
            expected_allowed=False,
            expected_triggered_rules=[RULE_MAX_ORDER_SIZE,
                                      RULE_FAT_FINGER_PRICE_COLLAR])
        self.assertTrue(res.passed, res.detail)

    def test_rule_order_does_not_affect_match(self):
        res = self.framework.run_test_case(
            "reordered", order(quantity=2000.0, price=300.0),
            expected_allowed=False,
            expected_triggered_rules=[RULE_FAT_FINGER_PRICE_COLLAR,
                                      RULE_MAX_ORDER_SIZE])
        self.assertTrue(res.passed, res.detail)

    def test_rejection_expected_without_naming_a_rule(self):
        res = self.framework.run_test_case(
            "any rejection", order(quantity=2000.0), expected_allowed=False)
        self.assertTrue(res.passed, res.detail)

    def test_wrong_expectation_is_flagged(self):
        res = self.framework.run_test_case(
            "wrong expectation", order(quantity=2000.0), expected_allowed=True)
        self.assertFalse(res.passed)
        self.assertIn("FAILED", res.detail)

    def test_detail_includes_rejection_reasons(self):
        res = self.framework.run_test_case(
            "reasons surfaced", order(quantity=2000.0), expected_allowed=True)
        self.assertIn("2000", res.detail)

    def test_legacy_expected_triggered_rule_field_populated(self):
        res = self.framework.run_test_case(
            "legacy field", order(quantity=2000.0), expected_allowed=False,
            expected_triggered_rule=RULE_MAX_ORDER_SIZE)
        self.assertEqual(res.expected_triggered_rule, RULE_MAX_ORDER_SIZE)


class TestHarnessMisWiringRaises(unittest.TestCase):
    def setUp(self):
        self.framework = RiskControlUnitTestFrameworkEngine(
            PreTradeRiskEngine(DEFAULT_CONFIG))

    def test_blank_test_name_raises(self):
        with self.assertRaises(ValueError):
            self.framework.run_test_case("  ", order(), expected_allowed=True)

    def test_allowed_true_with_expected_rule_raises(self):
        # Self-contradictory expectation: silently "passing" it would be worse.
        with self.assertRaises(ValueError):
            self.framework.run_test_case(
                "contradiction", order(), expected_allowed=True,
                expected_triggered_rule=RULE_MAX_ORDER_SIZE)

    def test_both_rule_arguments_raises(self):
        with self.assertRaises(ValueError):
            self.framework.run_test_case(
                "both", order(quantity=2000.0), expected_allowed=False,
                expected_triggered_rule=RULE_MAX_ORDER_SIZE,
                expected_triggered_rules=[RULE_MAX_ORDER_SIZE])

    def test_bare_string_rule_sequence_raises(self):
        # "MAX_ORDER_SIZE" would otherwise iterate into single characters.
        with self.assertRaises(TypeError):
            self.framework.run_test_case(
                "bare string", order(quantity=2000.0), expected_allowed=False,
                expected_triggered_rules=RULE_MAX_ORDER_SIZE)

    def test_non_order_raises(self):
        with self.assertRaises(TypeError):
            self.framework.run_test_case("bad order", {"quantity": 1}, expected_allowed=True)

    def test_non_bool_expected_allowed_raises(self):
        with self.assertRaises(TypeError):
            self.framework.run_test_case("truthy", order(), expected_allowed=1)

    def test_duplicate_test_names_raise(self):
        case = RiskTestCase("dup", order(), True, ())
        with self.assertRaises(ValueError):
            self.framework.run_suite([case, RiskTestCase("dup", order(), True, ())])

    def test_non_case_suite_entry_raises(self):
        with self.assertRaises(TypeError):
            self.framework.run_suite([object()])

    def test_engine_without_evaluate_order_raises(self):
        with self.assertRaises(TypeError):
            RiskControlUnitTestFrameworkEngine(risk_engine=object())

    def test_enforced_latency_budget_without_a_budget_raises(self):
        with self.assertRaises(ValueError):
            RiskControlUnitTestFrameworkEngine(enforce_latency_budget=True)

    def test_non_positive_latency_budget_raises(self):
        with self.assertRaises(ValueError):
            RiskControlUnitTestFrameworkEngine(latency_budget_microseconds=0.0)


class TestSuiteGate(unittest.TestCase):
    def setUp(self):
        self.framework = RiskControlUnitTestFrameworkEngine(
            PreTradeRiskEngine(DEFAULT_CONFIG))

    def test_standard_suite_passes_and_covers_every_rule(self):
        report = self.framework.run_standard_suite()
        self.assertEqual(report.status, STATUS_PASSED, report.audit_notes)
        self.assertEqual(report.failed_tests, 0)
        self.assertEqual(report.pass_rate_pct, 100.0)
        # Pinned, not derived from build_standard_suite(): SKILL.md documents 16 cases,
        # and silently dropping one must fail here rather than agree with itself.
        self.assertEqual(report.total_tests, 16)
        self.assertTrue(report.coverage_satisfied)
        self.assertEqual(report.missing_rule_coverage, ())
        self.assertEqual(set(report.rules_exercised),
                         set(DEFAULT_REQUIRED_RULE_COVERAGE))
        self.assertEqual(report.ruleset_id, "TEST_RULESET")

    def test_empty_suite_fails_closed(self):
        # failed_tests == 0 here; a pipeline branching on the count would ship.
        report = self.framework.run_suite([])
        self.assertEqual(report.status, STATUS_SUITE_EMPTY)
        self.assertEqual(report.failed_tests, 0)
        self.assertEqual(report.pass_rate_pct, 0.0)
        self.assertFalse(report.coverage_satisfied)

    def test_all_positive_suite_fails_coverage_despite_passing(self):
        cases = [RiskTestCase(f"ok-{i}", order(), True, ()) for i in range(3)]
        report = self.framework.run_suite(cases)
        self.assertEqual(report.failed_tests, 0)
        self.assertEqual(report.status, STATUS_COVERAGE_INCOMPLETE)
        self.assertEqual(set(report.missing_rule_coverage),
                         set(DEFAULT_REQUIRED_RULE_COVERAGE))

    def test_failures_take_precedence_over_coverage(self):
        cases = [RiskTestCase("bad expectation", order(quantity=2000.0), True, ())]
        report = self.framework.run_suite(cases)
        self.assertEqual(report.status, STATUS_FAILURES)
        self.assertEqual(report.failed_tests, 1)

    def test_custom_coverage_requirement_respected(self):
        framework = RiskControlUnitTestFrameworkEngine(
            PreTradeRiskEngine(DEFAULT_CONFIG),
            required_rule_coverage=[RULE_MAX_ORDER_SIZE],
        )
        cases = [RiskTestCase("size", order(quantity=2000.0), False,
                              (RULE_MAX_ORDER_SIZE,))]
        report = framework.run_suite(cases)
        self.assertEqual(report.status, STATUS_PASSED, report.audit_notes)
        self.assertTrue(report.coverage_satisfied)

    def test_disabled_ruleset_cannot_produce_a_passing_gate(self):
        framework = RiskControlUnitTestFrameworkEngine(
            PreTradeRiskEngine(RiskRuleConfig(rule_id="OFF", enabled=False)))
        with self.assertLogs("risk_unit_test_harness", level=logging.WARNING):
            report = framework.run_standard_suite()
        self.assertEqual(report.status, STATUS_FAILURES)

    def test_report_records_every_case(self):
        report = self.framework.run_standard_suite()
        self.assertEqual(len(report.test_results), report.total_tests)
        self.assertEqual({r.test_name for r in report.test_results}.__len__(),
                         report.total_tests)

    def test_pass_rate_is_a_percentage(self):
        cases = [
            RiskTestCase("pass", order(), True, ()),
            RiskTestCase("fail", order(quantity=2000.0), True, ()),
        ]
        report = self.framework.run_suite(cases)
        self.assertEqual(report.pass_rate_pct, 50.0)


class TestLatencyReporting(unittest.TestCase):
    def test_latency_measured_but_not_enforced_by_default(self):
        framework = RiskControlUnitTestFrameworkEngine(
            PreTradeRiskEngine(DEFAULT_CONFIG),
            latency_budget_microseconds=1e-9,  # unreachably tight
        )
        with self.assertLogs("risk_unit_test_harness", level=logging.WARNING):
            report = framework.run_standard_suite()
        self.assertTrue(report.latency_budget_breached)
        self.assertFalse(report.latency_budget_enforced)
        self.assertEqual(report.status, STATUS_PASSED)

    def test_enforced_budget_breach_fails_the_gate(self):
        framework = RiskControlUnitTestFrameworkEngine(
            PreTradeRiskEngine(DEFAULT_CONFIG),
            latency_budget_microseconds=1e-9,
            enforce_latency_budget=True,
        )
        report = framework.run_standard_suite()
        self.assertEqual(report.status, STATUS_LATENCY_BREACH)
        self.assertTrue(report.latency_budget_enforced)

    def test_generous_budget_is_not_breached(self):
        framework = RiskControlUnitTestFrameworkEngine(
            PreTradeRiskEngine(DEFAULT_CONFIG),
            latency_budget_microseconds=1e6,
            enforce_latency_budget=True,
        )
        report = framework.run_standard_suite()
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertFalse(report.latency_budget_breached)

    def test_latency_samples_recorded_and_ordered(self):
        framework = RiskControlUnitTestFrameworkEngine(
            PreTradeRiskEngine(DEFAULT_CONFIG))
        report = framework.run_standard_suite()
        self.assertEqual(report.latency_sample_count, report.total_tests)
        self.assertGreaterEqual(report.latency_p99_microseconds,
                                report.latency_p50_microseconds)
        self.assertTrue(all(math.isfinite(r.latency_microseconds)
                            for r in report.test_results))


class TestCustomRiskEngineIntegration(unittest.TestCase):
    """The framework must work against any object exposing evaluate_order."""

    class AlwaysAllows:
        def evaluate_order(self, o):
            return RiskCheckResult(o.order_id, True, [], [], 0.0)

    def test_a_permissive_engine_fails_every_negative_case(self):
        framework = RiskControlUnitTestFrameworkEngine(self.AlwaysAllows())
        report = framework.run_standard_suite()
        self.assertEqual(report.status, STATUS_FAILURES)
        self.assertGreater(report.failed_tests, 0)
        self.assertEqual(report.rules_exercised, ())
        self.assertEqual(report.ruleset_id, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
