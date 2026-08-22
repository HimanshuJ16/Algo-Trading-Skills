import unittest
from datetime import datetime, time, timedelta, timezone

from close_auction_participation_strategy import (
    US_EASTERN,
    AuctionVenue,
    CloseAuctionParticipationStrategy,
    DecisionReason,
    NoiiMessage,
    PriceBasis,
    imbalance_ratio,
)

TRADE_DATE = (2025, 1, 15)  # a Wednesday; US/Eastern is EST (UTC-5) on this date


def et(hour: int, minute: int, second: int = 0) -> datetime:
    """Timezone-aware US/Eastern datetime on the fixed trade date."""
    return datetime(*TRADE_DATE, hour, minute, second, tzinfo=US_EASTERN)


def noii(**overrides) -> NoiiMessage:
    """A Nasdaq closing-cross NOII with a 100k buy imbalance at 15:56 ET."""
    defaults = dict(
        symbol="AAPL",
        timestamp=et(15, 56),
        paired_shares=500_000,
        imbalance_shares=100_000,
        imbalance_direction="B",
        far_price=150.50,
        near_price=150.25,
        reference_price=150.00,
    )
    defaults.update(overrides)
    return NoiiMessage(**defaults)


class TestImbalanceRatio(unittest.TestCase):
    def test_ratio_matches_hand_computed_value(self):
        # 100,000 / (500,000 + 100,000) = 1/6
        self.assertAlmostEqual(imbalance_ratio(500_000, 100_000), 1.0 / 6.0, places=12)

    def test_empty_book_returns_zero_not_division_error(self):
        self.assertEqual(imbalance_ratio(0, 0), 0.0)

    def test_fully_unpaired_book_is_one(self):
        self.assertEqual(imbalance_ratio(0, 25_000), 1.0)


class TestOrderGeneration(unittest.TestCase):
    def setUp(self):
        self.strategy = CloseAuctionParticipationStrategy(max_participation_pct=0.10)

    def test_contra_sell_order_on_buy_imbalance(self):
        # 10% of a 100,000 share buy imbalance = 10,000 shares, and the
        # auction-volume cap (15% of 600,000 = 90,000) does not bind.
        decision = self.strategy.evaluate(noii())
        self.assertEqual(decision.reason, DecisionReason.ORDER_GENERATED)
        order = decision.order
        self.assertEqual(order.symbol, "AAPL")
        self.assertEqual(order.side, "SELL")
        self.assertEqual(order.quantity, 10_000)
        self.assertEqual(order.limit_price, 150.50)  # far price, no concession
        self.assertEqual(order.price_basis, PriceBasis.FAR)
        self.assertEqual(order.venue, AuctionVenue.NASDAQ)
        self.assertAlmostEqual(order.imbalance_ratio, 1.0 / 6.0, places=12)

    def test_contra_buy_order_on_sell_imbalance(self):
        decision = self.strategy.evaluate(
            noii(
                symbol="MSFT",
                timestamp=et(15, 56, 30),
                paired_shares=300_000,
                imbalance_shares=50_000,
                imbalance_direction="S",
                far_price=400.00,
                near_price=401.00,
                reference_price=402.00,
            )
        )
        order = decision.order
        self.assertEqual(order.side, "BUY")
        self.assertEqual(order.quantity, 5_000)
        self.assertEqual(order.limit_price, 400.00)

    def test_near_price_basis_is_used_when_selected(self):
        strategy = CloseAuctionParticipationStrategy(price_basis=PriceBasis.NEAR)
        self.assertEqual(strategy.evaluate(noii()).order.limit_price, 150.25)

    def test_generate_auction_order_wrapper_returns_order_only(self):
        self.assertEqual(self.strategy.generate_auction_order(noii()).quantity, 10_000)


class TestVenueTiming(unittest.TestCase):
    def setUp(self):
        self.strategy = CloseAuctionParticipationStrategy(safety_buffer_seconds=5.0)

    def test_nasdaq_accepts_loc_entry_after_1555(self):
        # Regression: the previous implementation hard-coded a 15:55 cutoff and
        # blocked entry that Nasdaq still accepts until 15:58.
        decision = self.strategy.evaluate(noii(timestamp=et(15, 57)))
        self.assertEqual(decision.reason, DecisionReason.ORDER_GENERATED)
        self.assertTrue(decision.order.late_entry_reprice_risk)

    def test_nasdaq_blocks_entry_at_loc_cutoff(self):
        decision = self.strategy.evaluate(noii(timestamp=et(15, 58)))
        self.assertEqual(decision.reason, DecisionReason.PAST_ENTRY_CUTOFF)
        self.assertIsNone(decision.order)

    def test_safety_buffer_blocks_submission_inside_the_buffer(self):
        self.assertEqual(
            self.strategy.evaluate(noii(timestamp=et(15, 57, 56))).reason,
            DecisionReason.PAST_ENTRY_CUTOFF,
        )
        self.assertEqual(
            self.strategy.evaluate(noii(timestamp=et(15, 57, 54))).reason,
            DecisionReason.ORDER_GENERATED,
        )

    def test_submission_time_not_message_time_governs_the_cutoff(self):
        # Message arrives in time, but the order would only reach the exchange
        # after the cutoff: it must be blocked.
        decision = self.strategy.evaluate(
            noii(timestamp=et(15, 56)), submission_time=et(15, 58, 30)
        )
        self.assertEqual(decision.reason, DecisionReason.PAST_ENTRY_CUTOFF)

    def test_stale_imbalance_data_is_not_acted_on(self):
        # Feed stalled: the observation is 90s old by the time we would submit.
        decision = self.strategy.evaluate(
            noii(timestamp=et(15, 56)), submission_time=et(15, 57, 30)
        )
        self.assertEqual(decision.reason, DecisionReason.STALE_IMBALANCE_DATA)

    def test_fresh_message_within_age_limit_is_acted_on(self):
        decision = self.strategy.evaluate(
            noii(timestamp=et(15, 56)), submission_time=et(15, 56, 2)
        )
        self.assertEqual(decision.reason, DecisionReason.ORDER_GENERATED)

    def test_submission_time_before_message_is_rejected(self):
        with self.assertRaises(ValueError):
            self.strategy.evaluate(noii(timestamp=et(15, 56)), submission_time=et(15, 55))

    def test_stricter_cutoff_override_is_honoured(self):
        strategy = CloseAuctionParticipationStrategy(
            cutoff_time=time(15, 56), safety_buffer_seconds=0.0
        )
        self.assertEqual(
            strategy.evaluate(noii(timestamp=et(15, 56))).reason,
            DecisionReason.PAST_ENTRY_CUTOFF,
        )

    def test_looser_cutoff_override_cannot_exceed_the_venue_rule(self):
        strategy = CloseAuctionParticipationStrategy(cutoff_time=time(16, 30))
        self.assertEqual(strategy.effective_entry_cutoff_et, time(15, 58))
        self.assertEqual(
            strategy.evaluate(noii(timestamp=et(15, 59))).reason,
            DecisionReason.PAST_ENTRY_CUTOFF,
        )

    def test_cancel_modify_freeze_at_1550(self):
        self.assertTrue(self.strategy.can_cancel_or_modify(et(15, 49, 59)))
        self.assertFalse(self.strategy.can_cancel_or_modify(et(15, 50, 0)))

    def test_utc_timestamps_are_converted_not_compared_raw(self):
        # 20:56 UTC == 15:56 EST. The previous implementation compared the naive
        # wall-clock time and would have blocked this message.
        utc_ts = datetime(*TRADE_DATE, 20, 56, tzinfo=timezone.utc)
        decision = self.strategy.evaluate(noii(timestamp=utc_ts))
        self.assertEqual(decision.reason, DecisionReason.ORDER_GENERATED)

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            self.strategy.evaluate(noii(timestamp=datetime(*TRADE_DATE, 15, 56)))

    def test_offset_only_timezone_is_accepted(self):
        est = timezone(timedelta(hours=-5))
        decision = self.strategy.evaluate(
            noii(timestamp=datetime(*TRADE_DATE, 15, 56, tzinfo=est))
        )
        self.assertEqual(decision.reason, DecisionReason.ORDER_GENERATED)


class TestNyseRules(unittest.TestCase):
    def setUp(self):
        self.strategy = CloseAuctionParticipationStrategy(venue=AuctionVenue.NYSE)

    def test_entry_before_freeze_is_not_blocked_by_the_cutoff(self):
        # NYSE publishes closing imbalance information only from 15:50, so a
        # 15:49 message legitimately has no indicative price -- but entry
        # itself must not be blocked at that time.
        decision = self.strategy.evaluate(noii(timestamp=et(15, 49)))
        self.assertEqual(decision.reason, DecisionReason.INDICATIVE_PRICE_UNAVAILABLE)

    def test_entry_after_the_close_is_blocked(self):
        decision = self.strategy.evaluate(
            noii(timestamp=et(16, 0), significant_imbalance_published=True)
        )
        self.assertEqual(decision.reason, DecisionReason.PAST_ENTRY_CUTOFF)

    def test_contra_side_entry_during_freeze_requires_published_imbalance(self):
        decision = self.strategy.evaluate(noii(timestamp=et(15, 52)))
        self.assertEqual(
            decision.reason, DecisionReason.ENTRY_FROZEN_NO_PUBLISHED_IMBALANCE
        )

    def test_contra_side_entry_allowed_against_published_imbalance(self):
        decision = self.strategy.evaluate(
            noii(timestamp=et(15, 52), significant_imbalance_published=True)
        )
        self.assertEqual(decision.reason, DecisionReason.ORDER_GENERATED)
        self.assertEqual(decision.order.side, "SELL")
        self.assertFalse(decision.order.late_entry_reprice_risk)


class TestIndicativePriceAvailability(unittest.TestCase):
    def setUp(self):
        self.strategy = CloseAuctionParticipationStrategy()

    def test_no_order_before_nasdaq_publishes_near_far_prices(self):
        # Regression: the previous implementation priced off far/near at 15:52,
        # but Nasdaq disseminates no indicative clearing price for the closing
        # cross before 15:55.
        decision = self.strategy.evaluate(noii(timestamp=et(15, 52)))
        self.assertEqual(decision.reason, DecisionReason.INDICATIVE_PRICE_UNAVAILABLE)
        self.assertIsNone(decision.order)

    def test_zero_far_price_is_treated_as_absent_not_as_a_price(self):
        # Regression: previously this produced an order with a $0.00 limit.
        decision = self.strategy.evaluate(noii(far_price=0.0))
        self.assertEqual(decision.reason, DecisionReason.INDICATIVE_PRICE_UNAVAILABLE)


class TestNonActionableStates(unittest.TestCase):
    def setUp(self):
        self.strategy = CloseAuctionParticipationStrategy()

    def test_no_imbalance(self):
        decision = self.strategy.evaluate(
            noii(imbalance_direction="N", imbalance_shares=0)
        )
        self.assertEqual(decision.reason, DecisionReason.NO_ACTIONABLE_IMBALANCE)

    def test_insufficient_orders_code(self):
        decision = self.strategy.evaluate(
            noii(imbalance_direction="O", imbalance_shares=0)
        )
        self.assertEqual(decision.reason, DecisionReason.NO_ACTIONABLE_IMBALANCE)

    def test_paused_security_never_produces_an_order(self):
        decision = self.strategy.evaluate(noii(imbalance_direction="P"))
        self.assertEqual(decision.reason, DecisionReason.SECURITY_PAUSED)
        self.assertIsNone(decision.order)

    def test_non_closing_cross_message_is_ignored(self):
        decision = self.strategy.evaluate(noii(cross_type="O"))
        self.assertEqual(decision.reason, DecisionReason.WRONG_CROSS_TYPE)

    def test_zero_imbalance_shares_with_buy_direction(self):
        decision = self.strategy.evaluate(noii(imbalance_shares=0))
        self.assertEqual(decision.reason, DecisionReason.NO_ACTIONABLE_IMBALANCE)


class TestThresholdsAndSizing(unittest.TestCase):
    def test_min_imbalance_shares_threshold(self):
        strategy = CloseAuctionParticipationStrategy(min_imbalance_shares=200_000)
        self.assertEqual(
            strategy.evaluate(noii()).reason, DecisionReason.IMBALANCE_BELOW_MIN_SHARES
        )

    def test_min_imbalance_ratio_threshold(self):
        # ratio is 1/6 ~= 0.1667, below the 0.25 floor.
        strategy = CloseAuctionParticipationStrategy(min_imbalance_ratio=0.25)
        decision = strategy.evaluate(noii())
        self.assertEqual(decision.reason, DecisionReason.IMBALANCE_RATIO_BELOW_THRESHOLD)
        self.assertAlmostEqual(decision.imbalance_ratio, 1.0 / 6.0, places=12)

    def test_target_qty_caps_the_order(self):
        strategy = CloseAuctionParticipationStrategy(max_participation_pct=0.10)
        self.assertEqual(strategy.evaluate(noii(), target_qty=2_500).order.quantity, 2_500)

    def test_auction_volume_cap_binds_when_participation_pct_is_high(self):
        # 100% of the imbalance would be 1,000 shares, but 15% of the predicted
        # auction volume (0 paired + 1,000 imbalance) is 150.
        strategy = CloseAuctionParticipationStrategy(
            max_participation_pct=1.0, max_auction_volume_pct=0.15
        )
        decision = strategy.evaluate(noii(paired_shares=0, imbalance_shares=1_000))
        self.assertEqual(decision.order.quantity, 150)

    def test_quantity_rounding_to_zero_produces_no_order(self):
        strategy = CloseAuctionParticipationStrategy(max_participation_pct=0.001)
        decision = strategy.evaluate(noii(paired_shares=0, imbalance_shares=100))
        self.assertEqual(decision.reason, DecisionReason.QUANTITY_ROUNDS_TO_ZERO)

    def test_quantity_is_floored_never_rounded_up(self):
        # 10% of 100,009 = 10,000.9 -> 10,000
        strategy = CloseAuctionParticipationStrategy(max_participation_pct=0.10)
        decision = strategy.evaluate(noii(imbalance_shares=100_009))
        self.assertEqual(decision.order.quantity, 10_000)

    def test_non_positive_target_qty_is_rejected(self):
        strategy = CloseAuctionParticipationStrategy()
        with self.assertRaises(ValueError):
            strategy.evaluate(noii(), target_qty=0)


class TestPricing(unittest.TestCase):
    def test_sell_concession_lowers_limit_and_rounds_up_to_the_tick(self):
        # 150.50 * (1 - 10bps) = 150.3485 -> rounded away from the aggressive
        # side for a sell (up) -> 150.35
        strategy = CloseAuctionParticipationStrategy(price_concession_bps=10.0)
        self.assertEqual(strategy.evaluate(noii()).order.limit_price, 150.35)

    def test_buy_concession_raises_limit_and_rounds_down_to_the_tick(self):
        # 100.03 * (1 + 7bps) = 100.100021 -> rounded down for a buy -> 100.10
        strategy = CloseAuctionParticipationStrategy(price_concession_bps=7.0)
        decision = strategy.evaluate(
            noii(imbalance_direction="S", far_price=100.03, near_price=100.03)
        )
        self.assertEqual(decision.order.limit_price, 100.10)

    def test_sub_dollar_tick_size(self):
        strategy = CloseAuctionParticipationStrategy(
            price_concession_bps=25.0, tick_size=0.0001
        )
        # 0.8000 * (1 - 25bps) = 0.798 exactly -> no rounding adjustment
        decision = strategy.evaluate(noii(far_price=0.80, near_price=0.80))
        self.assertAlmostEqual(decision.order.limit_price, 0.798, places=6)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.strategy = CloseAuctionParticipationStrategy()

    def test_negative_imbalance_shares(self):
        with self.assertRaises(ValueError):
            self.strategy.evaluate(noii(imbalance_shares=-1))

    def test_negative_paired_shares(self):
        with self.assertRaises(ValueError):
            self.strategy.evaluate(noii(paired_shares=-5))

    def test_unknown_imbalance_direction(self):
        with self.assertRaises(ValueError):
            self.strategy.evaluate(noii(imbalance_direction="b"))

    def test_nan_far_price(self):
        with self.assertRaises(ValueError):
            self.strategy.evaluate(noii(far_price=float("nan")))

    def test_infinite_near_price(self):
        with self.assertRaises(ValueError):
            self.strategy.evaluate(noii(near_price=float("inf")))

    def test_negative_price(self):
        with self.assertRaises(ValueError):
            self.strategy.evaluate(noii(reference_price=-1.0))

    def test_empty_symbol(self):
        with self.assertRaises(ValueError):
            self.strategy.evaluate(noii(symbol="   "))

    def test_invalid_constructor_arguments(self):
        for kwargs in (
            {"max_participation_pct": 0.0},
            {"max_participation_pct": 1.5},
            {"max_auction_volume_pct": 0.0},
            {"safety_buffer_seconds": -1.0},
            {"max_message_age_seconds": 0.0},
            {"min_imbalance_shares": -1},
            {"min_imbalance_ratio": 1.5},
            {"price_concession_bps": -1.0},
            {"tick_size": 0.0},
            {"venue": "NASDAQ"},
            {"price_basis": "FAR"},
            {"cutoff_time": "15:55"},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    CloseAuctionParticipationStrategy(**kwargs)


if __name__ == "__main__":
    unittest.main()
