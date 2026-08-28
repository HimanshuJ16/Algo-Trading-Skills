"""Unit tests for pattern-day-trader-rule-compliance-us.

Expected values are derived from the rule text and from a hand-checked July 2026
calendar, not from the implementation:

* FINRA Rule 4210(f)(8)(B)(i) (deleted 2026-06-04) defined a day trade as buying
  and selling the same security on the same day, *except* a long held overnight
  and sold the next day "prior to any new purchase of the same security" (and
  the mirror case for a short).
* 2026-07-13 is a Monday and 2026-07-17 the Friday of that week; 2026-07-02 is a
  Thursday and 2026-07-03 the Friday (the observed Independence Day holiday).
* Rule 4210(d)(2): deficits are satisfied by the 5th business day to avoid the
  90-day freeze trigger and expire after the 15th; the de minimis carve-out is
  the lesser of 5 percent of equity or $1,000.
"""
import datetime
import logging
import unittest
from zoneinfo import ZoneInfo

from pdt_tracker import (
    DE_MINIMIS_DEFICIT_CAP,
    DayTradePolicy,
    DayTradeTracker,
    IntradayMarginSnapshot,
    LEGACY_FINRA_PDT_POLICY,
    PDTComplianceEngine,
    PDTInputError,
    deficit_freeze_deadline,
    intraday_margin_deficit,
    is_de_minimis_deficit,
)

ET = ZoneInfo("America/New_York")

MON = datetime.date(2026, 7, 13)
TUE = datetime.date(2026, 7, 14)
WED = datetime.date(2026, 7, 15)
THU = datetime.date(2026, 7, 16)
FRI = datetime.date(2026, 7, 17)
SAT = datetime.date(2026, 7, 18)
NEXT_MON = datetime.date(2026, 7, 20)


def et(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


class TestDayTradeClassification(unittest.TestCase):
    def setUp(self):
        self.engine = PDTComplianceEngine()

    def test_same_day_round_trip_is_a_day_trade(self):
        self.engine.record_execution("AAPL", "BUY", 100, et(MON, 10))
        record = self.engine.record_execution("AAPL", "SELL", 100, et(MON, 14))

        self.assertIsNotNone(record)
        self.assertEqual(record.symbol, "AAPL")
        self.assertEqual(record.trade_date, MON)
        self.assertEqual(record.quantity, 100)
        self.assertEqual(self.engine.get_rolling_day_trade_count(MON), 1)

    def test_overnight_hold_is_not_a_day_trade(self):
        self.engine.record_execution("MSFT", "BUY", 100, et(MON, 10))
        record = self.engine.record_execution("MSFT", "SELL", 100, et(TUE, 10))

        self.assertIsNone(record)
        self.assertEqual(self.engine.get_rolling_day_trade_count(TUE), 0)

    def test_short_then_cover_same_day_is_a_day_trade(self):
        self.engine.record_execution("TSLA", "SELL_SHORT", 50, et(MON, 10))
        record = self.engine.record_execution("TSLA", "BUY_TO_COVER", 50, et(MON, 11))

        self.assertIsNotNone(record)
        self.assertEqual(record.quantity, 50)

    def test_overnight_long_sold_before_any_new_purchase_is_not_a_day_trade(self):
        """Rule 4210(f)(8)(B)(i)a carve-out, then a genuine round trip after it."""
        self.engine.record_execution("NVDA", "BUY", 100, et(MON, 10))

        # Tuesday: sell the overnight long first -- carve-out applies.
        self.assertIsNone(self.engine.record_execution("NVDA", "SELL", 100, et(TUE, 10)))
        # Then open and close a fresh position -- that one does count.
        self.engine.record_execution("NVDA", "BUY", 100, et(TUE, 11))
        self.assertIsNotNone(self.engine.record_execution("NVDA", "SELL", 100, et(TUE, 12)))

        self.assertEqual(self.engine.get_rolling_day_trade_count(TUE), 1)

    def test_new_purchase_before_the_sale_defeats_the_overnight_carve_out(self):
        """The carve-out requires the sale to precede any new purchase that day."""
        self.engine.record_execution("NVDA", "BUY", 100, et(MON, 10))  # held overnight

        self.engine.record_execution("NVDA", "BUY", 100, et(TUE, 10))
        record = self.engine.record_execution("NVDA", "SELL", 100, et(TUE, 11))

        self.assertIsNotNone(record, "sale after a same-day purchase is a day trade")
        self.assertEqual(record.quantity, 100)
        self.assertEqual(self.engine.get_rolling_day_trade_count(TUE), 1)
        # The overnight lot is untouched: 100 shares still open.
        self.assertEqual(sum(lot.quantity for lot in self.engine.open_positions["NVDA"]), 100)

    def test_scale_in_execution_is_not_discarded(self):
        """Regression: a same-side execution used to be dropped, corrupting state
        so that the *next* day's closing sale was booked as a phantom day trade."""
        self.engine.record_execution("AAPL", "BUY", 100, et(MON, 10))
        self.engine.record_execution("AAPL", "BUY", 100, et(MON, 10, 5))
        self.assertIsNotNone(self.engine.record_execution("AAPL", "SELL", 100, et(MON, 11)))
        self.assertEqual(sum(lot.quantity for lot in self.engine.open_positions["AAPL"]), 100)

        # Tuesday: closing the overnight remainder is not a day trade.
        self.assertIsNone(self.engine.record_execution("AAPL", "SELL", 100, et(TUE, 10)))
        self.assertEqual(self.engine.get_rolling_day_trade_count(TUE), 1)

    def test_single_close_of_two_same_day_lots_counts_once(self):
        self.engine.record_execution("AAPL", "BUY", 100, et(MON, 10))
        self.engine.record_execution("AAPL", "BUY", 100, et(MON, 10, 5))
        record = self.engine.record_execution("AAPL", "SELL", 200, et(MON, 11))

        self.assertEqual(record.quantity, 200)
        self.assertEqual(record.open_timestamp, et(MON, 10), "earliest matched lot")
        self.assertEqual(self.engine.get_rolling_day_trade_count(MON), 1)

    def test_reversal_opens_a_lot_on_the_opposite_side(self):
        self.engine.record_execution("AAPL", "BUY", 100, et(MON, 10))
        record = self.engine.record_execution("AAPL", "SELL", 150, et(MON, 11))

        self.assertEqual(record.quantity, 100, "only the offset quantity is a day trade")
        remaining = self.engine.open_positions["AAPL"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].side, "SELL")
        self.assertEqual(remaining[0].quantity, 50)

    def test_fractional_shares_do_not_leave_a_residual_lot(self):
        """0.1 + 0.2 - 0.3 leaves ~2.8e-17 in binary floating point; that residue
        must not survive as an open lot and be 'closed' again."""
        self.engine.record_execution("FRAC", "BUY", 0.1, et(MON, 10))
        self.engine.record_execution("FRAC", "BUY", 0.2, et(MON, 10, 5))
        self.assertIsNotNone(self.engine.record_execution("FRAC", "SELL", 0.3, et(MON, 11)))
        self.assertEqual(self.engine.open_positions["FRAC"], [])

        # Opening a short later the same day must not re-close the residue.
        self.assertIsNone(self.engine.record_execution("FRAC", "SELL", 0.5, et(MON, 12)))
        self.assertEqual(self.engine.get_rolling_day_trade_count(MON), 1)

    def test_intraday_reversal_and_close_counts_twice(self):
        self.engine.record_execution("REV", "BUY", 100, et(MON, 10))
        self.assertIsNotNone(self.engine.record_execution("REV", "SELL", 150, et(MON, 11)))
        self.assertIsNotNone(self.engine.record_execution("REV", "BUY", 50, et(MON, 12)))
        self.assertEqual(self.engine.get_rolling_day_trade_count(MON), 2)
        self.assertEqual(self.engine.open_positions["REV"], [])

    def test_positions_in_other_symbols_do_not_interact(self):
        self.engine.record_execution("AAPL", "BUY", 100, et(MON, 10))
        self.assertIsNone(self.engine.record_execution("MSFT", "SELL", 100, et(MON, 11)))
        self.assertEqual(self.engine.get_rolling_day_trade_count(MON), 0)


class TestTimezoneHandling(unittest.TestCase):
    def test_utc_timestamps_keep_a_late_session_round_trip_on_one_trade_date(self):
        """Regression: 19:00 and 20:30 ET are 23:00 and 00:30 UTC -- naive date
        arithmetic split the round trip across two dates and lost the day trade."""
        engine = PDTComplianceEngine()
        utc = datetime.timezone.utc
        engine.record_execution("XYZ", "BUY", 10, datetime.datetime(2026, 7, 13, 23, 0, tzinfo=utc))
        record = engine.record_execution(
            "XYZ", "SELL", 10, datetime.datetime(2026, 7, 14, 0, 30, tzinfo=utc)
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.trade_date, MON)
        self.assertEqual(engine.get_rolling_day_trade_count(MON), 1)

    def test_naive_timestamp_is_rejected_by_default(self):
        engine = PDTComplianceEngine()
        with self.assertRaises(PDTInputError):
            engine.record_execution("XYZ", "BUY", 10, datetime.datetime(2026, 7, 13, 10, 0))

    def test_naive_timestamp_accepted_when_declared_market_local(self):
        engine = PDTComplianceEngine(assume_naive_is_market_local=True)
        engine.record_execution("XYZ", "BUY", 10, datetime.datetime(2026, 7, 13, 10, 0))
        record = engine.record_execution("XYZ", "SELL", 10, datetime.datetime(2026, 7, 13, 14, 0))
        self.assertEqual(record.trade_date, MON)

    def test_unknown_timezone_rejected(self):
        with self.assertRaises(PDTInputError):
            PDTComplianceEngine(market_timezone="Mars/Olympus_Mons")


class TestRollingWindow(unittest.TestCase):
    def setUp(self):
        self.engine = PDTComplianceEngine()

    def _day_trade(self, day: datetime.date, symbol: str = "AAPL"):
        self.engine.record_execution(symbol, "BUY", 10, et(day, 10))
        self.engine.record_execution(symbol, "SELL", 10, et(day, 11))

    def test_window_is_five_business_days_inclusive_of_the_as_of_day(self):
        self._day_trade(MON)
        # Mon 13 .. Fri 17 is exactly five business days.
        self.assertEqual(self.engine.get_rolling_day_trade_count(FRI), 1)
        # Tue 14 .. Mon 20 no longer reaches Mon 13.
        self.assertEqual(self.engine.get_rolling_day_trade_count(NEXT_MON), 0)

    def test_weekend_as_of_date_falls_back_to_the_preceding_business_day(self):
        self._day_trade(MON)
        self.assertEqual(
            self.engine.get_rolling_day_trade_count(SAT),
            self.engine.get_rolling_day_trade_count(FRI),
        )

    def test_holiday_calendar_keeps_an_older_trade_inside_the_window(self):
        """Thu 2026-07-02 with the Fri 2026-07-03 holiday observed: the five
        business days ending Thu 2026-07-09 are 2, 6, 7, 8, 9 -- so the trade is
        still in the window. Ignoring the holiday they are 3, 6, 7, 8, 9."""
        holiday = datetime.date(2026, 7, 3)
        thursday = datetime.date(2026, 7, 2)
        next_thursday = datetime.date(2026, 7, 9)

        weekends_only = PDTComplianceEngine()
        weekends_only.record_execution("AAPL", "BUY", 10, et(thursday, 10))
        weekends_only.record_execution("AAPL", "SELL", 10, et(thursday, 11))
        self.assertEqual(weekends_only.get_rolling_day_trade_count(next_thursday), 0)

        with_holiday = PDTComplianceEngine(holidays=[holiday])
        with_holiday.record_execution("AAPL", "BUY", 10, et(thursday, 10))
        with_holiday.record_execution("AAPL", "SELL", 10, et(thursday, 11))
        self.assertEqual(with_holiday.get_rolling_day_trade_count(next_thursday), 1)

    def test_business_days_between_is_signed(self):
        self.assertEqual(self.engine.business_days_between(FRI, NEXT_MON), 1)
        self.assertEqual(self.engine.business_days_between(NEXT_MON, FRI), -1)
        self.assertEqual(self.engine.business_days_between(MON, MON), 0)

    def test_future_dated_records_are_excluded_and_flagged(self):
        """Regression: a trade dated after the as-of date used to fall inside the
        window because the business-day difference clamped to zero."""
        self._day_trade(datetime.date(2026, 12, 1))
        self.assertEqual(self.engine.get_rolling_day_trade_count(MON), 0)

        decision = self.engine.evaluate_day_trade_gate(10_000.0, MON)
        self.assertTrue(any("after as_of_date" in w for w in decision.warnings))

    def test_missing_holiday_calendar_is_flagged_on_every_decision(self):
        decision = self.engine.evaluate_day_trade_gate(10_000.0, MON)
        self.assertTrue(any("holiday calendar" in w for w in decision.warnings))

    def test_as_of_date_must_be_a_date(self):
        with self.assertRaises(PDTInputError):
            self.engine.get_rolling_day_trade_count("2026-07-13")


class TestGate(unittest.TestCase):
    def setUp(self):
        self.engine = PDTComplianceEngine()

    def _day_trades(self, count: int, day: datetime.date = MON):
        for index in range(count):
            symbol = f"SYM{index}"
            self.engine.record_execution(symbol, "BUY", 10, et(day, 10))
            self.engine.record_execution(symbol, "SELL", 10, et(day, 11))

    def test_fourth_day_trade_blocked_below_threshold(self):
        self._day_trades(3)
        decision = self.engine.evaluate_day_trade_gate(20_000.0, MON)

        self.assertTrue(decision.blocked)
        self.assertIn("PDT VETO", decision.reason)
        self.assertEqual(decision.rolling_day_trade_count, 3)
        self.assertEqual(decision.equity, 20_000.0)
        self.assertFalse(decision.designated_pattern_day_trader)

    def test_third_day_trade_allowed_below_threshold(self):
        self._day_trades(2)
        self.assertFalse(self.engine.evaluate_day_trade_gate(20_000.0, MON).blocked)

    def test_equity_exactly_at_threshold_is_allowed(self):
        self._day_trades(3)
        self.assertFalse(self.engine.evaluate_day_trade_gate(25_000.0, MON).blocked)
        self.assertTrue(self.engine.evaluate_day_trade_gate(24_999.99, MON).blocked)

    def test_stale_history_outside_the_window_does_not_block(self):
        """Regression: with no as-of date the window used to anchor on the last
        recorded trade, so a years-old history vetoed forever."""
        self._day_trades(3, day=datetime.date(2020, 1, 6))
        self.assertFalse(self.engine.evaluate_day_trade_gate(10_000.0, MON).blocked)

    def test_designation_is_sticky_once_the_limit_is_reached(self):
        self._day_trades(4)
        self.assertTrue(self.engine.designated_pattern_day_trader)

        # A month later the window is empty, but the designation stands and the
        # minimum equity must be maintained "at all times".
        later = datetime.date(2026, 8, 17)
        self.assertEqual(self.engine.get_rolling_day_trade_count(later), 0)
        decision = self.engine.evaluate_day_trade_gate(20_000.0, later)
        self.assertTrue(decision.blocked)
        self.assertIn("designated", decision.reason)

    def test_designated_account_above_threshold_is_allowed_with_a_maintenance_note(self):
        self._day_trades(4)
        decision = self.engine.evaluate_day_trade_gate(30_000.0, MON)
        self.assertFalse(decision.blocked)
        self.assertIn("at all times", decision.reason)

    def test_broker_designation_can_be_adopted_and_cleared(self):
        self.engine.set_broker_designation(True)
        self.assertTrue(self.engine.evaluate_day_trade_gate(10_000.0, MON).blocked)
        self.engine.set_broker_designation(False)
        self.assertFalse(self.engine.evaluate_day_trade_gate(10_000.0, MON).blocked)

    def test_policy_confirmed_migrated_allows_the_day_trade(self):
        migrated = DayTradePolicy(
            name="broker-migrated",
            source="broker confirmation e-mail",
            source_as_of="2026-07-01",
            confirmed_with_broker=False,
        )
        engine = PDTComplianceEngine(policy=migrated)
        for index in range(5):
            engine.record_execution(f"S{index}", "BUY", 10, et(MON, 10))
            engine.record_execution(f"S{index}", "SELL", 10, et(MON, 11))

        decision = engine.evaluate_day_trade_gate(5_000.0, MON)
        self.assertFalse(decision.blocked)
        self.assertIn("intraday margin", decision.reason)

    def test_unconfirmed_policy_after_rule_deletion_warns_but_still_blocks(self):
        self._day_trades(3)
        decision = self.engine.evaluate_day_trade_gate(20_000.0, MON)
        self.assertTrue(decision.blocked, "fail closed while the policy is unverified")
        self.assertTrue(any("4210(f)(8)(B) was deleted" in w for w in decision.warnings))

    def test_confirmed_policy_beyond_the_phase_in_is_flagged_as_house_policy(self):
        confirmed = DayTradePolicy(
            name="broker-house",
            source="broker margin agreement",
            source_as_of="2027-11-01",
            confirmed_with_broker=True,
        )
        engine = PDTComplianceEngine(policy=confirmed)
        decision = engine.evaluate_day_trade_gate(10_000.0, datetime.date(2027, 11, 1))
        self.assertTrue(any("phase-in ended" in w for w in decision.warnings))

    def test_de_minimis_exemption_is_opt_in(self):
        policy = DayTradePolicy(
            name="broker-with-6pct",
            source="broker margin agreement",
            source_as_of="2026-05-01",
            apply_de_minimis_exemption=True,
            confirmed_with_broker=True,
        )
        engine = PDTComplianceEngine(policy=policy)
        # 3 day trades (6 executions) plus 60 unrelated executions = 66 trades.
        # The projected share is 4/67 = 5.97%, inside the 6% de minimis test.
        for index in range(3):
            engine.record_execution(f"S{index}", "BUY", 10, et(MON, 10))
            engine.record_execution(f"S{index}", "SELL", 10, et(MON, 11))
        for index in range(60):
            engine.record_execution(f"H{index}", "BUY", 10, et(MON, 12))

        decision = engine.evaluate_day_trade_gate(10_000.0, MON)
        self.assertEqual(decision.total_trades_in_window, 66)
        self.assertAlmostEqual(decision.day_trade_ratio, 3 / 66)
        self.assertFalse(decision.blocked)
        self.assertIn("de minimis", decision.reason)

    def test_de_minimis_exemption_does_not_apply_to_a_concentrated_account(self):
        policy = DayTradePolicy(
            name="broker-with-6pct",
            source="broker margin agreement",
            source_as_of="2026-05-01",
            apply_de_minimis_exemption=True,
            confirmed_with_broker=True,
        )
        engine = PDTComplianceEngine(policy=policy)
        for index in range(3):
            engine.record_execution(f"S{index}", "BUY", 10, et(MON, 10))
            engine.record_execution(f"S{index}", "SELL", 10, et(MON, 11))

        decision = engine.evaluate_day_trade_gate(10_000.0, MON)
        self.assertTrue(decision.blocked)

    def test_decision_is_audit_loggable(self):
        self._day_trades(3)
        record = self.engine.evaluate_day_trade_gate(20_000.0, MON).as_log_record()

        self.assertEqual(record["as_of_date"], "2026-07-13")
        self.assertEqual(record["rolling_day_trade_count"], 3)
        self.assertEqual(record["equity"], 20_000.0)
        self.assertEqual(record["equity_threshold"], 25_000.0)
        self.assertEqual(record["policy_name"], LEGACY_FINRA_PDT_POLICY.name)
        self.assertIn("SR-FINRA-2025-017", record["policy_source"])

    def test_would_breach_pdt_tuple_view(self):
        self._day_trades(3)
        blocked, reason = self.engine.would_breach_pdt(20_000.0, MON)
        self.assertTrue(blocked)
        self.assertIn("PDT VETO", reason)


class TestReconciliation(unittest.TestCase):
    def setUp(self):
        self.engine = PDTComplianceEngine()
        self.engine.record_execution("AAPL", "BUY", 10, et(MON, 10))
        self.engine.record_execution("AAPL", "SELL", 10, et(MON, 11))

    def test_matching_count_reconciles(self):
        self.assertTrue(self.engine.reconcile_broker_count(1, MON))

    def test_mismatched_count_fails(self):
        self.assertFalse(self.engine.reconcile_broker_count(2, MON))

    def test_absent_broker_counter_is_reported_as_unverified(self):
        self.assertFalse(self.engine.reconcile_broker_count(None, MON))

    def test_invalid_broker_count_rejected(self):
        for value in (-1, True, 1.5, "1"):
            with self.assertRaises(PDTInputError):
                self.engine.reconcile_broker_count(value, MON)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = PDTComplianceEngine()

    def test_invalid_execution_inputs_rejected(self):
        cases = [
            ("", "BUY", 10),
            ("   ", "BUY", 10),
            ("AAPL", "HOLD", 10),
            ("AAPL", "", 10),
            ("AAPL", "BUY", 0),
            ("AAPL", "BUY", -5),
            ("AAPL", "BUY", float("nan")),
            ("AAPL", "BUY", "ten"),
        ]
        for symbol, side, quantity in cases:
            with self.subTest(symbol=symbol, side=side, quantity=quantity):
                with self.assertRaises(PDTInputError):
                    self.engine.record_execution(symbol, side, quantity, et(MON, 10))

    def test_non_finite_equity_rejected(self):
        for equity in (float("nan"), float("inf"), "nan", "twenty thousand", None):
            with self.subTest(equity=equity):
                with self.assertRaises(PDTInputError):
                    self.engine.evaluate_day_trade_gate(equity, MON)

    def test_numeric_string_equity_accepted(self):
        """Broker payloads routinely carry equity as a decimal string."""
        decision = self.engine.evaluate_day_trade_gate("20000.00", MON)
        self.assertEqual(decision.equity, 20_000.0)

    def test_invalid_policy_parameters_rejected(self):
        for kwargs in (
            {"equity_threshold": -1.0},
            {"max_day_trades_in_window": 0},
            {"window_business_days": 0},
            {"de_minimis_trade_fraction": 1.5},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(PDTInputError):
                    DayTradePolicy(name="x", source="s", source_as_of="2026-01-01", **kwargs)

    def test_symbol_and_side_are_normalised(self):
        self.engine.record_execution(" aapl ", "buy", 10, et(MON, 10))
        record = self.engine.record_execution("AAPL", "sell", 10, et(MON, 11))
        self.assertEqual(record.symbol, "AAPL")


class TestIntradayMargin(unittest.TestCase):
    """Rule 4210(d)(2), the standard that replaced the day-trading provisions."""

    def test_deficit_is_the_worst_negative_iml_after_an_iml_reducing_transaction(self):
        snapshots = [
            IntradayMarginSnapshot(et(MON, 10), equity=30_000.0, maintenance_margin_requirement=25_000.0),
            IntradayMarginSnapshot(et(MON, 11), equity=30_000.0, maintenance_margin_requirement=32_000.0),
            IntradayMarginSnapshot(et(MON, 12), equity=30_000.0, maintenance_margin_requirement=41_500.0),
            IntradayMarginSnapshot(et(MON, 13), equity=30_000.0, maintenance_margin_requirement=28_000.0),
        ]
        # Worst IML is 30,000 - 41,500 = -11,500.
        self.assertAlmostEqual(intraday_margin_deficit(snapshots), 11_500.0)

    def test_no_negative_iml_means_no_deficit(self):
        snapshots = [
            IntradayMarginSnapshot(et(MON, 10), equity=30_000.0, maintenance_margin_requirement=25_000.0)
        ]
        self.assertEqual(intraday_margin_deficit(snapshots), 0.0)
        self.assertEqual(intraday_margin_deficit([]), 0.0)

    def test_non_iml_reducing_snapshots_are_ignored(self):
        snapshots = [
            IntradayMarginSnapshot(
                et(MON, 11), equity=10_000.0, maintenance_margin_requirement=15_000.0,
                iml_reducing=False,
            )
        ]
        self.assertEqual(intraday_margin_deficit(snapshots), 0.0)

    def test_de_minimis_uses_the_lesser_of_five_percent_or_one_thousand(self):
        # 5% of $10,000 = $500, which is less than the $1,000 cap.
        self.assertTrue(is_de_minimis_deficit(500.0, 10_000.0))
        self.assertFalse(is_de_minimis_deficit(500.01, 10_000.0))
        # 5% of $100,000 = $5,000, so the $1,000 cap binds.
        self.assertTrue(is_de_minimis_deficit(DE_MINIMIS_DEFICIT_CAP, 100_000.0))
        self.assertFalse(is_de_minimis_deficit(1_000.01, 100_000.0))
        self.assertTrue(is_de_minimis_deficit(0.0, 100_000.0))

    def test_freeze_and_expiry_deadlines(self):
        prompt, expiry = deficit_freeze_deadline(datetime.date(2026, 7, 6))
        self.assertEqual(prompt, datetime.date(2026, 7, 13))
        self.assertEqual(expiry, datetime.date(2026, 7, 27))

    def test_pathological_holiday_calendar_raises_rather_than_hanging(self):
        every_day = [datetime.date(2026, 7, 6) + datetime.timedelta(days=n) for n in range(400)]
        with self.assertRaises(PDTInputError):
            deficit_freeze_deadline(datetime.date(2026, 7, 6), holidays=every_day)

    def test_holidays_push_the_deadlines_out(self):
        prompt, _ = deficit_freeze_deadline(
            datetime.date(2026, 7, 6), holidays=[datetime.date(2026, 7, 13)]
        )
        self.assertEqual(prompt, datetime.date(2026, 7, 14))


class TestBackwardCompatibility(unittest.TestCase):
    def test_tracker_blocks_the_fourth_day_trade(self):
        tracker = DayTradeTracker()
        for _ in range(3):
            tracker.record_day_trade(MON)

        self.assertTrue(tracker.would_breach(15_000.0, as_of_date=MON))
        self.assertFalse(tracker.would_breach(30_000.0, as_of_date=MON))

    def test_tracker_default_as_of_date_uses_market_today(self):
        """Calendar-independent: the default must equal an explicit market-local
        today, never the date of the last recorded trade."""
        tracker = DayTradeTracker()
        market_today = datetime.datetime.now(ET).date()
        for _ in range(3):
            tracker.record_day_trade(market_today)

        explicit, _ = tracker.engine.would_breach_pdt(15_000.0, market_today)
        self.assertEqual(tracker.would_breach(15_000.0), explicit)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
