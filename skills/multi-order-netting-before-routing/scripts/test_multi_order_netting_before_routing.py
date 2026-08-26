import unittest
from decimal import Decimal

from multi_order_netting_before_routing import (
    ALLOCATION_TIME_PRIORITY,
    CROSS_TYPE_BOOK_TRANSFER,
    CROSS_TYPE_NONE,
    CROSS_TYPE_REPORTABLE,
    CROSS_TYPE_UNCLASSIFIED,
    EXCLUDED_CROSSED_QUOTE,
    EXCLUDED_LIMIT_NOT_MARKETABLE,
    EXCLUDED_STALE_QUOTE,
    InternalOrder,
    MarketQuote,
    MultiOrderNettingEngine,
    NettingConfig,
    STATUS_NO_INTERNAL_CROSS,
    STATUS_SKIPPED_CROSSED_QUOTE,
    STATUS_SKIPPED_STALE_QUOTE,
    STATUS_SUCCESS,
    WARN_BUNCHED_RESIDUAL,
    WARN_INTERNALIZATION_COST_UNMODELLED,
    WARN_LIMIT_ORDER_EXCLUDED,
    WARN_OWNERSHIP_UNCLASSIFIED,
    WARN_QUOTE_AGE_UNVERIFIED,
    WARN_QUOTE_LOCKED,
    WARN_RESIDUAL_MARKET_ORDER,
    WARN_SUB_PENNY_MATCH_PRICE,
    to_decimal,
)

# Bid $150.00 / Ask $150.10 -> mid $150.05, spread $0.10.
NOW = 1_700_000_000.0


def quote(**overrides):
    params = dict(
        symbol="AAPL",
        bid_price="150.00",
        ask_price="150.10",
        fee_per_share_usd="0.003",
        as_of=NOW,
    )
    params.update(overrides)
    return MarketQuote(**params)


def order(order_id, side, quantity, **overrides):
    params = dict(
        order_id=order_id,
        strategy_id=f"STRAT_{order_id}",
        symbol="AAPL",
        side=side,
        quantity=quantity,
    )
    params.update(overrides)
    return InternalOrder(**params)


def fills_by_order(report):
    return {f.order_id: f.filled_quantity for f in report.internal_fills}


class NettingArithmeticTests(unittest.TestCase):
    """The core aggregate / match / residual arithmetic."""

    def setUp(self):
        self.engine = MultiOrderNettingEngine()

    def test_matches_opposing_volume_and_routes_net_residual(self):
        # Buy 500 + Buy 200 vs Sell 300 -> matched 300 @ 150.05, residual Buy 400.
        report = self.engine.net_and_route_orders(
            quote(),
            [order("ORD_1", "BUY", 500), order("ORD_2", "SELL", 300), order("ORD_3", "BUY", 200)],
            now=NOW,
        )

        self.assertEqual(report.status, STATUS_SUCCESS)
        self.assertEqual(report.internal_matched_quantity, 300)
        self.assertEqual(report.internal_match_price, Decimal("150.05"))
        self.assertEqual(report.quoted_spread, Decimal("0.10"))
        self.assertEqual(report.net_external_quantity, 400)
        self.assertIsNotNone(report.external_order)
        self.assertEqual(report.external_order.side, "BUY")
        self.assertEqual(report.external_order.quantity, 400)

    def test_perfect_match_leaves_no_residual_order(self):
        report = self.engine.net_and_route_orders(
            quote(), [order("ORD_1", "BUY", 500), order("ORD_2", "SELL", 500)], now=NOW
        )
        self.assertEqual(report.internal_matched_quantity, 500)
        self.assertEqual(report.net_external_quantity, 0)
        self.assertIsNone(report.external_order)

    def test_one_sided_batch_crosses_nothing_but_still_aggregates(self):
        report = self.engine.net_and_route_orders(
            quote(), [order("ORD_1", "BUY", 100), order("ORD_2", "BUY", 250)], now=NOW
        )
        self.assertEqual(report.status, STATUS_NO_INTERNAL_CROSS)
        self.assertEqual(report.internal_matched_quantity, 0)
        self.assertEqual(report.internal_fills, [])
        self.assertEqual(report.cross_type, CROSS_TYPE_NONE)
        self.assertEqual(report.external_order.quantity, 350)
        self.assertEqual(report.total_cost_savings_usd, Decimal("0"))

    def test_sell_heavy_batch_routes_sell_residual(self):
        report = self.engine.net_and_route_orders(
            quote(), [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 400)], now=NOW
        )
        self.assertEqual(report.internal_matched_quantity, 100)
        self.assertEqual(report.external_order.side, "SELL")
        self.assertEqual(report.external_order.quantity, 300)

    def test_mid_price_is_exact_and_unrounded_on_a_penny_spread(self):
        # Bid 150.00 / Ask 150.01 -> mid 150.005. Rounding that to a penny would
        # hand one side a half-cent per share on every cross; Rule 612 governs
        # the increments of orders and quotations, not of executions.
        report = self.engine.net_and_route_orders(
            quote(ask_price="150.01"),
            [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 100)],
            now=NOW,
        )
        self.assertEqual(report.internal_match_price, Decimal("150.005"))
        self.assertIn(WARN_SUB_PENNY_MATCH_PRICE, report.warnings)
        for fill in report.internal_fills:
            self.assertEqual(fill.fill_price, Decimal("150.005"))

    def test_no_sub_penny_warning_on_a_two_cent_spread(self):
        report = self.engine.net_and_route_orders(
            quote(ask_price="150.02"),
            [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 100)],
            now=NOW,
        )
        self.assertEqual(report.internal_match_price, Decimal("150.01"))
        self.assertNotIn(WARN_SUB_PENNY_MATCH_PRICE, report.warnings)

    def test_locked_quote_crosses_at_the_touch_with_zero_spread_saving(self):
        report = self.engine.net_and_route_orders(
            quote(ask_price="150.00"),
            [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 100)],
            now=NOW,
        )
        self.assertEqual(report.status, STATUS_SUCCESS)
        self.assertEqual(report.internal_match_price, Decimal("150.00"))
        self.assertEqual(report.spread_savings_usd, Decimal("0"))
        self.assertIn(WARN_QUOTE_LOCKED, report.warnings)


class AllocationTests(unittest.TestCase):
    """Who gets the mid-price fill, and does the allocation add up."""

    def test_pro_rata_allocation_is_proportional_and_exact(self):
        # Matched = 300 against buys of 500 and 200 (total 700):
        #   500/700 * 300 = 214.28... -> 214 + remainder .28
        #   200/700 * 300 =  85.71... ->  85 + remainder .71  <- larger remainder
        # Largest remainder gets the odd share: 214 and 86, summing to 300.
        engine = MultiOrderNettingEngine()
        report = engine.net_and_route_orders(
            quote(),
            [order("ORD_1", "BUY", 500), order("ORD_2", "BUY", 200), order("ORD_3", "SELL", 300)],
            now=NOW,
        )
        allocation = fills_by_order(report)
        self.assertEqual(allocation["ORD_1"], 214)
        self.assertEqual(allocation["ORD_2"], 86)
        self.assertEqual(allocation["ORD_1"] + allocation["ORD_2"], 300)
        self.assertEqual(allocation["ORD_3"], 300)

    def test_time_priority_policy_fills_in_batch_order(self):
        # Regression: this was the *only* behaviour, while the documentation said
        # pro-rata. Under time priority ORD_1 takes the whole 300 and ORD_2 none.
        engine = MultiOrderNettingEngine(
            NettingConfig(allocation_policy=ALLOCATION_TIME_PRIORITY)
        )
        report = engine.net_and_route_orders(
            quote(),
            [order("ORD_1", "BUY", 500), order("ORD_2", "BUY", 200), order("ORD_3", "SELL", 300)],
            now=NOW,
        )
        allocation = fills_by_order(report)
        self.assertEqual(allocation["ORD_1"], 300)
        self.assertNotIn("ORD_2", allocation)

    def test_pro_rata_is_deterministic_across_identical_batches(self):
        engine = MultiOrderNettingEngine()
        batch = [
            order("ORD_B", "BUY", 100),
            order("ORD_A", "BUY", 100),
            order("ORD_C", "BUY", 100),
            order("ORD_S", "SELL", 101),
        ]
        first = fills_by_order(engine.net_and_route_orders(quote(), batch, now=NOW))
        second = fills_by_order(engine.net_and_route_orders(quote(), batch, now=NOW))
        self.assertEqual(first, second)
        # Matched = min(300, 101) = 101, and the buy side allocation sums to it
        # exactly even though 101 does not divide evenly across three orders.
        self.assertEqual(sum(v for k, v in first.items() if k != "ORD_S"), 101)

    def test_allocation_always_sums_to_the_matched_quantity(self):
        engine = MultiOrderNettingEngine()
        report = engine.net_and_route_orders(
            quote(),
            [
                order("ORD_1", "BUY", 33),
                order("ORD_2", "BUY", 33),
                order("ORD_3", "BUY", 34),
                order("ORD_4", "SELL", 50),
            ],
            now=NOW,
        )
        allocation = fills_by_order(report)
        buy_side = sum(v for k, v in allocation.items() if k != "ORD_4")
        self.assertEqual(buy_side, 50)
        self.assertEqual(allocation["ORD_4"], 50)

    def test_invalid_allocation_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            NettingConfig(allocation_policy="FIFO")


class LimitPriceTests(unittest.TestCase):
    """A limit price must constrain the cross and survive into the residual."""

    def setUp(self):
        self.engine = MultiOrderNettingEngine()

    def test_buy_limit_below_mid_is_not_crossed_and_not_netted(self):
        # Regression: the limit price used to be ignored entirely, so this buy
        # was crossed at 150.05 -- $1.05 through its own limit.
        report = self.engine.net_and_route_orders(
            quote(),
            [order("ORD_1", "BUY", 500, limit_price="149.00"), order("ORD_2", "SELL", 300)],
            now=NOW,
        )
        self.assertEqual(report.internal_matched_quantity, 0)
        self.assertEqual(report.eligible_buy_quantity, 0)
        self.assertEqual(fills_by_order(report), {})
        self.assertEqual([e.order_id for e in report.excluded_orders], ["ORD_1"])
        self.assertEqual(report.excluded_orders[0].reason, EXCLUDED_LIMIT_NOT_MARKETABLE)
        self.assertIn(WARN_LIMIT_ORDER_EXCLUDED, report.warnings)
        # The excluded buy must not be netted against the sell either.
        self.assertEqual(report.external_order.side, "SELL")
        self.assertEqual(report.external_order.quantity, 300)

    def test_sell_limit_above_mid_is_not_crossed(self):
        report = self.engine.net_and_route_orders(
            quote(),
            [order("ORD_1", "BUY", 300), order("ORD_2", "SELL", 300, limit_price="151.00")],
            now=NOW,
        )
        self.assertEqual(report.internal_matched_quantity, 0)
        self.assertEqual(report.excluded_orders[0].order_id, "ORD_2")

    def test_limit_exactly_at_the_mid_is_crossable(self):
        report = self.engine.net_and_route_orders(
            quote(),
            [
                order("ORD_1", "BUY", 100, limit_price="150.05"),
                order("ORD_2", "SELL", 100, limit_price="150.05"),
            ],
            now=NOW,
        )
        self.assertEqual(report.internal_matched_quantity, 100)
        self.assertEqual(report.excluded_orders, [])

    def test_residual_from_a_limit_order_is_routed_as_a_limit_order(self):
        # Regression: a marketable limit order's residual used to be emitted as a
        # MARKET order, discarding the price constraint the strategy set.
        report = self.engine.net_and_route_orders(
            quote(),
            [order("ORD_1", "BUY", 500, limit_price="150.20"), order("ORD_2", "SELL", 300)],
            now=NOW,
        )
        self.assertEqual(report.internal_matched_quantity, 300)
        self.assertEqual(report.external_order.order_type, "LIMIT")
        self.assertEqual(report.external_order.limit_price, Decimal("150.20"))
        self.assertNotIn(WARN_RESIDUAL_MARKET_ORDER, report.warnings)

    def test_residual_limit_is_the_most_conservative_contributor_limit(self):
        # Two buys contribute residual at 150.20 and 150.50. Routing at 150.50
        # would fill the 150.20 order 30c through its limit, so the aggregate
        # order is priced at the lower of the two.
        report = self.engine.net_and_route_orders(
            quote(),
            [
                order("ORD_1", "BUY", 400, limit_price="150.20"),
                order("ORD_2", "BUY", 400, limit_price="150.50"),
                order("ORD_3", "SELL", 200),
            ],
            now=NOW,
        )
        self.assertEqual(report.external_order.side, "BUY")
        self.assertEqual(report.external_order.quantity, 600)
        self.assertEqual(report.external_order.limit_price, Decimal("150.20"))

    def test_sell_residual_limit_is_the_highest_contributor_limit(self):
        report = self.engine.net_and_route_orders(
            quote(),
            [
                order("ORD_1", "SELL", 400, limit_price="150.00"),
                order("ORD_2", "SELL", 400, limit_price="149.50"),
                order("ORD_3", "BUY", 200),
            ],
            now=NOW,
        )
        self.assertEqual(report.external_order.side, "SELL")
        self.assertEqual(report.external_order.limit_price, Decimal("150.00"))

    def test_unpriced_residual_is_a_market_order_and_says_so(self):
        report = self.engine.net_and_route_orders(
            quote(), [order("ORD_1", "BUY", 500), order("ORD_2", "SELL", 300)], now=NOW
        )
        self.assertEqual(report.external_order.order_type, "MARKET")
        self.assertIsNone(report.external_order.limit_price)
        self.assertIn(WARN_RESIDUAL_MARKET_ORDER, report.warnings)


class BeneficialOwnershipTests(unittest.TestCase):
    """Whether the matched quantity is a book transfer or a reportable execution."""

    def setUp(self):
        self.engine = MultiOrderNettingEngine()

    def test_same_owner_on_both_sides_is_a_book_transfer(self):
        report = self.engine.net_and_route_orders(
            quote(retained_internalization_cost_per_share_usd="0.001"),
            [
                order("ORD_1", "BUY", 300, beneficial_owner_id="FUND_A"),
                order("ORD_2", "SELL", 300, beneficial_owner_id="FUND_A"),
            ],
            now=NOW,
        )
        self.assertEqual(report.cross_type, CROSS_TYPE_BOOK_TRANSFER)
        self.assertFalse(report.requires_execution_report)
        # Nothing is printed, so no print-related cost survives.
        self.assertEqual(report.retained_internalization_cost_usd, Decimal("0"))
        self.assertEqual(report.net_fee_savings_usd, report.gross_fee_savings_usd)

    def test_different_owners_produce_a_reportable_cross(self):
        report = self.engine.net_and_route_orders(
            quote(retained_internalization_cost_per_share_usd="0.001"),
            [
                order("ORD_1", "BUY", 300, beneficial_owner_id="FUND_A"),
                order("ORD_2", "SELL", 300, beneficial_owner_id="FUND_B"),
            ],
            now=NOW,
        )
        self.assertEqual(report.cross_type, CROSS_TYPE_REPORTABLE)
        self.assertTrue(report.requires_execution_report)
        self.assertEqual(report.retained_internalization_cost_usd, Decimal("0.300"))

    def test_unknown_owner_is_treated_as_reportable_not_as_a_transfer(self):
        report = self.engine.net_and_route_orders(
            quote(),
            [order("ORD_1", "BUY", 300), order("ORD_2", "SELL", 300)],
            now=NOW,
        )
        self.assertEqual(report.cross_type, CROSS_TYPE_UNCLASSIFIED)
        self.assertTrue(report.requires_execution_report)
        self.assertIn(WARN_OWNERSHIP_UNCLASSIFIED, report.warnings)

    def test_ownership_is_classified_from_matched_participants_only(self):
        # ORD_3 receives no internal fill under time priority, so its owner must
        # not turn a single-owner transfer into a reportable cross.
        engine = MultiOrderNettingEngine(
            NettingConfig(allocation_policy=ALLOCATION_TIME_PRIORITY)
        )
        report = engine.net_and_route_orders(
            quote(),
            [
                order("ORD_1", "BUY", 300, beneficial_owner_id="FUND_A"),
                order("ORD_3", "BUY", 100, beneficial_owner_id="FUND_C"),
                order("ORD_2", "SELL", 300, beneficial_owner_id="FUND_A"),
            ],
            now=NOW,
        )
        self.assertEqual(report.cross_type, CROSS_TYPE_BOOK_TRANSFER)

    def test_bunched_residual_across_owners_is_flagged(self):
        report = self.engine.net_and_route_orders(
            quote(),
            [
                order("ORD_1", "BUY", 400, beneficial_owner_id="FUND_A"),
                order("ORD_2", "BUY", 400, beneficial_owner_id="FUND_B"),
                order("ORD_3", "SELL", 200, beneficial_owner_id="FUND_A"),
            ],
            now=NOW,
        )
        self.assertIn(WARN_BUNCHED_RESIDUAL, report.warnings)
        self.assertEqual(
            set(report.external_order.contributing_order_ids), {"ORD_1", "ORD_2"}
        )


class CostSavingsTests(unittest.TestCase):
    """The savings estimate and the costs that survive internalisation."""

    def setUp(self):
        self.engine = MultiOrderNettingEngine()

    def test_gross_savings_are_two_sided_fees_plus_one_full_spread(self):
        # 300 shares matched, $0.003/share access fee, $0.10 spread.
        # Each side avoids the fee: 2 * 300 * 0.003 = $1.80.
        # Each side avoids half the spread: 2 * 300 * 0.05 = $30.00.
        report = self.engine.net_and_route_orders(
            quote(),
            [
                order("ORD_1", "BUY", 500, beneficial_owner_id="FUND_A"),
                order("ORD_2", "SELL", 300, beneficial_owner_id="FUND_A"),
            ],
            now=NOW,
        )
        self.assertEqual(report.gross_fee_savings_usd, Decimal("1.800"))
        self.assertEqual(report.spread_savings_usd, Decimal("30.00"))
        self.assertEqual(report.total_cost_savings_usd, Decimal("31.800"))

    def test_retained_print_costs_reduce_the_net_saving(self):
        # A reportable cross still pays the costs attached to the print itself.
        # 300 shares at $0.002/share retained -> $0.60 off a $1.80 gross saving.
        report = self.engine.net_and_route_orders(
            quote(retained_internalization_cost_per_share_usd="0.002"),
            [
                order("ORD_1", "BUY", 300, beneficial_owner_id="FUND_A"),
                order("ORD_2", "SELL", 300, beneficial_owner_id="FUND_B"),
            ],
            now=NOW,
        )
        self.assertEqual(report.gross_fee_savings_usd, Decimal("1.800"))
        self.assertEqual(report.retained_internalization_cost_usd, Decimal("0.600"))
        self.assertEqual(report.net_fee_savings_usd, Decimal("1.200"))
        self.assertEqual(report.total_cost_savings_usd, Decimal("31.200"))

    def test_unmodelled_print_costs_are_flagged_on_a_reportable_cross(self):
        report = self.engine.net_and_route_orders(
            quote(),
            [
                order("ORD_1", "BUY", 300, beneficial_owner_id="FUND_A"),
                order("ORD_2", "SELL", 300, beneficial_owner_id="FUND_B"),
            ],
            now=NOW,
        )
        self.assertIn(WARN_INTERNALIZATION_COST_UNMODELLED, report.warnings)
        self.assertEqual(report.net_fee_savings_usd, report.gross_fee_savings_usd)

    def test_savings_use_exact_decimal_arithmetic(self):
        # 0.1 + 0.2 style drift: 3 * 0.07 is 0.21000000000000002 in binary floats.
        report = self.engine.net_and_route_orders(
            quote(bid_price="10.00", ask_price="10.07", fee_per_share_usd="0.07"),
            [
                order("ORD_1", "BUY", 3, beneficial_owner_id="FUND_A"),
                order("ORD_2", "SELL", 3, beneficial_owner_id="FUND_A"),
            ],
            now=NOW,
        )
        self.assertEqual(report.spread_savings_usd, Decimal("0.21"))
        self.assertEqual(report.gross_fee_savings_usd, Decimal("0.42"))

    def test_zero_matched_quantity_saves_nothing(self):
        report = self.engine.net_and_route_orders(
            quote(), [order("ORD_1", "BUY", 100)], now=NOW
        )
        self.assertEqual(report.gross_fee_savings_usd, Decimal("0"))
        self.assertEqual(report.spread_savings_usd, Decimal("0"))
        self.assertNotIn(WARN_INTERNALIZATION_COST_UNMODELLED, report.warnings)


class QuoteIntegrityTests(unittest.TestCase):
    """The engine refuses to cross against a quote it cannot stand behind."""

    def setUp(self):
        self.engine = MultiOrderNettingEngine()

    def test_stale_quote_crosses_nothing_and_returns_every_order(self):
        report = self.engine.net_and_route_orders(
            quote(),
            [order("ORD_1", "BUY", 500), order("ORD_2", "SELL", 300)],
            now=NOW + 5.0,
        )
        self.assertEqual(report.status, STATUS_SKIPPED_STALE_QUOTE)
        self.assertEqual(report.internal_matched_quantity, 0)
        self.assertIsNone(report.external_order)
        self.assertIsNone(report.internal_match_price)
        self.assertEqual(len(report.excluded_orders), 2)
        self.assertTrue(all(e.reason == EXCLUDED_STALE_QUOTE for e in report.excluded_orders))
        # Submitted totals survive so the caller can reconcile the batch.
        self.assertEqual(report.total_buy_quantity, 500)
        self.assertEqual(report.total_sell_quantity, 300)

    def test_quote_inside_the_age_limit_is_used(self):
        report = self.engine.net_and_route_orders(
            quote(), [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 100)], now=NOW + 0.5
        )
        self.assertEqual(report.status, STATUS_SUCCESS)

    def test_age_limit_boundary_is_inclusive(self):
        report = self.engine.net_and_route_orders(
            quote(), [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 100)], now=NOW + 1.0
        )
        self.assertEqual(report.status, STATUS_SUCCESS)

    def test_disabled_age_limit_never_rejects_on_staleness(self):
        engine = MultiOrderNettingEngine(NettingConfig(max_quote_age_seconds=None))
        report = engine.net_and_route_orders(
            quote(), [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 100)], now=NOW + 3600
        )
        self.assertEqual(report.status, STATUS_SUCCESS)

    def test_missing_timestamp_is_reported_as_unverified_not_as_fresh(self):
        report = self.engine.net_and_route_orders(
            quote(as_of=None),
            [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 100)],
            now=NOW,
        )
        self.assertIn(WARN_QUOTE_AGE_UNVERIFIED, report.warnings)
        self.assertEqual(report.status, STATUS_SUCCESS)

    def test_future_dated_quote_is_not_treated_as_stale(self):
        report = self.engine.net_and_route_orders(
            quote(), [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 100)], now=NOW - 30
        )
        self.assertEqual(report.status, STATUS_SUCCESS)

    def test_crossed_quote_crosses_nothing(self):
        report = self.engine.net_and_route_orders(
            quote(bid_price="150.20"),
            [order("ORD_1", "BUY", 500), order("ORD_2", "SELL", 300)],
            now=NOW,
        )
        self.assertEqual(report.status, STATUS_SKIPPED_CROSSED_QUOTE)
        self.assertEqual(report.spread_savings_usd, Decimal("0"))
        self.assertTrue(all(e.reason == EXCLUDED_CROSSED_QUOTE for e in report.excluded_orders))

    def test_non_positive_prices_are_rejected(self):
        for bad in ("0", "-1"):
            with self.subTest(bid=bad):
                with self.assertRaises(ValueError):
                    self.engine.net_and_route_orders(
                        quote(bid_price=bad), [order("ORD_1", "BUY", 100)], now=NOW
                    )

    def test_nan_price_is_rejected_rather_than_propagated(self):
        with self.assertRaises(ValueError):
            self.engine.net_and_route_orders(
                quote(ask_price=float("nan")), [order("ORD_1", "BUY", 100)], now=NOW
            )

    def test_negative_fee_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.net_and_route_orders(
                quote(fee_per_share_usd="-0.001"), [order("ORD_1", "BUY", 100)], now=NOW
            )


class BatchValidationTests(unittest.TestCase):
    """Malformed batches must fail loudly, never partially."""

    def setUp(self):
        self.engine = MultiOrderNettingEngine()

    def test_empty_batch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.net_and_route_orders(quote(), [], now=NOW)

    def test_order_in_another_symbol_is_rejected(self):
        # Regression: the symbol was taken from the quote and never checked, so a
        # stray MSFT order was netted against AAPL and vanished into the residual.
        with self.assertRaises(ValueError) as ctx:
            self.engine.net_and_route_orders(
                quote(),
                [order("ORD_1", "BUY", 100), order("ORD_2", "SELL", 100, symbol="MSFT")],
                now=NOW,
            )
        self.assertIn("MSFT", str(ctx.exception))

    def test_unknown_side_is_rejected_not_silently_dropped(self):
        # Regression: a side filter matched only BUY/SELL, so 'SHORT' was neither
        # netted nor routed -- the order simply disappeared.
        with self.assertRaises(ValueError) as ctx:
            self.engine.net_and_route_orders(
                quote(),
                [order("ORD_1", "BUY", 100), order("ORD_2", "SHORT", 100)],
                now=NOW,
            )
        self.assertIn("SHORT", str(ctx.exception))

    def test_side_is_case_and_whitespace_insensitive(self):
        report = self.engine.net_and_route_orders(
            quote(), [order("ORD_1", " buy ", 100), order("ORD_2", "Sell", 100)], now=NOW
        )
        self.assertEqual(report.internal_matched_quantity, 100)

    def test_duplicate_order_id_is_rejected(self):
        # Regression: a replayed batch double-counted the quantity and produced a
        # residual order for stock no strategy had asked to trade.
        with self.assertRaises(ValueError) as ctx:
            self.engine.net_and_route_orders(
                quote(),
                [order("ORD_1", "BUY", 100), order("ORD_1", "BUY", 100)],
                now=NOW,
            )
        self.assertIn("ORD_1", str(ctx.exception))

    def test_non_positive_quantity_is_rejected(self):
        for bad in (0, -100):
            with self.subTest(quantity=bad):
                with self.assertRaises(ValueError):
                    self.engine.net_and_route_orders(
                        quote(), [order("ORD_1", "BUY", bad)], now=NOW
                    )

    def test_fractional_quantity_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.net_and_route_orders(
                quote(), [order("ORD_1", "BUY", 100.5)], now=NOW
            )

    def test_boolean_quantity_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.net_and_route_orders(
                quote(), [order("ORD_1", "BUY", True)], now=NOW
            )

    def test_non_positive_limit_price_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.net_and_route_orders(
                quote(), [order("ORD_1", "BUY", 100, limit_price="0")], now=NOW
            )

    def test_engine_does_not_mutate_the_submitted_orders(self):
        submitted = [order("ORD_1", " buy ", 100), order("ORD_2", "SELL", 100)]
        self.engine.net_and_route_orders(quote(), submitted, now=NOW)
        self.assertEqual(submitted[0].side, " buy ")
        self.assertIsNone(submitted[0].limit_price)


class QuantityConservationTests(unittest.TestCase):
    """Nothing may be created or lost between the batch and the routing decision.

    Every submitted share must end up in exactly one of three places: crossed
    internally, carried by the external residual, or handed back excluded. A
    netting engine that violates this has either invented size or dropped an
    order, and both are silent in the aggregate totals.
    """

    def _assert_conserved(self, report, submitted):
        for side in ("BUY", "SELL"):
            excluded = sum(e.quantity for e in report.excluded_orders if e.side == side)
            filled = sum(f.filled_quantity for f in report.internal_fills if f.side == side)
            residual = (
                report.net_external_quantity
                if report.external_order and report.external_order.side == side
                else 0
            )
            self.assertEqual(
                submitted[side],
                excluded + filled + residual,
                f"{side} side lost or invented quantity: {report.audit_notes}",
            )
        buy_filled = sum(f.filled_quantity for f in report.internal_fills if f.side == "BUY")
        sell_filled = sum(f.filled_quantity for f in report.internal_fills if f.side == "SELL")
        self.assertEqual(buy_filled, report.internal_matched_quantity)
        self.assertEqual(sell_filled, report.internal_matched_quantity)

    def test_conservation_across_representative_batches(self):
        batches = [
            [order("A", "BUY", 500), order("B", "SELL", 300)],
            [order("A", "BUY", 500), order("B", "SELL", 500)],
            [order("A", "BUY", 137), order("B", "BUY", 41), order("C", "SELL", 89)],
            [order("A", "BUY", 500, limit_price="149.00"), order("B", "SELL", 300)],
            [
                order("A", "BUY", 400, limit_price="150.20"),
                order("B", "BUY", 400, limit_price="149.00"),
                order("C", "SELL", 250),
                order("D", "SELL", 51, limit_price="151.00"),
            ],
            [order("A", "SELL", 997), order("B", "BUY", 3)],
        ]
        for policy in (None, ALLOCATION_TIME_PRIORITY):
            engine = MultiOrderNettingEngine(
                NettingConfig(allocation_policy=policy) if policy else None
            )
            for index, batch in enumerate(batches):
                with self.subTest(policy=policy or "PRO_RATA", batch=index):
                    submitted = {
                        "BUY": sum(o.quantity for o in batch if o.side == "BUY"),
                        "SELL": sum(o.quantity for o in batch if o.side == "SELL"),
                    }
                    report = engine.net_and_route_orders(quote(), batch, now=NOW)
                    self._assert_conserved(report, submitted)

    def test_conservation_holds_when_the_quote_is_rejected(self):
        batch = [order("A", "BUY", 500), order("B", "SELL", 300)]
        report = MultiOrderNettingEngine().net_and_route_orders(
            quote(), batch, now=NOW + 60
        )
        self._assert_conserved(report, {"BUY": 500, "SELL": 300})


class DecimalConversionTests(unittest.TestCase):
    def test_float_input_recovers_the_decimal_literal(self):
        self.assertEqual(to_decimal(0.1, "x"), Decimal("0.1"))

    def test_infinity_is_rejected(self):
        with self.assertRaises(ValueError):
            to_decimal(float("inf"), "x")

    def test_unparseable_string_is_rejected(self):
        with self.assertRaises(ValueError):
            to_decimal("one fifty", "x")

    def test_unsupported_type_is_rejected(self):
        with self.assertRaises(TypeError):
            to_decimal(None, "x")


if __name__ == "__main__":
    unittest.main()
