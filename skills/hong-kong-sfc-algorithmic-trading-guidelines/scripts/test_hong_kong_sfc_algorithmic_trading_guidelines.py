"""Unit tests for the Hong Kong SFC pre-trade compliance gate.

Expected values are derived independently of the implementation: notional and
deviation figures are computed by hand in the test, and the short selling cases
are built from the Ordinance and Rules of the Exchange text quoted in
``references/standards.md`` rather than from the engine's own control flow.
"""

import logging
import unittest
from datetime import datetime, timedelta, timezone

from hong_kong_sfc_algorithmic_trading_guidelines import (
    EXEMPT_SECURITIES_MARKET_MAKER,
    KILL_SWITCH_SCOPE_ALGO,
    KILL_SWITCH_SCOPE_CLIENT,
    KILL_SWITCH_SCOPE_FIRM,
    ORDER_TYPE_AT_AUCTION,
    ORDER_TYPE_AT_AUCTION_LIMIT,
    ORDER_TYPE_LIMIT,
    SESSION_CAS,
    SESSION_CTS,
    SESSION_POS,
    STATUS_APPROVED,
    VIOLATION_ADV_PARTICIPATION_LIMIT,
    VIOLATION_ALGO_NOT_AUTHORISED,
    VIOLATION_ALGO_NOT_TESTED,
    VIOLATION_CHILD_PRICE_EXCEEDS_PARENT,
    VIOLATION_CHILD_QUANTITY_EXCEEDS_PARENT,
    VIOLATION_ILLEGAL_NAKED_SHORT,
    VIOLATION_KILL_SWITCH_ACTIVE,
    VIOLATION_MESSAGE_RATE_LIMIT,
    VIOLATION_MISSING_MARKET_DATA,
    VIOLATION_OPERATOR_NOT_APPROVED,
    VIOLATION_ORDER_QUANTITY_LIMIT,
    VIOLATION_ORDER_VALUE_LIMIT,
    VIOLATION_PRICE_DEVIATION_LIMIT,
    VIOLATION_SHORT_SELL_ASSURANCE_MISSING,
    VIOLATION_SHORT_SELL_NOT_DESIGNATED,
    VIOLATION_SHORT_SELL_NOT_FLAGGED,
    VIOLATION_SHORT_SELL_ORDER_TYPE_NOT_PERMITTED,
    VIOLATION_SHORT_SELL_TICK_RULE,
    HkSfcAlgorithmicTradingEngine,
    HkSfcComplianceReport,
    HkSfcOrderRequest,
)

FIXED_TIME = datetime(2026, 3, 2, 3, 30, 0, tzinfo=timezone.utc)


class _StepClock:
    """Deterministic clock the test advances explicitly."""

    def __init__(self, start: datetime = FIXED_TIME) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def long_order(**overrides) -> HkSfcOrderRequest:
    """A plain long buy that breaches nothing, so each test changes one thing."""
    base = dict(
        algo_id="HK_MOMENTUM_01",
        stock_code="00700",
        side="BUY",
        order_price=300.00,
        order_quantity=10_000,
        market_last_price=300.00,
    )
    base.update(overrides)
    return HkSfcOrderRequest(**base)


def compliant_short(**overrides) -> HkSfcOrderRequest:
    """A covered, assured, flagged short sale of a Designated Security in CTS,
    priced at the best current ask so Regulation (15) is satisfied."""
    base = dict(
        algo_id="HK_ARBITRAGE_02",
        stock_code="09988",
        side="SHORT_SELL",
        order_price=100.00,
        order_quantity=5_000,
        market_last_price=100.00,
        short_sell_reference_price=100.00,
        session=SESSION_CTS,
        is_short_sell=True,
        has_locate_borrow=True,
        documentary_assurance_ref="ASSURANCE-2026-0001",
        short_sell_flagged=True,
        is_designated_security=True,
    )
    base.update(overrides)
    return HkSfcOrderRequest(**base)


class TestApprovalPath(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HkSfcAlgorithmicTradingEngine(
            max_order_value_hkd=10_000_000.0, max_price_deviation_pct=5.0
        )

    def test_compliant_long_order_is_approved(self):
        report = self.engine.audit_sfc_compliance(long_order())

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertEqual(report.violations, ())
        self.assertFalse(report.blocks_order)
        # 300.00 x 10,000 = 3,000,000 HKD, computed independently here.
        self.assertEqual(report.order_value_hkd, 3_000_000.0)
        self.assertEqual(report.price_deviation_pct, 0.0)
        self.assertFalse(report.is_kill_switch_active)
        self.assertTrue(report.is_algo_authorised)

    def test_long_order_reports_short_sale_legality_as_not_applicable(self):
        report = self.engine.audit_sfc_compliance(long_order())
        self.assertFalse(report.is_short_sell)
        self.assertIsNone(report.is_short_sell_legal)

    def test_compliant_covered_short_sale_is_approved(self):
        report = self.engine.audit_sfc_compliance(compliant_short())

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_short_sell)
        self.assertTrue(report.is_short_sell_legal)

    def test_report_carries_timestamp_and_unique_reference(self):
        first = self.engine.audit_sfc_compliance(long_order())
        second = self.engine.audit_sfc_compliance(long_order())

        self.assertNotEqual(first.order_reference, second.order_reference)
        # Schedule 7 Annex: audit logs are time stamped.
        datetime.fromisoformat(first.decision_time_utc)

    def test_caller_supplied_order_reference_is_preserved(self):
        report = self.engine.audit_sfc_compliance(long_order(order_reference="OMS-77-A"))
        self.assertEqual(report.order_reference, "OMS-77-A")


class TestPreTradeThresholds(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HkSfcAlgorithmicTradingEngine(
            max_order_value_hkd=10_000_000.0,
            max_price_deviation_pct=5.0,
            max_order_quantity=100_000,
        )

    def test_order_value_above_limit_is_rejected(self):
        # 300.00 x 40,000 = 12,000,000 > 10,000,000.
        report = self.engine.audit_sfc_compliance(long_order(order_quantity=40_000))

        self.assertEqual(report.status, "REJECTED_ORDER_VALUE_LIMIT")
        self.assertIn(VIOLATION_ORDER_VALUE_LIMIT, report.violations)
        self.assertEqual(report.order_value_hkd, 12_000_000.0)

    def test_order_value_exactly_at_limit_is_approved(self):
        # 200.00 x 50,000 = 10,000,000 exactly; "must not exceed" permits it.
        report = self.engine.audit_sfc_compliance(
            long_order(order_price=200.00, order_quantity=50_000, market_last_price=200.00)
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_notional_at_limit_is_not_rejected_by_float_drift(self):
        # 100.04 * 10,000 is exactly HKD 1,000,400 but evaluates to
        # 1000400.0000000001 in binary floating point, which would push an
        # order sitting precisely on the limit over it.
        self.assertGreater(100.04 * 10_000, 1_000_400.0)
        engine = HkSfcAlgorithmicTradingEngine(
            max_order_value_hkd=1_000_400.0, max_price_deviation_pct=5.0
        )
        report = engine.audit_sfc_compliance(
            long_order(order_price=100.04, order_quantity=10_000, market_last_price=100.04)
        )
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertEqual(report.order_value_hkd, 1_000_400.0)

    def test_price_deviation_above_limit_is_rejected(self):
        # |320 - 300| / 300 = 6.666...% > 5%.
        report = self.engine.audit_sfc_compliance(long_order(order_price=320.00))

        self.assertEqual(report.status, "REJECTED_PRICE_DEVIATION_LIMIT")
        self.assertAlmostEqual(report.price_deviation_pct, 20.0 / 3.0, places=9)

    def test_price_deviation_is_symmetric_below_the_nominal_price(self):
        # |280 - 300| / 300 = 6.666...% -- a limit priced far below the market
        # is as erroneous as one far above it.
        report = self.engine.audit_sfc_compliance(long_order(order_price=280.00))
        self.assertEqual(report.status, "REJECTED_PRICE_DEVIATION_LIMIT")

    def test_deviation_marginally_over_limit_is_not_rounded_away(self):
        # Regression: rounding the deviation to 2dp before comparing turns
        # 5.004% into "5.00%" and lets the order through a 5.00% limit.
        # 300 * 1.05004 = 315.012 -> deviation exactly 5.004%.
        report = self.engine.audit_sfc_compliance(long_order(order_price=315.012))

        self.assertAlmostEqual(report.price_deviation_pct, 5.004, places=9)
        self.assertIn(VIOLATION_PRICE_DEVIATION_LIMIT, report.violations)

    def test_deviation_exactly_at_limit_is_approved(self):
        # 300 * 1.05 = 315.00 -> exactly 5.00%, which does not exceed the limit.
        report = self.engine.audit_sfc_compliance(long_order(order_price=315.00))
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_order_quantity_above_limit_is_rejected(self):
        engine = HkSfcAlgorithmicTradingEngine(
            max_order_value_hkd=10_000_000_000.0,
            max_price_deviation_pct=5.0,
            max_order_quantity=100_000,
        )
        report = engine.audit_sfc_compliance(long_order(order_quantity=100_001))

        self.assertEqual(report.status, "REJECTED_ORDER_QUANTITY_LIMIT")
        self.assertIn(VIOLATION_ORDER_QUANTITY_LIMIT, report.violations)

    def test_adv_participation_cap(self):
        engine = HkSfcAlgorithmicTradingEngine(
            max_order_value_hkd=10_000_000_000.0,
            max_price_deviation_pct=5.0,
            max_adv_participation_pct=10.0,
        )
        # 20,000 / 100,000 = 20% > 10%.
        breach = engine.audit_sfc_compliance(
            long_order(order_quantity=20_000, average_daily_volume=100_000)
        )
        self.assertIn(VIOLATION_ADV_PARTICIPATION_LIMIT, breach.violations)

        # 10,000 / 100,000 = 10% exactly, which does not exceed the cap.
        at_limit = engine.audit_sfc_compliance(
            long_order(order_quantity=10_000, average_daily_volume=100_000)
        )
        self.assertEqual(at_limit.status, STATUS_APPROVED)

    def test_adv_cap_without_adv_data_fails_closed(self):
        engine = HkSfcAlgorithmicTradingEngine(
            max_order_value_hkd=10_000_000_000.0,
            max_price_deviation_pct=5.0,
            max_adv_participation_pct=10.0,
        )
        report = engine.audit_sfc_compliance(long_order(average_daily_volume=None))

        self.assertIn(VIOLATION_MISSING_MARKET_DATA, report.violations)
        self.assertTrue(report.blocks_order)


class TestMissingMarketData(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HkSfcAlgorithmicTradingEngine()

    def test_absent_nominal_price_blocks_instead_of_raising(self):
        report = self.engine.audit_sfc_compliance(long_order(market_last_price=None))

        self.assertEqual(report.status, "REJECTED_MISSING_MARKET_DATA")
        self.assertIsNone(report.price_deviation_pct)
        self.assertTrue(report.blocks_order)

    def test_zero_nominal_price_blocks_instead_of_dividing_by_zero(self):
        # Regression: a stock with no trades yet reports a nominal price of 0
        # and used to raise ZeroDivisionError inside the order path.
        report = self.engine.audit_sfc_compliance(long_order(market_last_price=0.0))

        self.assertEqual(report.status, "REJECTED_MISSING_MARKET_DATA")
        self.assertIsNone(report.price_deviation_pct)

    def test_nan_nominal_price_blocks(self):
        report = self.engine.audit_sfc_compliance(long_order(market_last_price=float("nan")))

        self.assertIn(VIOLATION_MISSING_MARKET_DATA, report.violations)
        self.assertIsNone(report.price_deviation_pct)

    def test_missing_deviation_is_not_reported_as_zero(self):
        report = self.engine.audit_sfc_compliance(long_order(market_last_price=None))
        self.assertNotEqual(report.price_deviation_pct, 0.0)


class TestAuthorisationGates(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HkSfcAlgorithmicTradingEngine()

    def test_unauthorised_algo_is_rejected(self):
        report = self.engine.audit_sfc_compliance(
            long_order(algo_authorised_for_production=False)
        )

        self.assertEqual(report.status, "REJECTED_ALGO_NOT_AUTHORISED")
        self.assertIn(VIOLATION_ALGO_NOT_AUTHORISED, report.violations)
        self.assertFalse(report.is_algo_authorised)

    def test_untested_algo_is_rejected(self):
        report = self.engine.audit_sfc_compliance(long_order(algo_testing_signed_off=False))

        self.assertEqual(report.status, "REJECTED_ALGO_NOT_TESTED")
        self.assertIn(VIOLATION_ALGO_NOT_TESTED, report.violations)

    def test_unapproved_operator_is_rejected(self):
        report = self.engine.audit_sfc_compliance(long_order(operator_approved_to_use=False))

        self.assertEqual(report.status, "REJECTED_OPERATOR_NOT_APPROVED")
        self.assertIn(VIOLATION_OPERATOR_NOT_APPROVED, report.violations)


class TestShortSelling(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HkSfcAlgorithmicTradingEngine()

    def test_naked_short_sale_is_rejected(self):
        report = self.engine.audit_sfc_compliance(compliant_short(has_locate_borrow=False))

        self.assertEqual(report.status, "REJECTED_ILLEGAL_NAKED_SHORT")
        self.assertIn(VIOLATION_ILLEGAL_NAKED_SHORT, report.violations)
        self.assertFalse(report.is_short_sell_legal)

    def test_short_sale_without_documentary_assurance_is_rejected(self):
        report = self.engine.audit_sfc_compliance(compliant_short(documentary_assurance_ref=None))

        self.assertEqual(report.status, "REJECTED_SHORT_SELL_ASSURANCE_MISSING")
        self.assertIn(VIOLATION_SHORT_SELL_ASSURANCE_MISSING, report.violations)

    def test_blank_documentary_assurance_reference_is_not_accepted(self):
        report = self.engine.audit_sfc_compliance(compliant_short(documentary_assurance_ref="   "))
        self.assertIn(VIOLATION_SHORT_SELL_ASSURANCE_MISSING, report.violations)

    def test_unflagged_short_sale_is_rejected(self):
        report = self.engine.audit_sfc_compliance(compliant_short(short_sell_flagged=False))

        self.assertEqual(report.status, "REJECTED_SHORT_SELL_NOT_FLAGGED")
        self.assertIn(VIOLATION_SHORT_SELL_NOT_FLAGGED, report.violations)

    def test_short_sale_of_non_designated_security_is_rejected(self):
        report = self.engine.audit_sfc_compliance(compliant_short(is_designated_security=False))

        self.assertEqual(report.status, "REJECTED_SHORT_SELL_NOT_DESIGNATED")
        self.assertIn(VIOLATION_SHORT_SELL_NOT_DESIGNATED, report.violations)

    def test_side_short_sell_without_the_flag_is_still_treated_as_a_short_sale(self):
        report = self.engine.audit_sfc_compliance(
            HkSfcOrderRequest(
                algo_id="HK_ARBITRAGE_02",
                stock_code="09988",
                side="SHORT_SELL",
                order_price=100.00,
                order_quantity=5_000,
                market_last_price=100.00,
            )
        )
        self.assertTrue(report.is_short_sell)
        self.assertIn(VIOLATION_ILLEGAL_NAKED_SHORT, report.violations)

    # --- Eleventh Schedule Regulation (15): the tick rule -------------------

    def test_short_sale_below_best_ask_breaches_the_tick_rule(self):
        report = self.engine.audit_sfc_compliance(
            compliant_short(order_price=99.99, short_sell_reference_price=100.00)
        )

        self.assertEqual(report.status, "REJECTED_SHORT_SELL_TICK_RULE")
        self.assertIn(VIOLATION_SHORT_SELL_TICK_RULE, report.violations)
        self.assertFalse(report.is_short_sell_legal)

    def test_short_sale_at_the_best_ask_satisfies_the_tick_rule(self):
        report = self.engine.audit_sfc_compliance(
            compliant_short(order_price=100.00, short_sell_reference_price=100.00)
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_short_sale_above_the_best_ask_satisfies_the_tick_rule(self):
        report = self.engine.audit_sfc_compliance(
            compliant_short(order_price=100.05, short_sell_reference_price=100.00)
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_short_sale_without_reference_price_fails_closed(self):
        report = self.engine.audit_sfc_compliance(compliant_short(short_sell_reference_price=None))

        self.assertIn(VIOLATION_MISSING_MARKET_DATA, report.violations)
        self.assertTrue(report.blocks_order)
        self.assertFalse(report.is_short_sell_legal)

    # --- Rule 563D(1): session and order type ------------------------------

    def test_short_sale_in_cas_must_be_an_at_auction_limit_order(self):
        report = self.engine.audit_sfc_compliance(
            compliant_short(session=SESSION_CAS, order_type=ORDER_TYPE_LIMIT)
        )

        self.assertEqual(report.status, "REJECTED_SHORT_SELL_ORDER_TYPE_NOT_PERMITTED")
        self.assertIn(VIOLATION_SHORT_SELL_ORDER_TYPE_NOT_PERMITTED, report.violations)

    def test_short_sale_in_cas_as_at_auction_limit_order_is_approved(self):
        report = self.engine.audit_sfc_compliance(
            compliant_short(session=SESSION_CAS, order_type=ORDER_TYPE_AT_AUCTION_LIMIT)
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_short_sale_in_pos_as_plain_at_auction_order_is_rejected(self):
        report = self.engine.audit_sfc_compliance(
            compliant_short(session=SESSION_POS, order_type=ORDER_TYPE_AT_AUCTION)
        )
        self.assertIn(VIOLATION_SHORT_SELL_ORDER_TYPE_NOT_PERMITTED, report.violations)

    def test_long_sell_in_cas_is_unaffected_by_the_short_sell_order_type_rule(self):
        report = self.engine.audit_sfc_compliance(
            long_order(side="SELL", session=SESSION_CAS, order_type=ORDER_TYPE_LIMIT)
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    # --- Rule 563D(1) exempt categories ------------------------------------

    def test_exempt_category_waives_designated_and_tick_checks(self):
        report = self.engine.audit_sfc_compliance(
            compliant_short(
                exempt_short_sell_category=EXEMPT_SECURITIES_MARKET_MAKER,
                is_designated_security=False,
                order_price=99.00,
                short_sell_reference_price=100.00,
            )
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_exempt_category_does_not_waive_section_170_cover(self):
        report = self.engine.audit_sfc_compliance(
            compliant_short(
                exempt_short_sell_category=EXEMPT_SECURITIES_MARKET_MAKER,
                has_locate_borrow=False,
            )
        )
        self.assertEqual(report.status, "REJECTED_ILLEGAL_NAKED_SHORT")

    def test_unknown_exempt_category_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(
                compliant_short(exempt_short_sell_category="FRIEND_OF_THE_DESK")
            )

    def test_exempt_category_on_a_buy_order_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(
                long_order(exempt_short_sell_category=EXEMPT_SECURITIES_MARKET_MAKER)
            )


class TestViolationAggregation(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HkSfcAlgorithmicTradingEngine(
            max_order_value_hkd=10_000_000.0, max_price_deviation_pct=5.0
        )

    def test_all_breaches_are_recorded_not_just_the_first(self):
        # Naked, unassured, unflagged short sale that is also oversized.
        report = self.engine.audit_sfc_compliance(
            compliant_short(
                order_quantity=200_000,
                has_locate_borrow=False,
                documentary_assurance_ref=None,
                short_sell_flagged=False,
            )
        )

        self.assertIn(VIOLATION_ILLEGAL_NAKED_SHORT, report.violations)
        self.assertIn(VIOLATION_SHORT_SELL_ASSURANCE_MISSING, report.violations)
        self.assertIn(VIOLATION_SHORT_SELL_NOT_FLAGGED, report.violations)
        self.assertIn(VIOLATION_ORDER_VALUE_LIMIT, report.violations)
        # Statutory breach outranks the firm's own threshold in the headline.
        self.assertEqual(report.status, "REJECTED_ILLEGAL_NAKED_SHORT")

    def test_oversized_naked_short_is_not_recorded_as_a_legal_short_sale(self):
        # Regression: the value-limit branch used to hard-code
        # is_short_sell_legal=True, so a naked short that was also oversized
        # was filed as a lawful short sale.
        report = self.engine.audit_sfc_compliance(
            compliant_short(order_quantity=200_000, has_locate_borrow=False)
        )
        self.assertFalse(report.is_short_sell_legal)

    def test_violations_are_reported_in_precedence_order(self):
        report = self.engine.audit_sfc_compliance(
            compliant_short(
                order_quantity=200_000,
                has_locate_borrow=False,
                algo_authorised_for_production=False,
            )
        )
        self.assertEqual(report.violations[0], VIOLATION_ALGO_NOT_AUTHORISED)
        self.assertEqual(report.violations[1], VIOLATION_ILLEGAL_NAKED_SHORT)
        self.assertLess(
            report.violations.index(VIOLATION_ILLEGAL_NAKED_SHORT),
            report.violations.index(VIOLATION_ORDER_VALUE_LIMIT),
        )

    def test_no_duplicate_violation_codes(self):
        engine = HkSfcAlgorithmicTradingEngine(
            max_order_value_hkd=10_000_000_000.0,
            max_price_deviation_pct=5.0,
            max_adv_participation_pct=10.0,
        )
        # Both the price band and the ADV cap lack their input data, and the
        # tick rule lacks its reference price: one MISSING_MARKET_DATA entry.
        report = engine.audit_sfc_compliance(
            compliant_short(
                market_last_price=None,
                average_daily_volume=None,
                short_sell_reference_price=None,
            )
        )
        self.assertEqual(
            report.violations.count(VIOLATION_MISSING_MARKET_DATA), 1, report.violations
        )
        self.assertEqual(len(set(report.violations)), len(report.violations))


class TestKillSwitch(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HkSfcAlgorithmicTradingEngine()

    def test_firm_wide_kill_switch_blocks_every_order(self):
        self.engine.trigger_sfc_kill_switch(
            reason="Runaway child order rate on 00700", activated_by="ro.chan"
        )
        report = self.engine.audit_sfc_compliance(long_order())

        self.assertEqual(report.status, "REJECTED_KILL_SWITCH_ACTIVE")
        self.assertIn(VIOLATION_KILL_SWITCH_ACTIVE, report.violations)
        self.assertTrue(report.is_kill_switch_active)
        self.assertEqual(report.kill_switch_scopes, (KILL_SWITCH_SCOPE_FIRM,))

    def test_algo_scoped_kill_switch_blocks_only_that_algo(self):
        self.engine.trigger_sfc_kill_switch(
            reason="Strategy misprice", activated_by="ro.chan",
            scope=KILL_SWITCH_SCOPE_ALGO, key="HK_MOMENTUM_01",
        )
        blocked = self.engine.audit_sfc_compliance(long_order(algo_id="HK_MOMENTUM_01"))
        untouched = self.engine.audit_sfc_compliance(long_order(algo_id="HK_MEANREV_09"))

        self.assertEqual(blocked.status, "REJECTED_KILL_SWITCH_ACTIVE")
        self.assertEqual(blocked.kill_switch_scopes, ("ALGO:HK_MOMENTUM_01",))
        self.assertEqual(untouched.status, STATUS_APPROVED)

    def test_client_scoped_kill_switch_blocks_only_that_client(self):
        self.engine.trigger_sfc_kill_switch(
            reason="Client DMA limit breach", activated_by="risk.lee",
            scope=KILL_SWITCH_SCOPE_CLIENT, key="CLIENT_88",
        )
        blocked = self.engine.audit_sfc_compliance(long_order(client_id="CLIENT_88"))
        untouched = self.engine.audit_sfc_compliance(long_order(client_id="CLIENT_12"))

        self.assertEqual(blocked.status, "REJECTED_KILL_SWITCH_ACTIVE")
        self.assertEqual(untouched.status, STATUS_APPROVED)

    def test_order_without_client_id_is_not_caught_by_a_client_switch(self):
        self.engine.trigger_sfc_kill_switch(
            reason="Client DMA limit breach", activated_by="risk.lee",
            scope=KILL_SWITCH_SCOPE_CLIENT, key="CLIENT_88",
        )
        report = self.engine.audit_sfc_compliance(long_order(client_id=None))
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_kill_switch_requires_reason_and_actor(self):
        with self.assertRaises(ValueError):
            self.engine.trigger_sfc_kill_switch(reason="", activated_by="ro.chan")
        with self.assertRaises(ValueError):
            self.engine.trigger_sfc_kill_switch(reason="Runaway algo", activated_by="   ")

    def test_keyed_scope_requires_a_key(self):
        with self.assertRaises(ValueError):
            self.engine.trigger_sfc_kill_switch(
                reason="Strategy misprice", activated_by="ro.chan",
                scope=KILL_SWITCH_SCOPE_ALGO,
            )

    def test_firm_scope_rejects_a_key(self):
        with self.assertRaises(ValueError):
            self.engine.trigger_sfc_kill_switch(
                reason="Halt everything", activated_by="ro.chan",
                scope=KILL_SWITCH_SCOPE_FIRM, key="HK_MOMENTUM_01",
            )

    def test_unknown_scope_raises(self):
        with self.assertRaises(ValueError):
            self.engine.trigger_sfc_kill_switch(
                reason="Halt", activated_by="ro.chan", scope="DESK", key="EQ"
            )

    def test_release_restores_order_flow_and_is_attributed(self):
        self.engine.trigger_sfc_kill_switch(reason="Runaway algo", activated_by="ro.chan")
        with self.assertLogs("hong_kong_sfc_algorithmic_trading_guidelines", level="CRITICAL") as logs:
            self.engine.reset_kill_switch(
                reason="Root cause fixed and change approved", reset_by="ro.chan"
            )
        self.assertFalse(self.engine.is_kill_switch_active)
        self.assertIn("RELEASED", "\n".join(logs.output))
        self.assertEqual(self.engine.audit_sfc_compliance(long_order()).status, STATUS_APPROVED)

    def test_release_requires_reason_and_actor(self):
        self.engine.trigger_sfc_kill_switch(reason="Runaway algo", activated_by="ro.chan")
        with self.assertRaises(ValueError):
            self.engine.reset_kill_switch(reason="", reset_by="ro.chan")
        self.assertTrue(self.engine.is_kill_switch_active)

    def test_releasing_a_switch_that_was_never_engaged_warns_and_does_not_raise(self):
        with self.assertLogs("hong_kong_sfc_algorithmic_trading_guidelines", level="WARNING"):
            self.engine.reset_kill_switch(reason="Housekeeping", reset_by="ops.wong")
        self.assertFalse(self.engine.is_kill_switch_active)

    def test_releasing_one_scope_leaves_the_other_engaged(self):
        self.engine.trigger_sfc_kill_switch(reason="Firm halt", activated_by="ro.chan")
        self.engine.trigger_sfc_kill_switch(
            reason="Strategy halt", activated_by="ro.chan",
            scope=KILL_SWITCH_SCOPE_ALGO, key="HK_MOMENTUM_01",
        )
        self.engine.reset_kill_switch(reason="Firm resumed", reset_by="ro.chan")

        self.assertTrue(self.engine.is_kill_switch_active)
        report = self.engine.audit_sfc_compliance(long_order(algo_id="HK_MOMENTUM_01"))
        self.assertEqual(report.status, "REJECTED_KILL_SWITCH_ACTIVE")

    def test_active_kill_switches_expose_attribution(self):
        self.engine.trigger_sfc_kill_switch(reason="Runaway algo", activated_by="ro.chan")
        states = self.engine.active_kill_switches()

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].activated_by, "ro.chan")
        self.assertEqual(states[0].reason, "Runaway algo")

    def test_kill_switch_report_records_real_order_metrics(self):
        # Regression: the kill-switch branch used to file order value and
        # deviation as 0.0, corrupting the audit record of what was blocked.
        self.engine.trigger_sfc_kill_switch(reason="Runaway algo", activated_by="ro.chan")
        report = self.engine.audit_sfc_compliance(long_order())

        self.assertEqual(report.order_value_hkd, 3_000_000.0)
        self.assertEqual(report.price_deviation_pct, 0.0)


class TestMessageRateLimit(unittest.TestCase):
    def test_message_rate_limit_trips_within_the_interval(self):
        clock = _StepClock()
        engine = HkSfcAlgorithmicTradingEngine(
            max_messages_per_interval=2, message_interval_seconds=1.0, clock=clock
        )
        self.assertEqual(engine.audit_sfc_compliance(long_order()).status, STATUS_APPROVED)
        self.assertEqual(engine.audit_sfc_compliance(long_order()).status, STATUS_APPROVED)
        third = engine.audit_sfc_compliance(long_order())

        self.assertEqual(third.status, "REJECTED_MESSAGE_RATE_LIMIT")
        self.assertIn(VIOLATION_MESSAGE_RATE_LIMIT, third.violations)

    def test_window_rolls_forward_with_the_clock(self):
        clock = _StepClock()
        engine = HkSfcAlgorithmicTradingEngine(
            max_messages_per_interval=2, message_interval_seconds=1.0, clock=clock
        )
        engine.audit_sfc_compliance(long_order())
        engine.audit_sfc_compliance(long_order())
        clock.advance(1.5)

        self.assertEqual(engine.audit_sfc_compliance(long_order()).status, STATUS_APPROVED)

    def test_rate_limit_is_counted_per_algo(self):
        clock = _StepClock()
        engine = HkSfcAlgorithmicTradingEngine(
            max_messages_per_interval=2, message_interval_seconds=1.0, clock=clock
        )
        engine.audit_sfc_compliance(long_order(algo_id="A"))
        engine.audit_sfc_compliance(long_order(algo_id="A"))
        other = engine.audit_sfc_compliance(long_order(algo_id="B"))

        self.assertEqual(other.status, STATUS_APPROVED)

    def test_rejected_submissions_still_consume_the_message_budget(self):
        clock = _StepClock()
        engine = HkSfcAlgorithmicTradingEngine(
            max_messages_per_interval=2, message_interval_seconds=1.0, clock=clock
        )
        engine.audit_sfc_compliance(long_order(algo_authorised_for_production=False))
        engine.audit_sfc_compliance(long_order(algo_authorised_for_production=False))
        third = engine.audit_sfc_compliance(long_order())

        self.assertIn(VIOLATION_MESSAGE_RATE_LIMIT, third.violations)

    def test_rate_limit_is_off_by_default(self):
        engine = HkSfcAlgorithmicTradingEngine()
        for _ in range(50):
            report = engine.audit_sfc_compliance(long_order())
        self.assertEqual(report.status, STATUS_APPROVED)


class TestChildOrderControls(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HkSfcAlgorithmicTradingEngine()

    def test_child_buy_above_parent_limit_is_rejected(self):
        report = self.engine.audit_sfc_compliance(
            long_order(order_price=301.00, parent_order_id="P-1", parent_limit_price=300.00)
        )
        self.assertIn(VIOLATION_CHILD_PRICE_EXCEEDS_PARENT, report.violations)

    def test_child_buy_at_parent_limit_is_allowed(self):
        report = self.engine.audit_sfc_compliance(
            long_order(order_price=300.00, parent_order_id="P-1", parent_limit_price=300.00)
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_child_sell_below_parent_limit_is_rejected(self):
        report = self.engine.audit_sfc_compliance(
            long_order(
                side="SELL", order_price=299.00, parent_order_id="P-1", parent_limit_price=300.00
            )
        )
        self.assertIn(VIOLATION_CHILD_PRICE_EXCEEDS_PARENT, report.violations)

    def test_child_quantity_above_parent_remaining_is_rejected(self):
        report = self.engine.audit_sfc_compliance(
            long_order(
                order_quantity=10_000, parent_order_id="P-1", parent_remaining_quantity=9_999
            )
        )
        self.assertIn(VIOLATION_CHILD_QUANTITY_EXCEEDS_PARENT, report.violations)

    def test_child_quantity_equal_to_parent_remaining_is_allowed(self):
        report = self.engine.audit_sfc_compliance(
            long_order(
                order_quantity=10_000, parent_order_id="P-1", parent_remaining_quantity=10_000
            )
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_parent_context_is_ignored_without_a_parent_order_id(self):
        report = self.engine.audit_sfc_compliance(
            long_order(order_price=301.00, parent_limit_price=1.00)
        )
        self.assertNotIn(VIOLATION_CHILD_PRICE_EXCEEDS_PARENT, report.violations)


class TestRequestValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HkSfcAlgorithmicTradingEngine()

    def test_unknown_session_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(long_order(session="LUNCH"))

    def test_unknown_side_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(long_order(side="COVER"))

    def test_unknown_order_type_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(long_order(order_type="MARKET"))

    def test_empty_algo_id_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(long_order(algo_id="  "))

    def test_empty_stock_code_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(long_order(stock_code=""))

    def test_non_positive_quantity_raises(self):
        for quantity in (0, -100):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.engine.audit_sfc_compliance(long_order(order_quantity=quantity))

    def test_fractional_quantity_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(long_order(order_quantity=100.5))

    def test_non_positive_or_non_finite_order_price_raises(self):
        for price in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self.engine.audit_sfc_compliance(long_order(order_price=price))

    def test_short_sell_flag_on_a_buy_order_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(long_order(side="BUY", is_short_sell=True))

    def test_negative_parent_remaining_quantity_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_sfc_compliance(
                long_order(parent_order_id="P-1", parent_remaining_quantity=-1)
            )

    def test_non_request_argument_raises(self):
        with self.assertRaises(TypeError):
            self.engine.audit_sfc_compliance({"algo_id": "A"})


class TestEngineConstruction(unittest.TestCase):
    def test_invalid_constructor_parameters_raise(self):
        for kwargs in (
            {"max_order_value_hkd": 0.0},
            {"max_order_value_hkd": -1.0},
            {"max_order_value_hkd": float("inf")},
            {"max_price_deviation_pct": 0.0},
            {"max_order_quantity": 0},
            {"max_order_quantity": 10.5},
            {"max_adv_participation_pct": -1.0},
            {"max_messages_per_interval": 0},
            {"message_interval_seconds": 0.0},
        ):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                HkSfcAlgorithmicTradingEngine(**kwargs)


class TestAuditTrail(unittest.TestCase):
    def test_every_decision_is_recorded_including_approvals(self):
        engine = HkSfcAlgorithmicTradingEngine()
        engine.audit_sfc_compliance(long_order())
        engine.audit_sfc_compliance(long_order(algo_authorised_for_production=False))

        trail = engine.audit_trail
        self.assertEqual(len(trail), 2)
        self.assertEqual(trail[0].status, STATUS_APPROVED)
        self.assertEqual(trail[1].status, "REJECTED_ALGO_NOT_AUTHORISED")

    def test_audit_trail_is_not_mutable_from_outside(self):
        engine = HkSfcAlgorithmicTradingEngine()
        engine.audit_sfc_compliance(long_order())
        self.assertIsInstance(engine.audit_trail, tuple)

    def test_audit_sink_receives_each_decision(self):
        received = []
        engine = HkSfcAlgorithmicTradingEngine(audit_sink=received.append)
        engine.audit_sfc_compliance(long_order())

        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], HkSfcComplianceReport)

    def test_as_audit_record_carries_the_annex_fields(self):
        engine = HkSfcAlgorithmicTradingEngine()
        record = engine.audit_sfc_compliance(long_order()).as_audit_record()

        for key in (
            "order_reference",
            "decision_time_utc",
            "algo_id",
            "stock_code",
            "session",
            "side",
            "status",
            "violations",
            "blocks_order",
        ):
            self.assertIn(key, record)

    def test_statutory_breaches_are_logged_at_critical(self):
        engine = HkSfcAlgorithmicTradingEngine()
        with self.assertLogs("hong_kong_sfc_algorithmic_trading_guidelines", level="CRITICAL"):
            engine.audit_sfc_compliance(compliant_short(has_locate_borrow=False))

    def test_threshold_breaches_are_logged_at_warning_not_critical(self):
        engine = HkSfcAlgorithmicTradingEngine(max_order_value_hkd=1_000.0)
        with self.assertLogs("hong_kong_sfc_algorithmic_trading_guidelines", level="WARNING") as logs:
            engine.audit_sfc_compliance(long_order())
        self.assertNotIn("CRITICAL", "\n".join(logs.output))


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
