"""Unit tests for adverse-selection-measurement-for-passive-orders.

Test categories
---------------
* Core markout math: toxic buy (negative), profitable sell (positive), mixed
* No-lookahead: missing_pre_fill when data starts after fill; as-of mid;
  ``require_asof_mid`` enforced under BOTH bases
* Prevailing-mid semantics: the horizon mid is the last observation at or
  before fill_ts + h, never the nearest in time
* Truncation: horizons beyond data return None, recorded, not clamped
* Staleness: an over-age prevailing mid is refused, not used
* Verdict integrity: horizons with no data are excluded from the verdict and
  never reported as healthy flow
* Config validation: empty/duplicate/non-positive horizons, basis, etc.
* Fill validation: bad side, non-positive price/qty
* Market data validation: length mismatch, empty, non-finite, non-positive mid,
  unsorted/duplicate timestamps
* Weighting: quantity-weighted mean vs simple mean
* Basis: arrival_to_mid vs fill_to_mid
* Distribution stats: median/p25/p75/std present and correct
* API ergonomics: disabled engine, as_dict JSON, structured fields
"""
import json
import unittest

import numpy as np

from adverse_selection_measurement_for_passive_orders import (
    AdverseSelectionReport,
    BASIS_ARRIVAL_TO_MID,
    BASIS_FILL_TO_MID,
    HorizonStats,
    MarkoutConfig,
    MarkoutEngine,
    PassiveFill,
)


class TestCoreMarkoutMath(unittest.TestCase):
    def setUp(self):
        # Price falls steadily: T=0 ->100, T=1 ->99, T=5 ->98
        self.ts = [0.0, 1.0, 5.0]
        self.mids = [100.0, 99.0, 98.0]
        self.engine = MarkoutEngine(MarkoutConfig(horizons_sec=[1.0, 5.0]))

    def test_toxic_buy_order(self):
        fills = [PassiveFill("1", 0.0, "BUY", 100.0, 100)]
        report = self.engine.evaluate_fills(fills, self.ts, self.mids)
        self.assertTrue(report.is_toxic)
        self.assertEqual(report.average_markouts_bps[1.0], -100.0)
        self.assertEqual(report.average_markouts_bps[5.0], -200.0)
        self.assertEqual(report.fills_used, 1)
        self.assertEqual(report.missing_pre_fill, 0)

    def test_profitable_sell_order(self):
        fills = [PassiveFill("2", 0.0, "SELL", 100.0, 100)]
        report = self.engine.evaluate_fills(fills, self.ts, self.mids)
        self.assertFalse(report.is_toxic)
        self.assertAlmostEqual(report.average_markouts_bps[1.0], 101.01, places=1)
        self.assertAlmostEqual(report.average_markouts_bps[5.0], 204.08, places=1)

    def test_mixed_fills_toxicity_ratio(self):
        # One toxic buy + one profitable sell -> horizons net to ~0, not toxic.
        fills = [
            PassiveFill("a", 0.0, "BUY", 100.0, 100),
            PassiveFill("b", 0.0, "SELL", 100.0, 100),
        ]
        report = self.engine.evaluate_fills(fills, self.ts, self.mids)
        # Net mean ~ (+101-100)/2 = +0.5 at 1s -> positive, not toxic.
        self.assertFalse(report.is_toxic)
        self.assertEqual(report.fills_used, 2)

    def test_rising_market_buy_is_profitable(self):
        ts = [0.0, 1.0, 5.0]
        mids = [100.0, 101.0, 102.0]  # price rising
        fills = [PassiveFill("x", 0.0, "BUY", 100.0, 10)]
        report = self.engine.evaluate_fills(fills, ts, mids)
        self.assertEqual(report.average_markouts_bps[1.0], 100.0)  # +1%
        self.assertFalse(report.is_toxic)


class TestNoLookahead(unittest.TestCase):
    def test_fill_before_market_data_skipped(self):
        # Market data starts at t=10; a fill at t=0 has no as-of mid under
        # arrival_to_mid basis, and under fill_to_mid the forward horizons
        # would still need future mids — but the as-of guard flags it.
        ts = [10.0, 11.0, 15.0]
        mids = [100.0, 99.0, 98.0]
        engine = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], markout_basis=BASIS_ARRIVAL_TO_MID)
        )
        fills = [PassiveFill("1", 0.0, "BUY", 100.0, 10)]
        report = engine.evaluate_fills(fills, ts, mids)
        self.assertEqual(report.missing_pre_fill, 1)
        self.assertEqual(report.fills_used, 0)

    def test_asof_mid_uses_most_recent_pre_fill(self):
        # Fill at t=2.5; nearest as-of mid is the t=2 sample (mid=100),
        # not the t=3 sample (mid=101) which would be lookahead.
        ts = [0.0, 2.0, 3.0, 4.0]
        mids = [99.0, 100.0, 101.0, 102.0]
        engine = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], markout_basis=BASIS_ARRIVAL_TO_MID)
        )
        fills = [PassiveFill("1", 2.5, "BUY", 100.5, 10)]
        report = engine.evaluate_fills(fills, ts, mids)
        # future mid at t=3.5 -> prevailing (last at-or-before) is t=3 -> 101.
        # markout = (101/100 - 1)*10000 = +100 bps (ref=asof mid=100, not 100.5).
        self.assertAlmostEqual(report.average_markouts_bps[1.0], 100.0, places=4)


class TestTruncation(unittest.TestCase):
    def test_horizon_beyond_data_not_clamped(self):
        # Legacy engine clamped to last price; this one must record truncation.
        ts = [0.0, 1.0]
        mids = [100.0, 99.0]
        engine = MarkoutEngine(MarkoutConfig(horizons_sec=[1.0, 10.0]))
        fills = [PassiveFill("1", 0.0, "BUY", 100.0, 10)]
        report = engine.evaluate_fills(fills, ts, mids)
        # Horizon 1.0 lands at t=1 (mid 99) -> -100 bps.
        self.assertEqual(report.average_markouts_bps[1.0], -100.0)
        # Horizon 10.0 is beyond data (t=10 > t=1) -> truncated, mean stays 0.
        self.assertEqual(report.average_markouts_bps[10.0], 0.0)
        self.assertEqual(report.stats[10.0].truncated, 1)
        self.assertEqual(report.stats[10.0].count, 0)

    def test_partial_truncation_mixed(self):
        ts = [0.0, 1.0, 2.0]
        mids = [100.0, 99.0, 98.0]
        engine = MarkoutEngine(MarkoutConfig(horizons_sec=[5.0]))
        fills = [
            PassiveFill("a", 0.0, "BUY", 100.0, 10),  # t=5 > t=2 -> truncated
            PassiveFill("b", 0.0, "BUY", 100.0, 10),  # also truncated
        ]
        report = engine.evaluate_fills(fills, ts, mids)
        self.assertEqual(report.stats[5.0].truncated, 2)
        self.assertEqual(report.stats[5.0].count, 0)
        self.assertEqual(report.fills_used, 0)


class TestConfigValidation(unittest.TestCase):
    def test_empty_horizons_raises(self):
        with self.assertRaises(ValueError):
            MarkoutConfig(horizons_sec=[])

    def test_duplicate_horizons_raises(self):
        with self.assertRaises(ValueError):
            MarkoutConfig(horizons_sec=[1.0, 1.0])

    def test_non_positive_horizon_raises(self):
        with self.assertRaises(ValueError):
            MarkoutConfig(horizons_sec=[0.0, 1.0])
        with self.assertRaises(ValueError):
            MarkoutConfig(horizons_sec=[-1.0])

    def test_invalid_basis_raises(self):
        with self.assertRaises(ValueError):
            MarkoutConfig(markout_basis="trade_to_trade")

    def test_horizons_sorted(self):
        cfg = MarkoutConfig(horizons_sec=[5.0, 0.1, 1.0])
        self.assertEqual(cfg.horizons_sec, [0.1, 1.0, 5.0])


class TestFillValidation(unittest.TestCase):
    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            PassiveFill("1", 0.0, "UNKNOWN", 100.0, 10)

    def test_non_positive_price_raises(self):
        with self.assertRaises(ValueError):
            PassiveFill("1", 0.0, "BUY", 0.0, 10)
        with self.assertRaises(ValueError):
            PassiveFill("1", 0.0, "BUY", -5.0, 10)

    def test_non_positive_quantity_raises(self):
        with self.assertRaises(ValueError):
            PassiveFill("1", 0.0, "BUY", 100.0, 0.0)

    def test_side_case_normalized(self):
        f = PassiveFill("1", 0.0, "buy", 100.0, 10)
        self.assertEqual(f.side, "BUY")


class TestMarketDataValidation(unittest.TestCase):
    def test_length_mismatch_raises(self):
        engine = MarkoutEngine(MarkoutConfig())
        with self.assertRaises(ValueError):
            engine.evaluate_fills([], [0.0, 1.0], [100.0])

    def test_empty_market_data_raises(self):
        engine = MarkoutEngine(MarkoutConfig())
        with self.assertRaises(ValueError):
            engine.evaluate_fills([], [], [])

    def test_unsorted_timestamps_raises(self):
        engine = MarkoutEngine(MarkoutConfig())
        with self.assertRaises(ValueError):
            engine.evaluate_fills([], [0.0, 0.0, 1.0], [100.0, 99.0, 98.0])

    def test_non_positive_mid_raises(self):
        engine = MarkoutEngine(MarkoutConfig())
        with self.assertRaises(ValueError):
            engine.evaluate_fills([], [0.0, 1.0], [100.0, 0.0])

    def test_descending_timestamps_raises(self):
        engine = MarkoutEngine(MarkoutConfig())
        with self.assertRaises(ValueError):
            engine.evaluate_fills([], [2.0, 1.0, 0.0], [100.0, 99.0, 98.0])


class TestQuantityWeighting(unittest.TestCase):
    def test_quantity_weighted_mean(self):
        # Two buys at t=0; small qty at -100 bps, large qty at -200 bps.
        ts = [0.0, 1.0, 2.0]
        mids = [100.0, 99.0, 98.0]
        unweighted = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], quantity_weighted=False)
        )
        weighted = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], quantity_weighted=True)
        )
        fills = [
            PassiveFill("a", 0.0, "BUY", 100.0, 1),    # qty=1 -> -100 bps
            PassiveFill("b", 0.0, "BUY", 100.0, 99),   # qty=99 -> -100 bps too
        ]
        # Both fills give the same -100 bps here (same price path), so any
        # convex weighting must return exactly -100 bps. Asserting the value
        # (not merely equality of the two runs) keeps this a real assertion:
        # equality alone would hold even if the weighting path were removed.
        r_u = unweighted.evaluate_fills(fills, ts, mids)
        r_w = weighted.evaluate_fills(fills, ts, mids)
        self.assertAlmostEqual(r_u.average_markouts_bps[1.0], -100.0, places=6)
        self.assertAlmostEqual(r_w.average_markouts_bps[1.0], -100.0, places=6)

    def test_weighting_changes_result_when_quantities_differ_across_prices(self):
        # Fill A buys at 100 (future 99 -> -100 bps), qty 1.
        # Fill B buys at 100  at a different time with a different future mid.
        # Construct so weighted mean differs from simple mean.
        ts = [0.0, 1.0, 2.0, 3.0]
        mids = [100.0, 99.0, 100.0, 90.0]  # t=1 -> -100, t=3 -> -1000
        fills = [
            PassiveFill("a", 0.0, "BUY", 100.0, 1),     # horizon 1 -> mid 99 -> -100
            PassiveFill("b", 2.0, "BUY", 100.0, 99),   # horizon 1 -> mid 90 -> -1000
        ]
        unweighted = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], quantity_weighted=False)
        ).evaluate_fills(fills, ts, mids)
        weighted = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], quantity_weighted=True)
        ).evaluate_fills(fills, ts, mids)
        # simple mean = (-100 + -1000)/2 = -550
        self.assertAlmostEqual(unweighted.average_markouts_bps[1.0], -550.0, places=2)
        # weighted mean = (1*-100 + 99*-1000)/100 = -991.0
        self.assertAlmostEqual(weighted.average_markouts_bps[1.0], -991.0, places=1)


class TestBasisAndStats(unittest.TestCase):
    def test_arrival_basis_uses_asof_mid(self):
        # Fill at t=1.5 at price 100; as-of mid at t=1 is 100 (matches fill).
        # Future mid at t=2.5 -> prevailing (last at-or-before) is t=2 -> 101.
        ts = [0.0, 1.0, 2.0, 3.0]
        mids = [99.0, 100.0, 101.0, 102.0]
        engine = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], markout_basis=BASIS_ARRIVAL_TO_MID)
        )
        fills = [PassiveFill("1", 1.5, "BUY", 100.0, 10)]
        report = engine.evaluate_fills(fills, ts, mids)
        # ref = asof mid 100; future = 101 -> +100 bps.
        self.assertAlmostEqual(report.average_markouts_bps[1.0], 100.0, places=4)
        self.assertEqual(report.markout_basis, BASIS_ARRIVAL_TO_MID)

    def test_distribution_stats_populated(self):
        ts = [0.0, 1.0]
        mids = [100.0, 99.0]
        engine = MarkoutEngine(MarkoutConfig(horizons_sec=[1.0]))
        fills = [PassiveFill(str(i), 0.0, "BUY", 100.0, 10) for i in range(10)]
        report = engine.evaluate_fills(fills, ts, mids)
        s = report.stats[1.0]
        self.assertIsInstance(s, HorizonStats)
        self.assertEqual(s.count, 10)
        self.assertEqual(s.mean_bps, -100.0)
        self.assertEqual(s.median_bps, -100.0)
        self.assertEqual(s.std_bps, 0.0)
        self.assertEqual(s.truncated, 0)


class TestApiErgonomics(unittest.TestCase):
    def test_disabled_engine_returns_empty(self):
        engine = MarkoutEngine(MarkoutConfig(enabled=False))
        report = engine.evaluate_fills(
            [PassiveFill("1", 0.0, "BUY", 100.0, 10)], [0.0, 1.0], [100.0, 99.0]
        )
        self.assertFalse(report.is_toxic)
        self.assertEqual(report.fills_used, 0)
        self.assertIn("disabled", report.message.lower())

    def test_empty_fills_returns_empty(self):
        engine = MarkoutEngine(MarkoutConfig())
        report = engine.evaluate_fills([], [0.0, 1.0], [100.0, 99.0])
        self.assertEqual(report.total_fills_analyzed, 0)
        self.assertFalse(report.is_toxic)

    def test_report_as_dict_json_serializable(self):
        ts = [0.0, 1.0, 5.0]
        mids = [100.0, 99.0, 98.0]
        report = MarkoutEngine(MarkoutConfig(horizons_sec=[1.0, 5.0])).evaluate_fills(
            [PassiveFill("1", 0.0, "BUY", 100.0, 10)], ts, mids
        )
        d = report.as_dict()
        json.dumps(d)
        self.assertIn("stats", d)
        self.assertIn("1.0", d["stats"])
        self.assertTrue(d["is_toxic"])

    def test_toxicity_ratio_reflects_negative_horizons(self):
        ts = [0.0, 1.0, 5.0]
        mids = [100.0, 99.0, 98.0]
        report = MarkoutEngine(MarkoutConfig(horizons_sec=[1.0, 5.0])).evaluate_fills(
            [PassiveFill("1", 0.0, "BUY", 100.0, 10)], ts, mids
        )
        self.assertEqual(report.toxicity_ratio, 1.0)  # both horizons negative


class TestAsofGuardAppliesToBothBases(unittest.TestCase):
    """Regression: ``require_asof_mid`` was declared and documented but never
    read by the engine, so under the default ``fill_to_mid`` basis a fill that
    preceded the whole market-data window was scored against an arbitrarily
    later mid and ``missing_pre_fill`` stayed 0."""

    # Market data starts 10 s AFTER the fill.
    TS = [10.0, 11.0, 15.0]
    MIDS = [100.0, 99.0, 98.0]

    def test_fill_to_mid_skips_fill_before_market_data(self):
        engine = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], markout_basis=BASIS_FILL_TO_MID)
        )
        report = engine.evaluate_fills(
            [PassiveFill("1", 0.0, "BUY", 100.0, 10)], self.TS, self.MIDS
        )
        # Old behaviour: missing_pre_fill=0, count=1, markout fabricated as 0 bps
        # from the t=10 mid — a "1 second" markout measured 10 s later.
        self.assertEqual(report.missing_pre_fill, 1)
        self.assertEqual(report.fills_used, 0)
        self.assertEqual(report.stats[1.0].count, 0)

    def test_guard_disabled_still_never_fabricates_a_mid(self):
        # With the guard off the fill is not skipped, but the horizon still has
        # no prevailing mid (t=1.0 precedes the window) -> truncated, not clamped.
        engine = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], require_asof_mid=False)
        )
        report = engine.evaluate_fills(
            [PassiveFill("1", 0.0, "BUY", 100.0, 10)], self.TS, self.MIDS
        )
        self.assertEqual(report.missing_pre_fill, 0)
        self.assertEqual(report.stats[1.0].truncated, 1)
        self.assertEqual(report.stats[1.0].count, 0)


class TestPrevailingMidSemantics(unittest.TestCase):
    """The horizon mid must be the last observation at or before fill_ts + h.

    Nearest-in-time snapping may resolve to an observation *after* the target,
    silently lengthening the effective horizon.
    """

    def test_horizon_uses_prevailing_not_nearest_mid(self):
        # Mid series jumps 100 -> 90 at t=1.9. The prevailing mid at the 1 s
        # horizon is still the t=0 observation (100), so the markout is 0 bps.
        # Nearest-in-time would pick t=1.9 (|1.9-1.0|=0.9 < |0.0-1.0|=1.0) and
        # report -1000 bps — a 1.9 s drift charged to a 1 s horizon.
        ts = [0.0, 1.9, 30.0]
        mids = [100.0, 90.0, 100.0]
        engine = MarkoutEngine(MarkoutConfig(horizons_sec=[1.0]))
        report = engine.evaluate_fills(
            [PassiveFill("1", 0.0, "BUY", 100.0, 10)], ts, mids
        )
        self.assertAlmostEqual(report.average_markouts_bps[1.0], 0.0, places=6)
        self.assertEqual(report.stats[1.0].count, 1)

    def test_short_and_long_horizons_stay_distinguishable(self):
        # Price is flat through 1 s then falls hard. A latency-arbitrage curve
        # (negative at 0.1 s) must not be manufactured out of the later drop.
        ts = [0.0, 0.5, 2.0, 5.0]
        mids = [100.0, 100.0, 80.0, 80.0]
        engine = MarkoutEngine(MarkoutConfig(horizons_sec=[0.1, 3.0]))
        report = engine.evaluate_fills(
            [PassiveFill("1", 0.0, "BUY", 100.0, 10)], ts, mids
        )
        self.assertAlmostEqual(report.average_markouts_bps[0.1], 0.0, places=6)
        self.assertAlmostEqual(report.average_markouts_bps[3.0], -2000.0, places=6)


class TestVerdictExcludesUnevaluableHorizons(unittest.TestCase):
    """Regression: a horizon that produced no markout used to count toward the
    verdict as if it were non-toxic, so missing data voted 'healthy'."""

    def test_all_horizons_truncated_is_not_reported_healthy(self):
        engine = MarkoutEngine(MarkoutConfig(horizons_sec=[5.0]))
        report = engine.evaluate_fills(
            [PassiveFill("a", 0.0, "BUY", 100.0, 10)], [0.0, 1.0, 2.0],
            [100.0, 99.0, 98.0],
        )
        self.assertEqual(report.evaluable_horizons, 0)
        self.assertFalse(report.has_sufficient_data)
        self.assertFalse(report.is_toxic)  # vacuous, not a healthy-flow finding
        self.assertIn("INSUFFICIENT DATA", report.message)
        self.assertNotIn("Healthy Flow", report.message)

    def test_truncated_horizon_does_not_dilute_toxicity_ratio(self):
        # Horizon 1.0 s is measured and negative; horizon 100 s has no data.
        # Old behaviour: 1/2 horizons negative -> ratio 0.5, is_toxic False.
        # A uniformly negative *measured* curve must read as toxic.
        engine = MarkoutEngine(MarkoutConfig(horizons_sec=[1.0, 100.0]))
        report = engine.evaluate_fills(
            [PassiveFill("a", 0.0, "BUY", 100.0, 10)], [0.0, 1.0, 2.0],
            [100.0, 99.0, 98.0],
        )
        self.assertEqual(report.evaluable_horizons, 1)
        self.assertEqual(report.toxicity_ratio, 1.0)
        self.assertTrue(report.is_toxic)
        self.assertEqual(report.stats[100.0].truncated, 1)

    def test_disabled_engine_reports_no_evaluable_horizons(self):
        engine = MarkoutEngine(MarkoutConfig(enabled=False))
        report = engine.evaluate_fills(
            [PassiveFill("1", 0.0, "BUY", 100.0, 10)], [0.0, 1.0], [100.0, 99.0]
        )
        self.assertEqual(report.evaluable_horizons, 0)
        self.assertFalse(report.has_sufficient_data)


class TestMidStaleness(unittest.TestCase):
    def test_stale_horizon_mid_refused_not_used(self):
        # Prevailing mid at the 1 s horizon is the t=0 observation, 1.0 s old.
        # A 0.5 s bound must refuse it rather than measure against it.
        engine = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], max_mid_staleness_sec=0.5)
        )
        report = engine.evaluate_fills(
            [PassiveFill("a", 0.0, "BUY", 100.0, 10)], [0.0, 1000.0], [100.0, 90.0]
        )
        self.assertEqual(report.stats[1.0].stale, 1)
        self.assertEqual(report.stats[1.0].truncated, 0)
        self.assertEqual(report.stats[1.0].count, 0)
        self.assertEqual(report.evaluable_horizons, 0)

    def test_staleness_bound_is_off_by_default(self):
        # Same data, no bound: the 1.0 s-old mid is used and the markout is 0 bps.
        engine = MarkoutEngine(MarkoutConfig(horizons_sec=[1.0]))
        report = engine.evaluate_fills(
            [PassiveFill("a", 0.0, "BUY", 100.0, 10)], [0.0, 1000.0], [100.0, 90.0]
        )
        self.assertEqual(report.stats[1.0].stale, 0)
        self.assertEqual(report.stats[1.0].count, 1)
        self.assertAlmostEqual(report.average_markouts_bps[1.0], 0.0, places=6)

    def test_stale_reference_mid_skips_fill_separately_from_missing(self):
        # Fill 60 s after the last quote: an as-of mid exists but is stale.
        engine = MarkoutEngine(
            MarkoutConfig(
                horizons_sec=[1.0],
                markout_basis=BASIS_ARRIVAL_TO_MID,
                max_mid_staleness_sec=5.0,
            )
        )
        report = engine.evaluate_fills(
            [PassiveFill("a", 60.0, "BUY", 100.0, 10)], [0.0, 1.0], [100.0, 99.0]
        )
        self.assertEqual(report.stale_asof_mid, 1)
        self.assertEqual(report.missing_pre_fill, 0)
        self.assertEqual(report.fills_used, 0)

    def test_fresh_mid_within_bound_is_used(self):
        engine = MarkoutEngine(
            MarkoutConfig(horizons_sec=[1.0], max_mid_staleness_sec=2.0)
        )
        report = engine.evaluate_fills(
            [PassiveFill("a", 0.0, "BUY", 100.0, 10)], [0.0, 1.0, 2.0],
            [100.0, 99.0, 98.0],
        )
        self.assertEqual(report.stats[1.0].stale, 0)
        self.assertAlmostEqual(report.average_markouts_bps[1.0], -100.0, places=6)

    def test_invalid_staleness_bound_raises(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                MarkoutConfig(max_mid_staleness_sec=bad)

    def test_none_staleness_bound_accepted(self):
        self.assertIsNone(MarkoutConfig().max_mid_staleness_sec)


class TestAdditionalInputValidation(unittest.TestCase):
    def test_non_string_side_raises_value_error(self):
        # Previously raised AttributeError from .upper() on a non-str.
        for bad in (None, 1, ["BUY"]):
            with self.assertRaises(ValueError):
                PassiveFill("1", 0.0, bad, 100.0, 10)

    def test_non_finite_timestamp_raises(self):
        with self.assertRaises(ValueError):
            PassiveFill("1", float("nan"), "BUY", 100.0, 10)

    def test_report_as_dict_exposes_data_integrity_fields(self):
        report = MarkoutEngine(MarkoutConfig(horizons_sec=[1.0, 100.0])).evaluate_fills(
            [PassiveFill("1", 0.0, "BUY", 100.0, 10)], [0.0, 1.0, 2.0],
            [100.0, 99.0, 98.0],
        )
        d = report.as_dict()
        json.dumps(d)
        self.assertEqual(d["evaluable_horizons"], 1)
        self.assertTrue(d["has_sufficient_data"])
        self.assertEqual(d["stale_asof_mid"], 0)
        self.assertEqual(d["stats"]["100.0"]["truncated"], 1)
        self.assertEqual(d["stats"]["100.0"]["stale"], 0)


if __name__ == "__main__":
    unittest.main()
