"""
Unit tests for the Triple-Barrier labeller.

Expected values are derived independently of the implementation wherever a number
is asserted: the volatility target is checked against an explicitly written
adjusted-EWM weighted standard deviation, and every realised return is recomputed
from the raw prices rather than from the engine's own arithmetic.
"""
import logging
import math
import unittest

import numpy as np
import pandas as pd

from triple_barrier_labeler import (
    VALID_SIDES,
    BarrierTouched,
    TripleBarrierError,
    TripleBarrierLabel,
    TripleBarrierLabelerEngine,
)

logging.disable(logging.CRITICAL)

# Eight alternating bars give the EWM estimator a defined, non-zero target before
# the bar under test. v1.0.0 needed no warm-up only because it substituted a
# fabricated 0.01 for every NaN estimate.
WARMUP = [100.0, 100.5, 100.0, 100.5, 100.0, 100.5, 100.0, 100.5]
EVENT_POS = len(WARMUP)  # the bar every behavioural test seeds its event on


def independent_ewm_std(prices, span, min_periods=2):
    """
    Adjusted exponentially weighted, unbiased standard deviation of log returns,
    written out longhand: for bar t the weight on a return at bar j is
    (1 - alpha)^(t - j) with alpha = 2 / (span + 1); the undefined first return
    occupies its position without contributing (pandas' ignore_na=False).
    """
    alpha = 2.0 / (span + 1.0)
    returns = [float("nan")] + [
        math.log(prices.iloc[i] / prices.iloc[i - 1]) for i in range(1, len(prices))
    ]
    out = []
    for t in range(len(returns)):
        weights, values = [], []
        for j in range(t + 1):
            if not math.isnan(returns[j]):
                weights.append((1.0 - alpha) ** (t - j))
                values.append(returns[j])
        if len(values) < min_periods:
            out.append(float("nan"))
            continue
        sum_w = sum(weights)
        sum_w2 = sum(w * w for w in weights)
        mean = sum(w * x for w, x in zip(weights, values)) / sum_w
        variance = sum(w * (x - mean) ** 2 for w, x in zip(weights, values)) / (sum_w - sum_w2 / sum_w)
        out.append(math.sqrt(variance))
    return out


def series(*tail):
    """Price series: the warm-up block followed by the bars under test."""
    return pd.Series(WARMUP + list(tail))


class TestVolatilityTarget(unittest.TestCase):
    def setUp(self):
        self.engine = TripleBarrierLabelerEngine(vertical_bars=4, volatility_span=10)
        self.prices = pd.Series(
            [100.0, 101.0, 100.5, 102.0, 101.0, 103.0, 102.5, 104.0, 103.0, 105.0, 104.0, 106.0]
        )

    def test_matches_independently_derived_weighted_std(self):
        got = self.engine.compute_volatility(self.prices).tolist()
        expected = independent_ewm_std(self.prices, span=10)
        self.assertEqual(len(got), len(expected))
        for i, (a, b) in enumerate(zip(got, expected)):
            if math.isnan(b):
                self.assertTrue(math.isnan(a), f"bar {i} should be NaN during warm-up")
            else:
                self.assertAlmostEqual(a, b, places=12, msg=f"bar {i}")

    def test_warmup_stays_nan_and_is_not_backfilled(self):
        # Regression: v1.0.0 ran .fillna(0.01), handing warm-up bars a fabricated
        # 1% barrier width that the caller had no way to see.
        vol = self.engine.compute_volatility(self.prices)
        self.assertTrue(math.isnan(vol.iloc[0]))
        self.assertTrue(math.isnan(vol.iloc[1]))
        self.assertFalse(math.isnan(vol.iloc[2]))

    def test_zero_volatility_on_a_flat_series(self):
        vol = self.engine.compute_volatility(pd.Series([100.0] * 12))
        self.assertEqual(vol.iloc[-1], 0.0)

    def test_horizon_scaling_multiplies_by_sqrt_of_horizon(self):
        base = TripleBarrierLabelerEngine(vertical_bars=9, volatility_span=10)
        scaled = TripleBarrierLabelerEngine(
            vertical_bars=9, volatility_span=10, scale_target_by_horizon=True
        )
        unscaled_vol = base.compute_volatility(self.prices).iloc[-1]
        scaled_vol = scaled.compute_volatility(self.prices).iloc[-1]
        self.assertAlmostEqual(scaled_vol, unscaled_vol * 3.0, places=12)

    def test_volatility_is_causal(self):
        # Appending future bars must not change any earlier estimate. A look-ahead
        # in the target would silently size a label's barriers on its own outcome.
        extended = pd.concat([self.prices, pd.Series([140.0, 60.0, 150.0])], ignore_index=True)
        original = self.engine.compute_volatility(self.prices)
        recomputed = self.engine.compute_volatility(extended).iloc[: len(self.prices)]
        pd.testing.assert_series_equal(original, recomputed, check_names=False)


class TestBarrierSemantics(unittest.TestCase):
    def setUp(self):
        self.engine = TripleBarrierLabelerEngine(
            pt_mult=1.0, sl_mult=1.0, vertical_bars=4, volatility_span=10
        )

    def _event_row(self, prices, **kwargs):
        labels = self.engine.generate_labels(prices, **kwargs)
        matching = labels[labels["entry_timestamp"] == EVENT_POS]
        self.assertEqual(len(matching), 1, "the event bar must produce exactly one label")
        return matching.iloc[0]

    def test_upward_spike_labels_take_profit(self):
        prices = series(100.0, 101.0, 108.0, 109.0, 110.0)
        row = self._event_row(prices)
        self.assertEqual(row["barrier_touched"], BarrierTouched.TAKE_PROFIT.value)
        # First bar whose close exceeds 100 * (1 + sigma) is bar 9 at 101.0.
        self.assertEqual(row["exit_timestamp"], EVENT_POS + 1)
        self.assertEqual(row["holding_bars"], 1)
        self.assertAlmostEqual(row["realized_return"], 101.0 / 100.0 - 1.0, places=12)

    def test_downward_crash_labels_stop_loss(self):
        prices = series(100.0, 99.0, 92.0, 91.0, 90.0)
        row = self._event_row(prices)
        self.assertEqual(row["barrier_touched"], BarrierTouched.STOP_LOSS.value)
        self.assertEqual(row["exit_timestamp"], EVENT_POS + 1)
        self.assertAlmostEqual(row["realized_return"], 99.0 / 100.0 - 1.0, places=12)

    def test_flat_drift_labels_vertical_timeout(self):
        prices = series(100.0, 100.02, 100.01, 100.03, 100.02)
        row = self._event_row(prices)
        self.assertEqual(row["barrier_touched"], BarrierTouched.VERTICAL_TIMEOUT.value)
        self.assertEqual(row["holding_bars"], 4)
        self.assertEqual(row["exit_timestamp"], EVENT_POS + 4)
        self.assertAlmostEqual(row["realized_return"], 100.02 / 100.0 - 1.0, places=12)

    def test_price_exactly_on_the_barrier_does_not_touch_it(self):
        # AFML Snippet 3.2 tests df0 > pt and df0 < sl strictly. The boundary is
        # decided here, not by chance: build the fixture, read the target the entry
        # bar actually gets, then place the next close exactly on the barrier.
        prices = series(100.0, 100.02, 100.01, 100.03, 100.02)
        sigma = self.engine.compute_volatility(prices).iloc[EVENT_POS]
        on_barrier = prices.copy()
        on_barrier.iloc[EVENT_POS + 1] = 100.0 * (1.0 + sigma)
        self.assertEqual(
            self._event_row(on_barrier)["barrier_touched"], BarrierTouched.VERTICAL_TIMEOUT.value
        )

        just_past = prices.copy()
        just_past.iloc[EVENT_POS + 1] = 100.0 * (1.0 + sigma) * (1.0 + 1e-9)
        self.assertEqual(
            self._event_row(just_past)["barrier_touched"], BarrierTouched.TAKE_PROFIT.value
        )

    def test_flat_series_is_rejected_rather_than_labelled_take_profit(self):
        # Regression, v1.0.0: a constant series produced sigma = 0, collapsing both
        # barriers onto the entry price; the non-strict `>=` then labelled every
        # unchanged bar +1 with a realised return of exactly 0.0. A halted or
        # forward-filled instrument silently became a stream of winning trades.
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(pd.Series([100.0] * 30))

    def test_min_target_return_filters_thin_targets(self):
        prices = series(100.0, 100.02, 100.01, 100.03, 100.02)
        sigma = self.engine.compute_volatility(prices).iloc[EVENT_POS]
        permissive = TripleBarrierLabelerEngine(
            vertical_bars=4, volatility_span=10, min_target_return=sigma / 2.0
        )
        strict = TripleBarrierLabelerEngine(
            vertical_bars=4, volatility_span=10, min_target_return=sigma * 2.0
        )
        self.assertIn(EVENT_POS, permissive.generate_labels(prices)["entry_timestamp"].tolist())
        with self.assertRaises(TripleBarrierError):
            strict.generate_labels(prices)

    def test_zero_multiplier_disables_that_barrier(self):
        prices = series(100.0, 108.0, 109.0, 110.0, 111.0)
        no_upper = TripleBarrierLabelerEngine(
            pt_mult=0.0, sl_mult=1.0, vertical_bars=4, volatility_span=10
        )
        row = no_upper.generate_labels(prices)
        row = row[row["entry_timestamp"] == EVENT_POS].iloc[0]
        self.assertEqual(row["barrier_touched"], BarrierTouched.VERTICAL_TIMEOUT.value)
        self.assertAlmostEqual(row["realized_return"], 111.0 / 100.0 - 1.0, places=12)

    def test_output_is_deterministic(self):
        prices = series(100.0, 101.0, 99.0, 102.0, 98.0)
        pd.testing.assert_frame_equal(
            self.engine.generate_labels(prices), self.engine.generate_labels(prices)
        )

    def test_label_enum_values_match_the_documented_codes(self):
        self.assertEqual(int(BarrierTouched.TAKE_PROFIT), 1)
        self.assertEqual(int(BarrierTouched.VERTICAL_TIMEOUT), 0)
        self.assertEqual(int(BarrierTouched.STOP_LOSS), -1)

    def test_label_dataclass_defaults(self):
        label = TripleBarrierLabel(
            entry_timestamp=0,
            entry_price=100.0,
            exit_timestamp=1,
            exit_price=101.0,
            barrier_touched=BarrierTouched.TAKE_PROFIT,
            realized_return=0.01,
            target_volatility=0.005,
            side=1,
            holding_bars=1,
        )
        self.assertIsNone(label.meta_label)
        self.assertFalse(label.intrabar_ambiguous)


class TestSideAndMetaLabelling(unittest.TestCase):
    def setUp(self):
        self.engine = TripleBarrierLabelerEngine(
            pt_mult=1.0, sl_mult=1.0, vertical_bars=4, volatility_span=10
        )
        self.crash = series(100.0, 99.0, 92.0, 91.0, 90.0)

    def _row(self, **kwargs):
        labels = self.engine.generate_labels(self.crash, **kwargs)
        return labels[labels["entry_timestamp"] == EVENT_POS].iloc[0]

    def test_short_side_mirrors_the_long_label(self):
        long_row = self._row()
        short_row = self._row(side=-1)
        self.assertEqual(long_row["barrier_touched"], BarrierTouched.STOP_LOSS.value)
        self.assertEqual(short_row["barrier_touched"], BarrierTouched.TAKE_PROFIT.value)
        self.assertEqual(short_row["exit_timestamp"], long_row["exit_timestamp"])
        self.assertAlmostEqual(
            short_row["realized_return"], -long_row["realized_return"], places=12
        )

    def test_meta_label_is_one_for_a_profitable_bet_and_zero_otherwise(self):
        self.assertEqual(self._row(side=-1)["meta_label"], 1)
        self.assertEqual(self._row(side=1)["meta_label"], 0)

    def test_meta_label_is_absent_without_a_side(self):
        self.assertIsNone(self._row()["meta_label"])
        self.assertEqual(self._row()["side"], 1)

    def test_side_accepts_a_series_aligned_with_prices(self):
        sides = pd.Series([-1] * len(self.crash), index=self.crash.index)
        self.assertEqual(self._row(side=sides)["barrier_touched"], BarrierTouched.TAKE_PROFIT.value)

    def test_misaligned_or_invalid_sides_are_rejected(self):
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(self.crash, side=pd.Series([-1] * 3))
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(self.crash, side=0)
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(self.crash, side=2)
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(self.crash, side="long")
        with self.assertRaises(TripleBarrierError):
            # `signal > 0` would otherwise coerce to +1 and label a short book long.
            self.engine.generate_labels(
                self.crash, side=pd.Series([True] * len(self.crash), index=self.crash.index)
            )
        self.assertEqual(VALID_SIDES, (-1, 1))


class TestIntrabarScanning(unittest.TestCase):
    def setUp(self):
        self.engine = TripleBarrierLabelerEngine(
            pt_mult=1.0, sl_mult=1.0, vertical_bars=4, volatility_span=10
        )
        self.close = series(100.0, 100.1, 100.2, 100.15, 100.25)
        self.sigma = self.engine.compute_volatility(self.close).iloc[EVENT_POS]

    def _row(self, **kwargs):
        labels = self.engine.generate_labels(self.close, **kwargs)
        return labels[labels["entry_timestamp"] == EVENT_POS].iloc[0]

    def test_close_only_scan_misses_a_stop_breached_within_the_bar(self):
        highs = self.close.copy()
        lows = self.close.copy()
        lows.iloc[EVENT_POS + 2] = 95.0  # breached mid-bar, recovered by the close

        self.assertEqual(self._row()["barrier_touched"], BarrierTouched.VERTICAL_TIMEOUT.value)

        intrabar = self._row(highs=highs, lows=lows)
        self.assertEqual(intrabar["barrier_touched"], BarrierTouched.STOP_LOSS.value)
        self.assertEqual(intrabar["exit_timestamp"], EVENT_POS + 2)
        # The fill is the barrier level itself, not the bar's low.
        self.assertAlmostEqual(intrabar["exit_price"], 100.0 * (1.0 - self.sigma), places=12)
        self.assertAlmostEqual(intrabar["realized_return"], -self.sigma, places=12)
        self.assertFalse(intrabar["intrabar_ambiguous"])

    def test_a_bar_spanning_both_barriers_resolves_to_the_stop(self):
        highs = self.close.copy()
        lows = self.close.copy()
        highs.iloc[EVENT_POS + 2] = 110.0
        lows.iloc[EVENT_POS + 2] = 90.0
        row = self._row(highs=highs, lows=lows)
        self.assertEqual(row["barrier_touched"], BarrierTouched.STOP_LOSS.value)
        self.assertTrue(row["intrabar_ambiguous"])
        self.assertAlmostEqual(row["exit_price"], 100.0 * (1.0 - self.sigma), places=12)

    def test_short_bet_takes_profit_on_the_low(self):
        highs = self.close.copy()
        lows = self.close.copy()
        lows.iloc[EVENT_POS + 2] = 95.0
        row = self._row(highs=highs, lows=lows, side=-1)
        self.assertEqual(row["barrier_touched"], BarrierTouched.TAKE_PROFIT.value)
        self.assertAlmostEqual(row["exit_price"], 100.0 * (1.0 - self.sigma), places=12)
        self.assertAlmostEqual(row["realized_return"], self.sigma, places=12)

    def test_malformed_bar_ranges_are_rejected(self):
        highs = self.close.copy()
        lows = self.close.copy()
        with self.assertRaises(TripleBarrierError):  # only one of the two supplied
            self.engine.generate_labels(self.close, highs=highs)
        with self.assertRaises(TripleBarrierError):  # arguments swapped
            self.engine.generate_labels(self.close, highs=lows - 1.0, lows=highs + 1.0)
        outside = self.close.copy()
        outside.iloc[EVENT_POS] = 200.0  # close above its own bar high
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(outside, highs=highs, lows=lows)
        misaligned = pd.Series(highs.values, index=range(100, 100 + len(highs)))
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(self.close, highs=misaligned, lows=lows)


class TestEventSelection(unittest.TestCase):
    def setUp(self):
        self.engine = TripleBarrierLabelerEngine(vertical_bars=4, volatility_span=10)
        rng = np.random.default_rng(11)
        self.prices = pd.Series(
            100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, 40))),
            index=pd.date_range("2024-01-01", periods=40, freq="D"),
        )

    def test_event_subset_labels_exactly_those_bars(self):
        wanted = [self.prices.index[10], self.prices.index[20], self.prices.index[30]]
        labels = self.engine.generate_labels(self.prices, events=wanted)
        self.assertEqual(labels["entry_timestamp"].tolist(), wanted)

    def test_unknown_event_label_raises_instead_of_being_dropped(self):
        # Regression: v1.0.0 filtered unknown labels out of the result silently, so
        # a caller asking for N events could receive fewer with no indication.
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(
                self.prices, events=[self.prices.index[10], pd.Timestamp("2099-01-01")]
            )

    def test_duplicate_and_empty_event_lists_raise(self):
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(
                self.prices, events=[self.prices.index[10], self.prices.index[10]]
            )
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(self.prices, events=[])

    def test_events_without_a_full_horizon_are_dropped(self):
        near_end = self.prices.index[-2]
        labels = self.engine.generate_labels(
            self.prices, events=[self.prices.index[10], near_end]
        )
        self.assertEqual(labels["entry_timestamp"].tolist(), [self.prices.index[10]])

    def test_default_seeding_stops_short_of_the_vertical_barrier(self):
        labels = self.engine.generate_labels(self.prices)
        last_position = self.prices.index.get_loc(labels["entry_timestamp"].iloc[-1])
        self.assertEqual(last_position, len(self.prices) - self.engine.vertical_bars - 1)
        # Every exit must land inside the series.
        self.assertTrue(labels["exit_timestamp"].isin(self.prices.index).all())


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = TripleBarrierLabelerEngine(vertical_bars=4, volatility_span=10)

    def test_non_finite_prices_are_rejected(self):
        # Regression: a NaN close fails both `>= upper` and `<= lower`, so v1.0.0
        # scanned straight past the bar and could emit a NaN realised return.
        for bad in (float("nan"), float("inf")):
            corrupted = series(100.0, 101.0, 102.0, 103.0, 104.0)
            corrupted.iloc[EVENT_POS + 1] = bad
            with self.assertRaises(TripleBarrierError):
                self.engine.generate_labels(corrupted)

    def test_non_positive_prices_are_rejected(self):
        for bad in (0.0, -5.0):
            corrupted = series(100.0, 101.0, 102.0, 103.0, 104.0)
            corrupted.iloc[EVENT_POS + 1] = bad
            with self.assertRaises(TripleBarrierError):
                self.engine.generate_labels(corrupted)

    def test_unsorted_or_duplicated_index_is_rejected(self):
        prices = series(100.0, 101.0, 102.0, 103.0, 104.0)
        shuffled = prices.copy()
        shuffled.index = [0, 2, 1] + list(range(3, len(prices)))
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(shuffled)

        duplicated = prices.copy()
        duplicated.index = [0, 0] + list(range(2, len(prices)))
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(duplicated)

    def test_series_shorter_than_the_horizon_is_rejected(self):
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels(pd.Series([100.0, 101.0, 102.0, 103.0]))

    def test_non_series_input_is_rejected(self):
        with self.assertRaises(TripleBarrierError):
            self.engine.generate_labels([100.0] * 20)

    def test_constructor_arguments_are_validated(self):
        bad_kwargs = [
            {"pt_mult": -1.0},
            {"sl_mult": float("nan")},
            {"pt_mult": 0.0, "sl_mult": 0.0},
            {"vertical_bars": 0},
            {"vertical_bars": 2.5},
            {"volatility_span": 0},
            {"volatility_min_periods": 1},
            {"min_target_return": -0.1},
            {"pt_mult": "wide"},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(**kwargs):
                with self.assertRaises(TripleBarrierError):
                    TripleBarrierLabelerEngine(**kwargs)

    def test_asymmetric_barriers_without_a_side_are_warned_about(self):
        # AFML Snippet 3.3 forces symmetric barriers when the side is unknown; on a
        # driftless series asymmetric multipliers manufacture a class skew.
        logging.disable(logging.NOTSET)
        try:
            engine = TripleBarrierLabelerEngine(
                pt_mult=2.0, sl_mult=1.0, vertical_bars=4, volatility_span=10
            )
            prices = series(100.0, 101.0, 99.0, 102.0, 98.0)
            with self.assertLogs("triple_barrier_labeler", level="WARNING") as captured:
                engine.generate_labels(prices)
            self.assertTrue(any("symmetric" in message for message in captured.output))
        finally:
            logging.disable(logging.CRITICAL)


if __name__ == "__main__":
    unittest.main()
