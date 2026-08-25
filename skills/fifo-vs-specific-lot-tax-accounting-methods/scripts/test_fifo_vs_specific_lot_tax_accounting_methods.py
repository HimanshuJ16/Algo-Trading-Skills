import unittest
from datetime import date

from fifo_vs_specific_lot_tax_accounting_methods import (
    OpenTaxLot,
    TaxLotAccountingEngine,
    holding_period_days,
    is_long_term,
    one_year_anniversary,
    parse_trade_date,
)

# Identification record standing in for a broker confirmation or standing
# instruction. Any non-FIFO election needs one.
ID_REF = "BROKER-CONF-2026-0001"


class TestHoldingPeriodClassification(unittest.TestCase):
    """The 'more than one year' boundary, computed by calendar anniversary.

    Expected values here come from the IRS rule (holding period begins the day
    after acquisition, includes the day of disposition; long-term is more than
    one year), not from re-running the module's own arithmetic.
    """

    def test_sale_on_one_year_anniversary_is_short_term(self):
        # Bought 2025-03-10, holding period starts 2025-03-11. A sale on
        # 2026-03-10 is exactly one year -> short-term.
        self.assertFalse(is_long_term(date(2025, 3, 10), date(2026, 3, 10)))

    def test_sale_day_after_anniversary_is_long_term(self):
        self.assertTrue(is_long_term(date(2025, 3, 10), date(2026, 3, 11)))

    def test_leap_year_366_elapsed_days_is_still_only_one_year(self):
        # Regression: bought 2024-01-01, sold 2025-01-01 spans a leap year, so
        # 366 days elapse -- but it is one year to the day, which is SHORT-term.
        # A `days_held > 365` test calls this long-term. It is not.
        self.assertEqual(holding_period_days(date(2024, 1, 1), date(2025, 1, 1)), 366)
        self.assertFalse(is_long_term(date(2024, 1, 1), date(2025, 1, 1)))
        self.assertTrue(is_long_term(date(2024, 1, 1), date(2025, 1, 2)))

    def test_non_leap_year_365_elapsed_days_is_one_year(self):
        self.assertEqual(holding_period_days(date(2025, 1, 1), date(2026, 1, 1)), 365)
        self.assertFalse(is_long_term(date(2025, 1, 1), date(2026, 1, 1)))

    def test_end_of_month_anniversary(self):
        # Rev. Rul. 66-7 counts by calendar months: acquired 2025-04-30, the
        # period runs from 2025-05-01, so 2026-04-30 is exactly one year.
        self.assertFalse(is_long_term(date(2025, 4, 30), date(2026, 4, 30)))
        self.assertTrue(is_long_term(date(2025, 4, 30), date(2026, 5, 1)))

    def test_leap_day_acquisition_resolves_to_march_1(self):
        # February 29 has no anniversary in a common year; the module takes the
        # later (conservative) boundary and documents the uncertainty.
        self.assertEqual(one_year_anniversary(date(2024, 2, 29)), date(2025, 3, 1))
        self.assertFalse(is_long_term(date(2024, 2, 29), date(2025, 3, 1)))
        self.assertTrue(is_long_term(date(2024, 2, 29), date(2025, 3, 2)))

    def test_same_day_sale_is_short_term(self):
        self.assertFalse(is_long_term(date(2026, 5, 1), date(2026, 5, 1)))


class TestTradeDateParsing(unittest.TestCase):

    def test_accepts_date_and_datetime_forms(self):
        self.assertEqual(parse_trade_date("2026-05-01"), date(2026, 5, 1))
        self.assertEqual(parse_trade_date("2026-05-01T14:30:00"), date(2026, 5, 1))
        self.assertEqual(parse_trade_date("2026-05-01 14:30:00Z"), date(2026, 5, 1))

    def test_rejects_unparseable_and_non_string(self):
        with self.assertRaises(ValueError):
            parse_trade_date("01/05/2026")
        with self.assertRaises(ValueError):
            parse_trade_date(20260501)


class TestTaxLotAccountingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = TaxLotAccountingEngine()
        # Sale date for every case below is 2026-07-24.
        # Lot A: 100 sh @ $100.00, acquired 2025-06-01 -> held > 1 yr  -> LTCG
        # Lot B: 100 sh @ $150.00, acquired 2026-04-15 -> held < 1 yr  -> STCG
        # Lot C: 100 sh @ $120.00, acquired 2026-06-05 -> held < 1 yr  -> STCG
        self.sale_date = "2026-07-24"
        self.open_lots = [
            OpenTaxLot("LOT_A", "AAPL", "2025-06-01", 100, 100.0),
            OpenTaxLot("LOT_B", "AAPL", "2026-04-15", 100, 150.0),
            OpenTaxLot("LOT_C", "AAPL", "2026-06-05", 100, 120.0),
        ]

    # --- Method selection -------------------------------------------------

    def test_fifo_matches_oldest_lot(self):
        # 100 sh @ $140 against LOT_A's $100 basis = +$4,000, long-term.
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=100, sale_price=140.0, sale_date=self.sale_date
        )
        self.assertEqual(report.matching_strategy_used, "FIFO")
        self.assertEqual(report.matched_lots[0].lot_id, "LOT_A")
        self.assertEqual(report.total_realized_gain_loss_usd, 4000.0)
        self.assertEqual(report.total_ltcg_gain_loss_usd, 4000.0)
        self.assertEqual(report.total_stcg_gain_loss_usd, 0.0)
        self.assertEqual(report.total_sale_proceeds_usd, 14000.0)
        self.assertEqual(report.total_cost_basis_usd, 10000.0)
        self.assertFalse(report.is_mixed_term)

    def test_fifo_needs_no_identification_reference(self):
        # FIFO is the treatment absent an adequate identification, so it must
        # never be gated on one.
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=100, sale_price=140.0, sale_date=self.sale_date,
            strategy="FIFO",
        )
        self.assertIsNone(report.identification_reference)

    def test_lifo_matches_newest_lot(self):
        # LOT_C is newest: 100 sh @ $140 vs $120 basis = +$2,000, short-term.
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=100, sale_price=140.0, sale_date=self.sale_date,
            strategy="LIFO", identification_reference=ID_REF,
        )
        self.assertEqual(report.matched_lots[0].lot_id, "LOT_C")
        self.assertEqual(report.total_realized_gain_loss_usd, 2000.0)
        self.assertEqual(report.total_stcg_gain_loss_usd, 2000.0)

    def test_hifo_matches_highest_basis_and_harvests_a_loss(self):
        # LOT_B has the highest basis: 100 sh @ $140 vs $150 = -$1,000, short-term.
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=100, sale_price=140.0, sale_date=self.sale_date,
            strategy="HIFO", identification_reference=ID_REF,
        )
        self.assertEqual(report.matched_lots[0].lot_id, "LOT_B")
        self.assertEqual(report.total_realized_gain_loss_usd, -1000.0)
        self.assertEqual(report.total_stcg_gain_loss_usd, -1000.0)

    def test_specific_lot_matches_only_the_designated_lot(self):
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=100, sale_price=140.0, sale_date=self.sale_date,
            strategy="SPECIFIC_LOT", target_lot_ids=["LOT_C"],
            identification_reference=ID_REF,
        )
        self.assertEqual([m.lot_id for m in report.matched_lots], ["LOT_C"])
        self.assertEqual(report.total_realized_gain_loss_usd, 2000.0)
        self.assertEqual(report.identification_reference, ID_REF)

    def test_specific_lot_honours_designation_order(self):
        # Designating B before A must deliver B first, whatever the dates or bases.
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=150, sale_price=140.0, sale_date=self.sale_date,
            strategy="SPECIFIC_LOT", target_lot_ids=["LOT_B", "LOT_A"],
            identification_reference=ID_REF,
        )
        self.assertEqual(
            [(m.lot_id, m.shares_matched) for m in report.matched_lots],
            [("LOT_B", 100.0), ("LOT_A", 50.0)],
        )
        # -$1,000 short-term on B, +$2,000 long-term on 50 sh of A.
        self.assertEqual(report.total_stcg_gain_loss_usd, -1000.0)
        self.assertEqual(report.total_ltcg_gain_loss_usd, 2000.0)
        self.assertEqual(report.total_realized_gain_loss_usd, 1000.0)

    def test_unsupported_strategy_raises_rather_than_defaulting(self):
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                self.open_lots, sale_qty=100, sale_price=140.0,
                sale_date=self.sale_date, strategy="AVERAGE_COST",
            )

    # --- Specific identification is a regulatory gate ----------------------

    def test_non_fifo_election_without_identification_reference_raises(self):
        # Regression: HIFO/LIFO/SPECIFIC_LOT all depart from the FIFO default and
        # are elections of specific identification. Producing a favourable basis
        # with no identification record is a figure the taxpayer cannot support.
        for strategy, targets in (
            ("HIFO", None), ("LIFO", None), ("SPECIFIC_LOT", ["LOT_C"]),
        ):
            with self.subTest(strategy=strategy):
                with self.assertRaises(ValueError) as ctx:
                    self.engine.process_sell_order(
                        self.open_lots, sale_qty=100, sale_price=140.0,
                        sale_date=self.sale_date, strategy=strategy,
                        target_lot_ids=targets,
                    )
                self.assertIn("identification_reference", str(ctx.exception))

    def test_specific_lot_never_spills_into_undesignated_lots(self):
        # Regression: designating 100 shares and selling 250 previously consumed
        # LOT_A and LOT_B silently -- delivering shares never identified.
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_sell_order(
                self.open_lots, sale_qty=250, sale_price=140.0,
                sale_date=self.sale_date, strategy="SPECIFIC_LOT",
                target_lot_ids=["LOT_C"], identification_reference=ID_REF,
            )
        self.assertIn("SPECIFIC_LOT designation covers only", str(ctx.exception))

    def test_specific_lot_rejects_unknown_or_repeated_designation(self):
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                self.open_lots, sale_qty=100, sale_price=140.0,
                sale_date=self.sale_date, strategy="SPECIFIC_LOT",
                target_lot_ids=["LOT_Z"], identification_reference=ID_REF,
            )
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                self.open_lots, sale_qty=100, sale_price=140.0,
                sale_date=self.sale_date, strategy="SPECIFIC_LOT",
                target_lot_ids=["LOT_C", "LOT_C"], identification_reference=ID_REF,
            )

    def test_specific_lot_without_targets_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                self.open_lots, sale_qty=100, sale_price=140.0,
                sale_date=self.sale_date, strategy="SPECIFIC_LOT",
                identification_reference=ID_REF,
            )

    # --- Term classification through the engine ----------------------------

    def test_leap_year_boundary_classified_by_anniversary_not_day_count(self):
        # Regression: bought 2024-01-01, sold 2025-01-01 -> 366 elapsed days but
        # exactly one year, so STCG. The previous `holding_period_days > 365`
        # rule reported LTCG here.
        lots = [OpenTaxLot("L1", "AAPL", "2024-01-01", 100, 100.0)]
        report = self.engine.process_sell_order(
            lots, sale_qty=100, sale_price=140.0, sale_date="2025-01-01"
        )
        self.assertEqual(report.matched_lots[0].capital_gain_type, "STCG")
        self.assertEqual(report.matched_lots[0].holding_period_days, 366)
        self.assertEqual(report.total_stcg_gain_loss_usd, 4000.0)

        report = self.engine.process_sell_order(
            lots, sale_qty=100, sale_price=140.0, sale_date="2025-01-02"
        )
        self.assertEqual(report.matched_lots[0].capital_gain_type, "LTCG")

    def test_mixed_term_sale_is_flagged_and_split(self):
        # 150 sh FIFO: 100 from LOT_A (long-term, +$4,000) and 50 from LOT_B
        # (short-term, 50 x ($140-$150) = -$500).
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=150, sale_price=140.0, sale_date=self.sale_date
        )
        self.assertTrue(report.is_mixed_term)
        self.assertEqual(report.total_ltcg_gain_loss_usd, 4000.0)
        self.assertEqual(report.total_stcg_gain_loss_usd, -500.0)
        self.assertEqual(report.total_realized_gain_loss_usd, 3500.0)
        self.assertEqual(len(report.matched_lots), 2)

    def test_split_always_reconciles_to_the_total(self):
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=250, sale_price=137.77, sale_date=self.sale_date
        )
        self.assertAlmostEqual(
            report.total_realized_gain_loss_usd,
            report.total_stcg_gain_loss_usd + report.total_ltcg_gain_loss_usd,
            places=2,
        )

    def test_per_lot_rows_carry_their_own_dates_and_amounts(self):
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=150, sale_price=140.0, sale_date=self.sale_date
        )
        first = report.matched_lots[0]
        self.assertEqual(first.acquisition_date_iso, "2025-06-01")
        self.assertEqual(first.sale_date_iso, self.sale_date)
        self.assertEqual(first.cost_basis_usd, 10000.0)
        self.assertEqual(first.proceeds_usd, 14000.0)
        self.assertEqual(first.realized_gain_loss_usd, 4000.0)

    # --- Ordering correctness ----------------------------------------------

    def test_fifo_orders_by_parsed_date_not_string_comparison(self):
        # Regression: "2024-10-05" sorts before "2024-9-01" lexicographically,
        # so a string sort matched the NEWER lot first under FIFO.
        lots = [
            OpenTaxLot("L_SEP", "AAPL", "2024-9-01", 100, 100.0),
            OpenTaxLot("L_OCT", "AAPL", "2024-10-05", 100, 150.0),
        ]
        report = self.engine.process_sell_order(
            lots, sale_qty=100, sale_price=140.0, sale_date="2026-01-05"
        )
        self.assertEqual(report.matched_lots[0].lot_id, "L_SEP")

    def test_matching_is_deterministic_across_repeated_calls(self):
        first = self.engine.process_sell_order(
            self.open_lots, sale_qty=150, sale_price=140.0, sale_date=self.sale_date,
            strategy="HIFO", identification_reference=ID_REF,
        )
        second = self.engine.process_sell_order(
            self.open_lots, sale_qty=150, sale_price=140.0, sale_date=self.sale_date,
            strategy="HIFO", identification_reference=ID_REF,
        )
        self.assertEqual(
            [(m.lot_id, m.shares_matched) for m in first.matched_lots],
            [(m.lot_id, m.shares_matched) for m in second.matched_lots],
        )

    # --- Inventory integrity ------------------------------------------------

    def test_caller_lots_are_not_mutated(self):
        self.engine.process_sell_order(
            self.open_lots, sale_qty=250, sale_price=140.0, sale_date=self.sale_date
        )
        self.assertEqual([lot.quantity for lot in self.open_lots], [100, 100, 100])

    def test_remaining_open_lots_reflect_partial_depletion(self):
        report = self.engine.process_sell_order(
            self.open_lots, sale_qty=150, sale_price=140.0, sale_date=self.sale_date
        )
        remaining = {lot.lot_id: lot.quantity for lot in report.remaining_open_lots}
        self.assertNotIn("LOT_A", remaining)          # fully depleted
        self.assertEqual(remaining["LOT_B"], 50.0)    # partially depleted
        self.assertEqual(remaining["LOT_C"], 100.0)   # untouched

    def test_insufficient_inventory_raises_without_consuming_anything(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_sell_order(
                self.open_lots, sale_qty=500, sale_price=140.0, sale_date=self.sale_date
            )
        self.assertIn("Insufficient open tax lot quantity", str(ctx.exception))
        self.assertEqual([lot.quantity for lot in self.open_lots], [100, 100, 100])

    def test_mixed_symbols_raise(self):
        # Regression: a sale of AAPL previously consumed TSLA basis and reported
        # the whole thing under AAPL.
        lots = [
            OpenTaxLot("L1", "AAPL", "2025-01-01", 50, 100.0),
            OpenTaxLot("L2", "TSLA", "2025-02-01", 50, 200.0),
        ]
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_sell_order(
                lots, sale_qty=100, sale_price=140.0, sale_date=self.sale_date
            )
        self.assertIn("multiple symbols", str(ctx.exception))

    def test_duplicate_lot_ids_raise(self):
        lots = [
            OpenTaxLot("L1", "AAPL", "2025-01-01", 50, 100.0),
            OpenTaxLot("L1", "AAPL", "2025-02-01", 50, 200.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                lots, sale_qty=50, sale_price=140.0, sale_date=self.sale_date
            )

    def test_lot_acquired_after_the_sale_raises(self):
        # Regression: with no sale date the engine could not detect this, so a
        # lot bought after the sale could supply its basis.
        lots = [OpenTaxLot("L_FUTURE", "AAPL", "2026-09-01", 100, 100.0)]
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_sell_order(
                lots, sale_qty=100, sale_price=140.0, sale_date=self.sale_date
            )
        self.assertIn("after the sale date", str(ctx.exception))

    # --- Input validation ---------------------------------------------------

    def test_zero_proceeds_disposition_is_permitted(self):
        # A worthless-security or zero-proceeds disposition realizes the full
        # basis as a loss; rejecting price 0 made that unrepresentable.
        lots = [OpenTaxLot("L1", "AAPL", "2025-06-01", 100, 100.0)]
        report = self.engine.process_sell_order(
            lots, sale_qty=100, sale_price=0.0, sale_date=self.sale_date
        )
        self.assertEqual(report.total_realized_gain_loss_usd, -10000.0)
        self.assertEqual(report.total_ltcg_gain_loss_usd, -10000.0)

    def test_rejects_invalid_quantities_prices_and_amounts(self):
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                self.open_lots, sale_qty=0, sale_price=140.0, sale_date=self.sale_date
            )
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                self.open_lots, sale_qty=-10, sale_price=140.0, sale_date=self.sale_date
            )
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                self.open_lots, sale_qty=100, sale_price=-1.0, sale_date=self.sale_date
            )
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                self.open_lots, sale_qty=float("nan"), sale_price=140.0,
                sale_date=self.sale_date,
            )
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                [], sale_qty=100, sale_price=140.0, sale_date=self.sale_date
            )

    def test_rejects_lot_with_non_positive_quantity_or_negative_basis(self):
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                [OpenTaxLot("L1", "AAPL", "2025-01-01", 0, 100.0)],
                sale_qty=1, sale_price=140.0, sale_date=self.sale_date,
            )
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                [OpenTaxLot("L1", "AAPL", "2025-01-01", 10, -100.0)],
                sale_qty=1, sale_price=140.0, sale_date=self.sale_date,
            )

    def test_rejects_unparseable_acquisition_date(self):
        with self.assertRaises(ValueError):
            self.engine.process_sell_order(
                [OpenTaxLot("L1", "AAPL", "06/01/2025", 100, 100.0)],
                sale_qty=100, sale_price=140.0, sale_date=self.sale_date,
            )

    def test_fractional_shares_are_supported(self):
        lots = [OpenTaxLot("L1", "AAPL", "2025-06-01", 2.5, 100.0)]
        report = self.engine.process_sell_order(
            lots, sale_qty=1.25, sale_price=140.0, sale_date=self.sale_date
        )
        self.assertEqual(report.total_realized_gain_loss_usd, 50.0)
        self.assertAlmostEqual(report.remaining_open_lots[0].quantity, 1.25)

    def test_engine_rejects_unsupported_default_strategy(self):
        with self.assertRaises(ValueError):
            TaxLotAccountingEngine(default_strategy="AVERAGE_COST")


if __name__ == '__main__':
    unittest.main()
