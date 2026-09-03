import unittest
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from auction_only_order_types_for_illiquid_names import (
    CANCEL_MODIFY_FREEZE_BEFORE_CLOSE,
    CLOSING_AUCTION_CUTOFF_ET,
    DEFAULT_TICK_SIZE,
    EARLY_CLOSE_SESSION_CLOSE_ET,
    NASDAQ_LOC_ENTRY_CUTOFF_ET,
    NASDAQ_MOC_ENTRY_CUTOFF_ET,
    REGULAR_SESSION_CLOSE_ET,
    SUB_DOLLAR_TICK_SIZE,
    AuctionVenue,
    IlliquidAuctionExecutionEngine,
    IlliquidExecutionConfig,
    OrderType,
    cancel_modify_freeze_for,
    entry_cutoff_for,
    is_past_closing_auction_cutoff,
    to_eastern,
    validate_submission_window,
)


# Fixed offsets for deterministic testing. All test dates are chosen to fall
# inside US Eastern Standard Time (UTC-5), so a fixed-offset input maps to a
# known ET wall clock without depending on the input zone's own DST rules.
_ET = timezone(timedelta(hours=-5))   # US Eastern, winter
_PT = timezone(timedelta(hours=-8))   # US Pacific, winter
_UTC = timezone.utc


class TestIlliquidAuctionExecutionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = IlliquidAuctionExecutionEngine()

    def test_severe_illiquidity_100pct_loc(self):
        # Order is 10% of ADV (> 5% threshold)
        plan = self.engine.generate_routing_plan("MICROCAP", total_qty=10000, average_daily_volume=100000)

        self.assertEqual(plan.continuous_qty, 0)
        self.assertEqual(plan.auction_qty, 10000)
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)
        self.assertIn("Severe", plan.reason)

    def test_moderate_illiquidity_hybrid(self):
        # Order is 2.5% of ADV (between 1% and 5%)
        plan = self.engine.generate_routing_plan("MIDCAP", total_qty=25000, average_daily_volume=1000000)

        self.assertEqual(plan.continuous_qty, 12500)  # 50%
        self.assertEqual(plan.auction_qty, 12500)  # 50%
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)
        self.assertIn("Moderate", plan.reason)

    def test_liquid_100pct_continuous(self):
        # Order is 0.1% of ADV (< 1% threshold)
        plan = self.engine.generate_routing_plan("MEGA", total_qty=10000, average_daily_volume=10000000)

        self.assertEqual(plan.continuous_qty, 10000)
        self.assertEqual(plan.auction_qty, 0)
        # No auction order placed for liquid names; type reflects continuous strategy.
        self.assertEqual(plan.auction_order_type, OrderType.CONTINUOUS_VWAP)
        self.assertIsNone(plan.suggested_limit_price)
        self.assertIn("Liquid", plan.reason)

    def test_zero_adv_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan("ERR", total_qty=1000, average_daily_volume=0)

    def test_negative_adv_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan("ERR", total_qty=1000, average_daily_volume=-5)

    def test_non_positive_qty_raises_error(self):
        for bad_qty in (0, -1):
            with self.subTest(qty=bad_qty):
                with self.assertRaises(ValueError):
                    self.engine.generate_routing_plan("ERR", total_qty=bad_qty, average_daily_volume=100000)

    def test_empty_symbol_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan("", total_qty=1000, average_daily_volume=100000)

    def test_hybrid_split_sums_to_total(self):
        # Odd quantity must still conserve shares across continuous + auction.
        total = 25001
        plan = self.engine.generate_routing_plan("ODD", total_qty=total, average_daily_volume=1000000)
        self.assertEqual(plan.continuous_qty + plan.auction_qty, total)

    def test_severe_threshold_boundary(self):
        # Exactly at the 5% severe boundary -> severe (>=).
        plan = self.engine.generate_routing_plan("EDGE", total_qty=5000, average_daily_volume=100000)
        self.assertEqual(plan.auction_qty, 5000)
        self.assertEqual(plan.continuous_qty, 0)
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)

    def test_moderate_threshold_boundary(self):
        # Exactly at the 1% moderate boundary -> hybrid (>=).
        plan = self.engine.generate_routing_plan("EDGE", total_qty=1000, average_daily_volume=100000)
        self.assertGreater(plan.auction_qty, 0)
        self.assertGreater(plan.continuous_qty, 0)
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)

    # --- Quantity / ADV input validation ---

    def test_fractional_total_qty_rejected(self):
        # A fractional parent order cannot be split into whole-share children.
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan("ERR", total_qty=1000.5, average_daily_volume=100000)

    def test_bool_total_qty_rejected(self):
        # bool is an int subclass; True would otherwise route a 1-share order.
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan("ERR", total_qty=True, average_daily_volume=100000)

    def test_fractional_adv_accepted(self):
        # An average of daily volumes is generally fractional and must be allowed.
        plan = self.engine.generate_routing_plan("FRAC", total_qty=10000, average_daily_volume=99999.5)
        self.assertEqual(plan.auction_qty, 10000)

    def test_non_finite_adv_rejected(self):
        # inf ADV silently produced a 0% participation rate and routed
        # everything to continuous trading before this guard existed.
        for bad in (float("inf"), float("nan"), float("-inf")):
            with self.subTest(adv=bad):
                with self.assertRaises(ValueError):
                    self.engine.generate_routing_plan("ERR", total_qty=10000, average_daily_volume=bad)

    def test_non_string_side_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan(
                "ERR", total_qty=10000, average_daily_volume=100000, side=None,
            )

    # --- LOC limit-price derivation (LOC requires a limit price) ---

    def test_buy_limit_price_from_reference(self):
        # 50 bps default tolerance on a $20.00 reference -> 20.00 * 1.005 = 20.10
        plan = self.engine.generate_routing_plan(
            "MICROCAP", total_qty=10000, average_daily_volume=100000,
            reference_price=20.00, side="BUY",
        )
        self.assertIsNotNone(plan.suggested_limit_price)
        self.assertAlmostEqual(plan.suggested_limit_price, 20.10, places=4)

    def test_sell_limit_price_from_reference(self):
        # Sell: 20.00 * (1 - 0.005) = 19.90
        plan = self.engine.generate_routing_plan(
            "MICROCAP", total_qty=10000, average_daily_volume=100000,
            reference_price=20.00, side="SELL",
        )
        self.assertAlmostEqual(plan.suggested_limit_price, 19.90, places=4)

    def test_explicit_slippage_tolerance(self):
        # 100 bps on $100.00 buy -> 101.00
        plan = self.engine.generate_routing_plan(
            "MICROCAP", total_qty=10000, average_daily_volume=100000,
            reference_price=100.00, slippage_tolerance_bps=100.0, side="BUY",
        )
        self.assertAlmostEqual(plan.suggested_limit_price, 101.00, places=4)

    def test_no_reference_price_yields_none_limit(self):
        plan = self.engine.generate_routing_plan(
            "MICROCAP", total_qty=10000, average_daily_volume=100000,
        )
        # LOC order type but no suggested price; caller must set it before submit.
        self.assertIsNone(plan.suggested_limit_price)

    def test_invalid_reference_price_raises(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan(
                "MICROCAP", total_qty=10000, average_daily_volume=100000,
                reference_price=0.0,
            )

    def test_non_finite_reference_price_raises(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(ref=bad):
                with self.assertRaises(ValueError):
                    self.engine.generate_routing_plan(
                        "MICROCAP", total_qty=10000, average_daily_volume=100000,
                        reference_price=bad,
                    )

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan(
                "MICROCAP", total_qty=10000, average_daily_volume=100000,
                reference_price=20.0, side="HOLD",
            )

    # --- Tick-size compliance (17 CFR 242.612) ---

    def _limit(self, **kwargs):
        return self.engine.generate_routing_plan(
            "MICROCAP", total_qty=10000, average_daily_volume=100000, **kwargs
        ).suggested_limit_price

    def test_buy_limit_is_a_whole_penny_and_never_exceeds_tolerance(self):
        # 20.01 * 1.005 = 20.11005. A limit of 20.1101 is a sub-penny price on a
        # stock above $1.00 and is not a permissible minimum increment.
        limit = self._limit(reference_price=20.01, side="BUY")
        self.assertEqual(limit, 20.11)
        self.assertLessEqual(Decimal(str(limit)), Decimal("20.01") * Decimal("1.005"))

    def test_sell_limit_is_a_whole_penny_and_never_undercuts_tolerance(self):
        # 33.33 * 0.995 = 33.16335 -> rounds UP to 33.17 so the caller never
        # accepts less than the tolerance permits.
        limit = self._limit(reference_price=33.33, side="SELL")
        self.assertEqual(limit, 33.17)
        self.assertGreaterEqual(Decimal(str(limit)), Decimal("33.33") * Decimal("0.995"))

    def test_limit_price_is_an_exact_multiple_of_the_tick(self):
        for ref in (7.77, 12.345, 101.01, 998.99):
            for side in ("BUY", "SELL"):
                with self.subTest(ref=ref, side=side):
                    limit = self._limit(reference_price=ref, side=side)
                    remainder = Decimal(str(limit)) % Decimal(str(DEFAULT_TICK_SIZE))
                    self.assertEqual(remainder, Decimal("0"))

    def test_sub_dollar_tick_size(self):
        # Below $1.00 the Rule 612 increment is $0.0001.
        limit = self._limit(
            reference_price=0.5123, side="BUY", tick_size=SUB_DOLLAR_TICK_SIZE,
        )
        self.assertEqual(limit, 0.5148)  # 0.5123 * 1.005 = 0.51486... -> floor

    def test_decimal_arithmetic_avoids_binary_float_undershoot(self):
        # 20.00 * 1.005 is 20.099999999999998 in binary floating point; a naive
        # floor to the penny would yield 20.09 rather than 20.10.
        self.assertEqual(self._limit(reference_price=20.00, side="BUY"), 20.10)

    def test_invalid_tick_size_raises(self):
        for bad in (0.0, -0.01, float("nan"), float("inf")):
            with self.subTest(tick=bad):
                with self.assertRaises(ValueError):
                    self._limit(reference_price=20.0, tick_size=bad)

    def test_sell_tolerance_that_wipes_out_the_price_raises(self):
        # A 10,000 bps sell tolerance implies a limit of $0.00; there is no
        # submittable price that respects it.
        with self.assertRaises(ValueError):
            self._limit(reference_price=20.0, side="SELL", slippage_tolerance_bps=10000.0)

    def test_negative_and_non_finite_tolerance_raises(self):
        for bad in (-1.0, float("nan"), float("inf")):
            with self.subTest(tol=bad):
                with self.assertRaises(ValueError):
                    self._limit(reference_price=20.0, slippage_tolerance_bps=bad)

    def test_absurd_reference_price_does_not_yield_an_infinite_limit(self):
        # Decimal has no float range limit; float(Decimal(...)) can return inf.
        with self.assertRaises(ValueError):
            self._limit(reference_price=1.79e308, side="BUY")

    # --- Config validation ---

    def test_invalid_config_threshold_order_raises(self):
        bad = IlliquidExecutionConfig(
            severe_illiquidity_threshold_pct=0.01,
            moderate_illiquidity_threshold_pct=0.05,
        )
        with self.assertRaises(ValueError):
            IlliquidAuctionExecutionEngine(bad)

    def test_invalid_config_allocation_raises(self):
        bad = IlliquidExecutionConfig(hybrid_auction_allocation_pct=1.5)
        with self.assertRaises(ValueError):
            IlliquidAuctionExecutionEngine(bad)

    def test_default_config_is_not_shared_between_engines(self):
        # A mutable dataclass default argument is built once at import time and
        # would leak this mutation into every later default-constructed engine.
        first = IlliquidAuctionExecutionEngine()
        first.config.hybrid_auction_allocation_pct = 0.9
        second = IlliquidAuctionExecutionEngine()
        self.assertEqual(second.config.hybrid_auction_allocation_pct, 0.5)

    def test_zero_hybrid_allocation_places_no_auction_order(self):
        # A 0% auction allocation is legal config; the plan must not then claim
        # an LOC order type or carry an LOC limit price for a zero-share leg.
        engine = IlliquidAuctionExecutionEngine(
            IlliquidExecutionConfig(hybrid_auction_allocation_pct=0.0)
        )
        plan = engine.generate_routing_plan(
            "MID", total_qty=2000, average_daily_volume=100000, reference_price=20.0,
        )
        self.assertEqual(plan.auction_qty, 0)
        self.assertEqual(plan.continuous_qty, 2000)
        self.assertEqual(plan.auction_order_type, OrderType.CONTINUOUS_VWAP)
        self.assertIsNone(plan.suggested_limit_price)


class TestClosingAuctionCutoffs(unittest.TestCase):
    # --- Cutoff enforcement ---

    def test_cutoff_before_deadline_ok(self):
        ts = datetime(2024, 3, 1, 15, 49, 59, tzinfo=_ET)
        self.assertFalse(is_past_closing_auction_cutoff(ts))
        validate_submission_window(ts)  # must not raise

    def test_cutoff_at_deadline_rejected(self):
        ts = datetime(2024, 3, 1, 15, 50, 0, tzinfo=_ET)
        self.assertTrue(is_past_closing_auction_cutoff(ts))
        with self.assertRaises(ValueError):
            validate_submission_window(ts)

    def test_cutoff_after_deadline_rejected(self):
        ts = datetime(2024, 3, 1, 15, 55, 0, tzinfo=_ET)
        self.assertTrue(is_past_closing_auction_cutoff(ts))

    def test_cutoff_naive_datetime_raises(self):
        ts = datetime(2024, 3, 1, 15, 49, 59)
        with self.assertRaises(ValueError):
            is_past_closing_auction_cutoff(ts)

    def test_default_cutoff_value(self):
        self.assertEqual(CLOSING_AUCTION_CUTOFF_ET, time(15, 50))

    # --- Timezone conversion, not wall-clock comparison ---

    def test_non_eastern_zone_is_converted_before_comparison(self):
        # 12:55 US/Pacific IS 15:55 ET, five minutes past the cutoff. Comparing
        # the raw wall clock (12:55) against 15:50 would wave this through and
        # the order would be rejected by the venue -- or worse, accepted late.
        ts = datetime(2024, 3, 1, 12, 55, tzinfo=_PT)
        self.assertEqual(to_eastern(ts).hour, 15)
        self.assertTrue(is_past_closing_auction_cutoff(ts))
        with self.assertRaises(ValueError):
            validate_submission_window(ts)

    def test_utc_timestamp_before_cutoff_is_not_falsely_rejected(self):
        # 20:45 UTC is 15:45 ET, comfortably inside the window. A raw wall-clock
        # comparison would read 20:45 >= 15:50 and reject a legal submission.
        ts = datetime(2024, 3, 1, 20, 45, tzinfo=_UTC)
        self.assertEqual(to_eastern(ts).strftime("%H:%M"), "15:45")
        self.assertFalse(is_past_closing_auction_cutoff(ts))

    def test_to_eastern_rejects_naive(self):
        with self.assertRaises(ValueError):
            to_eastern(datetime(2024, 3, 1, 15, 0))

    def test_eastern_conversion_respects_daylight_saving(self):
        # 19:45 UTC in July is 15:45 EDT (UTC-4), inside the window; the same
        # UTC clock in March is 14:45 EST and also inside it.
        summer = datetime(2024, 7, 1, 19, 45, tzinfo=_UTC)
        self.assertEqual(to_eastern(summer).strftime("%H:%M"), "15:45")
        self.assertFalse(is_past_closing_auction_cutoff(summer))
        summer_late = datetime(2024, 7, 1, 19, 55, tzinfo=_UTC)
        self.assertEqual(to_eastern(summer_late).strftime("%H:%M"), "15:55")
        self.assertTrue(is_past_closing_auction_cutoff(summer_late))

    # --- Early-close (half) days ---

    def test_early_close_day_moves_the_cutoff(self):
        # NYSE deadlines are ten minutes before the *scheduled* end of Core
        # Trading Hours (Rule 7.35(a)(8)), so a 1:00 p.m. close means 12:50 p.m.
        ts = datetime(2026, 11, 27, 12, 55, tzinfo=_ET)
        self.assertFalse(is_past_closing_auction_cutoff(ts))  # vs a 16:00 close
        self.assertTrue(
            is_past_closing_auction_cutoff(
                ts, market_close_et=EARLY_CLOSE_SESSION_CLOSE_ET
            )
        )
        with self.assertRaises(ValueError):
            validate_submission_window(ts, market_close_et=EARLY_CLOSE_SESSION_CLOSE_ET)

    def test_early_close_day_before_cutoff_is_allowed(self):
        ts = datetime(2026, 11, 27, 12, 49, 59, tzinfo=_ET)
        self.assertFalse(
            is_past_closing_auction_cutoff(
                ts, market_close_et=EARLY_CLOSE_SESSION_CLOSE_ET
            )
        )
        validate_submission_window(ts, market_close_et=EARLY_CLOSE_SESSION_CLOSE_ET)

    def test_explicit_cutoff_overrides_the_derived_one(self):
        # Back-compatible positional form: an explicit cutoff still wins.
        ts = datetime(2024, 3, 1, 15, 56, tzinfo=_ET)
        self.assertTrue(is_past_closing_auction_cutoff(ts))
        self.assertFalse(is_past_closing_auction_cutoff(ts, NASDAQ_LOC_ENTRY_CUTOFF_ET))

    # --- Venue-specific entry cutoffs ---

    def test_venue_entry_cutoffs_on_a_regular_session(self):
        # NYSE Rule 7.35B: 3:50 for both. Nasdaq Equity 4 Rule 4702(b)(11):
        # MOC rejected at/after 3:55; 4702(b)(12): LOC rejected at/after 3:58.
        expected = {
            (AuctionVenue.NYSE, OrderType.MARKET_ON_CLOSE): time(15, 50),
            (AuctionVenue.NYSE, OrderType.LIMIT_ON_CLOSE): time(15, 50),
            (AuctionVenue.NASDAQ, OrderType.MARKET_ON_CLOSE): time(15, 55),
            (AuctionVenue.NASDAQ, OrderType.LIMIT_ON_CLOSE): time(15, 58),
        }
        for (venue, order_type), cutoff in expected.items():
            with self.subTest(venue=venue, order_type=order_type):
                self.assertEqual(entry_cutoff_for(venue, order_type), cutoff)

    def test_nasdaq_moc_cutoff_is_earlier_than_its_loc_cutoff(self):
        # MOC and LOC do not share an entry deadline on Nasdaq.
        self.assertLess(
            entry_cutoff_for(AuctionVenue.NASDAQ, OrderType.MARKET_ON_CLOSE),
            entry_cutoff_for(AuctionVenue.NASDAQ, OrderType.LIMIT_ON_CLOSE),
        )
        self.assertEqual(NASDAQ_MOC_ENTRY_CUTOFF_ET, time(15, 55))
        self.assertEqual(NASDAQ_LOC_ENTRY_CUTOFF_ET, time(15, 58))

    def test_venue_entry_cutoffs_on_an_early_close(self):
        self.assertEqual(
            entry_cutoff_for(
                AuctionVenue.NYSE, OrderType.LIMIT_ON_CLOSE,
                EARLY_CLOSE_SESSION_CLOSE_ET,
            ),
            time(12, 50),
        )
        self.assertEqual(
            entry_cutoff_for(
                AuctionVenue.NASDAQ, OrderType.LIMIT_ON_CLOSE,
                EARLY_CLOSE_SESSION_CLOSE_ET,
            ),
            time(12, 58),
        )

    def test_entry_cutoff_rejects_a_non_on_close_order_type(self):
        with self.assertRaises(ValueError):
            entry_cutoff_for(AuctionVenue.NYSE, OrderType.CONTINUOUS_VWAP)

    def test_cancel_modify_freeze(self):
        self.assertEqual(cancel_modify_freeze_for(), time(15, 50))
        self.assertEqual(
            cancel_modify_freeze_for(EARLY_CLOSE_SESSION_CLOSE_ET), time(12, 50)
        )

    def test_implausible_market_close_is_rejected_not_wrapped(self):
        # A midnight close would wrap the cutoff to 23:50, which every intraday
        # timestamp clears -- a silently permissive gate.
        for bad_close in (time(0, 0), time(0, 5)):
            with self.subTest(close=bad_close):
                with self.assertRaises(ValueError):
                    cancel_modify_freeze_for(bad_close)
                with self.assertRaises(ValueError):
                    entry_cutoff_for(
                        AuctionVenue.NYSE, OrderType.LIMIT_ON_CLOSE, bad_close
                    )

    def test_default_cutoff_matches_the_documented_constant(self):
        # The conservative default must stay in step with the freeze offset.
        self.assertEqual(
            cancel_modify_freeze_for(REGULAR_SESSION_CLOSE_ET),
            CLOSING_AUCTION_CUTOFF_ET,
        )
        self.assertEqual(CANCEL_MODIFY_FREEZE_BEFORE_CLOSE, timedelta(minutes=10))


if __name__ == "__main__":
    unittest.main()
