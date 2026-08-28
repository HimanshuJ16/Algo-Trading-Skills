import unittest
from datetime import datetime, timezone

from opening_auction_imbalance_based_execution import (
    US_EASTERN,
    AuctionImbalanceData,
    AuctionVenue,
    OnOpenOrderType,
    OpeningAuctionImbalanceBasedExecutionConfig,
    OpeningAuctionImbalanceBasedExecutionEngine,
    PriceBasis,
    imbalance_ratio,
    seconds_to_open_from,
)

SESSION = "2025-01-15"  # a Wednesday; US/Eastern is EST (UTC-5) on this date


def imb(**overrides) -> AuctionImbalanceData:
    """A Nasdaq opening-cross NOII with a 100k buy imbalance at 09:29 (60s to open).

    600,000 paired + 100,000 imbalance => ratio 1/7. Overridden per test.
    """
    defaults = dict(
        symbol="NVDA",
        paired_qty=300_000,
        imbalance_qty=100_000,
        imbalance_side="B",
        far_price=455.0,
        near_price=452.0,
        ref_price=450.0,
        seconds_to_open=60.0,
        cross_type="O",
        feed_age_seconds=1.0,
        session_date=SESSION,
    )
    defaults.update(overrides)
    return AuctionImbalanceData(**defaults)


def engine(**overrides) -> OpeningAuctionImbalanceBasedExecutionEngine:
    defaults = dict(
        venue=AuctionVenue.NASDAQ,
        order_type=OnOpenOrderType.OIO,
        price_basis=PriceBasis.FAR,
        size=5_000,
        session_date=None,
    )
    defaults.pop("session_date")
    defaults.update(overrides)
    return OpeningAuctionImbalanceBasedExecutionEngine(
        OpeningAuctionImbalanceBasedExecutionConfig(**defaults))


class TestImbalanceRatio(unittest.TestCase):

    def test_ratio_is_unpaired_share_of_total_interest(self):
        # 100,000 / (300,000 + 100,000) = 0.25 exactly.
        self.assertEqual(imbalance_ratio(300_000, 100_000), 0.25)

    def test_empty_book_returns_zero_not_zero_division(self):
        self.assertEqual(imbalance_ratio(0, 0), 0.0)

    def test_wholly_unpaired_book_is_one(self):
        self.assertEqual(imbalance_ratio(0, 50_000), 1.0)


class TestSecondsToOpen(unittest.TestCase):

    def test_eastern_0925_is_300_seconds_to_open(self):
        et_0925 = datetime(2025, 1, 15, 9, 25, tzinfo=US_EASTERN)
        self.assertEqual(seconds_to_open_from(et_0925), 300.0)

    def test_utc_input_is_converted_not_compared_raw(self):
        # 14:28Z is 09:28 EST => 120 seconds to open.
        utc_1428 = datetime(2025, 1, 15, 14, 28, tzinfo=timezone.utc)
        self.assertEqual(seconds_to_open_from(utc_1428), 120.0)

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            seconds_to_open_from(datetime(2025, 1, 15, 9, 28))


class TestOrderGeneration(unittest.TestCase):

    def test_buy_imbalance_produces_contra_side_sell_oio(self):
        report = engine().process_auction_imbalance(imb())
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertEqual(report.imbalance_ratio, 0.25)
        self.assertEqual(report.order_generated["side"], "SELL")
        self.assertEqual(report.order_generated["type"], "OIO")

    def test_sell_imbalance_produces_contra_side_buy(self):
        report = engine().process_auction_imbalance(imb(imbalance_side="S"))
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertEqual(report.order_generated["side"], "BUY")

    def test_quantity_is_smallest_cap_floored_to_lot(self):
        # participation 10% of 100,000 = 10,000; 5% of 400,000 total = 20,000;
        # size cap 5,000 -> binding cap is 5,000, already a whole lot.
        report = engine(size=5_000).process_auction_imbalance(imb())
        self.assertEqual(report.order_generated["qty"], 5_000)

        # Raise the size cap so participation binds: 10% of 100,000 = 10,000,
        # but 5% of total interest 400,000 = 20,000, so 10,000 wins.
        report = engine(size=1_000_000).process_auction_imbalance(imb())
        self.assertEqual(report.order_generated["qty"], 10_000)

        # Shrink the book so the auction-volume cap binds and needs lot rounding:
        # imbalance 12,000 (ratio 12,000/13,000 = 0.923), participation 10% = 1,200,
        # auction-volume 5% of 13,000 = 650 -> floors to 600 at a 100-share lot.
        report = engine(size=1_000_000).process_auction_imbalance(
            imb(paired_qty=1_000, imbalance_qty=12_000))
        self.assertEqual(report.order_generated["qty"], 600)

    def test_size_cap_is_never_exceeded_by_the_minimum_lot(self):
        # Regression: the previous implementation applied max(qty, 100) after the
        # caps, so a 50-share cap produced a 100-share order.
        report = engine(size=50).process_auction_imbalance(imb())
        self.assertEqual(report.status, "QUANTITY_BELOW_MINIMUM")
        self.assertIsNone(report.order_generated)

    def test_limit_price_derives_from_far_price_and_rounds_away_from_aggressive(self):
        # Sell at the far price with a 10bp passive offset: 455 * 1.001 = 455.455,
        # rounded up (away from aggressive for a sell) to 455.46.
        report = engine(price_offset_bps=10.0).process_auction_imbalance(imb())
        self.assertAlmostEqual(report.order_generated["limit_price"], 455.46, places=6)

        # Buy side: 455 * 0.999 = 454.545, rounded down to 454.54.
        report = engine(price_offset_bps=10.0).process_auction_imbalance(
            imb(imbalance_side="S"))
        self.assertAlmostEqual(report.order_generated["limit_price"], 454.54, places=6)

    def test_price_already_on_a_tick_is_not_moved_by_float_error(self):
        # Regression: 100.07 / 0.01 is 10006.999999999998 in binary floating
        # point, so a bare ceil() moved a sell limit that was already on a tick
        # up a full cent.
        for price in (100.07, 100.14, 100.29, 0.07, 12.58):
            sell = engine(price_basis=PriceBasis.REF).process_auction_imbalance(
                imb(ref_price=price))
            self.assertAlmostEqual(
                sell.order_generated["limit_price"], price, places=6,
                msg=f"sell limit at {price}")
            buy = engine(price_basis=PriceBasis.REF).process_auction_imbalance(
                imb(ref_price=price, imbalance_side="S"))
            self.assertAlmostEqual(
                buy.order_generated["limit_price"], price, places=6,
                msg=f"buy limit at {price}")

    def test_offset_price_between_ticks_rounds_away_from_aggressive(self):
        # 450 * 1.001 = 450.45 exactly on a tick for the sell; use a basis that
        # lands between ticks: 450.003 * 1.0 -> sell rounds up to 450.01.
        sell = engine(price_basis=PriceBasis.REF).process_auction_imbalance(
            imb(ref_price=450.003))
        self.assertAlmostEqual(sell.order_generated["limit_price"], 450.01, places=6)
        buy = engine(price_basis=PriceBasis.REF).process_auction_imbalance(
            imb(ref_price=450.003, imbalance_side="S"))
        self.assertAlmostEqual(buy.order_generated["limit_price"], 450.00, places=6)

    def test_near_price_basis_is_honoured(self):
        report = engine(price_basis=PriceBasis.NEAR).process_auction_imbalance(imb())
        self.assertAlmostEqual(report.order_generated["limit_price"], 452.0, places=6)


class TestVenueTiming(unittest.TestCase):

    def test_nasdaq_moo_entry_cutoff_is_0928(self):
        # MOO cutoff 120s; with the default 5s buffer an observation at 130s to
        # open still arrives at 125s and is accepted.
        eng = engine(order_type=OnOpenOrderType.MOO, allow_unpriced_moo=True)
        self.assertEqual(
            eng.process_auction_imbalance(
                imb(seconds_to_open=130.0, far_price=0.0, near_price=0.0)).status,
            "ORDER_GENERATED")

        # At 124s the projected arrival is 119s, inside the cutoff.
        eng = engine(order_type=OnOpenOrderType.MOO, allow_unpriced_moo=True)
        self.assertEqual(
            eng.process_auction_imbalance(
                imb(seconds_to_open=124.0, far_price=0.0, near_price=0.0)).status,
            "CUTOFF_EXCEEDED")

    def test_nasdaq_oio_is_accepted_after_the_moo_cutoff(self):
        # An OIO may be entered until the cross, so 60s to open is legal where a
        # MOO would already be rejected.
        report = engine(order_type=OnOpenOrderType.OIO).process_auction_imbalance(
            imb(seconds_to_open=60.0))
        self.assertEqual(report.status, "ORDER_GENERATED")

    def test_nasdaq_loo_entry_cutoff_is_092930(self):
        eng = engine(order_type=OnOpenOrderType.LOO)
        self.assertEqual(
            eng.process_auction_imbalance(imb(seconds_to_open=40.0)).status,
            "ORDER_GENERATED")

        eng = engine(order_type=OnOpenOrderType.LOO)
        self.assertEqual(
            eng.process_auction_imbalance(imb(seconds_to_open=34.0)).status,
            "CUTOFF_EXCEEDED")

    def test_late_loo_between_0928_and_092930_is_flagged_for_reprice_risk(self):
        # Arrival at 115s (120s observed less the 5s buffer) is inside the
        # 09:28-09:29:30 late-LOO window.
        report = engine(order_type=OnOpenOrderType.LOO).process_auction_imbalance(
            imb(seconds_to_open=120.0))
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertTrue(report.late_loo_reprice_risk)

        # An LOO arriving before 09:28 is not subject to the late-LOO reprice.
        report = engine(order_type=OnOpenOrderType.LOO,
                        price_basis=PriceBasis.REF).process_auction_imbalance(
            imb(seconds_to_open=126.0, far_price=0.0, near_price=0.0))
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertFalse(report.late_loo_reprice_risk)

    def test_order_placed_before_0925_is_reported_as_not_cancellable(self):
        # Nasdaq freezes cancel/modify of on-open orders at 09:25 (300s), so an
        # order entered at 09:25:10 can no longer be pulled. Priced off the
        # Current Reference Price because no far price exists this early.
        report = engine(order_type=OnOpenOrderType.LOO,
                        price_basis=PriceBasis.REF).process_auction_imbalance(
            imb(seconds_to_open=290.0, far_price=0.0, near_price=0.0))
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertFalse(report.is_cancellable)
        self.assertIn("NOT cancellable", report.audit_notes)

    def test_no_nasdaq_order_driven_by_the_feed_is_ever_cancellable(self):
        # Nasdaq publishes the opening imbalance from 09:25 and freezes
        # cancel/modify of on-open orders at 09:25, so any order this strategy
        # derives from a published Nasdaq imbalance is committed capital.
        for secs in (300.0, 250.0, 200.0, 150.0, 100.0, 50.0):
            report = engine(order_type=OnOpenOrderType.LOO,
                            price_basis=PriceBasis.REF).process_auction_imbalance(
                imb(seconds_to_open=secs, far_price=0.0, near_price=0.0))
            self.assertFalse(report.is_cancellable, f"at {secs}s to open")

    def test_nyse_order_before_0929_is_still_cancellable(self):
        # NYSE publishes from 08:00 and only freezes cancels at 09:29, so there
        # is a real window in which the order can still be pulled.
        report = engine(venue=AuctionVenue.NYSE,
                        order_type=OnOpenOrderType.LOO).process_auction_imbalance(
            imb(seconds_to_open=300.0))
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertTrue(report.is_cancellable)

    def test_nyse_rejects_oio_which_the_venue_does_not_offer(self):
        report = engine(venue=AuctionVenue.NYSE,
                        order_type=OnOpenOrderType.OIO).process_auction_imbalance(imb())
        self.assertEqual(report.status, "ORDER_TYPE_UNSUPPORTED_BY_VENUE")
        self.assertIsNone(report.order_generated)

    def test_nyse_loo_is_accepted_inside_the_nasdaq_loo_cutoff(self):
        # NYSE accepts MOO/LOO until the DMM opens the security, so 20s to open
        # is legal on NYSE where Nasdaq would reject the LOO.
        report = engine(venue=AuctionVenue.NYSE,
                        order_type=OnOpenOrderType.LOO).process_auction_imbalance(
            imb(seconds_to_open=20.0))
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertFalse(report.is_cancellable)  # NYSE freezes cancels at 09:29


class TestIndicativePriceAvailability(unittest.TestCase):

    def test_nasdaq_refuses_to_price_before_0928(self):
        # Nasdaq publishes no near/far indicative clearing price before 09:28,
        # so a limit-priced order cannot be derived at 180s to open.
        report = engine(order_type=OnOpenOrderType.LOO).process_auction_imbalance(
            imb(seconds_to_open=180.0))
        self.assertEqual(report.status, "INDICATIVE_PRICE_UNAVAILABLE")
        self.assertIsNone(report.order_generated)

    def test_populated_far_price_before_0928_is_flagged_as_a_parser_warning(self):
        report = engine(order_type=OnOpenOrderType.LOO).process_auction_imbalance(
            imb(seconds_to_open=180.0, far_price=455.0))
        self.assertTrue(any("check the feed parser" in w for w in report.feed_warnings))

    def test_reference_price_basis_works_before_0928(self):
        # The Current Reference Price IS disseminated from 09:25.
        report = engine(order_type=OnOpenOrderType.LOO,
                        price_basis=PriceBasis.REF).process_auction_imbalance(
            imb(seconds_to_open=180.0, far_price=0.0, near_price=0.0))
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertAlmostEqual(report.order_generated["limit_price"], 450.0, places=6)

    def test_zero_far_price_is_treated_as_absent_not_as_a_zero_limit(self):
        report = engine(order_type=OnOpenOrderType.LOO).process_auction_imbalance(
            imb(far_price=0.0))
        self.assertEqual(report.status, "INDICATIVE_PRICE_UNAVAILABLE")

    def test_generated_order_omits_prices_the_venue_has_not_published(self):
        report = engine(order_type=OnOpenOrderType.LOO,
                        price_basis=PriceBasis.REF).process_auction_imbalance(
            imb(seconds_to_open=180.0, far_price=0.0, near_price=0.0))
        self.assertIsNone(report.order_generated["far_price"])
        self.assertIsNone(report.order_generated["near_price"])


class TestNonTradableStates(unittest.TestCase):

    def test_paused_security_is_not_an_imbalance(self):
        report = engine().process_auction_imbalance(imb(imbalance_side="P"))
        self.assertEqual(report.status, "SECURITY_PAUSED")
        self.assertIsNone(report.order_generated)

    def test_insufficient_orders_to_calculate_is_not_an_imbalance(self):
        report = engine().process_auction_imbalance(imb(imbalance_side="O"))
        self.assertEqual(report.status, "IMBALANCE_NOT_CALCULABLE")

    def test_no_imbalance_direction_does_not_trigger(self):
        report = engine().process_auction_imbalance(imb(imbalance_side="N"))
        self.assertEqual(report.status, "NO_IMBALANCE_TRIGGER")

    def test_closing_cross_message_is_rejected(self):
        report = engine().process_auction_imbalance(imb(cross_type="C"))
        self.assertEqual(report.status, "WRONG_CROSS_TYPE")

    def test_halt_ipo_cross_message_is_rejected(self):
        self.assertEqual(
            engine().process_auction_imbalance(imb(cross_type="H")).status,
            "WRONG_CROSS_TYPE")

    def test_stale_observation_is_refused(self):
        report = engine(max_feed_age_seconds=15.0).process_auction_imbalance(
            imb(feed_age_seconds=45.0))
        self.assertEqual(report.status, "STALE_IMBALANCE_DATA")


class TestTriggerThresholds(unittest.TestCase):

    def test_ratio_below_threshold_does_not_trigger(self):
        # 30,000 / (1,000,000 + 30,000) = 2.9% < 20%.
        report = engine().process_auction_imbalance(
            imb(paired_qty=1_000_000, imbalance_qty=30_000))
        self.assertEqual(report.status, "NO_IMBALANCE_TRIGGER")

    def test_ratio_exactly_at_threshold_triggers(self):
        # 100,000 / (400,000 + 100,000) = 0.20 exactly.
        report = engine().process_auction_imbalance(
            imb(paired_qty=400_000, imbalance_qty=100_000))
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertEqual(report.imbalance_ratio, 0.20)

    def test_imbalance_below_minimum_shares_does_not_trigger(self):
        # Ratio is 90% but only 9,000 shares, under the 10,000-share floor.
        report = engine().process_auction_imbalance(
            imb(paired_qty=1_000, imbalance_qty=9_000))
        self.assertEqual(report.status, "NO_IMBALANCE_TRIGGER")


class TestUnpricedOrders(unittest.TestCase):

    def test_moo_requires_explicit_opt_in(self):
        report = engine(order_type=OnOpenOrderType.MOO).process_auction_imbalance(
            imb(seconds_to_open=200.0, far_price=0.0, near_price=0.0))
        self.assertEqual(report.status, "UNPRICED_ORDER_NOT_PERMITTED")
        self.assertIsNone(report.order_generated)

    def test_opted_in_moo_carries_no_limit_price(self):
        report = engine(order_type=OnOpenOrderType.MOO,
                        allow_unpriced_moo=True).process_auction_imbalance(
            imb(seconds_to_open=200.0, far_price=0.0, near_price=0.0))
        self.assertEqual(report.status, "ORDER_GENERATED")
        self.assertIsNone(report.order_generated["limit_price"])


class TestIdempotency(unittest.TestCase):

    def test_repeated_imbalance_updates_do_not_duplicate_the_order(self):
        # Nasdaq republishes the NOII every second from 09:28; a per-tick order
        # would submit the same intent dozens of times.
        eng = engine()
        first = eng.process_auction_imbalance(imb())
        self.assertEqual(first.status, "ORDER_GENERATED")
        for _ in range(9):
            repeat = eng.process_auction_imbalance(imb(paired_qty=305_000))
            self.assertEqual(repeat.status, "DUPLICATE_SUPPRESSED")
            self.assertEqual(
                repeat.order_generated["client_order_id"],
                first.order_generated["client_order_id"])
        self.assertEqual(len(eng.orders), 1)

    def test_client_order_id_is_deterministic_across_engine_instances(self):
        a = engine().process_auction_imbalance(imb())
        b = engine().process_auction_imbalance(imb())
        self.assertEqual(
            a.order_generated["client_order_id"],
            b.order_generated["client_order_id"])

    def test_different_symbols_get_different_intents(self):
        eng = engine()
        first = eng.process_auction_imbalance(imb(symbol="NVDA"))
        second = eng.process_auction_imbalance(imb(symbol="AAPL"))
        self.assertEqual(second.status, "ORDER_GENERATED")
        self.assertNotEqual(
            first.order_generated["client_order_id"],
            second.order_generated["client_order_id"])
        self.assertEqual(len(eng.orders), 2)


class TestInputValidation(unittest.TestCase):

    def test_nan_seconds_to_open_cannot_bypass_the_cutoff_gate(self):
        # Regression: every ordering comparison against NaN is False, so a NaN
        # clock reading previously slipped past the cutoff check and produced an
        # order.
        report = engine().process_auction_imbalance(
            imb(seconds_to_open=float("nan")))
        self.assertEqual(report.status, "INVALID_INPUT")
        self.assertIsNone(report.order_generated)

    def test_infinite_seconds_to_open_is_rejected(self):
        self.assertEqual(
            engine().process_auction_imbalance(
                imb(seconds_to_open=float("inf"))).status,
            "INVALID_INPUT")

    def test_nan_price_is_rejected(self):
        self.assertEqual(
            engine().process_auction_imbalance(imb(far_price=float("nan"))).status,
            "INVALID_INPUT")

    def test_negative_quantities_are_rejected(self):
        self.assertEqual(
            engine().process_auction_imbalance(imb(imbalance_qty=-5_000)).status,
            "INVALID_INPUT")

    def test_unknown_imbalance_direction_is_rejected(self):
        self.assertEqual(
            engine().process_auction_imbalance(imb(imbalance_side="X")).status,
            "INVALID_INPUT")

    def test_empty_symbol_is_rejected(self):
        self.assertEqual(
            engine().process_auction_imbalance(imb(symbol="")).status,
            "INVALID_INPUT")

    def test_negative_feed_age_is_rejected(self):
        self.assertEqual(
            engine().process_auction_imbalance(imb(feed_age_seconds=-1.0)).status,
            "INVALID_INPUT")

    def test_after_the_open_is_past_every_cutoff(self):
        report = engine().process_auction_imbalance(imb(seconds_to_open=-30.0))
        self.assertEqual(report.status, "CUTOFF_EXCEEDED")


class TestConfigValidation(unittest.TestCase):

    def test_nan_participation_pct_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            OpeningAuctionImbalanceBasedExecutionConfig(
                participation_pct=float("nan"))

    def test_participation_pct_above_one_is_rejected(self):
        with self.assertRaises(ValueError):
            OpeningAuctionImbalanceBasedExecutionConfig(participation_pct=1.5)

    def test_negative_size_cap_is_rejected(self):
        with self.assertRaises(ValueError):
            OpeningAuctionImbalanceBasedExecutionConfig(size=-100)

    def test_negative_safety_buffer_is_rejected(self):
        with self.assertRaises(ValueError):
            OpeningAuctionImbalanceBasedExecutionConfig(
                entry_safety_buffer_seconds=-5.0)


class TestEngineDisabled(unittest.TestCase):

    def test_disabled_engine_generates_nothing(self):
        eng = engine(enabled=False)
        report = eng.process_auction_imbalance(imb())
        self.assertEqual(report.status, "ENGINE_DISABLED")
        self.assertIsNone(report.order_generated)
        self.assertEqual(eng.orders, [])


if __name__ == "__main__":
    unittest.main()
