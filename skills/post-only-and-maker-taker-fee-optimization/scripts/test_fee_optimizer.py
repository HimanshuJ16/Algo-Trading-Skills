"""
Unit tests for the post-only / maker-taker fee optimizer.

Expected values are derived by hand from the inputs (never by re-running the
module's own expression), so a formula change fails the test rather than moving
the target with it.
"""
import unittest
from dataclasses import FrozenInstanceError

from fee_optimizer import (
    CrossingPolicy,
    FeeSchedule,
    MakerTakerFeeOptimizer,
    OrderSide,
    PostOnlyOrderError,
    PostOnlyStatus,
    TopOfBook,
    Venue,
)

# Maker 0.05% (5 bps), taker 0.25% (25 bps): a schedule where posting is cheaper.
MAKER_CHEAPER = FeeSchedule(maker_fee_rate=0.0005, taker_fee_rate=0.0025)
BOOK = TopOfBook(best_bid=60000.0, best_ask=60010.0)


def optimizer(venue=Venue.BINANCE_SPOT, schedule=MAKER_CHEAPER):
    return MakerTakerFeeOptimizer(venue, schedule)


class TestFeeSchedule(unittest.TestCase):
    def test_differential_rate_is_signed_and_unclamped(self):
        # Inverted schedule: taker 5 bps, maker 25 bps -> posting costs 20 bps more.
        inverted = FeeSchedule(maker_fee_rate=0.0025, taker_fee_rate=0.0005)
        self.assertAlmostEqual(inverted.differential_rate, -0.0020, places=10)
        self.assertAlmostEqual(MAKER_CHEAPER.differential_rate, 0.0020, places=10)

    def test_maker_rebate_is_accepted_as_a_negative_rate(self):
        rebate = FeeSchedule(maker_fee_rate=-0.0001, taker_fee_rate=0.0025)
        # taker - maker = 0.0025 - (-0.0001) = 0.0026
        self.assertAlmostEqual(rebate.differential_rate, 0.0026, places=10)

    def test_non_finite_rate_is_rejected(self):
        for bad in (float("nan"), float("inf"), "0.001", None):
            with self.assertRaises(PostOnlyOrderError):
                FeeSchedule(maker_fee_rate=bad, taker_fee_rate=0.0025)

    def test_schedule_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            MAKER_CHEAPER.maker_fee_rate = 0.9


class TestTopOfBook(unittest.TestCase):
    def test_locked_and_crossed_books_are_detected(self):
        self.assertFalse(BOOK.is_locked_or_crossed)
        self.assertTrue(TopOfBook(best_bid=100.0, best_ask=100.0).is_locked_or_crossed)
        self.assertTrue(TopOfBook(best_bid=101.0, best_ask=100.0).is_locked_or_crossed)

    def test_non_positive_or_non_finite_quotes_are_rejected(self):
        for bid, ask in ((0.0, 100.0), (-1.0, 100.0), (100.0, float("nan"))):
            with self.assertRaises(PostOnlyOrderError):
                TopOfBook(best_bid=bid, best_ask=ask)

    def test_tick_grid_membership(self):
        book = TopOfBook(best_bid=100.00, best_ask=100.05, tick_size=0.05)
        self.assertTrue(book.is_on_tick(100.10))
        self.assertFalse(book.is_on_tick(100.03))
        # Unknown tick size never rejects.
        self.assertTrue(BOOK.is_on_tick(60000.037))


class TestCrossingDetection(unittest.TestCase):
    def test_passive_price_is_submitted_unchanged(self):
        res = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 1.0, 60000.0, BOOK
        )
        self.assertIs(res.status, PostOnlyStatus.READY)
        self.assertTrue(res.is_accepted)
        self.assertFalse(res.repriced)
        self.assertEqual(res.submitted_limit_price, 60000.0)

    def test_price_inside_the_spread_is_not_marketable(self):
        # 60005 is above the bid but below the ask: it rests, it does not trade.
        res = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 1.0, 60005.0, BOOK
        )
        self.assertFalse(res.repriced)
        self.assertEqual(res.submitted_limit_price, 60005.0)

    def test_buy_at_exactly_the_ask_is_marketable(self):
        # Inclusive bound: a buy limit equal to the ask trades against it.
        res = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 1.0, 60010.0, BOOK
        )
        self.assertIs(res.status, PostOnlyStatus.REPRICED)
        self.assertEqual(res.submitted_limit_price, 60000.0)

    def test_sell_at_exactly_the_bid_is_marketable(self):
        res = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.SELL, 1.0, 60000.0, BOOK
        )
        self.assertIs(res.status, PostOnlyStatus.REPRICED)
        self.assertEqual(res.submitted_limit_price, 60010.0)

    def test_sell_above_the_bid_is_passive(self):
        res = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.SELL, 1.0, 60020.0, BOOK
        )
        self.assertIs(res.status, PostOnlyStatus.READY)
        self.assertEqual(res.submitted_limit_price, 60020.0)

    def test_crossing_price_is_rejected_under_reject_policy(self):
        res = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 1.0, 60020.0, BOOK,
            crossing_policy=CrossingPolicy.REJECT,
        )
        self.assertIs(res.status, PostOnlyStatus.REJECTED_WOULD_CROSS)
        self.assertFalse(res.is_accepted)
        self.assertEqual(res.order_payload, {})
        self.assertIsNone(res.submitted_limit_price)
        # A rejected order reports no fee benefit, because none was earned.
        self.assertEqual(res.estimated_fee_differential_if_filled_usd, 0.0)

    def test_locked_book_is_rejected_rather_than_repriced_into_a_cross(self):
        # Regression: repricing a buy to the bid on a locked book leaves the
        # price at the ask, so the venue cancels it instead of resting it.
        locked = TopOfBook(best_bid=60010.0, best_ask=60010.0)
        res = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 1.0, 60020.0, locked
        )
        self.assertIs(res.status, PostOnlyStatus.REJECTED_LOCKED_OR_CROSSED_BOOK)
        self.assertEqual(res.order_payload, {})
        self.assertTrue(any("locked or crossed" in w for w in res.warnings))

    def test_crossed_book_is_rejected_for_both_sides(self):
        crossed = TopOfBook(best_bid=60020.0, best_ask=60010.0)
        for side in (OrderSide.BUY, OrderSide.SELL):
            res = optimizer().prepare_post_only_payload(
                "BTCUSDT", side, 1.0, 60015.0, crossed
            )
            self.assertIs(
                res.status, PostOnlyStatus.REJECTED_LOCKED_OR_CROSSED_BOOK, side
            )


class TestFeeArithmetic(unittest.TestCase):
    def test_fee_differential_prices_the_taker_leg_at_the_touch_it_would_cross(self):
        # 2 BTC posted at the bid 60,000 -> maker fee 2 * 60000 * 0.0005 = $60.00
        # Crossing would have paid the ask 60,010 -> 2 * 60010 * 0.0025 = $300.05
        # Differential = 300.05 - 60.00 = $240.05
        res = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 2.0, 60000.0, BOOK
        )
        self.assertAlmostEqual(res.estimated_maker_fee_if_filled_usd, 60.00, places=2)
        self.assertAlmostEqual(res.counterfactual_taker_fee_usd, 300.05, places=2)
        self.assertAlmostEqual(
            res.estimated_fee_differential_if_filled_usd, 240.05, places=2
        )

    def test_spread_capture_is_reported_only_when_repriced(self):
        repriced = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 3.0, 60020.0, BOOK
        )
        # 3 * (60010 - 60000) = $30.00, conditional on the repriced order filling.
        self.assertAlmostEqual(repriced.spread_capture_if_filled_usd, 30.00, places=2)
        passive = optimizer().prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 3.0, 60000.0, BOOK
        )
        self.assertEqual(passive.spread_capture_if_filled_usd, 0.0)

    def test_differential_is_negative_on_an_inverted_schedule(self):
        # Regression: the old implementation clamped savings at zero, hiding the
        # case where post-only is the more expensive side.
        inverted = FeeSchedule(maker_fee_rate=0.0025, taker_fee_rate=0.0005)
        res = optimizer(schedule=inverted).prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 2.0, 60000.0, BOOK
        )
        # taker 2 * 60010 * 0.0005 = 60.01 ; maker 2 * 60000 * 0.0025 = 300.00
        self.assertAlmostEqual(res.counterfactual_taker_fee_usd, 60.01, places=2)
        self.assertAlmostEqual(res.estimated_maker_fee_if_filled_usd, 300.00, places=2)
        self.assertLess(res.estimated_fee_differential_if_filled_usd, 0.0)
        self.assertTrue(any("MORE expensive" in w for w in res.warnings))

    def test_equal_rates_warn_and_produce_a_near_zero_differential(self):
        # Binance spot Regular tier: maker == taker == 0.100%.
        flat = FeeSchedule(maker_fee_rate=0.0010, taker_fee_rate=0.0010)
        res = optimizer(schedule=flat).prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 1.0, 60000.0, BOOK
        )
        self.assertTrue(any("changes the fee bill by zero" in w for w in res.warnings))
        # Only the 10-dollar spread between bid and ask separates the two legs.
        self.assertAlmostEqual(
            res.estimated_fee_differential_if_filled_usd, 0.01, places=2
        )

    def test_preparing_payloads_never_accrues_realized_savings(self):
        # Regression: the old implementation booked savings at submission time,
        # so 100 orders that never filled reported $12,000 of savings.
        opt = optimizer()
        for _ in range(100):
            opt.prepare_post_only_payload("BTCUSDT", OrderSide.BUY, 1.0, 60000.0, BOOK)
        self.assertEqual(opt.realized_fee_differential_usd, 0.0)
        self.assertEqual(opt.recorded_fill_count, 0)

    def test_realized_differential_accrues_only_from_recorded_fills(self):
        opt = optimizer()
        # 0.5 BTC filled at 60,000 against a decision-time ask of 60,010:
        # taker 0.5 * 60010 * 0.0025 = 75.0125 ; maker 0.5 * 60000 * 0.0005 = 15.00
        first = opt.record_maker_fill(0.5, 60000.0, taker_reference_price=60010.0)
        self.assertAlmostEqual(first, 60.0125, places=4)
        # Default reference price is the fill price: 0.5 * 60000 * (0.0025-0.0005) = 60.00
        second = opt.record_maker_fill(0.5, 60000.0)
        self.assertAlmostEqual(second, 60.0, places=4)
        self.assertAlmostEqual(opt.realized_fee_differential_usd, 120.0125, places=4)
        self.assertEqual(opt.recorded_fill_count, 2)

    def test_duplicate_fill_id_is_rejected(self):
        # Overlapping paginated fill fetches are the ordinary way one fill
        # arrives twice; double-counting inflates the realized total silently.
        opt = optimizer()
        opt.record_maker_fill(1.0, 60000.0, fill_id="exec-1")
        with self.assertRaises(PostOnlyOrderError):
            opt.record_maker_fill(1.0, 60000.0, fill_id="exec-1")
        self.assertEqual(opt.recorded_fill_count, 1)
        # A distinct id accrues normally; an empty id is rejected outright.
        opt.record_maker_fill(1.0, 60000.0, fill_id="exec-2")
        self.assertEqual(opt.recorded_fill_count, 2)
        with self.assertRaises(PostOnlyOrderError):
            opt.record_maker_fill(1.0, 60000.0, fill_id="  ")

    def test_rejected_fill_does_not_accrue(self):
        opt = optimizer()
        with self.assertRaises(PostOnlyOrderError):
            opt.record_maker_fill(-1.0, 60000.0, fill_id="exec-9")
        self.assertEqual(opt.realized_fee_differential_usd, 0.0)
        self.assertEqual(opt.recorded_fill_count, 0)
        # The id of a rejected fill is not consumed.
        opt.record_maker_fill(1.0, 60000.0, fill_id="exec-9")
        self.assertEqual(opt.recorded_fill_count, 1)

    def test_record_maker_fill_rejects_unusable_quantities(self):
        opt = optimizer()
        for qty, price in ((0.0, 60000.0), (-1.0, 60000.0), (float("nan"), 60000.0)):
            with self.assertRaises(PostOnlyOrderError):
                opt.record_maker_fill(qty, price)
        with self.assertRaises(PostOnlyOrderError):
            opt.record_maker_fill(1.0, 0.0)


class TestVenuePayloads(unittest.TestCase):
    def test_binance_spot_uses_limit_maker_and_no_time_in_force(self):
        res = optimizer(Venue.BINANCE_SPOT).prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 1.0, 60000.0, BOOK
        )
        self.assertEqual(res.order_payload["type"], "LIMIT_MAKER")
        # Spot accepts only GTC/IOC/FOK; GTX is a futures value.
        self.assertNotIn("timeInForce", res.order_payload)

    def test_binance_futures_uses_gtx(self):
        res = optimizer(Venue.BINANCE_USDM_FUTURES).prepare_post_only_payload(
            "BTCUSDT", OrderSide.SELL, 1.0, 60020.0, BOOK
        )
        self.assertEqual(res.order_payload["type"], "LIMIT")
        self.assertEqual(res.order_payload["timeInForce"], "GTX")

    def test_bybit_uses_postonly_capitalised_side_and_string_numbers(self):
        res = optimizer(Venue.BYBIT_V5).prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 0.001, 60000.0, BOOK,
            venue_params={"category": "spot"},
        )
        payload = res.order_payload
        self.assertEqual(payload["timeInForce"], "PostOnly")
        self.assertEqual(payload["side"], "Buy")
        self.assertEqual(payload["orderType"], "Limit")
        self.assertEqual(payload["qty"], "0.001")
        self.assertEqual(payload["price"], "60000")
        self.assertEqual(payload["category"], "spot")

    def test_bybit_without_category_is_rejected(self):
        with self.assertRaises(PostOnlyOrderError):
            optimizer(Venue.BYBIT_V5).prepare_post_only_payload(
                "BTCUSDT", OrderSide.BUY, 0.001, 60000.0, BOOK
            )

    def test_coinbase_nests_post_only_inside_limit_limit_gtc(self):
        res = optimizer(Venue.COINBASE_ADVANCED).prepare_post_only_payload(
            "BTC-USD", OrderSide.BUY, 0.01, 60000.0, BOOK
        )
        config = res.order_payload["order_configuration"]["limit_limit_gtc"]
        self.assertIs(config["post_only"], True)
        self.assertEqual(config["base_size"], "0.01")
        self.assertEqual(config["limit_price"], "60000")
        self.assertEqual(res.order_payload["product_id"], "BTC-USD")

    def test_kraken_uses_post_oflag(self):
        res = optimizer(Venue.KRAKEN_SPOT).prepare_post_only_payload(
            "XBTUSD", OrderSide.SELL, 1.0, 60020.0, BOOK
        )
        self.assertEqual(res.order_payload["oflags"], "post")
        self.assertEqual(res.order_payload["ordertype"], "limit")
        self.assertEqual(res.order_payload["type"], "sell")

    def test_fix_uses_execinst_wire_value_6(self):
        res = optimizer(Venue.FIX_4_4).prepare_post_only_payload(
            "AAPL", OrderSide.SELL, 100.0, 190.0, TopOfBook(189.0, 189.5)
        )
        payload = res.order_payload
        # ExecInst is tag 18 and its wire value is "6", not a human-readable label.
        self.assertEqual(payload["18"], "6")
        self.assertEqual(payload["54"], "2")
        self.assertEqual(payload["40"], "2")

    def test_no_payload_carries_another_venues_post_only_spelling(self):
        # Regression: the old payload sent post_only, POC and execInst together.
        # An unknown field is commonly ignored, and an ignored post-only flag
        # submits a plain limit order that crosses at the taker rate.
        for venue in Venue:
            params = {"category": "spot"} if venue is Venue.BYBIT_V5 else None
            payload = optimizer(venue).prepare_post_only_payload(
                "BTCUSDT", OrderSide.BUY, 1.0, 60000.0, BOOK, venue_params=params
            ).order_payload
            self.assertNotIn("execInst", payload, venue)
            self.assertNotEqual(payload.get("time_in_force"), "POC", venue)
            self.assertNotEqual(payload.get("timeInForce"), "POC", venue)

    def test_venue_params_may_not_overwrite_the_post_only_instruction(self):
        with self.assertRaises(PostOnlyOrderError):
            optimizer(Venue.BINANCE_USDM_FUTURES).prepare_post_only_payload(
                "BTCUSDT", OrderSide.BUY, 1.0, 60000.0, BOOK,
                venue_params={"timeInForce": "GTC"},
            )

    def test_venue_params_may_not_overwrite_price_or_quantity(self):
        # The returned result reports the crossing-checked price; a payload that
        # disagrees with it would make the report describe a different order.
        for venue, params in (
            (Venue.BINANCE_SPOT, {"price": 60020.0}),
            (Venue.KRAKEN_SPOT, {"volume": "99"}),
            (Venue.FIX_4_4, {"44": "60020"}),
        ):
            with self.assertRaises(PostOnlyOrderError, msg=venue):
                optimizer(venue).prepare_post_only_payload(
                    "BTCUSDT", OrderSide.BUY, 1.0, 60000.0, BOOK, venue_params=params
                )

    def test_quantity_underflowing_string_serialisation_is_rejected(self):
        # 1e-12 is a positive quantity that would serialise to "0" at 10 dp.
        with self.assertRaises(PostOnlyOrderError):
            optimizer(Venue.KRAKEN_SPOT).prepare_post_only_payload(
                "XBTUSD", OrderSide.BUY, 1e-12, 60000.0, BOOK
            )

    def test_venue_params_add_benign_fields(self):
        res = optimizer(Venue.BINANCE_SPOT).prepare_post_only_payload(
            "BTCUSDT", OrderSide.BUY, 1.0, 60000.0, BOOK,
            venue_params={"newClientOrderId": "abc-123"},
        )
        self.assertEqual(res.order_payload["newClientOrderId"], "abc-123")
        self.assertEqual(res.order_payload["type"], "LIMIT_MAKER")


class TestInputValidation(unittest.TestCase):
    def test_free_text_side_is_rejected(self):
        # Regression: any side string other than exactly BUY/SELL used to skip
        # the crossing check and submit the marketable price unchanged.
        for bad_side in ("BUY", " BUY", "B", "BUY_TO_COVER", "sell_short", None, 1):
            with self.assertRaises(PostOnlyOrderError, msg=bad_side):
                optimizer().prepare_post_only_payload(
                    "BTCUSDT", bad_side, 1.0, 60020.0, BOOK
                )

    def test_non_positive_and_non_finite_quantities_are_rejected(self):
        for qty in (0.0, -5.0, float("nan"), float("inf"), "1.0", None, True):
            with self.assertRaises(PostOnlyOrderError, msg=qty):
                optimizer().prepare_post_only_payload(
                    "BTCUSDT", OrderSide.BUY, qty, 60000.0, BOOK
                )

    def test_non_positive_limit_price_is_rejected(self):
        for price in (0.0, -1.0, float("nan")):
            with self.assertRaises(PostOnlyOrderError):
                optimizer().prepare_post_only_payload(
                    "BTCUSDT", OrderSide.BUY, 1.0, price, BOOK
                )

    def test_empty_symbol_is_rejected(self):
        for symbol in ("", "   ", None, 5):
            with self.assertRaises(PostOnlyOrderError):
                optimizer().prepare_post_only_payload(
                    symbol, OrderSide.BUY, 1.0, 60000.0, BOOK
                )

    def test_raw_dict_in_place_of_a_book_is_rejected(self):
        with self.assertRaises(PostOnlyOrderError):
            optimizer().prepare_post_only_payload(
                "BTCUSDT", OrderSide.BUY, 1.0, 60000.0,
                {"best_bid": 60000.0, "best_ask": 60010.0},
            )

    def test_off_tick_limit_price_is_rejected(self):
        book = TopOfBook(best_bid=100.00, best_ask=100.05, tick_size=0.05)
        with self.assertRaises(PostOnlyOrderError):
            optimizer().prepare_post_only_payload(
                "AAPL", OrderSide.BUY, 1.0, 99.97, book
            )

    def test_engine_requires_a_venue_and_a_fee_schedule(self):
        with self.assertRaises(PostOnlyOrderError):
            MakerTakerFeeOptimizer("BINANCE_SPOT", MAKER_CHEAPER)
        with self.assertRaises(PostOnlyOrderError):
            MakerTakerFeeOptimizer(Venue.BINANCE_SPOT, {"maker": 0.0005})


if __name__ == "__main__":
    unittest.main()
