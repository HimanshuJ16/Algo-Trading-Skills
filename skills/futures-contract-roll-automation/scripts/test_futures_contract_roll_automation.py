import logging
import unittest

from futures_contract_roll_automation import (
    DEFERRED_MINUS_NEARBY,
    DELIVERY_RISK_APPROACHING_FIRST_NOTICE,
    DELIVERY_RISK_FIRST_NOTICE_PASSED,
    DELIVERY_RISK_NONE,
    DELIVERY_RISK_PAST_LAST_TRADING_DAY,
    NEARBY_MINUS_DEFERRED,
    STATUS_HOLD,
    STATUS_ROLL_ACTIVE,
    STATUS_TOO_LATE,
    TRIGGER_DAYS_TO_EXPIRATION,
    TRIGGER_FIRST_NOTICE,
    TRIGGER_OPEN_INTEREST_CROSSOVER,
    TRIGGER_VOLUME_CROSSOVER,
    FuturesContractRollEngine,
    FuturesContractState,
)

# The engine logs warnings and criticals by design; keep test output clean.
logging.getLogger("futures_contract_roll_automation").setLevel(logging.CRITICAL + 1)

# E-mini S&P 500: cash settled, $50 per index point.
ES_MULTIPLIER = 50.0


def es_front(**overrides):
    kwargs = dict(
        symbol="ESH6",
        expiration_date_iso="2026-03-20",
        days_to_expiration=10,
        daily_volume=50_000,
        open_interest=100_000,
        last_price=5000.0,
        contract_multiplier=ES_MULTIPLIER,
    )
    kwargs.update(overrides)
    return FuturesContractState(**kwargs)


def es_next(**overrides):
    kwargs = dict(
        symbol="ESM6",
        expiration_date_iso="2026-06-19",
        days_to_expiration=100,
        daily_volume=120_000,
        open_interest=150_000,
        last_price=5015.0,
        contract_multiplier=ES_MULTIPLIER,
    )
    kwargs.update(overrides)
    return FuturesContractState(**kwargs)


class TestRollTriggers(unittest.TestCase):

    def setUp(self):
        self.engine = FuturesContractRollEngine(min_days_to_expiration=5)

    def test_volume_and_oi_crossover_triggers_long_roll(self):
        report = self.engine.evaluate_and_build_roll_order(
            "ES", position_side="LONG", position_qty=10,
            front_contract=es_front(), next_contract=es_next(),
        )

        self.assertTrue(report.is_roll_triggered)
        self.assertEqual(report.status, STATUS_ROLL_ACTIVE)
        self.assertIn(TRIGGER_VOLUME_CROSSOVER, report.trigger_reasons)
        self.assertIn(TRIGGER_OPEN_INTEREST_CROSSOVER, report.trigger_reasons)
        self.assertNotIn(TRIGGER_DAYS_TO_EXPIRATION, report.trigger_reasons)
        self.assertEqual(report.delivery_risk_level, DELIVERY_RISK_NONE)

        order = report.calendar_spread_order
        self.assertIsNotNone(order)
        self.assertEqual(order.front_leg_action, "SELL")   # long roll sells the front
        self.assertEqual(order.next_leg_action, "BUY")     # long roll buys the next
        self.assertEqual(order.term_structure, "CONTANGO")
        self.assertEqual(order.spread_price_diff, 15.0)    # 5015.00 - 5000.00

    def test_open_interest_crossover_alone_triggers_roll(self):
        # Front still trades more, but open interest has already migrated.
        front = es_front(daily_volume=100_000, open_interest=150_000, days_to_expiration=20)
        nxt = es_next(daily_volume=50_000, open_interest=200_000)

        report = self.engine.evaluate_and_build_roll_order(
            "ES", position_side="LONG", position_qty=1,
            front_contract=front, next_contract=nxt,
        )

        self.assertTrue(report.is_roll_triggered)
        self.assertEqual(report.trigger_reasons, (TRIGGER_OPEN_INTEREST_CROSSOVER,))

    def test_days_to_expiration_threshold_triggers_roll(self):
        front = es_front(days_to_expiration=5, daily_volume=200_000, open_interest=300_000)
        nxt = es_next(daily_volume=10_000, open_interest=20_000)

        report = self.engine.evaluate_and_build_roll_order(
            "ES", position_side="LONG", position_qty=3,
            front_contract=front, next_contract=nxt,
        )

        self.assertEqual(report.trigger_reasons, (TRIGGER_DAYS_TO_EXPIRATION,))
        self.assertEqual(report.trigger_reason, TRIGGER_DAYS_TO_EXPIRATION)

    def test_multiple_triggers_are_all_reported(self):
        front = es_front(days_to_expiration=2, daily_volume=10_000, open_interest=300_000)
        nxt = es_next(daily_volume=90_000, open_interest=20_000)

        report = self.engine.evaluate_and_build_roll_order(
            "ES", position_side="LONG", position_qty=1,
            front_contract=front, next_contract=nxt,
        )

        self.assertEqual(
            report.trigger_reasons,
            (TRIGGER_DAYS_TO_EXPIRATION, TRIGGER_VOLUME_CROSSOVER),
        )
        self.assertEqual(
            report.trigger_reason,
            "DAYS_TO_EXPIRATION_THRESHOLD+VOLUME_CROSSOVER",
        )

    def test_hold_front_when_no_trigger(self):
        front = es_front(days_to_expiration=15, daily_volume=150_000, open_interest=200_000)
        nxt = es_next(daily_volume=20_000, open_interest=30_000, last_price=5010.0)

        report = self.engine.evaluate_and_build_roll_order(
            "ES", position_side="LONG", position_qty=10,
            front_contract=front, next_contract=nxt,
        )

        self.assertFalse(report.is_roll_triggered)
        self.assertEqual(report.status, STATUS_HOLD)
        self.assertEqual(report.trigger_reason, "NONE")
        self.assertEqual(report.trigger_reasons, ())
        self.assertIsNone(report.calendar_spread_order)

    def test_exact_threshold_boundaries(self):
        # DBE exactly at the threshold rolls; one day above it does not.
        quiet_next = dict(daily_volume=1, open_interest=1)
        at_threshold = self.engine.evaluate_and_build_roll_order(
            "ES", "LONG", 1,
            es_front(days_to_expiration=5, daily_volume=10, open_interest=10),
            es_next(**quiet_next),
        )
        above_threshold = self.engine.evaluate_and_build_roll_order(
            "ES", "LONG", 1,
            es_front(days_to_expiration=6, daily_volume=10, open_interest=10),
            es_next(**quiet_next),
        )
        self.assertTrue(at_threshold.is_roll_triggered)
        self.assertFalse(above_threshold.is_roll_triggered)

        # Equal volumes are not a crossover; strictly greater is required.
        equal_volume = self.engine.evaluate_and_build_roll_order(
            "ES", "LONG", 1,
            es_front(days_to_expiration=30, daily_volume=1_000, open_interest=1_000),
            es_next(daily_volume=1_000, open_interest=1_000),
        )
        self.assertFalse(equal_volume.is_roll_triggered)


class TestHoldAuditNotes(unittest.TestCase):
    """The hold note must describe only the conditions actually evaluated.

    Regression: the pre-2.0.0 note asserted "Front Vol > Next Vol" unconditionally,
    which was false whenever the crossover trigger was switched off.
    """

    def test_hold_note_does_not_claim_front_volume_leads_when_trigger_disabled(self):
        engine = FuturesContractRollEngine(
            min_days_to_expiration=5,
            enable_volume_crossover=False,
            enable_open_interest_crossover=False,
        )
        front = es_front(days_to_expiration=20, daily_volume=10_000, open_interest=10_000)
        nxt = es_next(daily_volume=90_000, open_interest=90_000)

        report = engine.evaluate_and_build_roll_order("ES", "LONG", 1, front, nxt)

        self.assertFalse(report.is_roll_triggered)
        self.assertIn("volume crossover trigger disabled", report.audit_notes)
        self.assertIn("open interest crossover trigger disabled", report.audit_notes)
        self.assertNotIn("10,000) >= next volume", report.audit_notes)

    def test_hold_note_reports_first_notice_slack_for_delivered_contracts(self):
        engine = FuturesContractRollEngine(min_days_to_expiration=5, min_days_to_first_notice=2)
        front = es_front(
            symbol="ZCZ6", days_to_expiration=40, daily_volume=100, open_interest=100,
            is_physically_delivered=True, days_to_first_notice=25,
        )
        nxt = es_next(symbol="ZCH7", daily_volume=1, open_interest=1)

        report = engine.evaluate_and_build_roll_order("ZC", "LONG", 1, front, nxt)

        self.assertFalse(report.is_roll_triggered)
        self.assertIn("days to first notice=25d", report.audit_notes)


class TestFirstNoticeDayHandling(unittest.TestCase):
    """First Notice Day, not Last Trading Day, bounds physical-delivery risk.

    CBOT grain First Notice Day is the last business day of the month preceding the
    delivery month, while the last trading day is the business day before the 15th
    of the delivery month — roughly ten business days later. A days-to-expiration
    rule alone therefore rolls after the position is already deliverable.
    """

    def setUp(self):
        self.engine = FuturesContractRollEngine(
            min_days_to_expiration=5, min_days_to_first_notice=2
        )
        # Corn Z6: FND 2026-11-30, LTD 2026-12-14. Front is still the liquid leg.
        self.front = FuturesContractState(
            symbol="ZCZ6", expiration_date_iso="2026-12-14", days_to_expiration=10,
            daily_volume=250_000, open_interest=400_000, last_price=4.50,
            is_physically_delivered=True, days_to_first_notice=1,
            contract_multiplier=5000.0,
        )
        self.nxt = FuturesContractState(
            symbol="ZCH7", expiration_date_iso="2027-03-12", days_to_expiration=70,
            daily_volume=90_000, open_interest=200_000, last_price=4.62,
            is_physically_delivered=True, days_to_first_notice=71,
            contract_multiplier=5000.0,
        )

    def test_approaching_first_notice_forces_roll_despite_slack_dbe_and_liquidity(self):
        report = self.engine.evaluate_and_build_roll_order("ZC", "LONG", 2, self.front, self.nxt)

        self.assertTrue(report.is_roll_triggered)
        self.assertEqual(report.delivery_risk_level, DELIVERY_RISK_APPROACHING_FIRST_NOTICE)
        self.assertEqual(report.trigger_reasons, (TRIGGER_FIRST_NOTICE,))
        self.assertIn("DELIVERY RISK", report.audit_notes)
        self.assertIsNotNone(report.calendar_spread_order)

    def test_first_notice_already_passed_is_flagged(self):
        self.front.days_to_first_notice = 0
        report = self.engine.evaluate_and_build_roll_order("ZC", "LONG", 2, self.front, self.nxt)

        self.assertEqual(report.delivery_risk_level, DELIVERY_RISK_FIRST_NOTICE_PASSED)
        self.assertTrue(report.is_roll_triggered)

    def test_cash_settled_contract_needs_no_first_notice_data(self):
        report = FuturesContractRollEngine().evaluate_and_build_roll_order(
            "ES", "LONG", 1,
            es_front(days_to_expiration=30, daily_volume=10, open_interest=10),
            es_next(daily_volume=1, open_interest=1),
        )
        self.assertEqual(report.delivery_risk_level, DELIVERY_RISK_NONE)
        self.assertFalse(report.is_roll_triggered)

    def test_physically_delivered_contract_without_fnd_raises(self):
        bad = FuturesContractState(
            symbol="ZCZ6", expiration_date_iso="2026-12-14", days_to_expiration=10,
            daily_volume=1, open_interest=1, last_price=4.50,
            is_physically_delivered=True,
        )
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_and_build_roll_order("ZC", "LONG", 1, bad, self.nxt)
        self.assertIn("days_to_first_notice", str(ctx.exception))

    def test_roll_target_inside_its_own_notice_window_is_flagged(self):
        self.nxt.days_to_first_notice = 1
        report = self.engine.evaluate_and_build_roll_order("ZC", "LONG", 2, self.front, self.nxt)

        self.assertIn("roll target ZCH7 is itself at", report.audit_notes)
        # The front contract's own risk level is unaffected by the target's.
        self.assertEqual(report.delivery_risk_level, DELIVERY_RISK_APPROACHING_FIRST_NOTICE)

    def test_past_last_trading_day_escalates_without_building_an_order(self):
        self.front.days_to_expiration = -1
        report = self.engine.evaluate_and_build_roll_order("ZC", "LONG", 2, self.front, self.nxt)

        self.assertEqual(report.status, STATUS_TOO_LATE)
        self.assertEqual(report.delivery_risk_level, DELIVERY_RISK_PAST_LAST_TRADING_DAY)
        self.assertIsNone(report.calendar_spread_order)

    def test_last_trading_session_is_still_tradable(self):
        self.front.days_to_expiration = 0
        self.front.days_to_first_notice = 30
        report = self.engine.evaluate_and_build_roll_order("ZC", "LONG", 2, self.front, self.nxt)

        self.assertEqual(report.status, STATUS_ROLL_ACTIVE)
        self.assertIsNotNone(report.calendar_spread_order)


class TestSpreadConstruction(unittest.TestCase):

    def test_long_roll_under_nearby_minus_deferred_sells_the_spread(self):
        engine = FuturesContractRollEngine(
            spread_quoting_convention=NEARBY_MINUS_DEFERRED
        )
        report = engine.evaluate_and_build_roll_order(
            "ES", "LONG", 10, es_front(), es_next()
        )
        order = report.calendar_spread_order

        # Buying this spread buys the nearby leg, so a long rolls by selling it.
        self.assertEqual(order.spread_side, "SELL")
        self.assertEqual(order.quoted_spread_price, -15.0)   # 5000.00 - 5015.00
        self.assertEqual(order.spread_price_diff, 15.0)      # convention-independent
        self.assertEqual(order.spread_symbol, "ESH6-ESM6")

    def test_long_roll_under_deferred_minus_nearby_buys_the_spread(self):
        engine = FuturesContractRollEngine(
            spread_quoting_convention=DEFERRED_MINUS_NEARBY
        )
        report = engine.evaluate_and_build_roll_order(
            "ES", "LONG", 10, es_front(), es_next()
        )
        order = report.calendar_spread_order

        self.assertEqual(order.spread_side, "BUY")
        self.assertEqual(order.quoted_spread_price, 15.0)
        self.assertEqual(order.front_leg_action, "SELL")     # legs are unchanged
        self.assertEqual(order.next_leg_action, "BUY")

    def test_short_roll_reverses_both_legs_and_the_spread_side(self):
        engine = FuturesContractRollEngine(
            spread_quoting_convention=NEARBY_MINUS_DEFERRED
        )
        report = engine.evaluate_and_build_roll_order(
            "ES", "short", 10, es_front(), es_next()
        )
        order = report.calendar_spread_order

        self.assertEqual(order.front_leg_action, "BUY")
        self.assertEqual(order.next_leg_action, "SELL")
        self.assertEqual(order.spread_side, "BUY")

    def test_backwardation_and_flat_classification(self):
        engine = FuturesContractRollEngine()
        back = engine.evaluate_and_build_roll_order(
            "CL", "LONG", 1,
            es_front(last_price=80.0, contract_multiplier=1000.0),
            es_next(last_price=78.5, contract_multiplier=1000.0),
        ).calendar_spread_order
        self.assertEqual(back.term_structure, "BACKWARDATION")
        self.assertAlmostEqual(back.spread_price_diff, -1.5, places=10)

        flat = engine.evaluate_and_build_roll_order(
            "CL", "LONG", 1,
            es_front(last_price=80.0), es_next(last_price=80.0),
        ).calendar_spread_order
        self.assertEqual(flat.term_structure, "FLAT")

    def test_unknown_quoting_convention_raises(self):
        with self.assertRaises(ValueError):
            FuturesContractRollEngine(spread_quoting_convention="NEAR_OVER_FAR")


class TestRollCostEstimate(unittest.TestCase):
    """Roll basis cost = (P_next - P_front) x qty x multiplier, signed by side."""

    def test_long_roll_in_contango_is_a_drag_scaled_by_the_multiplier(self):
        # Sell ESH6 at 5000, buy ESM6 at 5015: 15 index points x $50 x 10 = $7,500 paid.
        report = FuturesContractRollEngine().evaluate_and_build_roll_order(
            "ES", "LONG", 10, es_front(), es_next()
        )
        self.assertAlmostEqual(
            report.calendar_spread_order.estimated_roll_cost, 7_500.0, places=6
        )

    def test_short_roll_in_contango_is_a_credit(self):
        report = FuturesContractRollEngine().evaluate_and_build_roll_order(
            "ES", "SHORT", 10, es_front(), es_next()
        )
        self.assertAlmostEqual(
            report.calendar_spread_order.estimated_roll_cost, -7_500.0, places=6
        )

    def test_long_roll_in_backwardation_is_a_credit(self):
        # Corn: sell ZCZ6 at 4.60, buy ZCH7 at 4.55 -> -0.05 x 5,000 bu x 4 = -$1,000.
        front = FuturesContractState(
            "ZCZ6", "2026-12-14", 3, 100, 100, 4.60,
            is_physically_delivered=True, days_to_first_notice=20,
            contract_multiplier=5000.0,
        )
        nxt = FuturesContractState(
            "ZCH7", "2027-03-12", 70, 50, 50, 4.55,
            is_physically_delivered=True, days_to_first_notice=80,
            contract_multiplier=5000.0,
        )
        report = FuturesContractRollEngine().evaluate_and_build_roll_order(
            "ZC", "LONG", 4, front, nxt
        )
        self.assertAlmostEqual(
            report.calendar_spread_order.estimated_roll_cost, -1_000.0, places=6
        )


class TestSpreadPrecision(unittest.TestCase):
    """A hard-coded 4-decimal round destroys spreads on finer-quoted products."""

    def test_five_decimal_spread_survives_by_default(self):
        engine = FuturesContractRollEngine()
        front = es_front(last_price=1.08455, contract_multiplier=125_000.0)
        nxt = es_next(last_price=1.08512, contract_multiplier=125_000.0)

        order = engine.evaluate_and_build_roll_order("6E", "LONG", 1, front, nxt).calendar_spread_order

        self.assertAlmostEqual(order.spread_price_diff, 0.00057, places=10)
        # The pre-2.0.0 behaviour rounded this to 0.0006 — over one FX tick of error.
        self.assertNotEqual(order.spread_price_diff, 0.0006)

    def test_explicit_decimals_are_honoured(self):
        engine = FuturesContractRollEngine(spread_price_decimals=5)
        front = es_front(last_price=1.08455, contract_multiplier=125_000.0)
        nxt = es_next(last_price=1.08512, contract_multiplier=125_000.0)

        order = engine.evaluate_and_build_roll_order("6E", "LONG", 1, front, nxt).calendar_spread_order
        self.assertEqual(order.spread_price_diff, 0.00057)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = FuturesContractRollEngine()

    def test_rolling_a_contract_into_itself_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_and_build_roll_order(
                "ES", "LONG", 1, es_front(), es_front(days_to_expiration=100)
            )
        self.assertIn("same symbol", str(ctx.exception))

    def test_swapped_front_and_next_contracts_raise(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_and_build_roll_order(
                "ES", "LONG", 1, es_next(), es_front()
            )
        self.assertIn("swapped", str(ctx.exception))

    def test_mismatched_multipliers_raise(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_and_build_roll_order(
                "ES", "LONG", 1, es_front(), es_next(contract_multiplier=5.0)
            )
        self.assertIn("multipliers differ", str(ctx.exception))

    def test_invalid_contract_fields_raise(self):
        for overrides in (
            {"last_price": 0.0},
            {"last_price": float("nan")},
            {"daily_volume": -1},
            {"open_interest": -5},
            {"symbol": "  "},
            {"contract_multiplier": 0.0},
            {"days_to_expiration": float("nan")},
            {"days_to_expiration": 10.0},
            {"days_to_first_notice": 3.5},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.engine.evaluate_and_build_roll_order(
                        "ES", "LONG", 1, es_front(**overrides), es_next()
                    )

    def test_invalid_position_parameters_raise(self):
        for side, qty in (("LONG", 0), ("LONG", -3), ("FLAT", 1), ("", 1), ("LONG", 1.5)):
            with self.subTest(side=side, qty=qty):
                with self.assertRaises(ValueError):
                    self.engine.evaluate_and_build_roll_order(
                        "ES", side, qty, es_front(), es_next()
                    )

    def test_boolean_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_and_build_roll_order(
                "ES", "LONG", True, es_front(), es_next()
            )

    def test_negative_engine_thresholds_raise(self):
        with self.assertRaises(ValueError):
            FuturesContractRollEngine(min_days_to_expiration=-1)
        with self.assertRaises(ValueError):
            FuturesContractRollEngine(min_days_to_first_notice=-1)
        with self.assertRaises(ValueError):
            FuturesContractRollEngine(spread_price_decimals=-2)


if __name__ == "__main__":
    unittest.main()
