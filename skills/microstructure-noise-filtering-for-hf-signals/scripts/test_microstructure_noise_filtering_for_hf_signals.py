"""Unit tests for microstructure-noise-filtering-for-hf-signals.

Expected values are derived analytically (closed-form steady-state gain, hand-computed
weighted mids, closed-form EMA recursions) rather than re-running the implementation's
own arithmetic, so a regression in the estimator fails these tests.
"""
import math
import random
import unittest

from microstructure_noise_filtering_for_hf_signals import (
    FILTER_EMA,
    FILTER_KALMAN,
    FILTER_MICRO_PRICE,
    FILTER_WEIGHTED_MID,
    STATUS_NO_REDUCTION,
    STATUS_SUCCESS,
    FilterConfig,
    MicrostructureNoiseFilterEngine,
    RawTick,
    kalman_effective_span,
    steady_state_kalman_gain,
)


def quote(i, mid, half_spread=0.01, bid_vol=100.0, ask_vol=100.0):
    """A balanced two-sided quote centred on ``mid``."""
    return RawTick(
        timestamp_epoch=float(i),
        bid_price=mid - half_spread,
        ask_price=mid + half_spread,
        last_price=mid,
        bid_volume=bid_vol,
        ask_volume=ask_vol,
    )


class TestSteadyStateKalmanGain(unittest.TestCase):
    """
    K* is the positive root of R*K^2 + Q*K - Q = 0, i.e. 0.5*(sqrt(q^2+4q) - q) with
    q = Q/R. Verified here against values derived independently of the closed form.
    """

    def test_gain_solves_its_defining_quadratic(self):
        for q_val, r_val in [(1e-5, 1e-2), (1e-3, 1e-2), (1.0, 1.0), (4.0, 1.0)]:
            k = steady_state_kalman_gain(q_val, r_val)
            residual = r_val * k * k + q_val * k - q_val
            self.assertAlmostEqual(residual, 0.0, places=12, msg=f"Q={q_val} R={r_val}")

    def test_unit_signal_to_noise_gives_golden_ratio_conjugate(self):
        # q = 1 reduces the quadratic to K^2 + K - 1 = 0, whose positive root is
        # (sqrt(5) - 1)/2 = 0.6180339887..., independent of the implementation.
        self.assertAlmostEqual(
            steady_state_kalman_gain(1.0, 1.0), (math.sqrt(5.0) - 1.0) / 2.0, places=12
        )

    def test_gain_matches_the_iterated_recursion(self):
        # Independently iterate the Riccati recursion to convergence and compare.
        q_val, r_val = 1e-5, 1e-2
        p_est, gain = 1.0, 0.0
        for _ in range(200_000):
            p_predict = p_est + q_val
            gain = p_predict / (p_predict + r_val)
            p_est = (1.0 - gain) * p_predict
        self.assertAlmostEqual(gain, steady_state_kalman_gain(q_val, r_val), places=12)

    def test_effective_span_inverts_the_ema_alpha_relation(self):
        # N = 2/K* - 1. At q = 1, K* = 0.618034 => N = 2/0.618034 - 1 = 2.2360680.
        self.assertAlmostEqual(kalman_effective_span(1.0, 1.0), math.sqrt(5.0), places=9)
        self.assertEqual(kalman_effective_span(0.0, 1.0), math.inf)

    def test_invalid_parameters_raise(self):
        with self.assertRaises(ValueError):
            steady_state_kalman_gain(1e-5, 0.0)      # R = 0
        with self.assertRaises(ValueError):
            steady_state_kalman_gain(1e-5, -1.0)     # R < 0 inverts the gain
        with self.assertRaises(ValueError):
            steady_state_kalman_gain(-1e-5, 1e-2)    # Q < 0
        with self.assertRaises(ValueError):
            steady_state_kalman_gain(float("nan"), 1e-2)


class TestKalmanFilter(unittest.TestCase):

    def setUp(self):
        self.engine = MicrostructureNoiseFilterEngine()

    def test_kalman_reduces_dispersion_of_a_noisy_flat_series(self):
        random.seed(42)
        ticks = [quote(i, 100.0 + random.gauss(0.0, 0.15), 0.02) for i in range(200)]
        cfg = FilterConfig(
            filter_type=FILTER_KALMAN,
            kalman_process_noise_q=1e-5,
            kalman_obs_noise_r=1e-2,
            symbol="AAPL",
        )
        report = self.engine.filter_tick_stream(cfg, ticks)

        self.assertEqual(report.status, STATUS_SUCCESS)
        self.assertTrue(report.dispersion_reduced)
        self.assertEqual(report.total_ticks_processed, 200)
        self.assertLess(report.filtered_price_std_dev, report.raw_price_std_dev)
        self.assertGreater(report.noise_reduction_pct, 15.0)

    def test_variance_reduction_is_reported_separately_from_stddev_reduction(self):
        # The two are different quantities: 1 - r^2 vs 1 - r for the same ratio r.
        random.seed(7)
        ticks = [quote(i, 100.0 + random.gauss(0.0, 0.15), 0.02) for i in range(200)]
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_KALMAN), ticks
        )
        ratio = report.filtered_price_std_dev / report.raw_price_std_dev
        self.assertAlmostEqual(report.noise_reduction_pct, (1 - ratio) * 100.0, places=1)
        self.assertAlmostEqual(
            report.noise_variance_reduction_pct, (1 - ratio**2) * 100.0, places=1
        )
        # Variance reduction always exceeds stddev reduction when the filter reduces.
        self.assertGreater(
            report.noise_variance_reduction_pct, report.noise_reduction_pct
        )

    def test_report_exposes_canonical_snr_and_steady_state_gain(self):
        cfg = FilterConfig(
            filter_type=FILTER_KALMAN,
            kalman_process_noise_q=1e-3,
            kalman_obs_noise_r=1e-2,
        )
        report = self.engine.filter_tick_stream(cfg, [quote(i, 100.0) for i in range(5)])
        self.assertAlmostEqual(report.kalman_snr_q, 0.1, places=12)
        # q = 0.1 => K* = 0.5*(sqrt(0.01 + 0.4) - 0.1) = 0.5*(0.6403124 - 0.1)
        self.assertAlmostEqual(
            report.kalman_steady_state_gain,
            0.5 * (math.sqrt(0.41) - 0.1),
            places=12,
        )
        self.assertAlmostEqual(
            report.kalman_effective_span,
            2.0 / (0.5 * (math.sqrt(0.41) - 0.1)) - 1.0,
            places=9,
        )

    def test_first_two_updates_match_the_closed_form_gains(self):
        """
        Hand-derived from P0 = 1.0 (diffuse prior), independent of the loop:

            K1 = (P0 + Q) / (P0 + Q + R)
            P1 = (1 - K1)(P0 + Q) = R(P0 + Q) / (P0 + Q + R)
            K2 = (P1 + Q) / (P1 + Q + R)

        x is seeded at mid_0, so the first update is a no-op and the second lands at
        mid_0 + K2 * (mid_1 - mid_0). Note K2 ~ 0.50, not ~1.0: the error covariance
        collapses after a single observation, so the filter is already smoothing hard
        by the second tick.
        """
        q_val, r_val, p0 = 1e-5, 1e-2, 1.0
        k1 = (p0 + q_val) / (p0 + q_val + r_val)
        p1 = r_val * (p0 + q_val) / (p0 + q_val + r_val)
        k2 = (p1 + q_val) / (p1 + q_val + r_val)
        self.assertAlmostEqual(k1, 0.990099, places=6)
        self.assertAlmostEqual(k2, 0.497765, places=6)

        ticks = [quote(0, 100.0), quote(1, 101.0)]
        report = self.engine.filter_tick_stream(
            FilterConfig(
                filter_type=FILTER_KALMAN,
                kalman_process_noise_q=q_val,
                kalman_obs_noise_r=r_val,
            ),
            ticks,
        )
        self.assertAlmostEqual(report.filtered_ticks[0].filtered_price, 100.0, places=4)
        self.assertAlmostEqual(
            report.filtered_ticks[1].filtered_price,
            round(100.0 + k2 * 1.0, 4),
            places=4,
        )


class TestWeightedMidPrice(unittest.TestCase):

    def setUp(self):
        self.engine = MicrostructureNoiseFilterEngine()

    def test_weighted_mid_shifts_towards_the_ask_under_bid_pressure(self):
        # Bid 100.00 / Ask 100.20, V_bid = 900, V_ask = 100.
        # w = 900/1000 = 0.9 => W = 0.9*100.20 + 0.1*100.00 = 100.18.
        ticks = [
            RawTick(1.0, 100.0, 100.20, 100.10, bid_volume=900.0, ask_volume=100.0)
        ]
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_WEIGHTED_MID, symbol="BTC-USD"), ticks
        )
        self.assertEqual(report.filtered_ticks[0].raw_mid_price, 100.10)
        self.assertEqual(report.filtered_ticks[0].filtered_price, 100.18)

    def test_micro_price_alias_is_accepted_and_identical(self):
        ticks = [RawTick(1.0, 100.0, 100.20, 100.10, 900.0, 100.0)]
        legacy = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_MICRO_PRICE), ticks
        )
        current = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_WEIGHTED_MID), ticks
        )
        self.assertEqual(
            legacy.filtered_ticks[0].filtered_price,
            current.filtered_ticks[0].filtered_price,
        )

    def test_weighted_mid_always_lies_within_the_book(self):
        random.seed(3)
        for _ in range(300):
            bid = 100.0
            ask = 100.0 + random.uniform(0.01, 0.50)
            bv = random.uniform(0.0, 5000.0)
            av = random.uniform(0.0, 5000.0)
            report = self.engine.filter_tick_stream(
                FilterConfig(filter_type=FILTER_WEIGHTED_MID, price_precision=8),
                [RawTick(0.0, bid, ask, bid, bv, av)],
            )
            price = report.filtered_ticks[0].filtered_price
            self.assertGreaterEqual(price, bid)
            self.assertLessEqual(price, ask)

    def test_empty_book_falls_back_to_the_midpoint(self):
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_WEIGHTED_MID),
            [RawTick(0.0, 100.0, 100.20, 100.10, 0.0, 0.0)],
        )
        self.assertEqual(report.filtered_ticks[0].filtered_price, 100.10)

    def test_weighted_mid_is_reported_as_no_reduction_not_success(self):
        """
        Regression: the weighted mid is a fair-value estimator, not a smoother. Its
        dispersion normally exceeds the midpoint's, and the previous implementation
        reported that outcome as NOISE_FILTERING_SUCCESS.
        """
        random.seed(11)
        ticks = []
        for i in range(400):
            mid = 100.0 + random.gauss(0.0, 0.05)
            imbalance = random.random()
            ticks.append(
                RawTick(
                    float(i), mid - 0.01, mid + 0.01, mid,
                    1000.0 * imbalance, 1000.0 * (1.0 - imbalance),
                )
            )
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_WEIGHTED_MID), ticks
        )
        self.assertGreater(report.filtered_price_std_dev, report.raw_price_std_dev)
        self.assertLess(report.noise_reduction_pct, 0.0)
        self.assertFalse(report.dispersion_reduced)
        self.assertEqual(report.status, STATUS_NO_REDUCTION)
        self.assertIn("fair-value estimator", report.audit_notes)


class TestEmaFilter(unittest.TestCase):

    def setUp(self):
        self.engine = MicrostructureNoiseFilterEngine()

    def test_ema_matches_the_hand_computed_recursion(self):
        # N = 3 => alpha = 2/4 = 0.5, seeded from the first midpoint (100.0).
        # t0: 0.5*100 + 0.5*100 = 100.0
        # t1: 0.5*110 + 0.5*100 = 105.0
        # t2: 0.5*110 + 0.5*105 = 107.5
        ticks = [quote(0, 100.0), quote(1, 110.0), quote(2, 110.0)]
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_EMA, ema_span_n=3), ticks
        )
        prices = [f.filtered_price for f in report.filtered_ticks]
        self.assertEqual(prices, [100.0, 105.0, 107.5])

    def test_ema_span_must_be_at_least_one(self):
        ticks = [quote(0, 100.0)]
        for bad_span in (0, -1, -10):
            with self.assertRaises(ValueError):
                self.engine.filter_tick_stream(
                    FilterConfig(filter_type=FILTER_EMA, ema_span_n=bad_span), ticks
                )

    def test_span_one_is_a_passthrough(self):
        # N = 1 => alpha = 1.0, so the EMA reproduces the midpoint exactly.
        ticks = [quote(0, 100.0), quote(1, 101.0), quote(2, 99.0)]
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_EMA, ema_span_n=1), ticks
        )
        self.assertEqual(
            [f.filtered_price for f in report.filtered_ticks], [100.0, 101.0, 99.0]
        )


class TestPricePrecision(unittest.TestCase):

    def setUp(self):
        self.engine = MicrostructureNoiseFilterEngine()

    def test_default_precision_is_four_decimals(self):
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_WEIGHTED_MID),
            [RawTick(0.0, 1.234561, 1.234581, 1.23457, 100.0, 100.0)],
        )
        self.assertEqual(report.filtered_ticks[0].filtered_price, 1.2346)

    def test_fx_five_decimal_precision_is_preserved(self):
        # Regression: a hard-coded 4dp collapsed FX pip fractions and crypto satoshis.
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_WEIGHTED_MID, price_precision=5),
            [RawTick(0.0, 1.23456, 1.23458, 1.23457, 100.0, 100.0)],
        )
        self.assertEqual(report.filtered_ticks[0].filtered_price, 1.23457)

    def test_invalid_precision_raises(self):
        for bad in (-1, 2.5, "4"):
            with self.assertRaises(ValueError):
                self.engine.filter_tick_stream(
                    FilterConfig(filter_type=FILTER_KALMAN, price_precision=bad),
                    [quote(0, 100.0)],
                )


class TestInputValidation(unittest.TestCase):
    """
    Every case here was silently accepted by the previous implementation, producing
    either a corrupt report or an unhandled ZeroDivisionError.
    """

    def setUp(self):
        self.engine = MicrostructureNoiseFilterEngine()

    def test_empty_stream_raises(self):
        with self.assertRaises(ValueError):
            self.engine.filter_tick_stream(FilterConfig(), [])

    def test_unsupported_filter_type_raises(self):
        with self.assertRaises(ValueError):
            self.engine.filter_tick_stream(
                FilterConfig(filter_type="SAVITZKY_GOLAY"), [quote(0, 100.0)]
            )

    def test_nan_price_raises_instead_of_poisoning_the_state(self):
        # Regression: a NaN bid used to propagate through every later Kalman state and
        # still report status SUCCESS with a 0.00% reduction.
        ticks = [
            quote(0, 100.0),
            RawTick(1.0, float("nan"), 100.01, 100.0, 10.0, 10.0),
            quote(2, 100.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.filter_tick_stream(
                FilterConfig(filter_type=FILTER_KALMAN), ticks
            )

    def test_infinite_values_raise(self):
        for bad_tick in (
            RawTick(0.0, float("inf"), 100.01, 100.0, 10.0, 10.0),
            RawTick(0.0, 99.99, 100.01, 100.0, float("inf"), 10.0),
            RawTick(float("nan"), 99.99, 100.01, 100.0, 10.0, 10.0),
        ):
            with self.assertRaises(ValueError):
                self.engine.filter_tick_stream(FilterConfig(), [bad_tick])

    def test_crossed_book_raises(self):
        with self.assertRaises(ValueError):
            self.engine.filter_tick_stream(
                FilterConfig(), [RawTick(0.0, 101.0, 99.0, 100.0, 10.0, 10.0)]
            )

    def test_locked_book_is_accepted(self):
        # bid == ask is locked, not crossed: the midpoint is still well defined.
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_WEIGHTED_MID),
            [RawTick(0.0, 100.0, 100.0, 100.0, 10.0, 90.0)],
        )
        self.assertEqual(report.filtered_ticks[0].filtered_price, 100.0)

    def test_non_positive_price_raises(self):
        for bid, ask in ((0.0, 100.0), (-50.0, -49.0)):
            with self.assertRaises(ValueError):
                self.engine.filter_tick_stream(
                    FilterConfig(), [RawTick(0.0, bid, ask, 100.0, 10.0, 10.0)]
                )

    def test_negative_volume_raises(self):
        # Regression: V_bid=10, V_ask=-5 passes a naive `total > 0` guard and yields
        # 100.40 on a 100.00/100.20 book -- a price outside the book.
        with self.assertRaises(ValueError):
            self.engine.filter_tick_stream(
                FilterConfig(filter_type=FILTER_WEIGHTED_MID),
                [RawTick(0.0, 100.0, 100.20, 100.10, 10.0, -5.0)],
            )

    def test_out_of_order_timestamps_raise(self):
        with self.assertRaises(ValueError):
            self.engine.filter_tick_stream(
                FilterConfig(), [quote(0, 100.0), quote(5, 100.0), quote(2, 100.0)]
            )

    def test_duplicate_timestamps_are_accepted(self):
        # Same-microsecond ticks are normal in real feeds; only inversion is an error.
        report = self.engine.filter_tick_stream(
            FilterConfig(), [quote(1, 100.0), quote(1, 100.0), quote(1, 100.0)]
        )
        self.assertEqual(report.total_ticks_processed, 3)

    def test_kalman_rejects_non_positive_observation_noise(self):
        # Regression: R = -1.0 used to be accepted, inverting the gain to ~1e5.
        for bad_r in (0.0, -1.0):
            with self.assertRaises(ValueError):
                self.engine.filter_tick_stream(
                    FilterConfig(filter_type=FILTER_KALMAN, kalman_obs_noise_r=bad_r),
                    [quote(0, 100.0)],
                )

    def test_kalman_rejects_negative_process_noise(self):
        with self.assertRaises(ValueError):
            self.engine.filter_tick_stream(
                FilterConfig(filter_type=FILTER_KALMAN, kalman_process_noise_q=-1e-5),
                [quote(0, 100.0)],
            )


class TestDegenerateSeries(unittest.TestCase):

    def setUp(self):
        self.engine = MicrostructureNoiseFilterEngine()

    def test_constant_midpoint_reports_zero_reduction_not_a_division_error(self):
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_KALMAN), [quote(i, 100.0) for i in range(50)]
        )
        self.assertEqual(report.raw_price_std_dev, 0.0)
        self.assertEqual(report.noise_reduction_pct, 0.0)
        self.assertEqual(report.noise_variance_reduction_pct, 0.0)
        self.assertFalse(report.dispersion_reduced)
        self.assertEqual(report.status, STATUS_NO_REDUCTION)

    def test_zero_residual_dispersion_reports_infinite_ratio(self):
        # Previously a 999.99 magic sentinel indistinguishable from a real reading.
        report = self.engine.filter_tick_stream(
            FilterConfig(filter_type=FILTER_EMA, ema_span_n=1),
            [quote(i, 100.0 + i) for i in range(10)],
        )
        self.assertEqual(report.signal_to_noise_ratio, math.inf)

    def test_single_tick_stream_is_handled(self):
        report = self.engine.filter_tick_stream(FilterConfig(), [quote(0, 100.0)])
        self.assertEqual(report.total_ticks_processed, 1)
        self.assertEqual(report.raw_price_std_dev, 0.0)

    def test_engine_is_stateless_across_calls(self):
        ticks = [quote(i, 100.0 + (i % 2) * 0.5) for i in range(30)]
        cfg = FilterConfig(filter_type=FILTER_KALMAN)
        first = self.engine.filter_tick_stream(cfg, ticks)
        second = self.engine.filter_tick_stream(cfg, ticks)
        self.assertEqual(
            [f.filtered_price for f in first.filtered_ticks],
            [f.filtered_price for f in second.filtered_ticks],
        )


if __name__ == "__main__":
    unittest.main()
