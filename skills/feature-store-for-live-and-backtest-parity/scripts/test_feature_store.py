"""
Unit tests for feature-store-for-live-and-backtest-parity.

Expected feature values are derived independently of the implementation:

- Bollinger %B on an arithmetic close series uses the closed form for the population
  variance of an arithmetic progression, var = (n^2 - 1) * step^2 / 12, rather than
  re-running the engine's own standard-deviation call.
- The volatility z-score fixture is built multiplicatively so its absolute returns are
  exactly {0.01, 0.01, 0.01, 0.02}, whose z-score for the last observation is sqrt(3)
  by hand: (0.02 - 0.0125) / (0.0025 * sqrt(3)).

Regression coverage (each of these fails against the pre-audit implementation):
flat-window RSI, absolute-value consistency in the volatility z-score, removal of the
4-decimal rounding, the population/sample ddof convention, and `validate_parity`
destroying a live engine's ring buffer.
"""
import logging
import math
import unittest

from feature_store import (
    Bar,
    BarSequenceError,
    DEFAULT_PARITY_TOLERANCE,
    FeatureParityMismatchError,
    FeatureVector,
    ParityFeatureStoreEngine,
)


# The parity mismatch tests exercise logger.critical on purpose; keep the expected
# failure banners out of the suite's output without disabling logging globally.
logging.getLogger("feature_store").addHandler(logging.NullHandler())
logging.getLogger("feature_store").propagate = False


def make_bars(closes, start_ts=1700000000.0, step=60.0):
    """Builds a valid, chronological bar series from a list of closes."""
    return [
        Bar(
            timestamp=start_ts + i * step,
            open=c,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            volume=1000.0 + i,
        )
        for i, c in enumerate(closes)
    ]


class _DivergentEngine(ParityFeatureStoreEngine):
    """Online path deliberately skewed from the batch path, by `offset`, at one index."""

    offset = 1.0
    corrupt_index = 25

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen = 0

    def compute_online_feature(self, incoming_bar):
        fv = super().compute_online_feature(incoming_bar)
        self._seen += 1
        if self._seen - 1 == self.corrupt_index:
            return FeatureVector(
                timestamp=fv.timestamp,
                rsi=fv.rsi + self.offset,
                bollinger_b=fv.bollinger_b,
                volatility_zscore=fv.volatility_zscore,
                return_1d=fv.return_1d,
                is_warm=fv.is_warm,
                bars_in_window=fv.bars_in_window,
            )
        return fv


class TestSharedFeatureCore(unittest.TestCase):
    """The pure function both pipelines call."""

    def test_bollinger_b_matches_closed_form_population_stddev(self):
        # closes 100..119, step 1. Population variance of an arithmetic progression of
        # n terms with step d is (n^2 - 1) * d^2 / 12 -> (400 - 1)/12 = 33.25.
        closes = [100.0 + i for i in range(20)]
        sigma = math.sqrt((20 ** 2 - 1) / 12.0)
        mean = 109.5
        expected_b = (closes[-1] - (mean - 2.0 * sigma)) / (4.0 * sigma)

        _, bollinger_b, _, _ = ParityFeatureStoreEngine.compute_features_from_window(closes)

        # places=12 also pins the removal of the old round(x, 4) quantisation: a
        # 4-decimal result cannot match a 12-decimal expectation.
        self.assertAlmostEqual(bollinger_b, expected_b, places=12)

    def test_ddof_one_reproduces_pandas_rolling_std_convention(self):
        # pandas .rolling().std() defaults to ddof=1; sample variance of the same
        # progression is n(n+1)d^2/12 = 20*21/12 = 35.
        closes = [100.0 + i for i in range(20)]
        sigma_sample = math.sqrt(20 * 21 / 12.0)
        expected_b = (closes[-1] - (109.5 - 2.0 * sigma_sample)) / (4.0 * sigma_sample)

        _, b_pop, _, _ = ParityFeatureStoreEngine.compute_features_from_window(closes, 0)
        _, b_sample, _, _ = ParityFeatureStoreEngine.compute_features_from_window(closes, 1)

        self.assertAlmostEqual(b_sample, expected_b, places=12)
        self.assertNotAlmostEqual(b_pop, b_sample, places=6)  # the skew this skill prevents

    def test_volatility_zscore_uses_absolute_returns_on_both_sides(self):
        # Absolute returns are exactly {0.01, 0.01, 0.01, 0.02} with mixed signs, so an
        # implementation comparing |r_t| against the mean of *signed* returns lands on
        # 1.5 instead of the correct sqrt(3).
        closes = [100.0]
        for r in (0.01, -0.01, 0.01, -0.02):
            closes.append(closes[-1] * (1.0 + r))

        _, _, vol_z, ret_1d = ParityFeatureStoreEngine.compute_features_from_window(closes)

        self.assertAlmostEqual(ret_1d, -0.02, places=12)
        self.assertAlmostEqual(vol_z, math.sqrt(3.0), places=9)

    def test_volatility_zscore_is_zero_when_all_moves_are_equal_magnitude(self):
        closes = [100.0]
        for r in (0.01, -0.01, 0.01, -0.01, 0.01):
            closes.append(closes[-1] * (1.0 + r))

        _, _, vol_z, _ = ParityFeatureStoreEngine.compute_features_from_window(closes)

        self.assertEqual(vol_z, 0.0)

    def test_flat_window_is_neutral_not_overbought(self):
        # Regression: 0 gains AND 0 losses is 0/0, previously reported as RSI 100 --
        # maximum overbought for a non-trading instrument.
        rsi, bollinger_b, vol_z, ret_1d = ParityFeatureStoreEngine.compute_features_from_window(
            [100.0] * 20
        )

        self.assertEqual(rsi, 50.0)
        self.assertEqual(bollinger_b, 0.5)
        self.assertEqual(vol_z, 0.0)
        self.assertEqual(ret_1d, 0.0)

    def test_monotonic_up_and_down_windows_hit_the_rsi_bounds(self):
        up, _, _, ret_up = ParityFeatureStoreEngine.compute_features_from_window(
            [100.0 + i for i in range(20)]
        )
        down, _, _, ret_down = ParityFeatureStoreEngine.compute_features_from_window(
            [200.0 - i for i in range(20)]
        )

        self.assertEqual(up, 100.0)
        self.assertEqual(down, 0.0)
        self.assertAlmostEqual(ret_up, 1.0 / 118.0, places=12)
        self.assertAlmostEqual(ret_down, -1.0 / 182.0, places=12)

    def test_short_window_returns_documented_neutral_placeholder(self):
        self.assertEqual(
            ParityFeatureStoreEngine.compute_features_from_window([100.0, 101.0, 102.0]),
            (50.0, 0.5, 0.0, 0.0),
        )

    def test_non_finite_or_non_positive_close_is_rejected(self):
        for bad in (float("nan"), float("inf"), 0.0, -5.0):
            with self.assertRaises(ValueError):
                ParityFeatureStoreEngine.compute_features_from_window(
                    [100.0, 101.0, 102.0, 103.0, bad]
                )


class TestEngineConfiguration(unittest.TestCase):

    def test_lookback_shorter_than_the_indicator_periods_is_rejected(self):
        # A 10-bar window would silently compute RSI(9)/Bollinger(10) under an RSI(14)
        # /Bollinger(20) label.
        for bad in (1, 5, 14, 19):
            with self.assertRaises(ValueError):
                ParityFeatureStoreEngine(lookback_period=bad)

    def test_non_integer_lookback_and_bad_ddof_are_rejected(self):
        with self.assertRaises(ValueError):
            ParityFeatureStoreEngine(lookback_period=20.0)
        with self.assertRaises(ValueError):
            ParityFeatureStoreEngine(lookback_period=20, stddev_ddof=2)


class TestPipelines(unittest.TestCase):

    def setUp(self):
        self.engine = ParityFeatureStoreEngine(lookback_period=20)
        self.bars = make_bars([101.0 + i for i in range(50)])

    def test_batch_flags_warm_up_rows(self):
        rows = self.engine.compute_batch_features(self.bars)

        self.assertEqual(len(rows), 50)
        self.assertFalse(any(r.is_warm for r in rows[:19]))
        self.assertTrue(all(r.is_warm for r in rows[19:]))
        self.assertEqual(rows[0].bars_in_window, 1)
        self.assertEqual(rows[-1].bars_in_window, 20)

    def test_batch_window_never_reaches_a_future_bar(self):
        # Truncating the series must not change any feature already emitted for the
        # bars that remain -- the look-ahead check for the batch pipeline.
        full = self.engine.compute_batch_features(self.bars)
        truncated = self.engine.compute_batch_features(self.bars[:30])

        for a, b in zip(full[:30], truncated):
            self.assertEqual(a.to_dict(), b.to_dict())

    def test_online_rejects_duplicate_and_out_of_order_bars(self):
        for bar in self.bars[:5]:
            self.engine.compute_online_feature(bar)
        depth = len(self.engine.online_ring_buffer)

        with self.assertRaises(BarSequenceError):
            self.engine.compute_online_feature(self.bars[4])      # websocket replay
        with self.assertRaises(BarSequenceError):
            self.engine.compute_online_feature(self.bars[2])      # late/out-of-order

        self.assertEqual(len(self.engine.online_ring_buffer), depth)

    def test_online_buffer_is_bounded_by_lookback(self):
        for bar in self.bars:
            self.engine.compute_online_feature(bar)

        self.assertEqual(len(self.engine.online_ring_buffer), 20)
        self.assertTrue(self.engine.is_warm)

    def test_warm_up_seeds_the_buffer_and_reset_clears_it(self):
        self.assertFalse(self.engine.is_warm)
        self.assertEqual(self.engine.warm_up(self.bars[:20]), 20)
        self.assertTrue(self.engine.is_warm)

        self.engine.reset()

        self.assertFalse(self.engine.is_warm)
        self.assertEqual(len(self.engine.online_ring_buffer), 0)
        # reset() must also clear the timestamp watermark, or replay is impossible.
        self.engine.compute_online_feature(self.bars[0])

    def test_unsorted_or_duplicated_batch_series_is_rejected(self):
        shuffled = list(self.bars)
        shuffled[3], shuffled[9] = shuffled[9], shuffled[3]
        with self.assertRaises(BarSequenceError):
            self.engine.compute_batch_features(shuffled)

        with self.assertRaises(BarSequenceError):
            self.engine.compute_batch_features([])

    def test_malformed_bar_is_rejected_with_an_actionable_error(self):
        with self.assertRaises(BarSequenceError):
            self.engine.compute_online_feature(
                Bar(timestamp=1.0, open=1.0, high=1.0, low=1.0, close=float("nan"), volume=1.0)
            )
        with self.assertRaises(BarSequenceError):
            self.engine.compute_online_feature(
                Bar(timestamp=1.0, open=1.0, high=1.0, low=2.0, close=1.0, volume=1.0)
            )


class TestParityValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ParityFeatureStoreEngine(lookback_period=20)
        self.bars = make_bars([101.0 + i for i in range(50)])

    def test_shared_core_gives_exact_parity(self):
        # Both pipelines call one function over identical windows, so the difference is
        # not merely small -- it is exactly zero. Asserted at tolerance 0.0.
        self.assertTrue(self.engine.validate_parity(self.bars, tolerance=0.0))
        self.assertTrue(self.engine.validate_parity(self.bars))

    def test_parity_holds_on_a_volatile_mixed_sign_series(self):
        closes = [100.0]
        for i in range(60):
            closes.append(closes[-1] * (1.0 + (0.03 if i % 3 else -0.045)))

        self.assertTrue(self.engine.validate_parity(make_bars(closes), tolerance=0.0))

    def test_real_divergence_between_pipelines_is_detected(self):
        # The online path is genuinely skewed here; the validator, not the test, must
        # be what raises.
        with self.assertRaises(FeatureParityMismatchError) as ctx:
            _DivergentEngine(lookback_period=20).validate_parity(self.bars)

        self.assertIn("rsi", str(ctx.exception))
        self.assertIn("index 25", str(ctx.exception))

    def test_divergence_below_tolerance_passes_and_above_it_fails(self):
        class _Tiny(_DivergentEngine):
            offset = DEFAULT_PARITY_TOLERANCE

        class _JustOver(_DivergentEngine):
            offset = DEFAULT_PARITY_TOLERANCE * 10.0

        # Exactly at the threshold: the comparison is strict (diff > tolerance).
        self.assertTrue(_Tiny(lookback_period=20).validate_parity(self.bars))
        with self.assertRaises(FeatureParityMismatchError):
            _JustOver(lookback_period=20).validate_parity(self.bars)

    def test_validate_parity_does_not_disturb_live_online_state(self):
        # Regression: the validator used to clear self.online_ring_buffer, so running the
        # self-check against a live engine silently reset its warm-up and replaced the
        # buffered bars with the validation series.
        live_bars = make_bars([500.0 + i for i in range(30)], start_ts=1800000000.0)
        for bar in live_bars:
            self.engine.compute_online_feature(bar)
        before = list(self.engine.online_ring_buffer)

        self.engine.validate_parity(self.bars)

        self.assertEqual(list(self.engine.online_ring_buffer), before)
        self.assertTrue(self.engine.is_warm)
        # And the engine must still accept the next live bar.
        nxt = Bar(timestamp=1800000000.0 + 30 * 60, open=530.0, high=531.0,
                  low=529.0, close=530.0, volume=1.0)
        self.assertTrue(self.engine.compute_online_feature(nxt).is_warm)

    def test_negative_or_non_finite_tolerance_is_rejected(self):
        for bad in (-1e-9, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.engine.validate_parity(self.bars, tolerance=bad)


if __name__ == "__main__":
    unittest.main()
