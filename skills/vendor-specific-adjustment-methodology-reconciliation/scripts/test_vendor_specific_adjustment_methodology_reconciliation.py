import datetime
import math
import unittest

from vendor_specific_adjustment_methodology_reconciliation import (
    AdjustmentValidationError,
    CorporateAction,
    CorporateActionType,
    PriceBar,
    ReconciliationError,
    VendorAdjustmentReconciliationEngine,
    VendorMethodology,
)


class TestVendorAdjustmentReconciliationEngine(unittest.TestCase):
    def setUp(self):
        self.engine = VendorAdjustmentReconciliationEngine()

        self.d1 = datetime.date(2025, 1, 10)
        self.d2 = datetime.date(2025, 1, 13)
        self.d3 = datetime.date(2025, 1, 14)  # Ex-date

        self.raw_bars = [
            PriceBar("AAPL", self.d1, 200.0, 205.0, 199.0, 200.0, 1_000_000.0),
            PriceBar("AAPL", self.d2, 200.0, 202.0, 198.0, 200.0, 1_200_000.0),
            PriceBar("AAPL", self.d3, 100.0, 103.0, 99.0, 100.0, 2_400_000.0),
        ]

    def _split(self, ex_date=None, ratio=2.0, event_id="CA-001"):
        return CorporateAction(
            event_id=event_id,
            symbol="AAPL",
            ex_date=ex_date or self.d3,
            action_type=CorporateActionType.STOCK_SPLIT,
            split_ratio=ratio,
        )

    def _dividend(self, amount=10.0, cum_price=200.0, ex_date=None, event_id="CA-002",
                  action_type=CorporateActionType.CASH_DIVIDEND):
        return CorporateAction(
            event_id=event_id,
            symbol="AAPL",
            ex_date=ex_date or self.d3,
            action_type=action_type,
            cash_amount=amount,
            cum_price=cum_price,
        )

    # ------------------------------------------------------------------ price factors

    def test_stock_split_adjustment_factor(self):
        # 2-for-1 stock split on d3: split_ratio is new shares per old share.
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars, [self._split()], VendorMethodology.CRSP_TOTAL_RETURN
        )

        # Before split (d1, d2): prices halved (200 -> 100), volume doubled (1M -> 2M).
        self.assertEqual(adj_bars[0].close, 100.0)
        self.assertEqual(adj_bars[0].volume, 2_000_000.0)
        # On/after split (d3): prices unadjusted (100 -> 100).
        self.assertEqual(adj_bars[2].close, 100.0)
        self.assertEqual(adj_bars[2].volume, 2_400_000.0)

    def test_reverse_split_scales_history_up_and_volume_down(self):
        # 1-for-10 reverse split: 10 old shares -> 1 new share, i.e. 0.1 new per old.
        # Independently derived: historical $200 print is comparable to $2000 post-split,
        # and 1,000,000 shares traded pre-split represent 100,000 post-split shares.
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars,
            [self._split(ratio=0.1)],
            VendorMethodology.CRSP_TOTAL_RETURN,
        )
        self.assertAlmostEqual(adj_bars[0].close, 2000.0, places=9)
        self.assertAlmostEqual(adj_bars[0].volume, 100_000.0, places=6)

    def test_cash_dividend_adjustment_factor(self):
        # $10 cash dividend on d3, cum_price = $200 -> factor = 1 - 10/200 = 0.95.
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars, [self._dividend()], VendorMethodology.CRSP_TOTAL_RETURN
        )

        self.assertAlmostEqual(adj_bars[0].close, 190.0, places=9)
        self.assertEqual(adj_bars[2].close, 100.0)

    def test_cash_dividend_does_not_rescale_volume(self):
        """Regression: volume must not move for a distribution that leaves shares intact.

        CRSP: "Shares and volumes are only adjusted using stock splits and stock
        dividends." The previous implementation derived the volume factor from the full
        price factor, inflating pre-dividend volume by 1/0.95 = 1.0526x.
        """
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars, [self._dividend()], VendorMethodology.CRSP_TOTAL_RETURN
        )
        self.assertEqual(adj_bars[0].volume, 1_000_000.0)
        self.assertEqual(adj_bars[1].volume, 1_200_000.0)
        self.assertEqual(adj_bars[2].volume, 2_400_000.0)

    def test_spin_off_adjusts_price_only(self):
        """Spin-off distributes assets, not shares: price factor moves, volume does not."""
        spin = self._dividend(
            amount=20.0, cum_price=200.0, event_id="CA-SPIN",
            action_type=CorporateActionType.SPIN_OFF,
        )
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars, [spin], VendorMethodology.CRSP_TOTAL_RETURN
        )
        # 1 - 20/200 = 0.90 -> 200 * 0.90 = 180.0
        self.assertAlmostEqual(adj_bars[0].close, 180.0, places=9)
        self.assertEqual(adj_bars[0].volume, 1_000_000.0)

    def test_multiple_actions_on_same_ex_date_are_multiplied(self):
        """Regression: a split and a dividend sharing an ex-date must both apply.

        Independently derived: 0.5 (2-for-1) x 0.95 (10/200 dividend) = 0.475,
        so the $200 pre-event close adjusts to $95.00, and volume doubles from the
        split alone.
        """
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars,
            [self._split(), self._dividend()],
            VendorMethodology.CRSP_TOTAL_RETURN,
        )
        self.assertAlmostEqual(adj_bars[0].close, 95.0, places=9)
        self.assertEqual(adj_bars[0].volume, 2_000_000.0)

    def test_multiple_cash_dividends_same_ex_date_are_summed(self):
        """Xignite/QUODD: same-ex-date ordinary cash dividends sum into one factor.

        1 - (6 + 4)/200 = 0.95, which differs from multiplying the two separate
        factors (0.97 x 0.98 = 0.9506).
        """
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars,
            [
                self._dividend(amount=6.0, event_id="D1"),
                self._dividend(amount=4.0, event_id="D2"),
            ],
            VendorMethodology.CRSP_TOTAL_RETURN,
        )
        self.assertAlmostEqual(adj_bars[0].close, 190.0, places=9)

    def test_ex_date_without_matching_bar_still_adjusts_history(self):
        """Regression: a holiday/gap ex-date must not silently drop the adjustment."""
        gap_ex_date = datetime.date(2025, 1, 12)  # No bar exists on this date.
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars,
            [self._split(ex_date=gap_ex_date)],
            VendorMethodology.CRSP_TOTAL_RETURN,
        )
        self.assertEqual(adj_bars[0].close, 100.0)   # d1 is before the ex-date
        self.assertEqual(adj_bars[1].close, 200.0)   # d2 is after the ex-date
        self.assertEqual(adj_bars[2].close, 100.0)

    def test_as_of_suppresses_not_yet_effective_actions(self):
        """An announced action whose ex-date has not arrived must not adjust history."""
        future_split = self._split(ex_date=datetime.date(2025, 2, 1))
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars,
            [future_split],
            VendorMethodology.CRSP_TOTAL_RETURN,
            as_of=self.d3,
        )
        self.assertEqual([b.close for b in adj_bars], [200.0, 200.0, 100.0])

    def test_cumulative_factors_compound_across_events(self):
        """Two events before the anchor compound: 0.5 x 0.95 = 0.475 for the oldest bar."""
        split = self._split(ex_date=self.d2)
        dividend = self._dividend(ex_date=self.d3, amount=10.0, cum_price=200.0)
        factors = self.engine.calculate_adjustment_factors(
            self.raw_bars, [split, dividend], VendorMethodology.CRSP_TOTAL_RETURN
        )
        self.assertAlmostEqual(factors[self.d1][0], 0.475, places=12)
        self.assertAlmostEqual(factors[self.d1][1], 2.0, places=12)   # split only
        self.assertAlmostEqual(factors[self.d2][0], 0.95, places=12)
        self.assertAlmostEqual(factors[self.d2][1], 1.0, places=12)
        self.assertEqual(factors[self.d3], (1.0, 1.0))

    # ------------------------------------------------------------------ methodologies

    def test_price_return_methodology_ignores_ordinary_cash_only(self):
        """Price return drops ordinary cash dividends but keeps splits and specials."""
        actions = [
            self._split(),
            self._dividend(amount=10.0, event_id="ORD"),
            self._dividend(
                amount=20.0, event_id="SPECIAL",
                action_type=CorporateActionType.SPECIAL_DIVIDEND,
            ),
        ]
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars, actions, VendorMethodology.SPLIT_ONLY_PRICE_RETURN
        )
        # 0.5 (split) x 0.90 (special) = 0.45; the $10 ordinary dividend is excluded.
        self.assertAlmostEqual(adj_bars[0].close, 90.0, places=9)

    def test_raw_unadjusted_methodology_is_identity(self):
        adj_bars = self.engine.adjust_price_series(
            self.raw_bars,
            [self._split(), self._dividend()],
            VendorMethodology.RAW_UNADJUSTED,
        )
        self.assertEqual([b.close for b in adj_bars], [200.0, 200.0, 100.0])
        self.assertEqual([b.volume for b in adj_bars], [1_000_000.0, 1_200_000.0, 2_400_000.0])

    def test_legacy_split_only_value_still_resolves(self):
        self.assertIs(
            VendorMethodology("SPLIT_ONLY"), VendorMethodology.SPLIT_ONLY_PRICE_RETURN
        )

    def test_total_return_and_price_return_diverge_on_dividends(self):
        """The reconciliation use case: mixing feeds produces a detectable divergence."""
        dividend = self._dividend()
        total_return = self.engine.adjust_price_series(
            self.raw_bars, [dividend], VendorMethodology.CRSP_TOTAL_RETURN
        )
        price_return = self.engine.adjust_price_series(
            self.raw_bars, [dividend], VendorMethodology.SPLIT_ONLY_PRICE_RETURN
        )
        report = self.engine.reconcile_vendor_series(
            "AAPL", total_return, "CRSP", price_return, "Price Return", tolerance_pct=0.5
        )
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(report.divergence_count, 2)  # d1 and d2, not the ex-date bar

    # ------------------------------------------------------------------ validation

    def test_dividend_not_less_than_cum_price_is_rejected(self):
        """Regression: 1 - D/P <= 0 previously produced negative adjusted prices."""
        with self.assertRaises(AdjustmentValidationError):
            self.engine.adjust_price_series(
                self.raw_bars,
                [self._dividend(amount=250.0, cum_price=200.0)],
                VendorMethodology.CRSP_TOTAL_RETURN,
            )

    def test_non_positive_split_ratio_is_rejected(self):
        with self.assertRaises(AdjustmentValidationError):
            self.engine.adjust_price_series(
                self.raw_bars, [self._split(ratio=0.0)], VendorMethodology.CRSP_TOTAL_RETURN
            )

    def test_missing_cum_price_for_distribution_is_rejected(self):
        with self.assertRaises(AdjustmentValidationError):
            self.engine.adjust_price_series(
                self.raw_bars,
                [self._dividend(amount=1.0, cum_price=0.0)],
                VendorMethodology.CRSP_TOTAL_RETURN,
            )

    def test_action_for_another_symbol_is_rejected(self):
        """Regression: a foreign-symbol action previously adjusted the series anyway."""
        foreign = CorporateAction(
            event_id="X", symbol="MSFT", ex_date=self.d3,
            action_type=CorporateActionType.STOCK_SPLIT, split_ratio=2.0,
        )
        with self.assertRaises(AdjustmentValidationError):
            self.engine.adjust_price_series(
                self.raw_bars, [foreign], VendorMethodology.CRSP_TOTAL_RETURN
            )

    def test_duplicate_bar_dates_are_rejected(self):
        dupes = self.raw_bars + [PriceBar("AAPL", self.d1, 1.0, 1.0, 1.0, 1.0, 1.0)]
        with self.assertRaises(AdjustmentValidationError):
            self.engine.adjust_price_series(dupes, [], VendorMethodology.CRSP_TOTAL_RETURN)

    def test_non_finite_bar_price_is_rejected(self):
        bad = list(self.raw_bars)
        bad[0] = PriceBar("AAPL", self.d1, 200.0, 205.0, 199.0, float("nan"), 1_000_000.0)
        with self.assertRaises(AdjustmentValidationError):
            self.engine.adjust_price_series(bad, [], VendorMethodology.CRSP_TOTAL_RETURN)

    def test_empty_bars_return_empty_factors(self):
        self.assertEqual(
            self.engine.calculate_adjustment_factors(
                [], [self._split()], VendorMethodology.CRSP_TOTAL_RETURN
            ),
            {},
        )

    def test_unsorted_input_is_normalized(self):
        shuffled = [self.raw_bars[2], self.raw_bars[0], self.raw_bars[1]]
        adj_bars = self.engine.adjust_price_series(
            shuffled, [self._split()], VendorMethodology.CRSP_TOTAL_RETURN
        )
        self.assertEqual([b.date for b in adj_bars], [self.d1, self.d2, self.d3])
        self.assertEqual(adj_bars[0].close, 100.0)

    def test_rounding_is_opt_in(self):
        """Default output is unrounded so long histories do not accumulate error."""
        dividend = self._dividend(amount=1.0, cum_price=3.0)  # factor = 2/3
        unrounded = self.engine.adjust_price_series(
            self.raw_bars, [dividend], VendorMethodology.CRSP_TOTAL_RETURN
        )
        rounded = self.engine.adjust_price_series(
            self.raw_bars, [dividend], VendorMethodology.CRSP_TOTAL_RETURN, price_decimals=4
        )
        self.assertAlmostEqual(unrounded[0].close, 200.0 * 2.0 / 3.0, places=12)
        self.assertEqual(rounded[0].close, round(200.0 * 2.0 / 3.0, 4))
        self.assertNotEqual(unrounded[0].close, rounded[0].close)

    # ------------------------------------------------------------------ reconciliation

    def test_reconcile_identical_series_passed(self):
        report = self.engine.reconcile_vendor_series(
            symbol="AAPL",
            series_a=self.raw_bars,
            vendor_a_name="Bloomberg",
            series_b=self.raw_bars,
            vendor_b_name="Refinitiv",
            tolerance_pct=0.5,
        )

        self.assertEqual(report.status, "PASSED")
        self.assertEqual(report.divergence_count, 0)
        self.assertEqual(report.max_divergence_pct, 0.0)
        self.assertEqual(report.coverage_pct, 100.0)

    def test_reconcile_divergent_vendor_series_failed(self):
        series_a = self.engine.adjust_price_series(
            self.raw_bars, [self._split()], VendorMethodology.CRSP_TOTAL_RETURN
        )
        report = self.engine.reconcile_vendor_series(
            symbol="AAPL",
            series_a=series_a,
            vendor_a_name="CRSP",
            series_b=self.raw_bars,
            vendor_b_name="Unadjusted Feed",
            tolerance_pct=0.5,
        )

        self.assertEqual(report.status, "FAILED")
        self.assertTrue(report.divergence_count > 0)
        # 100 vs 200 on a 150 mid = 66.67%.
        self.assertAlmostEqual(report.max_divergence_pct, 200.0 / 3.0, places=9)

    def test_non_finite_price_is_flagged_not_silently_passed(self):
        """Regression: nan > tolerance is False, so a nan close used to report PASSED."""
        series_a = [PriceBar("AAPL", self.d1, 1.0, 1.0, 1.0, float("nan"), 1.0)]
        series_b = [PriceBar("AAPL", self.d1, 200.0, 200.0, 200.0, 200.0, 1.0)]
        report = self.engine.reconcile_vendor_series(
            "AAPL", series_a, "A", series_b, "B"
        )
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(report.divergences[0].reason, "NON_FINITE_PRICE")
        self.assertTrue(math.isinf(report.max_divergence_pct))

    def test_partial_overlap_reports_coverage(self):
        """Regression: agreement on 1 of 3 dates used to report a clean PASS."""
        series_b = [self.raw_bars[0]]
        report = self.engine.reconcile_vendor_series(
            "AAPL", self.raw_bars, "A", series_b, "B"
        )
        self.assertEqual(report.total_bars_compared, 1)
        self.assertEqual(report.dates_only_in_a, 2)
        self.assertEqual(report.dates_only_in_b, 0)
        self.assertAlmostEqual(report.coverage_pct, 100.0 / 3.0, places=9)

    def test_min_coverage_threshold_fails_thin_overlap(self):
        series_b = [self.raw_bars[0]]
        report = self.engine.reconcile_vendor_series(
            "AAPL", self.raw_bars, "A", series_b, "B", min_coverage_pct=90.0
        )
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(report.divergence_count, 0)

    def test_negative_tolerance_is_rejected(self):
        with self.assertRaises(AdjustmentValidationError):
            self.engine.reconcile_vendor_series(
                "AAPL", self.raw_bars, "A", self.raw_bars, "B", tolerance_pct=-1.0
            )

    def test_duplicate_dates_in_reconciled_series_rejected(self):
        dupes = self.raw_bars + [self.raw_bars[0]]
        with self.assertRaises(AdjustmentValidationError):
            self.engine.reconcile_vendor_series("AAPL", dupes, "A", self.raw_bars, "B")

    def test_no_common_dates_raises_error(self):
        other_bars = [
            PriceBar("AAPL", datetime.date(2020, 1, 1), 10.0, 10.0, 10.0, 10.0, 100.0)
        ]
        with self.assertRaises(ReconciliationError):
            self.engine.reconcile_vendor_series(
                "AAPL", self.raw_bars, "Vendor A", other_bars, "Vendor B"
            )


if __name__ == "__main__":
    unittest.main()
