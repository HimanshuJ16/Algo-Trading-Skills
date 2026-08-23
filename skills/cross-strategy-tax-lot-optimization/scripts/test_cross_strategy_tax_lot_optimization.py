import unittest
from datetime import date

from cross_strategy_tax_lot_optimization import (
    CrossStrategyTaxLotOptimizer,
    StrategyOrder,
    TaxLot,
    is_long_term,
)


def _lot(lot_id, strategy, acq, days, basis, qty=100.0, symbol="AAPL"):
    return TaxLot(lot_id, strategy, symbol, acq, days_held=days,
                  cost_basis_per_share=basis, quantity=qty)


class TestTaxLotSelection(unittest.TestCase):
    """Lot ordering under each supported selection method."""

    def setUp(self):
        self.optimizer = CrossStrategyTaxLotOptimizer(wash_sale_window_days=30)
        # LOT_A: $150 basis, 400 days held (long-term), acquired first
        # LOT_B: $200 basis, 100 days held (short-term, highest basis)
        # LOT_C: $100 basis,  50 days held (short-term, lowest basis)
        self.optimizer.add_tax_lot(_lot("LOT_A", "StatArb", "2025-01-01", 400, 150.0))
        self.optimizer.add_tax_lot(_lot("LOT_B", "TrendFollow", "2025-10-01", 100, 200.0))
        self.optimizer.add_tax_lot(_lot("LOT_C", "AltData", "2025-12-01", 50, 100.0))

    def test_hifo_min_tax_selection(self):
        # HIFO takes the $200 lot: (180 - 200) * 100 = -$2,000
        res = self.optimizer.optimize_sell_order(
            "AAPL", sell_quantity=100.0, current_market_price=180.0, method="HIFO_MIN_TAX")

        self.assertEqual(len(res.executed_lots), 1)
        executed = res.executed_lots[0]
        self.assertEqual(executed.lot_id, "LOT_B")
        self.assertEqual(executed.realized_gain_loss_usd, -2000.0)
        self.assertFalse(executed.is_wash_sale_triggered)
        self.assertEqual(res.total_disallowed_loss_usd, 0.0)
        self.assertEqual(res.net_deductible_gain_loss_usd, -2000.0)

    def test_hifo_spans_multiple_lots_and_splits_st_lt(self):
        # 250 shares @ $180 under HIFO consumes B(100) -> A(100) -> C(50):
        #   B: (180-200)*100 = -2,000  short-term
        #   A: (180-150)*100 = +3,000  long-term
        #   C: (180-100)* 50 = +4,000  short-term
        res = self.optimizer.optimize_sell_order(
            "AAPL", sell_quantity=250.0, current_market_price=180.0, method="HIFO_MIN_TAX")

        self.assertEqual([e.lot_id for e in res.executed_lots], ["LOT_B", "LOT_A", "LOT_C"])
        self.assertEqual(res.executed_lots[2].shares_sold, 50.0)
        self.assertEqual(res.total_realized_gain_loss_usd, 5000.0)
        self.assertEqual(res.short_term_realized_usd, 2000.0)
        self.assertEqual(res.long_term_realized_usd, 3000.0)

    def test_ltcg_optimized_prefers_long_term_lot(self):
        # LOT_A is the only long-term lot, so it is taken before the higher-basis
        # short-term LOT_B: (180 - 150) * 100 = +$3,000 at the long-term rate.
        res = self.optimizer.optimize_sell_order(
            "AAPL", sell_quantity=100.0, current_market_price=180.0, method="LTCG_OPTIMIZED")

        self.assertEqual(res.executed_lots[0].lot_id, "LOT_A")
        self.assertTrue(res.executed_lots[0].is_long_term)
        self.assertEqual(res.long_term_realized_usd, 3000.0)
        self.assertEqual(res.short_term_realized_usd, 0.0)

    def test_fifo_uses_earliest_acquisition_date_not_days_held(self):
        # Treas. Reg. 1.1012-1(c)(1)(i) charges the sale against the earliest lot
        # ACQUIRED. Two lots carrying an identical days_held must still be
        # ordered by acquisition date, and insertion order must not decide it.
        opt = CrossStrategyTaxLotOptimizer()
        opt.add_tax_lot(_lot("LATER", "PodA", "2025-03-01", 100, 210.0))
        opt.add_tax_lot(_lot("EARLIER", "PodB", "2025-01-15", 100, 190.0))

        res = opt.optimize_sell_order(
            "AAPL", sell_quantity=100.0, current_market_price=180.0, method="FIFO")

        self.assertEqual(res.executed_lots[0].lot_id, "EARLIER")

    def test_unknown_method_raises_instead_of_silently_using_fifo(self):
        # Regression: a typo previously fell through to FIFO and produced a
        # different, silently wrong tax treatment.
        with self.assertRaises(ValueError):
            self.optimizer.optimize_sell_order(
                "AAPL", sell_quantity=10.0, current_market_price=180.0, method="HIFO")

    def test_method_is_case_insensitive(self):
        res = self.optimizer.optimize_sell_order(
            "AAPL", sell_quantity=10.0, current_market_price=180.0, method="hifo_min_tax")
        self.assertEqual(res.method, "HIFO_MIN_TAX")

    def test_dry_run_leaves_inventory_untouched(self):
        self.optimizer.optimize_sell_order(
            "AAPL", sell_quantity=100.0, current_market_price=180.0, dry_run=True)
        self.assertEqual([l.quantity for l in self.optimizer.tax_lots], [100.0, 100.0, 100.0])

        self.optimizer.optimize_sell_order("AAPL", sell_quantity=100.0, current_market_price=180.0)
        # LOT_B (index 1) is the HIFO pick and is the only lot depleted.
        self.assertEqual([l.quantity for l in self.optimizer.tax_lots], [100.0, 0.0, 100.0])

    def test_specific_identification_warning_is_emitted_for_non_fifo(self):
        res = self.optimizer.optimize_sell_order(
            "AAPL", sell_quantity=10.0, current_market_price=180.0, method="HIFO_MIN_TAX")
        self.assertTrue(any("1.1012-1(c)(8)" in w for w in res.warnings))

        fifo = self.optimizer.optimize_sell_order(
            "AAPL", sell_quantity=10.0, current_market_price=180.0, method="FIFO")
        self.assertFalse(any("1.1012-1(c)(8)" in w for w in fifo.warnings))


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.optimizer = CrossStrategyTaxLotOptimizer()
        self.optimizer.add_tax_lot(_lot("LOT_A", "StatArb", "2025-01-01", 400, 150.0))

    def test_insufficient_inventory_raises(self):
        # Regression: the sell used to be silently under-filled while the report
        # still echoed the full requested quantity.
        with self.assertRaises(ValueError):
            self.optimizer.optimize_sell_order(
                "AAPL", sell_quantity=500.0, current_market_price=180.0)

    def test_non_positive_quantity_and_price_raise(self):
        with self.assertRaises(ValueError):
            self.optimizer.optimize_sell_order("AAPL", 0.0, 180.0)
        with self.assertRaises(ValueError):
            self.optimizer.optimize_sell_order("AAPL", -10.0, 180.0)
        with self.assertRaises(ValueError):
            self.optimizer.optimize_sell_order("AAPL", 10.0, 0.0)
        with self.assertRaises(ValueError):
            self.optimizer.optimize_sell_order("AAPL", 10.0, -180.0)

    def test_unknown_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.optimizer.optimize_sell_order("MSFT", 10.0, 180.0)

    def test_malformed_lot_is_rejected_at_registration(self):
        with self.assertRaises(ValueError):
            self.optimizer.add_tax_lot(_lot("BAD_DATE", "P", "01/15/2025", 10, 100.0))
        with self.assertRaises(ValueError):
            self.optimizer.add_tax_lot(_lot("BAD_QTY", "P", "2025-01-15", 10, 100.0, qty=0.0))
        with self.assertRaises(ValueError):
            self.optimizer.add_tax_lot(_lot("BAD_BASIS", "P", "2025-01-15", 10, -1.0))

    def test_negative_days_ago_rejected(self):
        with self.assertRaises(ValueError):
            self.optimizer.register_recent_buy("AAPL", "PodX", days_ago=-5)

    def test_non_finite_values_are_rejected(self):
        # NaN passes every `< 0` guard, so without an explicit finiteness check it
        # propagates silently into realized PnL and the whole report reads NaN.
        nan, inf = float("nan"), float("inf")
        with self.assertRaises(ValueError):
            self.optimizer.add_tax_lot(_lot("NAN_BASIS", "P", "2025-01-15", 10, nan))
        with self.assertRaises(ValueError):
            self.optimizer.add_tax_lot(_lot("INF_QTY", "P", "2025-01-15", 10, 100.0, qty=inf))
        with self.assertRaises(ValueError):
            self.optimizer.register_replacement_purchase("AAPL", "P", 5, quantity=nan)
        with self.assertRaises(ValueError):
            self.optimizer.optimize_sell_order("AAPL", nan, 180.0)
        with self.assertRaises(ValueError):
            self.optimizer.optimize_sell_order("AAPL", 10.0, nan)

    def test_sale_date_before_acquisition_is_rejected(self):
        # A lot cannot be disposed of before it was acquired; accepting this
        # would let a mis-joined lot silently report as short-term.
        opt = CrossStrategyTaxLotOptimizer()
        opt.add_tax_lot(_lot("FUTURE", "PodA", "2026-01-01", 10, 200.0))
        with self.assertRaises(ValueError):
            opt.optimize_sell_order("AAPL", 100.0, 180.0, sale_date="2025-01-01")

    def test_depleted_lot_does_not_leave_floating_point_dust(self):
        # 0.1 + 0.2 - 0.3 == 5.55e-17, which would keep a fully-sold lot in the
        # `quantity > 0` pool forever as a phantom holding.
        opt = CrossStrategyTaxLotOptimizer()
        opt.add_tax_lot(_lot("DUST", "PodA", "2025-01-01", 10, 200.0, qty=0.1 + 0.2))
        opt.optimize_sell_order("AAPL", 0.3, 180.0)
        self.assertEqual(opt.tax_lots[0].quantity, 0.0)


class TestWashSaleInterception(unittest.TestCase):

    def setUp(self):
        self.optimizer = CrossStrategyTaxLotOptimizer(wash_sale_window_days=30)
        # A single $200-basis lot; selling at $180 realizes -$20/share.
        self.optimizer.add_tax_lot(_lot("LOT_B", "TrendFollow", "2025-10-01", 100, 200.0))

    def _sell(self):
        return self.optimizer.optimize_sell_order(
            "AAPL", sell_quantity=100.0, current_market_price=180.0, method="HIFO_MIN_TAX")

    def test_wash_sale_interception_prior_purchase(self):
        self.optimizer.register_recent_buy("AAPL", "Pod_Gamma", days_ago=10)
        res = self._sell()

        self.assertTrue(res.wash_sale_warning)
        self.assertTrue(res.executed_lots[0].is_wash_sale_triggered)

    def test_wash_sale_interception_subsequent_purchase(self):
        # Regression: IRC 1091(a) covers the 30 days AFTER the loss sale, but the
        # screen previously scanned backwards only and passed this case clean.
        self.optimizer.register_replacement_purchase(
            "AAPL", "Pod_Gamma", days_from_sale=25, quantity=100.0)
        res = self._sell()

        self.assertTrue(res.wash_sale_warning)
        self.assertEqual(res.total_disallowed_loss_usd, 2000.0)
        self.assertEqual(res.net_deductible_gain_loss_usd, 0.0)

    def test_same_day_purchase_is_inside_the_window(self):
        self.optimizer.register_replacement_purchase("AAPL", "Pod_Gamma", 0, quantity=100.0)
        self.assertTrue(self._sell().wash_sale_warning)

    def test_window_boundaries(self):
        # Day 30 either side is inside the 61-day window; day 31 is outside.
        for offset, expected in ((30, True), (-30, True), (31, False), (-31, False)):
            with self.subTest(offset=offset):
                opt = CrossStrategyTaxLotOptimizer(wash_sale_window_days=30)
                opt.add_tax_lot(_lot("LOT_B", "TrendFollow", "2025-10-01", 100, 200.0))
                opt.register_replacement_purchase("AAPL", "Pod", offset, quantity=100.0)
                res = opt.optimize_sell_order("AAPL", 100.0, 180.0)
                self.assertEqual(res.wash_sale_warning, expected)

    def test_partial_replacement_limits_the_disallowance(self):
        # IRC 1091(b): only 40 of the 100 loss shares are replaced, so only
        # 40 * $20 = $800 of the $2,000 loss is disallowed; $1,200 stays deductible.
        self.optimizer.register_replacement_purchase(
            "AAPL", "Pod_Gamma", days_from_sale=5, quantity=40.0)
        res = self._sell()

        executed = res.executed_lots[0]
        self.assertEqual(executed.wash_sale_matched_quantity, 40.0)
        self.assertEqual(executed.disallowed_loss_usd, 800.0)
        self.assertEqual(res.total_disallowed_loss_usd, 800.0)
        self.assertEqual(res.net_deductible_gain_loss_usd, -1200.0)

    def test_unknown_replacement_quantity_assumes_full_coverage(self):
        self.optimizer.register_replacement_purchase("AAPL", "Pod_Gamma", 5)
        res = self._sell()

        self.assertEqual(res.total_disallowed_loss_usd, 2000.0)
        self.assertTrue(any("upper bound" in w for w in res.warnings))

    def test_gain_lots_are_never_wash_sales(self):
        # A wash sale requires a realized LOSS; a profitable disposition inside
        # the window must not be flagged.
        opt = CrossStrategyTaxLotOptimizer()
        opt.add_tax_lot(_lot("GAIN", "PodA", "2025-10-01", 100, 100.0))
        opt.register_replacement_purchase("AAPL", "PodB", 3, quantity=100.0)
        res = opt.optimize_sell_order("AAPL", 100.0, 180.0)

        self.assertFalse(res.wash_sale_warning)
        self.assertFalse(res.executed_lots[0].is_wash_sale_triggered)
        self.assertEqual(res.total_disallowed_loss_usd, 0.0)

    def test_replacement_pool_is_consumed_across_lots(self):
        # 150 replacement shares against two 100-share loss lots: the first lot
        # is fully matched, the second only 50 shares.
        opt = CrossStrategyTaxLotOptimizer()
        opt.add_tax_lot(_lot("HI", "PodA", "2025-10-01", 100, 200.0))
        opt.add_tax_lot(_lot("LO", "PodB", "2025-11-01", 100, 190.0))
        opt.register_replacement_purchase("AAPL", "PodC", 2, quantity=150.0)

        res = opt.optimize_sell_order("AAPL", 200.0, 180.0, method="HIFO_MIN_TAX")

        self.assertEqual(res.executed_lots[0].wash_sale_matched_quantity, 100.0)
        self.assertEqual(res.executed_lots[1].wash_sale_matched_quantity, 50.0)
        # 100 * $20 + 50 * $10 = $2,500
        self.assertEqual(res.total_disallowed_loss_usd, 2500.0)

    def test_replacement_in_another_symbol_is_ignored(self):
        self.optimizer.register_replacement_purchase("MSFT", "Pod_Gamma", 5, quantity=100.0)
        self.assertFalse(self._sell().wash_sale_warning)


class TestHoldingPeriod(unittest.TestCase):

    def test_more_than_one_year_boundary(self):
        # Acquired 1 Jan; the holding period starts 2 Jan, so 1 Jan of the next
        # year is exactly one year -> still short-term. 2 Jan is long-term.
        self.assertFalse(is_long_term(date(2023, 1, 1), date(2024, 1, 1)))
        self.assertTrue(is_long_term(date(2023, 1, 1), date(2024, 1, 2)))

    def test_leap_day_acquisition_clamps_to_feb_28(self):
        self.assertFalse(is_long_term(date(2024, 2, 29), date(2025, 2, 28)))
        self.assertTrue(is_long_term(date(2024, 2, 29), date(2025, 3, 1)))

    def test_sale_date_overrides_the_leap_year_day_count_proxy(self):
        # 2024-01-01 -> 2025-01-01 is 366 calendar days because 2024 is a leap
        # year, so the days_held > 365 proxy says long-term. The statutory test
        # is "more than one year", which this lot fails.
        opt = CrossStrategyTaxLotOptimizer()
        opt.add_tax_lot(_lot("LEAP", "PodA", "2024-01-01", 366, 150.0))

        proxy = opt.optimize_sell_order("AAPL", 100.0, 180.0, dry_run=True)
        self.assertTrue(proxy.executed_lots[0].is_long_term)

        dated = opt.optimize_sell_order("AAPL", 100.0, 180.0, sale_date="2025-01-01")
        self.assertFalse(dated.executed_lots[0].is_long_term)
        self.assertEqual(dated.short_term_realized_usd, 3000.0)
        self.assertEqual(dated.long_term_realized_usd, 0.0)

    def test_malformed_sale_date_raises(self):
        opt = CrossStrategyTaxLotOptimizer()
        opt.add_tax_lot(_lot("LOT_A", "PodA", "2024-01-01", 366, 150.0))
        with self.assertRaises(ValueError):
            opt.optimize_sell_order("AAPL", 100.0, 180.0, sale_date="2025/01/01")


class TestCrossStrategyNetting(unittest.TestCase):

    def setUp(self):
        self.optimizer = CrossStrategyTaxLotOptimizer()

    def test_offsetting_orders_are_netted(self):
        res = self.optimizer.net_cross_strategy_orders([
            StrategyOrder("PodA", "AAPL", "SELL", 1000.0),
            StrategyOrder("PodB", "AAPL", "BUY", 600.0),
        ])

        self.assertEqual(res.gross_sell_quantity, 1000.0)
        self.assertEqual(res.gross_buy_quantity, 600.0)
        self.assertEqual(res.internally_crossed_quantity, 600.0)
        self.assertEqual(res.net_side, "SELL")
        self.assertEqual(res.net_quantity, 400.0)
        self.assertTrue(res.wash_sale_risk)
        self.assertTrue(any("does not cure a wash sale" in w for w in res.warnings))

    def test_fully_offsetting_orders_net_flat(self):
        res = self.optimizer.net_cross_strategy_orders([
            StrategyOrder("PodA", "AAPL", "SELL", 500.0),
            StrategyOrder("PodB", "AAPL", "BUY", 500.0),
        ])
        self.assertEqual(res.net_side, "FLAT")
        self.assertEqual(res.net_quantity, 0.0)
        self.assertEqual(res.internally_crossed_quantity, 500.0)

    def test_same_side_orders_produce_no_cross(self):
        res = self.optimizer.net_cross_strategy_orders([
            StrategyOrder("PodA", "aapl", "BUY", 300.0),
            StrategyOrder("PodB", "AAPL", "buy", 200.0),
        ])
        self.assertEqual(res.symbol, "AAPL")
        self.assertEqual(res.net_side, "BUY")
        self.assertEqual(res.net_quantity, 500.0)
        self.assertEqual(res.internally_crossed_quantity, 0.0)
        self.assertFalse(res.wash_sale_risk)
        self.assertEqual(res.warnings, [])

    def test_invalid_netting_inputs_raise(self):
        with self.assertRaises(ValueError):
            self.optimizer.net_cross_strategy_orders([])
        with self.assertRaises(ValueError):
            self.optimizer.net_cross_strategy_orders([
                StrategyOrder("PodA", "AAPL", "BUY", 100.0),
                StrategyOrder("PodB", "MSFT", "SELL", 100.0),
            ])
        with self.assertRaises(ValueError):
            self.optimizer.net_cross_strategy_orders(
                [StrategyOrder("PodA", "AAPL", "SHORT", 100.0)])
        with self.assertRaises(ValueError):
            self.optimizer.net_cross_strategy_orders(
                [StrategyOrder("PodA", "AAPL", "BUY", 0.0)])

    def test_netting_then_lot_selection_avoids_double_counting_losses(self):
        # PodA sells 1,000 and PodB buys 600 of the same name inside one tax
        # entity. Only the 400-share residual is a disposition; taxing the gross
        # 1,000 would overstate the harvested loss by 600 * $20 = $12,000.
        opt = CrossStrategyTaxLotOptimizer()
        opt.add_tax_lot(_lot("LOT_B", "PodA", "2025-10-01", 100, 200.0, qty=1000.0))

        netted = opt.net_cross_strategy_orders([
            StrategyOrder("PodA", "AAPL", "SELL", 1000.0),
            StrategyOrder("PodB", "AAPL", "BUY", 600.0),
        ])
        res = opt.optimize_sell_order("AAPL", netted.net_quantity, 180.0)

        self.assertEqual(res.requested_sell_quantity, 400.0)
        self.assertEqual(res.total_realized_gain_loss_usd, -8000.0)


if __name__ == '__main__':
    unittest.main()
