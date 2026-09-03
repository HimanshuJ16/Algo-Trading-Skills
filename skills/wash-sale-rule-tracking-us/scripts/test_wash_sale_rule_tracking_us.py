"""Unit tests for the IRC section 1091 wash sale tracking engine.

Expected values are derived by hand from the statute and regulation, not by
re-running the engine's own arithmetic. Each test states the derivation.
"""

import datetime
import random
import unittest

from wash_sale_rule_tracking_us import (
    IRC_1091_WINDOW_DAYS,
    TradeExecution,
    TradeSide,
    USWashSaleTrackingEngine,
    WashSaleError,
    WashSaleSummary,
)


def buy(trade_id, symbol, date, price, qty):
    return TradeExecution(trade_id, symbol, date, TradeSide.BUY, price, qty)


def sell(trade_id, symbol, date, price, qty):
    return TradeExecution(trade_id, symbol, date, TradeSide.SELL, price, qty)


def _unadjusted_fifo_pnl(trades):
    """Realized P&L from unadjusted purchase prices, computed independently of the engine."""
    lots = []
    total = 0.0
    for trade in trades:
        if trade.side is TradeSide.BUY:
            lots.append([trade.quantity, trade.price])
            continue
        remaining = trade.quantity
        while remaining > 1e-9:
            qty = min(remaining, lots[0][0])
            total += (trade.price - lots[0][1]) * qty
            lots[0][0] -= qty
            remaining -= qty
            if lots[0][0] <= 1e-9:
                lots.pop(0)
    return total


class TestUSWashSaleTrackingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = USWashSaleTrackingEngine(window_days=30)

    def _evaluate(self, symbol, *trades):
        for trade in trades:
            self.engine.add_trade(trade)
        return self.engine.evaluate_wash_sales_for_symbol(symbol)

    # ------------------------------------------------------- core section 1091

    def test_classic_wash_sale_replacement_buy_after_loss(self):
        # Buy 100 @ 150 (Jan 10); sell 100 @ 100 (Jan 20) => 5,000 loss.
        # Buy 100 @ 110 (Feb 5) is 16 days after the sale, inside the window.
        # Section 1091(a) disallows the whole loss; 1091(d) basis = 110 + 50 = 160.
        summary = self._evaluate(
            "AAPL",
            buy("T1", "AAPL", datetime.date(2025, 1, 10), 150.0, 100.0),
            sell("T2", "AAPL", datetime.date(2025, 1, 20), 100.0, 100.0),
            buy("T3", "AAPL", datetime.date(2025, 2, 5), 110.0, 100.0),
        )

        self.assertEqual(summary.total_realized_gross_pnl_usd, -5000.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 5000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, 0.0)
        self.assertEqual(summary.deferred_loss_in_open_lots_usd, 5000.0)
        self.assertEqual(len(summary.wash_matches), 1)

        match = summary.wash_matches[0]
        self.assertEqual(match.loss_trade_id, "T2")
        self.assertEqual(match.replacement_trade_id, "T3")
        self.assertEqual(match.matched_quantity, 100.0)
        self.assertEqual(match.disallowed_loss_usd, 5000.0)
        self.assertEqual(match.adjusted_replacement_basis_per_share, 160.0)

    def test_pre_loss_replacement_buy_within_30_days(self):
        # Section 1091(a) covers acquisitions in the 30 days BEFORE the sale.
        # Buy 100 @ 200 (Mar 1); buy 100 @ 180 (Mar 10); sell 100 @ 150 (Mar 15).
        # FIFO sells the Mar 1 lot: loss = (200 - 150) * 100 = 5,000. The Mar 10
        # lot is still held after the sale, so it is replacement stock.
        summary = self._evaluate(
            "TSLA",
            buy("T1", "TSLA", datetime.date(2025, 3, 1), 200.0, 100.0),
            buy("T2", "TSLA", datetime.date(2025, 3, 10), 180.0, 100.0),
            sell("T3", "TSLA", datetime.date(2025, 3, 15), 150.0, 100.0),
        )

        self.assertEqual(summary.total_realized_gross_pnl_usd, -5000.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 5000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, 0.0)
        self.assertEqual(summary.wash_matches[0].replacement_trade_id, "T2")
        # 1091(d): 180 + 50.
        self.assertEqual(summary.wash_matches[0].adjusted_replacement_basis_per_share, 230.0)

    def test_sale_outside_61_day_window_no_wash_sale(self):
        summary = self._evaluate(
            "NVDA",
            buy("T1", "NVDA", datetime.date(2025, 1, 10), 100.0, 100.0),
            sell("T2", "NVDA", datetime.date(2025, 1, 20), 80.0, 100.0),
            buy("T3", "NVDA", datetime.date(2025, 3, 15), 85.0, 100.0),
        )

        self.assertEqual(summary.total_realized_gross_pnl_usd, -2000.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 0.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -2000.0)
        self.assertEqual(summary.wash_matches, [])

    def test_partial_wash_sale_quantity_matching(self):
        # Sell 100 at a 100/share loss, replace only 40 shares.
        # Disallowed = 40 * 100 = 4,000; allowed = 60 * 100 = 6,000.
        summary = self._evaluate(
            "MSFT",
            buy("T1", "MSFT", datetime.date(2025, 4, 1), 300.0, 100.0),
            sell("T2", "MSFT", datetime.date(2025, 4, 10), 200.0, 100.0),
            buy("T3", "MSFT", datetime.date(2025, 4, 15), 210.0, 40.0),
        )

        self.assertEqual(summary.total_realized_gross_pnl_usd, -10000.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 4000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -6000.0)
        self.assertEqual(summary.wash_matches[0].matched_quantity, 40.0)
        self.assertEqual(summary.wash_matches[0].adjusted_replacement_basis_per_share, 310.0)

    # ------------------------------------------------- window boundary (30/31)

    def test_replacement_exactly_30_days_after_is_inside_the_window(self):
        summary = self._evaluate(
            "SPY",
            buy("T1", "SPY", datetime.date(2025, 1, 1), 50.0, 100.0),
            sell("T2", "SPY", datetime.date(2025, 2, 1), 40.0, 100.0),
            buy("T3", "SPY", datetime.date(2025, 3, 3), 42.0, 100.0),  # Feb 1 + 30 days
        )
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 1000.0)

    def test_replacement_31_days_after_is_outside_the_window(self):
        summary = self._evaluate(
            "SPY",
            buy("T1", "SPY", datetime.date(2025, 1, 1), 50.0, 100.0),
            sell("T2", "SPY", datetime.date(2025, 2, 1), 40.0, 100.0),
            buy("T3", "SPY", datetime.date(2025, 3, 4), 42.0, 100.0),  # Feb 1 + 31 days
        )
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 0.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -1000.0)

    def test_replacement_exactly_30_days_before_is_inside_the_window(self):
        # The acquisition is 30 days before the loss sale and is still held after
        # it, so it is replacement stock under section 1091(a).
        summary = self._evaluate(
            "SPY",
            buy("T1", "SPY", datetime.date(2025, 1, 1), 50.0, 100.0),
            buy("T2", "SPY", datetime.date(2025, 1, 2), 48.0, 100.0),  # Feb 1 - 30 days
            sell("T3", "SPY", datetime.date(2025, 2, 1), 40.0, 100.0),
        )
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 1000.0)
        self.assertEqual(summary.wash_matches[0].replacement_trade_id, "T2")

    # ------------------------------- regressions for the two calculation defects

    def test_disallowed_loss_flows_into_the_later_sale_of_the_replacement_lot(self):
        # Regression: section 1091(d) increases the replacement lot's basis, so a
        # later disposition of those shares must use the increased basis.
        #   Buy 100 @ 50 (Jan 1); sell 100 @ 40 (Jan 10)  -> 1,000 loss, disallowed.
        #   Buy 100 @ 42 (Jan 15) -> adjusted basis 52.
        #   Sell 100 @ 45 (Mar 1) -> (45 - 52) * 100 = -700, no replacement in window.
        # Economically the taxpayer is down 700 and holds nothing, so the whole
        # 700 is deductible in the year. A two-pass engine that computes P&L from
        # unadjusted basis and then adds the disallowance back reports +300.
        summary = self._evaluate(
            "AMD",
            buy("B1", "AMD", datetime.date(2025, 1, 1), 50.0, 100.0),
            sell("S1", "AMD", datetime.date(2025, 1, 10), 40.0, 100.0),
            buy("B2", "AMD", datetime.date(2025, 1, 15), 42.0, 100.0),
            sell("S2", "AMD", datetime.date(2025, 3, 1), 45.0, 100.0),
        )

        # Box 1d proceeds = 100*40 + 100*45 = 8,500.
        # Box 1e basis    = 100*50 + 100*52 = 10,200.
        self.assertEqual(summary.total_proceeds_usd, 8500.0)
        self.assertEqual(summary.total_cost_basis_usd, 10200.0)
        self.assertEqual(summary.total_realized_gross_pnl_usd, -1700.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 1000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -700.0)
        self.assertEqual(summary.deferred_loss_in_open_lots_usd, 0.0)

    def test_full_liquidation_of_two_lots_is_not_a_wash_sale(self):
        # Regression: shares disposed of by the loss sale itself are not
        # replacement stock for that sale. Buy 100 @ 50 (Jan 1), buy 100 @ 50
        # (Jan 5), sell all 200 @ 40 (Jan 10) and never repurchase. Nothing is
        # held afterwards, so the entire 2,000 loss is deductible. Treating each
        # lot as the other's replacement reports 2,000 disallowed and a net of 0.
        summary = self._evaluate(
            "INTC",
            buy("B1", "INTC", datetime.date(2025, 1, 1), 50.0, 100.0),
            buy("B2", "INTC", datetime.date(2025, 1, 5), 50.0, 100.0),
            sell("S1", "INTC", datetime.date(2025, 1, 10), 40.0, 200.0),
        )

        self.assertEqual(summary.total_realized_gross_pnl_usd, -2000.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 0.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -2000.0)
        self.assertEqual(summary.wash_matches, [])

    def test_chained_wash_sale_closed_before_year_end_recognizes_full_loss(self):
        # Buy 100 @ 50 (Dec 1); sell @ 40 (Dec 20) -> 1,000 disallowed into the
        # Dec 28 lot (basis 52); sell @ 41 (Dec 30) -> (41 - 52) * 100 = -1,100.
        # The Dec 30 loss has no replacement: the Dec 1 lot was disposed of and
        # the Dec 28 lot is the origin lot of the loss. Total deductible = 1,100,
        # which equals the economic loss of 1,000 + 100.
        summary = self._evaluate(
            "F",
            buy("B1", "F", datetime.date(2025, 12, 1), 50.0, 100.0),
            sell("S1", "F", datetime.date(2025, 12, 20), 40.0, 100.0),
            buy("B2", "F", datetime.date(2025, 12, 28), 42.0, 100.0),
            sell("S2", "F", datetime.date(2025, 12, 30), 41.0, 100.0),
        )

        self.assertEqual(summary.total_realized_gross_pnl_usd, -2100.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 1000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -1100.0)
        self.assertEqual(len(summary.wash_matches), 1)

    # ------------------------------------------- Treas. Reg. 1.1091-1 mechanics

    def test_same_acquisition_shares_are_not_their_own_replacement(self):
        # Buy 200 @ 50 in one lot (Jun 1), sell 100 @ 40 (Jun 12), keep 100.
        # The retained shares came from the same acquisition as the shares sold,
        # so they were not bought to replace them: no wash sale.
        summary = self._evaluate(
            "KO",
            buy("B1", "KO", datetime.date(2025, 6, 1), 50.0, 200.0),
            sell("S1", "KO", datetime.date(2025, 6, 12), 40.0, 100.0),
        )
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 0.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -1000.0)

    def test_separate_same_day_acquisition_is_replacement_stock(self):
        # Two separate purchases the same day; the morning lot is sold at a loss
        # while the afternoon lot is held. Unlike a single 200-share lot, the
        # second acquisition is replacement stock.
        summary = self._evaluate(
            "KO",
            buy("B1", "KO", datetime.date(2025, 6, 1), 50.0, 100.0),
            buy("B2", "KO", datetime.date(2025, 6, 1), 45.0, 100.0),
            sell("S1", "KO", datetime.date(2025, 6, 12), 40.0, 100.0),
        )
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 1000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, 0.0)
        # 1091(d): 45 + (50 - 40) = 55.
        self.assertEqual(summary.wash_matches[0].adjusted_replacement_basis_per_share, 55.0)

    def test_replacement_capacity_is_not_reused_across_two_losses(self):
        # Treas. Reg. 1.1091-1(e): an acquisition that has already made one loss
        # nondeductible is disregarded for any other loss.
        #   Buy B1 100 @ 60 (Jan 1), buy B2 100 @ 55 (Jan 2), sell 200 @ 40 (Jan 5)
        #   -> losses of 2,000 (B1) and 1,500 (B2), nothing held afterwards.
        #   Buy B3 100 @ 41 (Jan 6): 100 replacement shares for 200 loss shares.
        # Regulation 1.1091-1(b) applies losses in disposition order and (c)
        # matches the earliest acquisition first, so the single 100-share
        # replacement absorbs the B1 loss slice: 100 * 20 = 2,000 disallowed.
        # The B2 slice finds no remaining capacity: 1,500 stays deductible.
        summary = self._evaluate(
            "GM",
            buy("B1", "GM", datetime.date(2025, 1, 1), 60.0, 100.0),
            buy("B2", "GM", datetime.date(2025, 1, 2), 55.0, 100.0),
            sell("S1", "GM", datetime.date(2025, 1, 5), 40.0, 200.0),
            buy("B3", "GM", datetime.date(2025, 1, 6), 41.0, 100.0),
        )

        self.assertEqual(summary.total_realized_gross_pnl_usd, -3500.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 2000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -1500.0)
        self.assertEqual(len(summary.wash_matches), 1)
        self.assertEqual(summary.wash_matches[0].matched_quantity, 100.0)
        # 1091(d): 41 + 20 = 61.
        self.assertEqual(summary.wash_matches[0].adjusted_replacement_basis_per_share, 61.0)

    def test_held_but_already_adjusted_shares_are_not_offered_twice(self):
        # Regression: replacement capacity must be capped at the shares that are
        # both still held AND have not already absorbed a loss. Capping on total
        # held quantity alone offers the same 20 shares to a second loss slice.
        #   B1 Feb 15, 20 @ 60 (45 days before the sale -- outside the window)
        #   B2 Mar 2,  50 @ 50 (30 days before -- inside)
        #   B3 Mar 2,  40 @ 85 (inside)
        #   S1 Apr 1,  sell 90 @ 40
        # FIFO consumes B1 (20), B2 (50) and 20 of B3, leaving 20 B3 shares held.
        # Loss slices: 20 @ 20/sh, 50 @ 10/sh, 20 @ 45/sh -> gross -1,800.
        # Slice 1 takes the 20 held B3 shares: 20 * 20 = 400 disallowed, basis 105.
        # Slice 2 finds nothing -- B2 is gone and B3's held shares are used up.
        # Slice 3 originates in B3 itself. Total disallowed 400, net -1,400,
        # and the 400 is still sitting in the 20 open shares.
        summary = self._evaluate(
            "XOM",
            buy("B1", "XOM", datetime.date(2025, 2, 15), 60.0, 20.0),
            buy("B2", "XOM", datetime.date(2025, 3, 2), 50.0, 50.0),
            buy("B3", "XOM", datetime.date(2025, 3, 2), 85.0, 40.0),
            sell("S1", "XOM", datetime.date(2025, 4, 1), 40.0, 90.0),
        )

        self.assertEqual(summary.total_realized_gross_pnl_usd, -1800.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 400.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -1400.0)
        self.assertEqual(summary.deferred_loss_in_open_lots_usd, 400.0)
        self.assertEqual(len(summary.wash_matches), 1)
        self.assertEqual(summary.wash_matches[0].replacement_trade_id, "B3")
        self.assertEqual(summary.wash_matches[0].matched_quantity, 20.0)
        self.assertEqual(summary.wash_matches[0].adjusted_replacement_basis_per_share, 105.0)

    def test_only_the_matched_portion_of_a_larger_replacement_lot_is_adjusted(self):
        # Loss on 40 shares, replacement lot of 100. Only 40 shares take the
        # +10 adjustment; the other 60 keep basis 42.
        # Box 1e = 40*50 (first sale) + 40*52 + 60*42 = 2,000 + 2,080 + 2,520.
        summary = self._evaluate(
            "T",
            buy("B1", "T", datetime.date(2025, 1, 1), 50.0, 40.0),
            sell("S1", "T", datetime.date(2025, 1, 10), 40.0, 40.0),
            buy("B2", "T", datetime.date(2025, 1, 15), 42.0, 100.0),
            sell("S2", "T", datetime.date(2025, 6, 1), 42.0, 100.0),
        )

        self.assertEqual(summary.total_proceeds_usd, 5800.0)
        self.assertEqual(summary.total_cost_basis_usd, 6600.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 400.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -400.0)

    def test_gain_disposition_is_never_a_wash_sale(self):
        summary = self._evaluate(
            "V",
            buy("B1", "V", datetime.date(2025, 1, 1), 50.0, 100.0),
            sell("S1", "V", datetime.date(2025, 1, 10), 60.0, 100.0),
            buy("B2", "V", datetime.date(2025, 1, 12), 55.0, 100.0),
        )
        self.assertEqual(summary.total_realized_gross_pnl_usd, 1000.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 0.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, 1000.0)

    def test_deferred_loss_is_reported_for_a_position_open_at_year_end(self):
        summary = self._evaluate(
            "PG",
            buy("B1", "PG", datetime.date(2025, 12, 1), 50.0, 100.0),
            sell("S1", "PG", datetime.date(2025, 12, 20), 40.0, 100.0),
            buy("B2", "PG", datetime.date(2025, 12, 28), 42.0, 100.0),
        )
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 1000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, 0.0)
        # The 1,000 is sitting in the basis of the 100 shares still held.
        self.assertEqual(summary.deferred_loss_in_open_lots_usd, 1000.0)

    def test_1099b_box_identity_holds(self):
        summary = self._evaluate(
            "QQQ",
            buy("B1", "QQQ", datetime.date(2025, 2, 3), 300.0, 50.0),
            sell("S1", "QQQ", datetime.date(2025, 2, 20), 280.0, 50.0),
            buy("B2", "QQQ", datetime.date(2025, 2, 25), 285.0, 30.0),
        )
        # Net taxable = Box 1d - Box 1e + Box 1g.
        self.assertAlmostEqual(
            summary.net_allowed_taxable_pnl_usd,
            summary.total_proceeds_usd
            - summary.total_cost_basis_usd
            + summary.total_disallowed_wash_loss_usd,
            places=2,
        )
        # 20/share loss on 50 shares, 30 replaced: 30 * 20 = 600 disallowed.
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 600.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -400.0)

    def test_symbols_are_evaluated_independently(self):
        self.engine.add_trade(buy("A1", "AAPL", datetime.date(2025, 1, 1), 50.0, 100.0))
        self.engine.add_trade(sell("A2", "AAPL", datetime.date(2025, 1, 10), 40.0, 100.0))
        self.engine.add_trade(buy("M1", "MSFT", datetime.date(2025, 1, 12), 42.0, 100.0))

        aapl = self.engine.evaluate_wash_sales_for_symbol("AAPL")
        self.assertEqual(aapl.total_disallowed_wash_loss_usd, 0.0)
        self.assertEqual(aapl.net_allowed_taxable_pnl_usd, -1000.0)

        msft = self.engine.evaluate_wash_sales_for_symbol("MSFT")
        self.assertEqual(msft.total_realized_gross_pnl_usd, 0.0)

    def test_evaluation_is_repeatable(self):
        trades = (
            buy("B1", "AAPL", datetime.date(2025, 1, 1), 50.0, 100.0),
            sell("S1", "AAPL", datetime.date(2025, 1, 10), 40.0, 100.0),
            buy("B2", "AAPL", datetime.date(2025, 1, 15), 42.0, 100.0),
        )
        first = self._evaluate("AAPL", *trades)
        second = self.engine.evaluate_wash_sales_for_symbol("AAPL")
        self.assertEqual(first, second)

    def test_unknown_symbol_returns_empty_summary(self):
        summary = self.engine.evaluate_wash_sales_for_symbol("NOPE")
        self.assertIsInstance(summary, WashSaleSummary)
        self.assertEqual(summary.total_realized_gross_pnl_usd, 0.0)
        self.assertEqual(summary.wash_matches, [])

    def test_trades_may_be_added_out_of_date_order(self):
        summary = self._evaluate(
            "AAPL",
            buy("B2", "AAPL", datetime.date(2025, 1, 15), 42.0, 100.0),
            sell("S1", "AAPL", datetime.date(2025, 1, 10), 40.0, 100.0),
            buy("B1", "AAPL", datetime.date(2025, 1, 1), 50.0, 100.0),
        )
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 1000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, 0.0)

    def test_fractional_share_quantities_close_out_cleanly(self):
        summary = self._evaluate(
            "AAPL",
            buy("B1", "AAPL", datetime.date(2025, 1, 1), 100.0, 0.3),
            buy("B2", "AAPL", datetime.date(2025, 1, 2), 100.0, 0.3),
            buy("B3", "AAPL", datetime.date(2025, 1, 3), 100.0, 0.4),
            sell("S1", "AAPL", datetime.date(2025, 1, 20), 90.0, 1.0),
        )
        # Every share is disposed of and nothing is repurchased: no wash sale.
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 0.0)
        self.assertAlmostEqual(summary.net_allowed_taxable_pnl_usd, -10.0, places=2)

    def test_deferral_identity_holds_over_randomized_ledgers(self):
        # Property: net_allowed - deferred == realized P&L computed FIFO from
        # unadjusted purchase prices. Every disallowed dollar is either still in
        # the basis of an open lot (deferred) or has already flowed through a
        # later disposition, so the two must reconcile to the economic result.
        # Both original calculation defects break this identity, as does letting
        # a held-but-already-adjusted share serve as replacement twice.
        rng = random.Random(20250901)

        for _ in range(400):
            trades = []
            position = 0.0
            day = datetime.date(2025, 1, 1)
            for i in range(rng.randint(2, 14)):
                day += datetime.timedelta(days=rng.choice([0, 1, 3, 7, 15, 29, 30, 31, 45]))
                price = round(rng.uniform(10.0, 100.0), 2)
                if position <= 0 or rng.random() < 0.5:
                    qty = float(rng.randint(1, 5) * 10)
                    trades.append(buy(f"T{i}", "X", day, price, qty))
                    position += qty
                else:
                    qty = float(rng.randint(1, int(position // 10)) * 10)
                    trades.append(sell(f"T{i}", "X", day, price, qty))
                    position -= qty

            engine = USWashSaleTrackingEngine()
            for trade in trades:
                engine.add_trade(trade)
            summary = engine.evaluate_wash_sales_for_symbol("X")

            self.assertGreaterEqual(summary.total_disallowed_wash_loss_usd, 0.0)
            self.assertGreaterEqual(summary.deferred_loss_in_open_lots_usd, 0.0)
            self.assertLessEqual(
                summary.deferred_loss_in_open_lots_usd,
                summary.total_disallowed_wash_loss_usd + 1e-6,
            )
            self.assertAlmostEqual(
                summary.net_allowed_taxable_pnl_usd - summary.deferred_loss_in_open_lots_usd,
                _unadjusted_fifo_pnl(trades),
                delta=0.02,
                msg=f"deferral identity broken for {[(t.trade_id, t.trade_date, t.side.value, t.price, t.quantity) for t in trades]}",
            )

    # ------------------------------------------------------- input validation

    def test_invalid_trade_inputs_raise_error(self):
        with self.assertRaises(WashSaleError):
            self.engine.add_trade(buy("ERR", "AAPL", datetime.date(2025, 1, 1), -10.0, 100.0))
        with self.assertRaises(WashSaleError):
            self.engine.add_trade(buy("ERR", "AAPL", datetime.date(2025, 1, 1), 10.0, 0.0))
        with self.assertRaises(WashSaleError):
            self.engine.add_trade(buy("", "AAPL", datetime.date(2025, 1, 1), 10.0, 1.0))
        with self.assertRaises(WashSaleError):
            self.engine.add_trade(buy("ERR", "", datetime.date(2025, 1, 1), 10.0, 1.0))
        self.assertEqual(self.engine.trades, [])

    def test_datetime_trade_date_is_rejected(self):
        # datetime.datetime subclasses date; accepting it would make the +/-30 day
        # comparison depend on a time-of-day component.
        with self.assertRaises(WashSaleError):
            self.engine.add_trade(
                buy("D1", "AAPL", datetime.datetime(2025, 1, 1, 9, 30), 10.0, 1.0)
            )

    def test_non_tradeside_side_is_rejected(self):
        with self.assertRaises(WashSaleError):
            self.engine.add_trade(
                TradeExecution("X1", "AAPL", datetime.date(2025, 1, 1), "BUY", 10.0, 1.0)
            )

    def test_duplicate_trade_id_is_rejected(self):
        self.engine.add_trade(buy("T1", "AAPL", datetime.date(2025, 1, 1), 10.0, 1.0))
        with self.assertRaises(WashSaleError):
            self.engine.add_trade(buy("T1", "AAPL", datetime.date(2025, 1, 2), 11.0, 1.0))
        with self.assertRaises(WashSaleError):
            # Also across symbols: replacement capacity and pending adjustments
            # are keyed by trade_id alone.
            self.engine.add_trade(buy("T1", "MSFT", datetime.date(2025, 1, 2), 11.0, 1.0))

    def test_sell_exceeding_open_quantity_raises(self):
        # Section 1091(e) governs short sales and is not modelled here, so an
        # unmatched sell is reported rather than silently dropped.
        self.engine.add_trade(buy("B1", "AAPL", datetime.date(2025, 1, 1), 50.0, 100.0))
        self.engine.add_trade(sell("S1", "AAPL", datetime.date(2025, 1, 10), 40.0, 150.0))
        with self.assertRaises(WashSaleError):
            self.engine.evaluate_wash_sales_for_symbol("AAPL")

    def test_sell_with_no_open_lots_raises(self):
        self.engine.add_trade(sell("S1", "AAPL", datetime.date(2025, 1, 10), 40.0, 100.0))
        with self.assertRaises(WashSaleError):
            self.engine.evaluate_wash_sales_for_symbol("AAPL")

    def test_invalid_window_days_rejected(self):
        for bad in (-1, 30.0, True, "30"):
            with self.assertRaises(WashSaleError):
                USWashSaleTrackingEngine(window_days=bad)

    def test_non_statutory_window_is_permitted_but_warned(self):
        with self.assertLogs("wash_sale_rule_tracking_us", level="WARNING") as logs:
            engine = USWashSaleTrackingEngine(window_days=5)
        self.assertEqual(engine.window_days, 5)
        self.assertTrue(any("1091(a)" in line for line in logs.output))

    def test_statutory_window_constant(self):
        self.assertEqual(IRC_1091_WINDOW_DAYS, 30)
        self.assertEqual(USWashSaleTrackingEngine().window_days, 30)

    def test_invalid_symbol_argument_rejected(self):
        with self.assertRaises(WashSaleError):
            self.engine.evaluate_wash_sales_for_symbol("")


if __name__ == "__main__":
    unittest.main()
