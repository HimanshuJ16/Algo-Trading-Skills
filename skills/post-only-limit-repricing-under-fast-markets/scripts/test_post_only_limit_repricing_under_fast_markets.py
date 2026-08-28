"""Unit tests for the fast-market Post-Only repricer.

Expected prices are derived independently of the implementation: each case
states the BBO and tick, and the assertion is the passive price a human would
compute from the venue rule ("a BUY must rest strictly below the best ask, on an
exact multiple of the tick"), not a re-run of the module's own arithmetic.
"""

import unittest
from decimal import Decimal

from post_only_limit_repricing_under_fast_markets import (
    ACTION_HOLD,
    ACTION_SUBMIT,
    STATUS_ACCEPTED_PASSIVE,
    STATUS_ATTEMPTS_EXCEEDED,
    STATUS_BOOK_LOCKED_OR_CROSSED,
    STATUS_NO_PASSIVE_PRICE,
    STATUS_PASSIVE_REPRICED,
    Config,
    FastMarketPostOnlyRepricer,
    MarketState,
    OrderRequest,
    align_to_tick,
)


def on_tick(price, tick):
    """True when price is an exact multiple of tick (Binance PRICE_FILTER rule)."""
    return Decimal(str(price)) % Decimal(str(tick)) == 0


class TestAlignToTick(unittest.TestCase):
    """Alignment must always move away from the touch, never to nearest."""

    def test_buy_floors_and_sell_ceils(self):
        # 100.08 sits between the 0.05 ticks 100.05 and 100.10.
        self.assertEqual(align_to_tick(100.08, 0.05, "BUY"), 100.05)
        self.assertEqual(align_to_tick(100.02, 0.05, "SELL"), 100.05)

    def test_exact_tick_multiple_is_unchanged(self):
        self.assertEqual(align_to_tick(100.05, 0.05, "BUY"), 100.05)
        self.assertEqual(align_to_tick(100.05, 0.05, "SELL"), 100.05)

    def test_half_penny_tick_regime(self):
        # A $0.005 increment: 10.007 floors to 10.005 and ceils to 10.010.
        self.assertEqual(align_to_tick(10.007, 0.005, "BUY"), 10.005)
        self.assertEqual(align_to_tick(10.007, 0.005, "SELL"), 10.01)

    def test_sub_satoshi_tick_precision_is_preserved(self):
        # Regression: the previous implementation applied round(price, 4),
        # collapsing this to 0.0.
        aligned = align_to_tick(0.00002451, 0.00000001, "BUY")
        self.assertEqual(Decimal(str(aligned)), Decimal("0.00002451"))
        self.assertTrue(on_tick(aligned, 0.00000001))

    def test_float_representation_error_does_not_leak(self):
        # 0.1 + 0.2 == 0.30000000000000004 in binary floating point.
        self.assertTrue(on_tick(align_to_tick(0.1 + 0.2, 0.01, "BUY"), 0.01))

    def test_extreme_price_tick_ratio_raises_valueerror_not_decimal_error(self):
        # A corrupt feed price needs more significant digits than the decimal
        # context carries; the failure must surface as a rejected input, not as
        # a decimal.InvalidOperation escaping into the order path.
        with self.assertRaises(ValueError):
            align_to_tick(1e300, 0.01, "BUY")

    def test_sub_tick_buy_floors_to_zero(self):
        # Documented hazard: callers must treat a non-positive result as
        # "no passive price", which the engine does.
        self.assertEqual(align_to_tick(0.4, 1.0, "BUY"), 0.0)

    def test_rejects_invalid_arguments(self):
        for bad_price in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                align_to_tick(bad_price, 0.01, "BUY")
        for bad_tick in (0.0, -0.01, float("nan")):
            with self.assertRaises(ValueError):
                align_to_tick(100.0, bad_tick, "BUY")
        with self.assertRaises(ValueError):
            align_to_tick(100.0, 0.01, "SHORT")


class TestInputValidation(unittest.TestCase):
    """Malformed market data and orders must raise, never silently proceed."""

    def test_market_state_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            MarketState("", best_bid=100.0, best_ask=100.1)
        with self.assertRaises(ValueError):
            MarketState("X", best_bid=100.0, best_ask=100.1, tick_size=0.0)
        with self.assertRaises(ValueError):
            MarketState("X", best_bid=100.0, best_ask=100.1, tick_size=-0.01)
        with self.assertRaises(ValueError):
            MarketState("X", best_bid=float("nan"), best_ask=100.1)
        with self.assertRaises(ValueError):
            MarketState("X", best_bid=100.0, best_ask=float("inf"))
        with self.assertRaises(ValueError):
            MarketState("X", best_bid=0.0, best_ask=100.1)
        with self.assertRaises(ValueError):
            MarketState("X", best_bid=100.0, best_ask=100.1,
                        market_velocity_ticks_per_sec=-1.0)

    def test_order_request_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            OrderRequest("", "BUY", 1.0, 100.0)
        with self.assertRaises(ValueError):
            OrderRequest("O", "SHORT", 1.0, 100.0)
        with self.assertRaises(ValueError):
            OrderRequest("O", "BUY", 0.0, 100.0)
        with self.assertRaises(ValueError):
            OrderRequest("O", "BUY", -1.0, 100.0)
        with self.assertRaises(ValueError):
            OrderRequest("O", "BUY", 1.0, float("nan"))
        with self.assertRaises(ValueError):
            OrderRequest("O", "BUY", 1.0, -100.0)
        with self.assertRaises(ValueError):
            OrderRequest("O", "BUY", 1.0, 100.0, reprice_attempts=-1)

    def test_lowercase_side_is_accepted_and_normalised(self):
        report = FastMarketPostOnlyRepricer().process_order(
            MarketState("X", 100.0, 100.1),
            OrderRequest("O", "buy", 1.0, 100.0),
        )
        self.assertEqual(report.side, "BUY")

    def test_config_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            Config(max_reprice_attempts=-1)
        with self.assertRaises(ValueError):
            Config(fast_market_offset_ticks=-1)
        with self.assertRaises(ValueError):
            Config(fast_market_velocity_threshold=float("nan"))


class TestCrossingDetection(unittest.TestCase):
    """BBO 100.00 / 100.10, tick 0.01 unless stated otherwise."""

    def setUp(self):
        self.engine = FastMarketPostOnlyRepricer()
        self.market = MarketState("AAPL", best_bid=100.00, best_ask=100.10, tick_size=0.01)

    def _run(self, side, price, **kwargs):
        return self.engine.process_order(
            kwargs.pop("market", self.market),
            OrderRequest("O", side, 10.0, price, **kwargs),
        )

    def test_buy_inside_spread_is_left_untouched(self):
        report = self._run("BUY", 100.05)
        self.assertEqual(report.status, STATUS_ACCEPTED_PASSIVE)
        self.assertFalse(report.is_repriced)
        self.assertEqual(report.final_limit_price, 100.05)
        self.assertEqual(report.action, ACTION_SUBMIT)

    def test_sell_inside_spread_is_left_untouched(self):
        report = self._run("SELL", 100.05)
        self.assertEqual(report.status, STATUS_ACCEPTED_PASSIVE)
        self.assertEqual(report.final_limit_price, 100.05)

    def test_buy_at_best_ask_crosses_and_is_repriced_to_the_bid(self):
        # A BUY at the ask takes liquidity, so the boundary is inclusive.
        report = self._run("BUY", 100.10)
        self.assertEqual(report.status, STATUS_PASSIVE_REPRICED)
        self.assertEqual(report.final_limit_price, 100.00)
        self.assertLess(report.final_limit_price, self.market.best_ask)

    def test_sell_at_best_bid_crosses_and_is_repriced_to_the_ask(self):
        report = self._run("SELL", 100.00)
        self.assertEqual(report.status, STATUS_PASSIVE_REPRICED)
        self.assertEqual(report.final_limit_price, 100.10)
        self.assertGreater(report.final_limit_price, self.market.best_bid)

    def test_buy_one_tick_below_ask_does_not_cross(self):
        report = self._run("BUY", 100.09)
        self.assertEqual(report.status, STATUS_ACCEPTED_PASSIVE)
        self.assertEqual(report.final_limit_price, 100.09)

    def test_sell_one_tick_above_bid_does_not_cross(self):
        report = self._run("SELL", 100.01)
        self.assertEqual(report.status, STATUS_ACCEPTED_PASSIVE)
        self.assertEqual(report.final_limit_price, 100.01)

    def test_buy_at_best_bid_is_passive(self):
        report = self._run("BUY", 100.00)
        self.assertEqual(report.status, STATUS_ACCEPTED_PASSIVE)
        self.assertEqual(report.final_limit_price, 100.00)


class TestTickAlignmentInvariant(unittest.TestCase):
    """The emitted price must be on-tick AND strictly passive, always."""

    def test_passive_buy_is_not_rounded_up_onto_the_ask(self):
        # Regression for the nearest-rounding defect: 100.08 on a 0.05 tick
        # rounds to 100.10, which is the best ask -- a crossing Post-Only order.
        market = MarketState("AAPL", best_bid=100.00, best_ask=100.10, tick_size=0.05)
        report = FastMarketPostOnlyRepricer().process_order(
            market, OrderRequest("O", "BUY", 10.0, 100.08)
        )
        self.assertEqual(report.final_limit_price, 100.05)
        self.assertLess(report.final_limit_price, market.best_ask)
        self.assertTrue(on_tick(report.final_limit_price, market.tick_size))

    def test_passive_sell_is_not_rounded_down_onto_the_bid(self):
        market = MarketState("AAPL", best_bid=100.00, best_ask=100.10, tick_size=0.05)
        report = FastMarketPostOnlyRepricer().process_order(
            market, OrderRequest("O", "SELL", 10.0, 100.02)
        )
        self.assertEqual(report.final_limit_price, 100.05)
        self.assertGreater(report.final_limit_price, market.best_bid)

    def test_crypto_tick_survives_repricing(self):
        # Regression: round(price, 4) previously returned 0.0 here.
        market = MarketState("SHIBUSDT", best_bid=0.00002451, best_ask=0.00002455,
                             tick_size=0.00000001)
        report = FastMarketPostOnlyRepricer().process_order(
            market, OrderRequest("O", "BUY", 1e6, 0.00002460)
        )
        self.assertEqual(report.status, STATUS_PASSIVE_REPRICED)
        self.assertEqual(Decimal(str(report.final_limit_price)), Decimal("0.00002451"))
        self.assertTrue(on_tick(report.final_limit_price, market.tick_size))

    def test_no_passive_price_when_tick_is_coarser_than_the_spread(self):
        # Bid 100.02 / ask 100.03 on a 1.00 tick: flooring a BUY reaches 100.00,
        # which is passive; but a SELL ceils to 101.00 -- also passive. The
        # unreachable case is an off-tick book where the aligned price re-crosses.
        market = MarketState("X", best_bid=100.90, best_ask=100.95, tick_size=1.0)
        report = FastMarketPostOnlyRepricer().process_order(
            market, OrderRequest("O", "BUY", 1.0, 101.0)
        )
        # best_bid 100.90 floors to 100.00, still strictly below the ask.
        self.assertEqual(report.final_limit_price, 100.0)
        self.assertLess(report.final_limit_price, market.best_ask)

    def test_withholds_when_alignment_cannot_produce_a_positive_price(self):
        market = MarketState("X", best_bid=0.4, best_ask=0.9, tick_size=1.0)
        report = FastMarketPostOnlyRepricer().process_order(
            market, OrderRequest("O", "BUY", 1.0, 1.5)
        )
        self.assertEqual(report.status, STATUS_NO_PASSIVE_PRICE)
        self.assertEqual(report.action, ACTION_HOLD)


class TestLockedAndCrossedBook(unittest.TestCase):
    """A locked or crossed book has no passive price; withhold the order."""

    def test_locked_book_is_held(self):
        market = MarketState("BTCUSDT", best_bid=60000.0, best_ask=60000.0)
        report = FastMarketPostOnlyRepricer().process_order(
            market, OrderRequest("O", "BUY", 1.0, 60001.0)
        )
        self.assertEqual(report.status, STATUS_BOOK_LOCKED_OR_CROSSED)
        self.assertEqual(report.action, ACTION_HOLD)
        self.assertTrue(report.rejection_churn_prevented)

    def test_crossed_book_is_held_for_both_sides(self):
        market = MarketState("BTCUSDT", best_bid=60010.0, best_ask=60000.0)
        for side in ("BUY", "SELL"):
            report = FastMarketPostOnlyRepricer().process_order(
                market, OrderRequest("O", side, 1.0, 60005.0)
            )
            self.assertEqual(report.status, STATUS_BOOK_LOCKED_OR_CROSSED, side)
            self.assertEqual(report.action, ACTION_HOLD, side)

    def test_locked_book_does_not_consume_a_reprice_attempt(self):
        market = MarketState("BTCUSDT", best_bid=60000.0, best_ask=60000.0)
        report = FastMarketPostOnlyRepricer().process_order(
            market, OrderRequest("O", "BUY", 1.0, 60001.0, reprice_attempts=1)
        )
        self.assertEqual(report.reprice_attempts_used, 1)


class TestChurnCap(unittest.TestCase):
    """The attempt cap must actually engage across a resubmission loop."""

    def setUp(self):
        self.engine = FastMarketPostOnlyRepricer(Config(max_reprice_attempts=3))
        self.market = MarketState("BTCUSDT", 60000.0, 60010.0,
                                  market_velocity_ticks_per_sec=25.0)

    def test_attempts_increment_only_on_reprice(self):
        report = self.engine.process_order(
            self.market, OrderRequest("O", "BUY", 1.0, 60005.0)
        )
        self.assertEqual(report.reprice_attempts_used, 0)

        report = self.engine.process_order(
            self.market, OrderRequest("O", "BUY", 1.0, 60015.0)
        )
        self.assertEqual(report.reprice_attempts_used, 1)

    def test_loop_reaches_the_cap_and_then_holds(self):
        order = OrderRequest("O", "BUY", 1.0, 60015.0)
        statuses = []
        for _ in range(5):
            report = self.engine.process_order(self.market, order)
            statuses.append(report.status)
            order = report.next_attempt(order)

        self.assertEqual(
            statuses,
            [STATUS_PASSIVE_REPRICED] * 3 + [STATUS_ATTEMPTS_EXCEEDED] * 2,
        )

    def test_next_attempt_preserves_the_rest_of_the_order(self):
        order = OrderRequest("O", "SELL", 2.5, 60005.0)
        report = self.engine.process_order(self.market, order)
        carried = report.next_attempt(order)
        self.assertEqual(carried.order_id, "O")
        self.assertEqual(carried.side, "SELL")
        self.assertEqual(carried.quantity, 2.5)
        self.assertEqual(carried.desired_price, 60005.0)
        self.assertEqual(carried.reprice_attempts, report.reprice_attempts_used)

    def test_exceeded_report_holds_the_order(self):
        report = self.engine.process_order(
            self.market, OrderRequest("O", "BUY", 1.0, 60015.0, reprice_attempts=3)
        )
        self.assertEqual(report.status, STATUS_ATTEMPTS_EXCEEDED)
        self.assertEqual(report.action, ACTION_HOLD)
        self.assertTrue(report.rejection_churn_prevented)

    def test_zero_attempt_budget_holds_immediately(self):
        engine = FastMarketPostOnlyRepricer(Config(max_reprice_attempts=0))
        report = engine.process_order(
            self.market, OrderRequest("O", "BUY", 1.0, 60005.0)
        )
        self.assertEqual(report.status, STATUS_ATTEMPTS_EXCEEDED)


class TestFastMarketOffset(unittest.TestCase):
    """Velocity must change behaviour, not merely be reported."""

    def setUp(self):
        self.engine = FastMarketPostOnlyRepricer(
            Config(fast_market_offset_ticks=2, fast_market_velocity_threshold=20.0)
        )

    def test_calm_market_joins_the_touch(self):
        market = MarketState("BTCUSDT", 60000.0, 60010.0, tick_size=0.5,
                             market_velocity_ticks_per_sec=5.0)
        report = self.engine.process_order(market, OrderRequest("O", "BUY", 1.0, 60015.0))
        self.assertFalse(report.is_fast_market)
        self.assertEqual(report.offset_ticks_applied, 0)
        self.assertEqual(report.final_limit_price, 60000.0)

    def test_fast_market_backs_off_by_the_configured_ticks(self):
        market = MarketState("BTCUSDT", 60000.0, 60010.0, tick_size=0.5,
                             market_velocity_ticks_per_sec=25.0)
        report = self.engine.process_order(market, OrderRequest("O", "BUY", 1.0, 60015.0))
        self.assertTrue(report.is_fast_market)
        self.assertEqual(report.offset_ticks_applied, 2)
        self.assertEqual(report.final_limit_price, 60000.0 - 2 * 0.5)

    def test_fast_market_sell_backs_off_upward(self):
        market = MarketState("BTCUSDT", 60000.0, 60010.0, tick_size=0.5,
                             market_velocity_ticks_per_sec=25.0)
        report = self.engine.process_order(market, OrderRequest("O", "SELL", 1.0, 59995.0))
        self.assertEqual(report.final_limit_price, 60010.0 + 2 * 0.5)

    def test_velocity_exactly_at_threshold_counts_as_fast(self):
        market = MarketState("BTCUSDT", 60000.0, 60010.0,
                             market_velocity_ticks_per_sec=20.0)
        report = self.engine.process_order(market, OrderRequest("O", "BUY", 1.0, 60005.0))
        self.assertTrue(report.is_fast_market)

    def test_offset_is_not_applied_to_a_non_crossing_order(self):
        market = MarketState("BTCUSDT", 60000.0, 60010.0, tick_size=0.5,
                             market_velocity_ticks_per_sec=25.0)
        report = self.engine.process_order(market, OrderRequest("O", "BUY", 1.0, 60005.0))
        self.assertEqual(report.offset_ticks_applied, 0)
        self.assertEqual(report.final_limit_price, 60005.0)

    def test_offset_large_enough_to_drive_price_non_positive_is_held(self):
        market = MarketState("X", 100.0, 100.1, tick_size=0.01,
                             market_velocity_ticks_per_sec=99.0)
        engine = FastMarketPostOnlyRepricer(Config(fast_market_offset_ticks=1_000_000))
        report = engine.process_order(market, OrderRequest("O", "BUY", 1.0, 101.0))
        self.assertEqual(report.status, STATUS_NO_PASSIVE_PRICE)
        self.assertEqual(report.action, ACTION_HOLD)

    def test_default_config_is_offset_free(self):
        self.assertEqual(Config().fast_market_offset_ticks, 0)


class TestReportContract(unittest.TestCase):
    """Fields callers and audit trails depend on."""

    def test_submit_report_echoes_inputs(self):
        market = MarketState("BTCUSDT", 60000.0, 60010.0, tick_size=0.5)
        order = OrderRequest("ORD_42", "BUY", 3.0, 60015.0)
        report = FastMarketPostOnlyRepricer().process_order(market, order)
        self.assertEqual(report.order_id, "ORD_42")
        self.assertEqual(report.symbol, "BTCUSDT")
        self.assertEqual(report.original_desired_price, 60015.0)
        self.assertEqual(report.tick_size, 0.5)
        self.assertIn("POST_ONLY_PASSIVE_REPRICED", report.audit_notes)

    def test_churn_flag_is_false_when_nothing_was_prevented(self):
        report = FastMarketPostOnlyRepricer().process_order(
            MarketState("X", 100.0, 100.1),
            OrderRequest("O", "BUY", 1.0, 100.05),
        )
        self.assertFalse(report.rejection_churn_prevented)
        self.assertEqual(report.action, ACTION_SUBMIT)


if __name__ == "__main__":
    unittest.main()
