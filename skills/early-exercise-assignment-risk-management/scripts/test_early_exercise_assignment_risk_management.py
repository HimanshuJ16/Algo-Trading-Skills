import math
import unittest

from early_exercise_assignment_risk_management import (
    DAYS_PER_YEAR,
    EarlyExerciseRiskEngine,
    EarlyExerciseRiskError,
    ShortOptionPosition,
)


def make_position(**overrides):
    """A benign short American call, overridable per test."""
    params = dict(
        position_id="POS_1", symbol="AAPL", option_type="CALL",
        exercise_style="AMERICAN", strike=100.0, option_market_price=5.20,
        underlying_price=105.0, contracts_qty=10, days_to_expiry=15.0,
    )
    params.update(overrides)
    return ShortOptionPosition(**params)


class TestIntrinsicExtrinsicDecomposition(unittest.TestCase):

    def setUp(self):
        self.engine = EarlyExerciseRiskEngine()

    def test_itm_call_decomposition(self):
        # S=105, K=100 -> intrinsic 5.00; price 5.20 -> extrinsic 0.20.
        report = self.engine.audit_short_position_assignment_risk(make_position())
        self.assertEqual(report.intrinsic_value_usd, 5.00)
        self.assertEqual(report.extrinsic_value_usd, 0.20)
        self.assertFalse(report.quoted_below_parity)

    def test_otm_put_is_all_extrinsic(self):
        # S=105, K=100 put -> intrinsic 0; the whole premium is extrinsic.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(option_type="PUT", option_market_price=1.75))
        self.assertEqual(report.intrinsic_value_usd, 0.0)
        self.assertEqual(report.extrinsic_value_usd, 1.75)
        self.assertEqual(report.risk_level, "LOW_RISK")

    def test_quote_below_intrinsic_is_flagged_not_silently_clamped(self):
        # Regression: extrinsic is clamped at 0, but the below-parity condition
        # (the strongest exercise signal there is) must survive into the report.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(option_market_price=4.90))
        self.assertEqual(report.extrinsic_value_usd, 0.0)
        self.assertTrue(report.quoted_below_parity)
        self.assertIn("QUOTE_BELOW_INTRINSIC", report.data_quality_flags)
        self.assertEqual(report.risk_level, "HIGH_ASSIGNMENT_RISK")

    def test_assigned_notional_uses_contract_multiplier(self):
        report = self.engine.audit_short_position_assignment_risk(
            make_position(contracts_qty=10, contract_multiplier=100))
        self.assertEqual(report.assigned_share_notional_usd, 10 * 100 * 105.0)


class TestExDividendCallRisk(unittest.TestCase):

    def setUp(self):
        self.engine = EarlyExerciseRiskEngine()

    def test_dividend_exceeding_extrinsic_inside_window_is_critical(self):
        # D=$1.00 > extrinsic $0.20 with ex-div tomorrow -> critical.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(upcoming_dividend_usd=1.00, days_to_ex_div=0.5))
        self.assertEqual(report.risk_level, "CRITICAL_ASSIGNMENT_RISK")
        self.assertEqual(report.recommended_action, "CLOSE_OR_ROLL_SHORT_CALL")
        self.assertEqual(report.exercise_test_used, "EXTRINSIC_SCREEN")
        # 10 contracts x 100 shares x $1.00 dividend owed if assigned.
        self.assertEqual(report.dividend_liability_usd, 1000.0)

    def test_dividend_below_extrinsic_is_not_flagged(self):
        report = self.engine.audit_short_position_assignment_risk(
            make_position(upcoming_dividend_usd=0.10, days_to_ex_div=0.5))
        self.assertEqual(report.risk_level, "LOW_RISK")
        self.assertEqual(report.recommended_action, "NO_ACTION_REQUIRED")

    def test_dividend_exactly_equal_to_extrinsic_does_not_escalate(self):
        # Boundary: the test is strictly greater-than, so indifference does not
        # trigger a close/roll directive.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(upcoming_dividend_usd=0.20, days_to_ex_div=0.5))
        self.assertEqual(report.risk_level, "LOW_RISK")

    def test_dividend_outside_decision_window_pre_warns(self):
        # Same economics, ex-div 2.5 days out: pre-warning, not a close order.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(upcoming_dividend_usd=1.00, days_to_ex_div=2.5))
        self.assertEqual(report.risk_level, "ELEVATED_ASSIGNMENT_RISK")
        self.assertEqual(report.recommended_action, "MONITOR")
        self.assertEqual(report.dividend_liability_usd, 0.0)

    def test_dividend_beyond_warning_window_is_low_risk(self):
        report = self.engine.audit_short_position_assignment_risk(
            make_position(upcoming_dividend_usd=1.00, days_to_ex_div=9.0))
        self.assertEqual(report.risk_level, "LOW_RISK")

    def test_dividend_after_expiry_cannot_be_captured(self):
        # Regression: a 0DTE call whose ex-date falls after expiry carries no
        # dividend-capture assignment risk, however large the dividend.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(days_to_expiry=0.4, upcoming_dividend_usd=5.00,
                          days_to_ex_div=0.9))
        self.assertEqual(report.exercise_test_used, "NOT_APPLICABLE")
        self.assertIn("DIVIDEND_AFTER_EXPIRY_IGNORED", report.data_quality_flags)
        self.assertNotEqual(report.risk_level, "CRITICAL_ASSIGNMENT_RISK")

    def test_short_put_is_unaffected_by_upcoming_dividend(self):
        # The dividend-capture rule is a call-only mechanic.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(option_type="PUT", option_market_price=1.75,
                          upcoming_dividend_usd=1.00, days_to_ex_div=0.5))
        self.assertEqual(report.risk_level, "LOW_RISK")


class TestMertonExactTest(unittest.TestCase):
    """The exact test is D > put + K*(1 - exp(-r*tau)) (Merton, 1973)."""

    def test_exact_test_can_clear_a_position_the_screen_would_flag(self):
        # Independently derived: K=100, tau=15/365, r=5% ->
        # interest = 100*(1-exp(-0.05*15/365)) = 0.2052685
        # put = 0.60 -> TV_ex = 0.8052685 > D = 0.75 -> NOT favoured.
        # The cum-dividend extrinsic screen (D 0.75 > 0.20) would have fired.
        engine = EarlyExerciseRiskEngine(risk_free_rate=0.05)
        interest = 100.0 * (1.0 - math.exp(-0.05 * 15.0 / DAYS_PER_YEAR))
        self.assertAlmostEqual(interest, 0.2052685, places=6)

        report = engine.audit_short_position_assignment_risk(
            make_position(upcoming_dividend_usd=0.75, days_to_ex_div=0.5,
                          same_strike_put_price=0.60))
        self.assertEqual(report.exercise_test_used, "MERTON_PUT_PARITY")
        self.assertAlmostEqual(report.early_exercise_edge_usd,
                               round(0.75 - (0.60 + interest), 4), places=4)
        self.assertLess(report.early_exercise_edge_usd, 0.0)
        self.assertEqual(report.risk_level, "LOW_RISK")

    def test_exact_test_fires_when_dividend_beats_put_plus_carry(self):
        engine = EarlyExerciseRiskEngine(risk_free_rate=0.05)
        report = engine.audit_short_position_assignment_risk(
            make_position(upcoming_dividend_usd=0.75, days_to_ex_div=0.5,
                          same_strike_put_price=0.05))
        self.assertEqual(report.exercise_test_used, "MERTON_PUT_PARITY")
        self.assertGreater(report.early_exercise_edge_usd, 0.0)
        self.assertEqual(report.risk_level, "CRITICAL_ASSIGNMENT_RISK")

    def test_zero_rate_reduces_exact_test_to_dividend_vs_put(self):
        engine = EarlyExerciseRiskEngine(risk_free_rate=0.0)
        report = engine.audit_short_position_assignment_risk(
            make_position(upcoming_dividend_usd=0.75, days_to_ex_div=0.5,
                          same_strike_put_price=0.60))
        self.assertAlmostEqual(report.early_exercise_edge_usd, 0.15, places=4)
        self.assertEqual(report.risk_level, "CRITICAL_ASSIGNMENT_RISK")


class TestParityRule(unittest.TestCase):

    def setUp(self):
        self.engine = EarlyExerciseRiskEngine()

    def test_deep_itm_put_with_no_extrinsic_is_high_risk(self):
        report = self.engine.audit_short_position_assignment_risk(
            make_position(option_type="PUT", strike=100.0, underlying_price=80.0,
                          option_market_price=20.02))
        self.assertEqual(report.risk_level, "HIGH_ASSIGNMENT_RISK")
        self.assertEqual(report.recommended_action, "CLOSE_OR_ROLL_SHORT_PUT")

    def test_deep_itm_call_with_no_extrinsic_is_high_risk_without_any_dividend(self):
        # Regression: the original engine only ever looked at calls inside an
        # ex-dividend window, so a call pinned at parity scored LOW_RISK.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(strike=100.0, underlying_price=140.0,
                          option_market_price=40.01))
        self.assertEqual(report.risk_level, "HIGH_ASSIGNMENT_RISK")
        self.assertEqual(report.recommended_action, "CLOSE_OR_ROLL_SHORT_CALL")

    def test_parity_threshold_scales_with_strike(self):
        # 5 bp of a $5,000 strike is $2.50, so $1.00 of extrinsic is parity here
        # but would be comfortably clear of it on a $100 strike.
        big = self.engine.audit_short_position_assignment_risk(
            make_position(option_type="PUT", strike=5000.0,
                          underlying_price=4000.0, option_market_price=1001.00))
        self.assertEqual(big.risk_level, "HIGH_ASSIGNMENT_RISK")

        small = self.engine.audit_short_position_assignment_risk(
            make_position(option_type="PUT", strike=100.0, underlying_price=80.0,
                          option_market_price=21.00))
        self.assertEqual(small.risk_level, "LOW_RISK")

    def test_otm_option_at_zero_extrinsic_is_not_parity_risk(self):
        # Boundary: intrinsic must be strictly positive for the parity rule.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(strike=200.0, underlying_price=105.0,
                          option_market_price=0.01))
        self.assertEqual(report.intrinsic_value_usd, 0.0)
        self.assertEqual(report.risk_level, "LOW_RISK")

    def test_critical_dividend_verdict_outranks_parity(self):
        report = self.engine.audit_short_position_assignment_risk(
            make_position(strike=100.0, underlying_price=140.0,
                          option_market_price=40.01,
                          upcoming_dividend_usd=1.00, days_to_ex_div=0.5))
        self.assertEqual(report.risk_level, "CRITICAL_ASSIGNMENT_RISK")
        self.assertEqual(report.recommended_action, "CLOSE_OR_ROLL_SHORT_CALL")


class TestExerciseStyle(unittest.TestCase):

    def setUp(self):
        self.engine = EarlyExerciseRiskEngine()

    def test_european_option_cannot_be_assigned_early(self):
        report = self.engine.audit_short_position_assignment_risk(
            make_position(symbol="SPX", exercise_style="EUROPEAN",
                          upcoming_dividend_usd=1.00, days_to_ex_div=0.5))
        self.assertEqual(report.risk_level, "LOW_RISK")
        self.assertEqual(report.assignment_risk_score, 0.0)
        self.assertEqual(report.recommended_action, "NO_ACTION_REQUIRED")

    def test_european_deep_itm_at_parity_still_not_early_assignable(self):
        report = self.engine.audit_short_position_assignment_risk(
            make_position(symbol="SPX", exercise_style="EUROPEAN",
                          strike=100.0, underlying_price=140.0,
                          option_market_price=40.01))
        self.assertEqual(report.risk_level, "LOW_RISK")

    def test_american_cash_settled_index_option_is_still_screened(self):
        # OEX is American-style and cash-settled; settlement method must not be
        # conflated with exercise style.
        report = self.engine.audit_short_position_assignment_risk(
            make_position(symbol="OEX", exercise_style="AMERICAN",
                          strike=100.0, underlying_price=140.0,
                          option_market_price=40.01))
        self.assertEqual(report.risk_level, "HIGH_ASSIGNMENT_RISK")


class TestInputValidation(unittest.TestCase):

    def test_unknown_option_type_is_rejected_not_treated_as_put(self):
        # Regression: the original engine used `if CALL ... else PUT`, so a typo
        # was silently priced as a put.
        with self.assertRaises(EarlyExerciseRiskError):
            make_position(option_type="CALLS")

    def test_unknown_exercise_style_is_rejected(self):
        with self.assertRaises(EarlyExerciseRiskError):
            make_position(exercise_style="EURO")

    def test_case_and_whitespace_are_normalised(self):
        pos = make_position(option_type=" call ", exercise_style="american")
        self.assertEqual(pos.option_type, "CALL")
        self.assertEqual(pos.exercise_style, "AMERICAN")

    def test_non_finite_and_negative_inputs_are_rejected(self):
        for kwargs in (
            {"underlying_price": float("nan")},
            {"underlying_price": 0.0},
            {"strike": -100.0},
            {"option_market_price": -0.01},
            {"days_to_expiry": -1.0},
            {"days_to_ex_div": -1.0},
            {"upcoming_dividend_usd": float("inf")},
            {"contracts_qty": 0},
            {"contract_multiplier": 0},
            {"same_strike_put_price": -1.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(EarlyExerciseRiskError):
                    make_position(**kwargs)

    def test_no_dividend_default_is_infinite_days_to_ex_div(self):
        pos = make_position()
        self.assertTrue(math.isinf(pos.days_to_ex_div))

    def test_engine_rejects_inverted_dividend_windows(self):
        with self.assertRaises(EarlyExerciseRiskError):
            EarlyExerciseRiskEngine(ex_div_decision_days=5.0,
                                    ex_div_warning_days=1.0)

    def test_engine_rejects_non_finite_rate(self):
        with self.assertRaises(EarlyExerciseRiskError):
            EarlyExerciseRiskEngine(risk_free_rate=float("nan"))


class TestReportContract(unittest.TestCase):

    def test_score_is_not_a_probability_and_never_claims_certainty(self):
        # Assignment allocation is FIFO/random per FINRA Rule 2360(b)(23)(C):
        # no input here can support a 100% assignment claim.
        engine = EarlyExerciseRiskEngine()
        report = engine.audit_short_position_assignment_risk(
            make_position(upcoming_dividend_usd=1.00, days_to_ex_div=0.5))
        self.assertLess(report.assignment_risk_score, 100.0)
        self.assertNotIn("WILL exercise", report.risk_summary)

    def test_pathological_rate_and_tenor_raise_instead_of_overflowing(self):
        # A risk screen must not die with an unhandled OverflowError on a bad
        # rate convention (e.g. -100% fed as a decimal over a long tenor).
        engine = EarlyExerciseRiskEngine(risk_free_rate=-1.0)
        with self.assertRaises(EarlyExerciseRiskError):
            engine.audit_short_position_assignment_risk(
                make_position(option_type="PUT", strike=100.0,
                              underlying_price=80.0, option_market_price=25.0,
                              days_to_expiry=1_000_000.0,
                              same_strike_call_price=0.10))

    def test_summary_is_always_populated(self):
        engine = EarlyExerciseRiskEngine()
        report = engine.audit_short_position_assignment_risk(make_position())
        self.assertTrue(report.risk_summary.strip())
        self.assertEqual(report.risk_level, "LOW_RISK")


if __name__ == "__main__":
    unittest.main()
