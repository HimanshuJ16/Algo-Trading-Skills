"""Behavioural tests for the clock skew corrector.

Expected values are derived from the synthetic generator's own parameters
(a known drift rate injected into the data), never from re-running the
estimator's formula, so a wrong estimator fails rather than agrees with itself.
"""
import logging
import unittest

import numpy as np

from clock_skew_corrector import ClockSkewCorrector, _enforce_monotonic


def make_feed(
    n=4000, t0=1_700_000_000.0, span=200.0, drift=1e-4, base_offset=0.050,
    jitter_lo=5e-5, jitter_hi=2e-3, seed=7,
):
    """Synthetic one-way feed with a known drift rate.

    Returns ``(exchange_ts, local_ts)`` in float seconds. The local clock gains
    ``drift`` seconds per second of exchange time, and every observation carries a
    strictly positive queueing draw, so the true lower envelope is
    ``base_offset + drift * t``.
    """
    rng = np.random.default_rng(seed)
    exchange_ts = np.sort(rng.uniform(t0, t0 + span, n))
    rel = exchange_ts - t0
    local_ts = exchange_ts + base_offset + drift * rel + rng.uniform(
        jitter_lo, jitter_hi, n)
    return exchange_ts, np.maximum.accumulate(local_ts)


class TestSkewEstimation(unittest.TestCase):
    def test_recovers_injected_drift_rate(self):
        ex, loc = make_feed(drift=1e-4)
        c = ClockSkewCorrector(window_size_sec=5.0).fit(ex, loc)
        # 100 ppm was injected by the generator, independent of the estimator.
        self.assertAlmostEqual(c.drift_ppm, 100.0, delta=5.0)

    def test_recovers_negative_drift(self):
        ex, loc = make_feed(drift=-5e-5, base_offset=0.5)
        c = ClockSkewCorrector(window_size_sec=5.0).fit(ex, loc)
        self.assertAlmostEqual(c.drift_ppm, -50.0, delta=5.0)

    def test_zero_drift_is_not_invented(self):
        ex, loc = make_feed(drift=0.0)
        c = ClockSkewCorrector(window_size_sec=5.0).fit(ex, loc)
        self.assertLess(abs(c.drift_ppm), 5.0)

    def test_intermittent_congestion_does_not_become_drift(self):
        """Queueing that worsens through the session must not read as drift.

        Half the ticks in the second half of the session are delayed by 50 ms,
        leaving quiet samples in every window. Least squares over *all* delay
        points would report hundreds of ppm of drift that does not exist; minimum
        filtering must see through it.
        """
        rng = np.random.default_rng(11)
        ex, loc = make_feed(drift=0.0, n=6000, span=300.0)
        congested = (ex > ex[0] + 150.0) & (rng.random(ex.size) < 0.5)
        loc = np.maximum.accumulate(loc + np.where(congested, 0.05, 0.0))

        naive_ppm = float(np.polyfit(ex - ex[0], loc - ex, deg=1)[0]) * 1e6
        self.assertGreater(abs(naive_ppm), 100.0)  # the trap this skill avoids

        c = ClockSkewCorrector(window_size_sec=5.0).fit(ex, loc)
        self.assertLess(abs(c.drift_ppm), 20.0)

    def test_sustained_latency_shift_is_not_separable_from_the_clock(self):
        """Honest limitation: a whole-window latency regime change fools the fit.

        Minimum filtering rejects congestion only when quiet samples remain in the
        window. If *every* sample in a run of windows is delayed, the lower envelope
        itself moves, and one-way data cannot say whether the path or the clock
        changed. This test pins that behaviour so the documentation stays truthful.
        """
        ex, loc = make_feed(drift=0.0, n=6000, span=300.0)
        loc = np.maximum.accumulate(loc + np.where(ex > ex[0] + 200.0, 0.05, 0.0))
        c = ClockSkewCorrector(window_size_sec=5.0).fit(ex, loc)
        self.assertGreater(abs(c.drift_ppm), 100.0)

    def test_constant_term_is_transit_plus_offset_not_offset(self):
        """alpha must equal the *sum* the data actually contains."""
        ex, loc = make_feed(drift=1e-4, base_offset=0.050, jitter_lo=5e-5)
        c = ClockSkewCorrector(window_size_sec=5.0).fit(ex, loc)
        # base_offset + smallest achievable jitter, not base_offset alone.
        self.assertAlmostEqual(c.alpha, 0.050 + 5e-5, delta=2e-4)

    def test_diagnostics_report_fit_quality(self):
        ex, loc = make_feed(drift=1e-4, span=200.0)
        c = ClockSkewCorrector(window_size_sec=5.0).fit(ex, loc)
        d = c.diagnostics
        self.assertTrue(d.reliable)
        self.assertGreaterEqual(d.n_windows_used, 30)
        self.assertEqual(d.n_samples, ex.size)
        self.assertGreater(d.r_squared, 0.9)
        self.assertAlmostEqual(d.span_sec, ex[-1] - ex[0], places=6)


class TestFitTransformSeparation(unittest.TestCase):
    """Regression tests for the reference-epoch bug.

    ``transform`` used to re-base on its own first exchange timestamp, so applying
    a fit to any later batch mis-applied ``alpha`` at the wrong origin. With
    100 ppm drift and a 300 s gap that was a silent 30 ms error -- an eternity in
    a tick pipeline.
    """

    def test_causal_apply_to_later_batch_is_accurate(self):
        ex, loc = make_feed(n=8000, span=600.0, drift=1e-4, jitter_lo=5e-5,
                            jitter_hi=2e-3)
        half = ex.size // 2
        c = ClockSkewCorrector(window_size_sec=10.0).fit(ex[:half], loc[:half])
        out = c.transform(ex[half:], loc[half:])
        residual = out - ex[half:]
        # After correction the residual is excess queueing over the session
        # minimum: bounded by the jitter span, nowhere near the 30 ms the
        # epoch bug produced.
        self.assertLess(float(np.mean(residual)), 3e-3)
        self.assertGreater(float(np.mean(residual)), -1e-3)

    def test_reference_epoch_survives_batching(self):
        """Transforming in chunks must equal transforming in one call."""
        ex, loc = make_feed(n=3000, span=300.0, drift=1e-4)
        c = ClockSkewCorrector(window_size_sec=10.0).fit(ex, loc)
        whole = c.transform(ex, loc)
        chunks = np.concatenate([c.transform(ex[i:i + 500], loc[i:i + 500])
                                 for i in range(0, ex.size, 500)])
        np.testing.assert_allclose(whole, chunks, atol=1e-9)

    def test_transform_before_fit_raises(self):
        ex, loc = make_feed(n=100)
        with self.assertRaises(RuntimeError):
            ClockSkewCorrector().transform(ex, loc)

    def test_drift_only_mode_keeps_delays_positive(self):
        ex, loc = make_feed(drift=1e-4, base_offset=0.050)
        c = ClockSkewCorrector(window_size_sec=5.0)
        out = c.fit_transform(ex, loc, remove_constant_offset=False)
        delays = out - ex
        self.assertTrue(np.all(delays > 0))
        # Drift removed => the delay series is flat, so its spread collapses.
        self.assertLess(float(np.std(delays)), 0.25 * float(np.std(loc - ex)))
        self.assertAlmostEqual(float(np.mean(delays)), 0.050, delta=3e-3)


class TestMonotonicity(unittest.TestCase):
    def test_output_is_strictly_increasing(self):
        ex, loc = make_feed()
        out = ClockSkewCorrector(window_size_sec=5.0).fit_transform(ex, loc)
        self.assertTrue(np.all(np.diff(out) > 0))

    def test_duplicate_timestamps_are_separated(self):
        """A burst stamped at one microsecond must still come out increasing."""
        ex = np.full(500, 1_700_000_000.0)
        loc = np.full(500, 1_700_000_000.05)
        c = ClockSkewCorrector(window_size_sec=5.0).fit(ex, loc)
        out = c.transform(ex, loc)
        self.assertTrue(np.all(np.diff(out) > 0))

    def test_epsilon_below_float_ulp_still_yields_strict_monotonicity(self):
        """Regression: the guarantee used to fail silently.

        At POSIX epoch magnitudes one float64 ULP is ~238 ns, so adding a 1 ns
        epsilon is a no-op and the old implementation emitted equal timestamps
        while claiming strict monotonicity.
        """
        ex = np.full(64, 1_700_000_000.0)
        loc = np.full(64, 1_700_000_000.05)
        c = ClockSkewCorrector(window_size_sec=5.0, min_epsilon_sec=1e-9)
        with self.assertLogs("clock_skew_corrector", level=logging.WARNING):
            out = c.fit_transform(ex, loc)
        self.assertTrue(np.all(np.diff(out) > 0))

    def test_enforce_monotonic_matches_sequential_recursion(self):
        rng = np.random.default_rng(3)
        values = np.cumsum(rng.normal(0, 1e-3, 500))
        eps = 1e-6
        expected = values.copy()
        for i in range(1, expected.size):
            expected[i] = max(values[i], expected[i - 1] + eps)
        np.testing.assert_allclose(_enforce_monotonic(values, eps), expected,
                                   rtol=0, atol=1e-15)

    def test_correction_does_not_reorder_events(self):
        """Corrected order must match input order -- sequence integrity."""
        ex, loc = make_feed(n=2000, span=120.0)
        out = ClockSkewCorrector(window_size_sec=5.0).fit_transform(ex, loc)
        np.testing.assert_array_equal(np.argsort(out, kind="stable"),
                                      np.arange(out.size))


class TestIntegerNanosecondMode(unittest.TestCase):
    def test_nanosecond_input_returns_nanosecond_integers(self):
        ex_f, loc_f = make_feed(drift=1e-4)
        ex = (ex_f * 1e9).astype(np.int64)
        loc = (loc_f * 1e9).astype(np.int64)
        c = ClockSkewCorrector(window_size_sec=5.0, time_unit="ns")
        out = c.fit_transform(ex, loc)
        self.assertTrue(np.issubdtype(out.dtype, np.integer))
        self.assertAlmostEqual(c.drift_ppm, 100.0, delta=5.0)
        self.assertTrue(np.all(np.diff(out) > 0))

    def test_nanosecond_epoch_is_not_routed_through_float(self):
        """A zero-skew fit must return int64 nanoseconds unchanged.

        1.7e18 ns is past float64's 53-bit exact-integer range (the ULP there is
        256 ns), so holding the reference epoch as a float silently quantises
        every output.
        """
        base = 1_700_000_000_123_456_789
        ex = base + np.arange(400, dtype=np.int64) * 1_000_000  # 1 ms apart
        loc = ex + 50_000_000  # constant 50 ms transit, no drift
        c = ClockSkewCorrector(window_size_sec=0.05, time_unit="ns")
        out = c.fit_transform(ex, loc, remove_constant_offset=False)
        np.testing.assert_array_equal(out, loc)

    def test_mixed_integer_and_float_input_raises(self):
        ex_f, loc_f = make_feed(n=300, span=60.0)
        with self.assertRaises(ValueError):
            ClockSkewCorrector(time_unit="ns").fit((ex_f * 1e9).astype(np.int64), loc_f)

    def test_transform_representation_must_match_fit(self):
        ex_f, loc_f = make_feed(n=600, span=60.0)
        c = ClockSkewCorrector(window_size_sec=5.0).fit(ex_f, loc_f)
        with self.assertRaises(ValueError):
            c.transform((ex_f * 1e9).astype(np.int64), (loc_f * 1e9).astype(np.int64))

    def test_nanosecond_mode_preserves_100ns_separation(self):
        """Float64 seconds cannot hold this; int64 nanoseconds can."""
        base = 1_700_000_000_000_000_000
        ex = base + np.arange(200, dtype=np.int64) * 100  # 100 ns apart
        loc = ex + 50_000_000  # constant 50 ms transit
        c = ClockSkewCorrector(window_size_sec=5.0, time_unit="ns")
        out = c.fit_transform(ex, loc)
        self.assertTrue(np.all(np.diff(out) >= 100))


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.ex, self.loc = make_feed(n=500, span=60.0)

    def test_unsorted_exchange_timestamps_raise(self):
        """Regression: shuffled input used to yield beta = 0 with no signal."""
        shuffled = self.ex.copy()
        rng = np.random.default_rng(1)
        rng.shuffle(shuffled)
        with self.assertRaises(ValueError):
            ClockSkewCorrector().fit(shuffled, self.loc)

    def test_out_of_order_local_timestamps_raise(self):
        loc = self.loc.copy()
        loc[10], loc[11] = loc[11], loc[10]
        with self.assertRaises(ValueError):
            ClockSkewCorrector().fit(self.ex, loc)

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            ClockSkewCorrector().fit(self.ex, self.loc[:-1])

    def test_empty_input_raises(self):
        with self.assertRaises(ValueError):
            ClockSkewCorrector().fit(np.array([]), np.array([]))

    def test_nan_input_raises(self):
        loc = self.loc.copy()
        loc[5] = np.nan
        with self.assertRaises(ValueError):
            ClockSkewCorrector().fit(self.ex, loc)

    def test_bad_time_unit_raises(self):
        with self.assertRaises(ValueError):
            ClockSkewCorrector(time_unit="minutes")

    def test_non_positive_window_raises(self):
        with self.assertRaises(ValueError):
            ClockSkewCorrector(window_size_sec=0.0)


class TestImplausibleFits(unittest.TestCase):
    def test_implausible_drift_raises(self):
        """A 1% rate error is a unit or data problem, not a clock."""
        ex, loc = make_feed(drift=1e-2, span=200.0)
        with self.assertRaises(ValueError):
            ClockSkewCorrector(window_size_sec=5.0).fit(ex, loc)

    def test_plausibility_ceiling_can_be_disabled(self):
        ex, loc = make_feed(drift=1e-2, span=200.0)
        c = ClockSkewCorrector(window_size_sec=5.0,
                               max_drift_ppm=float("inf")).fit(ex, loc)
        self.assertAlmostEqual(c.drift_ppm, 10_000.0, delta=500.0)

    def test_too_few_windows_falls_back_loudly(self):
        ex, loc = make_feed(n=40, span=3.0, drift=1e-4)
        c = ClockSkewCorrector(window_size_sec=10.0)
        with self.assertLogs("clock_skew_corrector", level=logging.WARNING):
            c.fit(ex, loc)
        self.assertEqual(c.beta, 0.0)
        self.assertFalse(c.diagnostics.reliable)

    def test_sparse_windows_are_excluded_from_the_fit(self):
        ex, loc = make_feed(n=600, span=600.0, drift=1e-4)  # ~1 tick/second
        c = ClockSkewCorrector(window_size_sec=10.0, min_points_per_window=50)
        with self.assertLogs("clock_skew_corrector", level=logging.WARNING):
            c.fit(ex, loc)
        self.assertGreater(c.diagnostics.n_windows_dropped, 0)
        self.assertFalse(c.diagnostics.reliable)

    def test_clock_step_is_flagged(self):
        """An NTP step inside the window breaks the linear model; warn about it."""
        ex, loc = make_feed(n=6000, span=300.0, drift=0.0)
        loc = np.maximum.accumulate(loc + np.where(ex > ex[0] + 150.0, 0.128, 0.0))
        c = ClockSkewCorrector(window_size_sec=5.0, max_drift_ppm=float("inf"))
        with self.assertLogs("clock_skew_corrector", level=logging.WARNING):
            c.fit(ex, loc)


if __name__ == "__main__":
    unittest.main()
