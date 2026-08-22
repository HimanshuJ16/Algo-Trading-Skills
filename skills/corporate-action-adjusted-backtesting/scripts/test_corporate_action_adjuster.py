import unittest
from datetime import date

from corporate_action_adjuster import (
    AdjustedBarData,
    BarData,
    CorporateActionAdjuster,
    CorporateActionError,
    CorporateActionEvent,
)


def bar(day, close, volume=1000.0):
    """Flat OHLC bar on 2025-01-<day>. Prices are equal so CAF effects are visible."""
    return BarData(date(2025, 1, day), close, close, close, close, volume)


class TestSplitAdjustment(unittest.TestCase):

    def test_2_for_1_split_halves_prior_prices_and_doubles_prior_volume(self):
        # Hand-derived: alpha = 1/2 = 0.5 applies to every bar before the ex-date.
        # Prices 100 -> 50; volume 1000 -> 2000 (same notional, new share basis).
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 3), "SPLIT", 2.0)]
        )
        bars = [bar(1, 100), bar(2, 100), bar(3, 50, 2000), bar(4, 50, 2000)]

        adj = adjuster.adjust_bars(bars)

        self.assertEqual([b.caf for b in adj], [0.5, 0.5, 1.0, 1.0])
        self.assertEqual([b.adj_close for b in adj], [50.0, 50.0, 50.0, 50.0])
        self.assertEqual([b.adj_volume for b in adj], [2000.0, 2000.0, 2000.0, 2000.0])

    def test_1_for_5_reverse_split_multiplies_prior_prices_by_five(self):
        # Hand-derived: alpha = 5.0. A $10 pre-split price is $50 on the new basis,
        # and 5000 shares of volume become 1000.
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 2), "REVERSE_SPLIT", 5.0)]
        )
        adj = adjuster.adjust_bars([bar(1, 10, 5000), bar(2, 50, 1000)])

        self.assertEqual(adj[0].caf, 5.0)
        self.assertEqual(adj[0].adj_close, 50.0)
        self.assertEqual(adj[0].adj_volume, 1000.0)
        self.assertEqual(adj[1].caf, 1.0)

    def test_split_ex_date_on_a_non_trading_day_is_still_applied(self):
        # REGRESSION: keying the factor to a matching bar dropped this event in
        # silence, leaving a 50% gap inside a series labelled "adjusted".
        # 2025-01-04 is a Saturday; the split is effective for the 2025-01-06 session.
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 4), "SPLIT", 2.0)]
        )
        adj = adjuster.adjust_bars([bar(1, 100), bar(6, 50)])

        self.assertEqual(adj[0].caf, 0.5)
        self.assertEqual(adj[0].adj_close, 50.0)
        self.assertEqual(adj[1].adj_close, 50.0)

    def test_duplicate_bar_dates_do_not_double_apply_the_factor(self):
        # REGRESSION: walking bars (rather than dates) compounded the split once
        # per duplicated bar, producing CAF 0.25 instead of 0.5.
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 2), "SPLIT", 2.0)]
        )
        with self.assertLogs("corporate_action_adjuster", level="WARNING") as logs:
            adj = adjuster.adjust_bars([bar(1, 100), bar(2, 50), bar(2, 50)])
        self.assertTrue(any("duplicate bar dates" in m for m in logs.output))

        self.assertEqual(adj[0].caf, 0.5)
        self.assertEqual(adj[0].adj_close, 50.0)


class TestDividendAdjustment(unittest.TestCase):

    def test_dividend_factor_uses_the_close_preceding_the_ex_date(self):
        # REGRESSION: using the ex-date close coupled the factor to that day's
        # market move. Here the stock also fell 100 -> 90 on the ex-date, so the
        # wrong convention gives 1 - 2/90 = 0.97778 and the right one 1 - 2/100.
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 2), "DIVIDEND", 2.0)]
        )
        adj = adjuster.adjust_bars([bar(1, 100), bar(2, 90)])

        self.assertAlmostEqual(adj[0].caf, 0.98, places=10)
        self.assertAlmostEqual(adj[0].adj_close, 98.0, places=6)
        self.assertEqual(adj[1].caf, 1.0)

    def test_matches_yahoo_finance_published_worked_example(self):
        # Independent expected value, not re-derived from this implementation:
        # Yahoo Finance documents a $0.08 dividend against a $24.96 prior close
        # giving a multiplier of (1 - 0.08/24.96) = 0.9968.
        # https://help.yahoo.com/kb/SLN28256.html
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 2), "DIVIDEND", 0.08)]
        )
        adj = adjuster.adjust_bars([bar(1, 24.96), bar(2, 24.88)])

        self.assertAlmostEqual(adj[0].caf, 0.9968, places=4)

    def test_cash_dividend_leaves_volume_untouched(self):
        # REGRESSION: folding the dividend factor into the volume factor inflated
        # historical share volume by the dividend yield (1000 -> 1020.41 here),
        # biasing every ADV liquidity check. A cash dividend changes no share count.
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 2), "DIVIDEND", 2.0)]
        )
        adj = adjuster.adjust_bars([bar(1, 100, 1000), bar(2, 98, 1000)])

        self.assertEqual(adj[0].volume_caf, 1.0)
        self.assertEqual(adj[0].adj_volume, 1000.0)
        self.assertAlmostEqual(adj[0].caf, 0.98, places=10)

    def test_dividend_at_or_above_reference_close_is_rejected(self):
        # Would drive the factor to <= 0, i.e. zero or negative adjusted prices.
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 2), "DIVIDEND", 150.0)]
        )
        with self.assertRaises(CorporateActionError):
            adjuster.adjust_bars([bar(1, 100), bar(2, 100)])

    def test_dividend_against_a_zero_reference_close_is_rejected(self):
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 2), "DIVIDEND", 1.0)]
        )
        with self.assertRaises(CorporateActionError):
            adjuster.adjust_bars([bar(1, 0.0), bar(2, 10.0)])


class TestCompoundingAndAnchoring(unittest.TestCase):

    def test_dividend_and_split_compound_multiplicatively(self):
        # Hand-derived. Dividend $1 ex 2025-01-03 against the 2025-01-02 close of
        # $50 -> 0.98. Split 2-for-1 ex 2025-01-05 -> 0.5.
        #   bars before 01-03 : 0.98 * 0.5 = 0.49   volume_caf 0.5
        #   bars 01-03..01-04 : 0.5                 volume_caf 0.5
        #   bars from 01-05   : 1.0                 volume_caf 1.0
        adjuster = CorporateActionAdjuster([
            CorporateActionEvent(date(2025, 1, 5), "SPLIT", 2.0),
            CorporateActionEvent(date(2025, 1, 3), "DIVIDEND", 1.0),
        ])
        bars = [bar(1, 50), bar(2, 50), bar(3, 49), bar(4, 49), bar(5, 24.5)]

        adj = adjuster.adjust_bars(bars)

        self.assertAlmostEqual(adj[0].caf, 0.49, places=10)
        self.assertAlmostEqual(adj[1].caf, 0.49, places=10)
        self.assertAlmostEqual(adj[2].caf, 0.5, places=10)
        self.assertAlmostEqual(adj[3].caf, 0.5, places=10)
        self.assertEqual(adj[4].caf, 1.0)
        self.assertEqual([b.volume_caf for b in adj], [0.5, 0.5, 0.5, 0.5, 1.0])
        # Adjusted series is continuous across both events: 50 * 0.49 == 24.5.
        self.assertAlmostEqual(adj[0].adj_close, 24.5, places=6)
        self.assertAlmostEqual(adj[2].adj_close, 24.5, places=6)
        self.assertEqual(adj[4].adj_close, 24.5)

    def test_latest_bar_is_always_anchored_at_caf_one(self):
        adjuster = CorporateActionAdjuster([
            CorporateActionEvent(date(2025, 1, 2), "SPLIT", 4.0),
            CorporateActionEvent(date(2025, 1, 3), "DIVIDEND", 0.5),
        ])
        adj = adjuster.adjust_bars([bar(1, 100), bar(2, 25), bar(3, 24.5)])

        self.assertEqual(adj[-1].caf, 1.0)
        self.assertEqual(adj[-1].volume_caf, 1.0)
        self.assertEqual(adj[-1].adj_close, adj[-1].raw_close)

    def test_event_after_the_last_bar_is_not_pre_applied(self):
        # Pre-applying a not-yet-occurred split would scale the whole sample and
        # break the "newest adjusted price == newest raw price" anchor.
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 9), "SPLIT", 2.0)]
        )
        adj = adjuster.adjust_bars([bar(1, 100), bar(2, 100)])

        self.assertEqual([b.caf for b in adj], [1.0, 1.0])

    def test_event_on_or_before_the_first_bar_multiplies_nothing(self):
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 1), "SPLIT", 2.0)]
        )
        adj = adjuster.adjust_bars([bar(1, 50), bar(2, 50)])

        self.assertEqual([b.caf for b in adj], [1.0, 1.0])

    def test_deeply_split_history_does_not_collapse_to_zero(self):
        # REGRESSION: rounding the CAF to six decimals destroyed it entirely once
        # the cumulative factor fell below 1e-6 (1e-8 here -> 0.0 -> zero prices,
        # and a zero divisor for volume).
        events = [
            CorporateActionEvent(date(2025, 1, day), "SPLIT", 100.0)
            for day in (2, 3, 4, 5)
        ]
        adj = CorporateActionAdjuster(events).adjust_bars(
            [bar(1, 1000.0), bar(2, 10.0), bar(3, 0.1), bar(4, 0.001), bar(5, 0.00001)]
        )

        self.assertAlmostEqual(adj[0].caf, 1e-8, places=12)
        self.assertAlmostEqual(adj[0].adj_close, 1e-5, places=9)
        self.assertGreater(adj[0].adj_close, 0.0)
        self.assertAlmostEqual(adj[0].adj_volume, 1000.0 / 1e-8, places=2)


class TestPointInTime(unittest.TestCase):

    def test_as_of_hides_events_that_had_not_gone_ex_yet(self):
        # Look-ahead guard: standing on 2025-01-01, the 01-03 split is unknown, so
        # the adjusted series must equal the raw series.
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 3), "SPLIT", 2.0)]
        )
        bars = [bar(1, 100), bar(2, 100), bar(3, 50)]

        pit = adjuster.adjust_bars(bars, as_of=date(2025, 1, 1))

        self.assertEqual(len(pit), 1)
        self.assertEqual(pit[0].caf, 1.0)
        self.assertEqual(pit[0].adj_close, 100.0)
        # The same adjuster, run without as_of, does apply the split.
        self.assertEqual(adjuster.adjust_bars(bars)[0].caf, 0.5)

    def test_as_of_on_the_ex_date_applies_the_event(self):
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 3), "SPLIT", 2.0)]
        )
        pit = adjuster.adjust_bars(
            [bar(1, 100), bar(2, 100), bar(3, 50)], as_of=date(2025, 1, 3)
        )

        self.assertEqual([b.caf for b in pit], [0.5, 0.5, 1.0])

    def test_compute_caf_series_accepts_as_of(self):
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 3), "SPLIT", 2.0)]
        )
        caf = adjuster.compute_caf_series([bar(1, 100), bar(3, 50)], as_of=date(2025, 1, 1))

        self.assertEqual(caf, {date(2025, 1, 1): 1.0})


class TestInputValidation(unittest.TestCase):

    def test_unknown_event_type_raises_instead_of_being_ignored(self):
        # REGRESSION: an unrecognised (or merely mis-cased) type silently
        # contributed a factor of 1.0, leaving the split gap in the series.
        with self.assertRaises(CorporateActionError):
            CorporateActionEvent(date(2025, 1, 1), "MERGER", 1.0)

    def test_event_type_is_case_normalised(self):
        self.assertEqual(
            CorporateActionEvent(date(2025, 1, 1), " split ", 2.0).event_type, "SPLIT"
        )

    def test_non_positive_split_ratio_raises(self):
        # REGRESSION: 0.0 raised ZeroDivisionError from deep inside the CAF loop;
        # a negative ratio flipped the sign of every historical price.
        for ratio in (0.0, -2.0):
            with self.subTest(ratio=ratio):
                with self.assertRaises(CorporateActionError):
                    CorporateActionEvent(date(2025, 1, 1), "SPLIT", ratio)

    def test_non_finite_values_raise(self):
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(CorporateActionError):
                    CorporateActionEvent(date(2025, 1, 1), "DIVIDEND", value)

    def test_non_date_ex_date_raises(self):
        with self.assertRaises(CorporateActionError):
            CorporateActionEvent("2025-01-01", "SPLIT", 2.0)

    def test_negative_and_non_finite_bar_fields_raise(self):
        with self.assertRaises(CorporateActionError):
            BarData(date(2025, 1, 1), 10, 10, 10, -1, 100)
        with self.assertRaises(CorporateActionError):
            BarData(date(2025, 1, 1), 10, 10, 10, 10, float("nan"))


class TestSeriesHandling(unittest.TestCase):

    def test_empty_series_returns_empty_results(self):
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 2), "SPLIT", 2.0)]
        )
        self.assertEqual(adjuster.compute_caf_series([]), {})
        self.assertEqual(adjuster.adjust_bars([]), [])

    def test_no_events_leaves_the_series_untouched(self):
        adj = CorporateActionAdjuster().adjust_bars([bar(1, 100), bar(2, 101)])

        self.assertEqual([b.caf for b in adj], [1.0, 1.0])
        self.assertEqual([b.adj_close for b in adj], [100.0, 101.0])
        self.assertEqual([b.adj_volume for b in adj], [1000.0, 1000.0])

    def test_unsorted_input_is_returned_in_date_order(self):
        adjuster = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 3), "SPLIT", 2.0)]
        )
        adj = adjuster.adjust_bars([bar(3, 50), bar(1, 100), bar(2, 100)])

        self.assertEqual([b.dt.day for b in adj], [1, 2, 3])
        self.assertEqual([b.caf for b in adj], [0.5, 0.5, 1.0])

    def test_raw_fields_survive_adjustment(self):
        adj = CorporateActionAdjuster(
            [CorporateActionEvent(date(2025, 1, 2), "SPLIT", 2.0)]
        ).adjust_bars([BarData(date(2025, 1, 1), 99, 101, 98, 100, 1000), bar(2, 50)])

        first = adj[0]
        self.assertIsInstance(first, AdjustedBarData)
        self.assertEqual(
            (first.raw_open, first.raw_high, first.raw_low, first.raw_close),
            (99.0, 101.0, 98.0, 100.0),
        )
        self.assertEqual(first.raw_volume, 1000.0)
        self.assertEqual(
            (first.adj_open, first.adj_high, first.adj_low, first.adj_close),
            (49.5, 50.5, 49.0, 50.0),
        )


if __name__ == "__main__":
    unittest.main()
