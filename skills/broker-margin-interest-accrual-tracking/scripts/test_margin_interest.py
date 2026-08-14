import datetime
import unittest

from margin_interest import (
    DEFAULT_BLENDED_TIERS,
    EodBalance,
    FinancingDataError,
    MarginInterestTracker,
    MarginRateTier,
    RateScheduleError,
    tiers_from_benchmark,
)

MONDAY = datetime.date(2026, 6, 1)
FRIDAY = datetime.date(2026, 6, 5)
NEXT_MONDAY = datetime.date(2026, 6, 8)
# 2026-05-25 is the US Memorial Day Monday; 2026-05-22 is the Friday before it.
FRIDAY_BEFORE_HOLIDAY = datetime.date(2026, 5, 22)
HOLIDAY_MONDAY = datetime.date(2026, 5, 25)


def _simple_tiers():
    return [
        MarginRateTier(0.0, 100000.0, 0.05),          # 5% up to 100k
        MarginRateTier(100000.0, float("inf"), 0.04),  # 4% beyond 100k
    ]


class TestEffectiveApr(unittest.TestCase):

    def setUp(self):
        self.tracker_blended = MarginInterestTracker(
            rate_tiers=_simple_tiers(),
            day_count_convention_margin=360,
            is_blended_rate=True,
        )
        self.tracker_flat = MarginInterestTracker(
            rate_tiers=_simple_tiers(),
            day_count_convention_margin=360,
            is_blended_rate=False,
        )

    def test_blended_apr_lookup(self):
        # 150k debit -> 100k @ 5%, 50k @ 4% -> (5000 + 2000) / 150000 = 4.6667%
        self.assertAlmostEqual(self.tracker_blended.get_effective_apr(150000.0), 0.0466666, places=5)

    def test_flat_apr_lookup(self):
        # 150k debit falls entirely in the second bracket under a flat schedule.
        self.assertAlmostEqual(self.tracker_flat.get_effective_apr(150000.0), 0.04, places=5)

    def test_blended_apr_prices_the_whole_balance_above_the_top_bracket(self):
        # 1,000,000 -> 100k @ 5% (5,000) + 900k @ 4% (36,000) = 41,000 / 1,000,000 = 4.1%.
        # A schedule that left the excess unpriced would report a lower rate.
        self.assertAlmostEqual(self.tracker_blended.get_effective_apr(1000000.0), 0.041, places=6)

    def test_credit_balance_owes_no_margin_interest(self):
        self.assertEqual(self.tracker_blended.get_effective_apr(0.0), 0.0)
        self.assertEqual(self.tracker_blended.get_effective_apr(-50000.0), 0.0)

    def test_nan_balance_is_rejected_rather_than_propagated(self):
        with self.assertRaises(FinancingDataError):
            self.tracker_blended.get_effective_apr(float("nan"))


class TestRateScheduleValidation(unittest.TestCase):

    def test_finite_top_tier_is_rejected(self):
        # A capped top tier silently prices everything above it at 0%, understating
        # interest on exactly the largest loans.
        with self.assertRaises(RateScheduleError):
            MarginInterestTracker(rate_tiers=[MarginRateTier(0.0, 100000.0, 0.05)])

    def test_gap_in_schedule_is_rejected(self):
        with self.assertRaises(RateScheduleError):
            MarginInterestTracker(rate_tiers=[
                MarginRateTier(0.0, 100000.0, 0.05),
                MarginRateTier(250000.0, float("inf"), 0.04),
            ])

    def test_schedule_not_starting_at_zero_is_rejected(self):
        with self.assertRaises(RateScheduleError):
            MarginInterestTracker(rate_tiers=[MarginRateTier(50000.0, float("inf"), 0.05)])

    def test_inverted_bracket_is_rejected(self):
        with self.assertRaises(RateScheduleError):
            MarginInterestTracker(rate_tiers=[MarginRateTier(0.0, -1.0, 0.05)])

    def test_caller_tier_list_is_not_mutated(self):
        # The constructor sorts its schedule; doing so in place would reorder the
        # caller's list and, for the module default, every other tracker's too.
        unsorted_tiers = [
            MarginRateTier(100000.0, float("inf"), 0.04),
            MarginRateTier(0.0, 100000.0, 0.05),
        ]
        MarginInterestTracker(rate_tiers=unsorted_tiers)
        self.assertEqual(unsorted_tiers[0].min_balance_usd, 100000.0)

    def test_module_default_schedule_is_not_reordered_by_construction(self):
        before = [t.apr for t in DEFAULT_BLENDED_TIERS]
        MarginInterestTracker()
        MarginInterestTracker()
        self.assertEqual([t.apr for t in DEFAULT_BLENDED_TIERS], before)

    def test_zero_day_count_convention_is_rejected_at_construction(self):
        with self.assertRaises(FinancingDataError):
            MarginInterestTracker(rate_tiers=_simple_tiers(), day_count_convention_margin=0)

    def test_tiers_from_benchmark_adds_spread_to_benchmark(self):
        tiers = tiers_from_benchmark(0.0433, [(100000.0, 0.015), (float("inf"), 0.005)])
        self.assertEqual(len(tiers), 2)
        self.assertAlmostEqual(tiers[0].apr, 0.0583, places=6)
        self.assertAlmostEqual(tiers[1].apr, 0.0483, places=6)
        self.assertEqual(tiers[0].min_balance_usd, 0.0)
        self.assertEqual(tiers[1].max_balance_usd, float("inf"))

    def test_tiers_from_benchmark_requires_open_ended_top_bound(self):
        with self.assertRaises(RateScheduleError):
            tiers_from_benchmark(0.0433, [(100000.0, 0.015)])


class TestConstantBalanceAccrual(unittest.TestCase):

    def setUp(self):
        self.tracker = MarginInterestTracker(
            rate_tiers=_simple_tiers(),
            day_count_convention_margin=360,
            is_blended_rate=True,
        )

    def test_daily_accrual_and_pnl_deduction(self):
        # 100,000 at 5% for 5 calendar days: 100000 * 0.05 / 360 * 5 = 69.4444
        summary = self.tracker.calculate_interest_accrual(
            start_date=MONDAY,
            holding_days=5,
            daily_debit_balance_usd=100000.0,
            gross_pnl_usd=5000.0,
        )
        self.assertAlmostEqual(summary.total_margin_interest_usd, 69.4444, places=3)
        self.assertAlmostEqual(summary.adjusted_net_pnl_usd, 5000.0 - 69.4444, places=3)
        self.assertEqual(summary.total_days_held, 5)

    def test_weekend_compounding_accrual_and_borrow_fees(self):
        # A short carried from Friday's close accrues Friday, Saturday and Sunday.
        # 100,000 at 10% for 3 days: 100000 * 0.10 / 360 * 3 = 83.3333
        summary = self.tracker.calculate_interest_accrual(
            start_date=FRIDAY,
            holding_days=3,
            daily_debit_balance_usd=0.0,
            daily_short_mv_usd=100000.0,
            short_borrow_fee_apr=0.10,
            gross_pnl_usd=1000.0,
        )
        self.assertEqual(len(summary.daily_records), 1)
        self.assertEqual(summary.daily_records[0].days_accrued, 3)
        self.assertTrue(summary.daily_records[0].is_weekend)
        self.assertAlmostEqual(summary.total_borrow_fees_usd, 83.3333, places=3)
        self.assertAlmostEqual(summary.adjusted_net_pnl_usd, 1000.0 - 83.3333, places=3)

    def test_full_week_costs_seven_calendar_days_not_five_trading_days(self):
        # Monday close to the following Monday close is 7 calendar days of financing:
        # 100000 * 0.05 / 360 * 7 = 97.2222. Feeding this a trading-day count (5)
        # would under-charge by 27.78 -- the error this parameter's contract exists
        # to prevent.
        summary = self.tracker.calculate_interest_accrual(
            start_date=MONDAY,
            holding_days=7,
            daily_debit_balance_usd=100000.0,
        )
        self.assertEqual(summary.total_days_held, 7)
        self.assertAlmostEqual(summary.total_margin_interest_usd, 97.2222, places=3)
        # Mon-Thu carry one day each; Friday's row carries the weekend.
        self.assertEqual([r.days_accrued for r in summary.daily_records], [1, 1, 1, 1, 3])

    def test_total_is_independent_of_the_starting_weekday(self):
        # Weekend batching changes ledger granularity, never the total. A model that
        # adds a weekend multiplier on top of a calendar-day count double-charges.
        from_monday = self.tracker.calculate_interest_accrual(
            start_date=MONDAY, holding_days=14, daily_debit_balance_usd=100000.0)
        from_friday = self.tracker.calculate_interest_accrual(
            start_date=FRIDAY, holding_days=14, daily_debit_balance_usd=100000.0)
        self.assertAlmostEqual(
            from_monday.total_margin_interest_usd, from_friday.total_margin_interest_usd, places=9)

    def test_holiday_monday_produces_a_single_four_day_accrual_block(self):
        # Friday before Memorial Day: the next settlement day is Tuesday, so the
        # ledger carries one four-day row and no row is dated on the holiday itself.
        self.tracker.add_holidays([HOLIDAY_MONDAY])
        summary = self.tracker.calculate_interest_accrual(
            start_date=FRIDAY_BEFORE_HOLIDAY,
            holding_days=4,
            daily_debit_balance_usd=100000.0,
        )
        self.assertEqual(len(summary.daily_records), 1)
        record = summary.daily_records[0]
        self.assertEqual(record.days_accrued, 4)
        self.assertTrue(record.is_holiday)
        self.assertTrue(record.is_weekend)
        # 100000 * 0.05 / 360 * 4 = 55.5556
        self.assertAlmostEqual(summary.total_margin_interest_usd, 55.5556, places=3)

    def test_zero_holding_period_is_free(self):
        summary = self.tracker.calculate_interest_accrual(
            start_date=MONDAY, holding_days=0, daily_debit_balance_usd=100000.0,
            gross_pnl_usd=250.0)
        self.assertEqual(summary.total_margin_interest_usd, 0.0)
        self.assertEqual(summary.adjusted_net_pnl_usd, 250.0)

    def test_negative_holding_period_is_rejected(self):
        with self.assertRaises(FinancingDataError):
            self.tracker.calculate_interest_accrual(
                start_date=MONDAY, holding_days=-5, daily_debit_balance_usd=100000.0)

    def test_trading_day_count_passed_as_float_is_rejected(self):
        with self.assertRaises(FinancingDataError):
            self.tracker.calculate_interest_accrual(
                start_date=MONDAY, holding_days=5.0, daily_debit_balance_usd=100000.0)

    def test_nan_balance_does_not_silently_poison_net_pnl(self):
        with self.assertRaises(FinancingDataError):
            self.tracker.calculate_interest_accrual(
                start_date=MONDAY, holding_days=5,
                daily_debit_balance_usd=float("nan"), gross_pnl_usd=5000.0)

    def test_credit_balance_does_not_offset_borrow_fees(self):
        # A credit cash balance earns interest this module does not model; it must
        # not show up as negative margin interest netting off the borrow fee.
        summary = self.tracker.calculate_interest_accrual(
            start_date=MONDAY,
            holding_days=1,
            daily_debit_balance_usd=-500000.0,
            daily_short_mv_usd=100000.0,
            short_borrow_fee_apr=0.10,
        )
        self.assertEqual(summary.total_margin_interest_usd, 0.0)
        self.assertAlmostEqual(summary.total_borrow_fees_usd, 27.7778, places=3)


class TestBorrowCollateralBasis(unittest.TestCase):

    def test_collateral_markup_raises_the_fee_above_raw_market_value(self):
        # IBKR charges the borrow fee on collateral, not market value:
        # 100,000 * 1.02 = 102,000 -> 102000 * 0.10 / 360 * 3 = 85.00 exactly,
        # against 83.3333 on raw market value.
        tracker = MarginInterestTracker(
            rate_tiers=_simple_tiers(), short_collateral_markup=1.02)
        summary = tracker.calculate_interest_accrual(
            start_date=FRIDAY,
            holding_days=3,
            daily_debit_balance_usd=0.0,
            daily_short_mv_usd=100000.0,
            short_borrow_fee_apr=0.10,
        )
        self.assertAlmostEqual(summary.total_borrow_fees_usd, 85.0, places=6)
        self.assertAlmostEqual(summary.daily_records[0].short_collateral_usd, 102000.0, places=6)

    def test_explicit_collateral_overrides_the_markup(self):
        # Broker-exact collateral (102% of the prior settlement price rounded up to
        # the next whole dollar, times shares) must win over the approximation.
        tracker = MarginInterestTracker(
            rate_tiers=_simple_tiers(), short_collateral_markup=1.02)
        summary = tracker.accrue_daily_balances(
            [EodBalance(
                date=MONDAY,
                short_market_value_usd=100000.0,
                short_borrow_fee_apr=0.10,
                short_collateral_usd=103000.0,
            )],
            through_date=datetime.date(2026, 6, 2),
        )
        # 103000 * 0.10 / 360 * 1 = 28.6111
        self.assertAlmostEqual(summary.total_borrow_fees_usd, 28.6111, places=3)


class TestDailyBalanceSchedule(unittest.TestCase):

    def setUp(self):
        self.tracker = MarginInterestTracker(rate_tiers=_simple_tiers())

    def test_blended_rate_is_recomputed_per_day(self):
        # Day 1: 50,000 entirely in the 5% bracket -> 50000 * 0.05 / 360 = 6.94444
        # Day 2: 200,000 -> (100k*5% + 100k*4%) / 200k = 4.5% -> 200000 * 0.045 / 360 = 25.0
        # Total 31.94444. Averaging the balances first would give 33.3333 -- the
        # tiered rate is not linear in the balance, so an average balance is wrong.
        summary = self.tracker.accrue_daily_balances(
            [
                EodBalance(date=MONDAY, debit_balance_usd=50000.0),
                EodBalance(date=datetime.date(2026, 6, 2), debit_balance_usd=200000.0),
            ],
            through_date=datetime.date(2026, 6, 3),
        )
        self.assertAlmostEqual(summary.total_margin_interest_usd, 31.94444, places=4)
        self.assertAlmostEqual(summary.daily_records[1].margin_interest_usd, 25.0, places=9)
        self.assertAlmostEqual(summary.daily_records[1].effective_margin_apr, 0.045, places=9)
        self.assertEqual(summary.total_days_held, 2)

    def test_gap_between_observations_accrues_every_calendar_day(self):
        # Observing Friday then the following Monday charges three days, without
        # the caller having to know anything about the calendar.
        summary = self.tracker.accrue_daily_balances(
            [
                EodBalance(date=FRIDAY, debit_balance_usd=100000.0),
                EodBalance(date=NEXT_MONDAY, debit_balance_usd=100000.0),
            ],
            through_date=datetime.date(2026, 6, 9),
        )
        self.assertEqual([r.days_accrued for r in summary.daily_records], [3, 1])
        # 100000 * 0.05 / 360 * 4 = 55.5556
        self.assertAlmostEqual(summary.total_margin_interest_usd, 55.5556, places=3)

    def test_duplicate_dates_are_rejected(self):
        with self.assertRaises(FinancingDataError):
            self.tracker.accrue_daily_balances(
                [
                    EodBalance(date=MONDAY, debit_balance_usd=100000.0),
                    EodBalance(date=MONDAY, debit_balance_usd=100000.0),
                ],
                through_date=datetime.date(2026, 6, 3),
            )

    def test_unordered_dates_are_rejected(self):
        with self.assertRaises(FinancingDataError):
            self.tracker.accrue_daily_balances(
                [
                    EodBalance(date=datetime.date(2026, 6, 2), debit_balance_usd=100000.0),
                    EodBalance(date=MONDAY, debit_balance_usd=100000.0),
                ],
                through_date=datetime.date(2026, 6, 3),
            )

    def test_through_date_must_follow_the_last_observation(self):
        with self.assertRaises(FinancingDataError):
            self.tracker.accrue_daily_balances(
                [EodBalance(date=MONDAY, debit_balance_usd=100000.0)],
                through_date=MONDAY,
            )

    def test_datetime_instead_of_date_is_rejected(self):
        with self.assertRaises(FinancingDataError):
            self.tracker.accrue_daily_balances(
                [EodBalance(date=datetime.datetime(2026, 6, 1, 17, 0), debit_balance_usd=100000.0)],
                through_date=datetime.date(2026, 6, 3),
            )


if __name__ == "__main__":
    unittest.main()
