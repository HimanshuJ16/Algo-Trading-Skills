import logging
import math
import random
import unittest

from order_book_microstructure_signal_research import (
    FINDING_CONSTANT_SPREAD_COLLINEARITY,
    FINDING_DEGENERATE_DEPTH,
    FINDING_INSUFFICIENT_EFFECTIVE_SAMPLE,
    FINDING_IC_SIGN_INVERTED,
    FINDING_ZERO_VARIANCE_SIGNAL,
    MIN_EFFECTIVE_OBSERVATIONS,
    STATUS_INSUFFICIENT_SAMPLES,
    STATUS_PREDICTIVE,
    STATUS_WEAK,
    MicrostructureConfigError,
    MicrostructureInputError,
    OrderBookMicrostructureSignalResearchEngine,
    OrderBookTick,
)

# The engine logs every audit verdict at warning level; keep the suite output clean.
logging.getLogger("order_book_microstructure_signal_research").setLevel(logging.CRITICAL)


def tick(i, bid_p, bid_q, ask_p, ask_q, symbol="AAPL"):
    return OrderBookTick(1000 * i, symbol, bid_p, bid_q, ask_p, ask_q)


class TestFeatureExtraction(unittest.TestCase):
    def setUp(self):
        self.engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=2)

    def test_first_row_has_no_observed_event(self):
        """e_0 has no predecessor, so it is a placeholder rather than an observation."""
        feats = self.engine.extract_features([
            tick(0, 150.0, 100.0, 150.1, 100.0),
            tick(1, 150.0, 200.0, 150.1, 50.0),
        ])
        self.assertFalse(feats[0].is_event_observed)
        self.assertEqual(feats[0].ofi, 0.0)
        self.assertTrue(feats[1].is_event_observed)

    def test_price_unchanged_branch(self):
        """Both prices unchanged: e_n = (qB_n - qB_n-1) - (qA_n - qA_n-1)."""
        feats = self.engine.extract_features([
            tick(0, 150.0, 100.0, 150.1, 100.0),
            tick(1, 150.0, 200.0, 150.1, 50.0),
        ])
        # bid +100, ask -50  ->  100 - (-50) = +150
        self.assertEqual(feats[1].ofi, 150.0)
        # VOI = (200 - 50) / 250
        self.assertEqual(feats[1].voi, 0.6)

    def test_price_improving_branches(self):
        """Bid price up contributes +qB_n; ask price down contributes -qA_n."""
        feats = self.engine.extract_features([
            tick(0, 150.0, 100.0, 150.3, 100.0),
            tick(1, 150.1, 70.0, 150.2, 40.0),
        ])
        # bid improved -> +70 ; ask improved -> -40 ; e_n = 30
        self.assertEqual(feats[1].ofi, 30.0)

    def test_bid_price_falls_uses_previous_bid_size(self):
        """Regression: a collapsing bid is -qB_n-1, not 0.

        The prior revision assigned 0.0 to this branch, so a book whose entire bid
        queue was consumed or pulled reported zero order flow imbalance.
        """
        feats = self.engine.extract_features([
            tick(0, 150.0, 900.0, 150.1, 100.0),
            tick(1, 149.9, 10.0, 150.1, 100.0),   # bid collapsed, ask unchanged
        ])
        # bid down -> -900 ; ask unchanged, size unchanged -> 0
        self.assertEqual(feats[1].ofi, -900.0)

    def test_ask_price_rises_uses_previous_ask_size(self):
        """Regression: a retreating ask is +qA_n-1, not 0."""
        feats = self.engine.extract_features([
            tick(0, 150.0, 100.0, 150.1, 800.0),
            tick(1, 150.0, 100.0, 150.2, 5.0),    # ask retreated, bid unchanged
        ])
        # bid unchanged -> 0 ; ask up -> +800
        self.assertEqual(feats[1].ofi, 800.0)

    def test_both_sides_deplete(self):
        """Bid down and ask up simultaneously: -qB_n-1 + qA_n-1."""
        feats = self.engine.extract_features([
            tick(0, 150.0, 300.0, 150.1, 500.0),
            tick(1, 149.9, 20.0, 150.2, 20.0),
        ])
        self.assertEqual(feats[1].ofi, -300.0 + 500.0)

    def test_micro_price_deviation_equals_half_voi_times_spread(self):
        """Independently derived identity: P_w - mid == (VOI / 2) * spread.

        Derived by hand from P_w = (qB*Pa + qA*Pb)/(qB+qA), not from the implementation.
        """
        for bid_q, ask_q, bid_p, ask_p in (
            (800.0, 200.0, 100.00, 100.05),
            (17.0, 993.0, 25.5, 25.75),
            (1.0, 1.0, 7.0, 9.0),
        ):
            feats = self.engine.extract_features([tick(0, bid_p, bid_q, ask_p, ask_q)])
            f = feats[0]
            expected = (f.voi / 2.0) * (ask_p - bid_p)
            self.assertAlmostEqual(f.micro_price_dev, expected, places=12)

    def test_weighted_mid_leans_toward_the_heavier_side(self):
        """Independently computed: qB=800, qA=200, Pb=100.00, Pa=100.05."""
        feats = self.engine.extract_features([tick(0, 100.00, 800.0, 100.05, 200.0)])
        # (800*100.05 + 200*100.00) / 1000 = 100.04
        self.assertAlmostEqual(feats[0].micro_price, 100.04, places=10)
        self.assertAlmostEqual(feats[0].mid_price, 100.025, places=10)
        self.assertAlmostEqual(feats[0].spread, 0.05, places=10)

    def test_features_are_not_rounded(self):
        """A five-decimal FX mid must survive extraction intact."""
        feats = self.engine.extract_features([tick(0, 1.234565, 3.0, 1.234575, 1.0)])
        self.assertAlmostEqual(feats[0].mid_price, 1.23457, places=12)
        # A 4dp round would have collapsed this deviation to 0.0.
        self.assertNotEqual(feats[0].micro_price_dev, 0.0)

    def test_fractional_quantities_survive(self):
        """Crypto-sized quantities must not be rounded away."""
        feats = self.engine.extract_features([
            tick(0, 30000.0, 0.004, 30000.5, 1.0),
            tick(1, 30000.0, 0.007, 30000.5, 1.0),
        ])
        self.assertAlmostEqual(feats[1].ofi, 0.003, places=12)

    def test_empty_top_of_book_falls_back_to_mid(self):
        feats = self.engine.extract_features([tick(0, 10.0, 0.0, 10.2, 0.0)])
        self.assertEqual(feats[0].voi, 0.0)
        self.assertEqual(feats[0].micro_price, feats[0].mid_price)

    def test_empty_input_returns_empty(self):
        self.assertEqual(self.engine.extract_features([]), [])

    def test_rolling_window_sums_events(self):
        engine = OrderBookMicrostructureSignalResearchEngine(
            forward_horizon_ticks=2, ofi_window_ticks=3)
        ticks = [
            tick(0, 150.0, 100.0, 150.1, 100.0),
            tick(1, 150.0, 110.0, 150.1, 100.0),   # e = +10
            tick(2, 150.0, 130.0, 150.1, 100.0),   # e = +20
            tick(3, 150.0, 160.0, 150.1, 100.0),   # e = +30
            tick(4, 150.0, 200.0, 150.1, 100.0),   # e = +40
        ]
        feats = engine.extract_features(ticks)
        self.assertFalse(feats[2].is_window_complete)   # only 2 events so far
        self.assertTrue(feats[3].is_window_complete)
        self.assertEqual(feats[3].ofi_window, 60.0)     # 10 + 20 + 30
        self.assertEqual(feats[4].ofi_window, 90.0)     # 20 + 30 + 40


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=2)

    def test_nan_price_is_rejected(self):
        with self.assertRaises(MicrostructureInputError):
            self.engine.extract_features([
                tick(0, 150.0, 100.0, 150.1, 100.0),
                tick(1, float("nan"), 100.0, 150.1, 100.0),
            ])

    def test_inf_quantity_is_rejected(self):
        with self.assertRaises(MicrostructureInputError):
            self.engine.extract_features([tick(0, 150.0, float("inf"), 150.1, 100.0)])

    def test_negative_quantity_is_rejected(self):
        with self.assertRaises(MicrostructureInputError):
            self.engine.extract_features([tick(0, 150.0, -1.0, 150.1, 100.0)])

    def test_non_positive_price_is_rejected(self):
        with self.assertRaises(MicrostructureInputError):
            self.engine.extract_features([tick(0, 0.0, 100.0, 150.1, 100.0)])

    def test_crossed_book_is_rejected(self):
        with self.assertRaises(MicrostructureInputError):
            self.engine.extract_features([tick(0, 150.2, 100.0, 150.1, 100.0)])

    def test_locked_book_is_accepted(self):
        feats = self.engine.extract_features([tick(0, 150.0, 100.0, 150.0, 100.0)])
        self.assertEqual(feats[0].spread, 0.0)
        self.assertEqual(feats[0].micro_price_dev, 0.0)

    def test_out_of_order_timestamps_are_rejected(self):
        with self.assertRaises(MicrostructureInputError):
            self.engine.extract_features([
                OrderBookTick(2000, "AAPL", 150.0, 100.0, 150.1, 100.0),
                OrderBookTick(1000, "AAPL", 150.0, 110.0, 150.1, 100.0),
            ])

    def test_equal_timestamps_are_accepted(self):
        feats = self.engine.extract_features([
            OrderBookTick(1000, "AAPL", 150.0, 100.0, 150.1, 100.0),
            OrderBookTick(1000, "AAPL", 150.0, 110.0, 150.1, 100.0),
        ])
        self.assertEqual(feats[1].ofi, 10.0)

    def test_mixed_symbols_are_rejected(self):
        with self.assertRaises(MicrostructureInputError):
            self.engine.extract_features([
                tick(0, 150.0, 100.0, 150.1, 100.0, symbol="AAPL"),
                tick(1, 250.0, 100.0, 250.1, 100.0, symbol="MSFT"),
            ])

    def test_non_positive_horizon_is_rejected(self):
        """A zero horizon zeroes every return; a negative one is a look-back."""
        with self.assertRaises(MicrostructureConfigError):
            OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=0)
        with self.assertRaises(MicrostructureConfigError):
            OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=-3)

    def test_non_positive_window_is_rejected(self):
        with self.assertRaises(MicrostructureConfigError):
            OrderBookMicrostructureSignalResearchEngine(ofi_window_ticks=0)

    def test_short_series_raises_value_error(self):
        """Preserves the pre-existing ValueError contract for a too-short series."""
        with self.assertRaises(ValueError):
            self.engine.evaluate_signal_efficacy([
                tick(i, 150.0, 100.0, 150.1, 100.0) for i in range(5)
            ])


def _rising_bid_series(n, seed=11, buy_pressure=0.62, symbol="SIM"):
    """A one-tick-spread book driven by directional order flow.

    Buy pressure adds to the bid queue and consumes the ask queue; when the ask queue is
    exhausted the price moves up a tick and both queues are replenished. This is the
    mechanism Cont/Kukanov/Stoikov describe, so order flow imbalance genuinely leads the
    mid-price here rather than being made to correlate by construction.

    Seeded, so the series is identical on every run.
    """
    rng = random.Random(seed)
    tick_size = 0.01
    bid = 100.00
    bid_qty, ask_qty = 100.0, 100.0
    ticks = []
    for i in range(n):
        ticks.append(OrderBookTick(
            1000 * i, symbol, round(bid, 2), bid_qty, round(bid + tick_size, 2), ask_qty))
        size = float(rng.randint(5, 40))
        if rng.random() < buy_pressure:
            bid_qty += size
            ask_qty -= size
            if ask_qty <= 0:                       # ask consumed: price steps up
                bid = round(bid + tick_size, 2)
                bid_qty = float(rng.randint(60, 140))
                ask_qty = float(rng.randint(60, 140))
        else:
            ask_qty += size
            bid_qty -= size
            if bid_qty <= 0:                       # bid consumed: price steps down
                bid = round(bid - tick_size, 2)
                bid_qty = float(rng.randint(60, 140))
                ask_qty = float(rng.randint(60, 140))
    return ticks


class TestEfficacyAudit(unittest.TestCase):
    def test_positive_ofi_leading_rising_prices_is_certified(self):
        engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=2)
        report = engine.evaluate_signal_efficacy(_rising_bid_series(400))
        self.assertEqual(report.status, STATUS_PREDICTIVE)
        self.assertGreaterEqual(report.ic_ofi_forward_return, 0.05)
        self.assertGreaterEqual(report.hit_ratio_pct, 53.0)
        self.assertGreaterEqual(report.effective_observations, MIN_EFFECTIVE_OBSERVATIONS)
        self.assertEqual(report.findings, [FINDING_CONSTANT_SPREAD_COLLINEARITY])

    def test_short_series_cannot_be_certified(self):
        """Twenty ticks give ~9 independent observations, which certifies nothing."""
        engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=2)
        report = engine.evaluate_signal_efficacy(_rising_bid_series(20))
        self.assertEqual(report.status, STATUS_INSUFFICIENT_SAMPLES)
        self.assertIn(FINDING_INSUFFICIENT_EFFECTIVE_SAMPLE, report.findings)
        self.assertLess(report.effective_observations, MIN_EFFECTIVE_OBSERVATIONS)

    def test_effective_sample_divides_by_the_horizon(self):
        engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=5)
        report = engine.evaluate_signal_efficacy(_rising_bid_series(300))
        self.assertEqual(report.effective_observations, report.observations // 5)
        self.assertEqual(report.forward_horizon_ticks, 5)

    def test_index_zero_is_excluded_from_the_sample(self):
        """observations = n_ticks - 1 (undefined e_0) - k (forward horizon)."""
        engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=3)
        report = engine.evaluate_signal_efficacy(_rising_bid_series(120))
        self.assertEqual(report.observations, 120 - 1 - 3)

    def test_window_warmup_is_excluded_from_the_sample(self):
        engine = OrderBookMicrostructureSignalResearchEngine(
            forward_horizon_ticks=3, ofi_window_ticks=4)
        report = engine.evaluate_signal_efficacy(_rising_bid_series(120))
        # first usable index is ofi_window_ticks (4), last is n - 1 - k
        self.assertEqual(report.observations, (120 - 1 - 3) - 4 + 1)

    def test_hit_ratio_excludes_non_directional_ticks(self):
        """A flat book predicts nothing and must not score 100%.

        The prior revision counted every (OFI == 0, return == 0) tick as a hit, so a
        completely static book reported a perfect hit ratio.
        """
        engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=2)
        flat = [tick(i, 150.0, 100.0, 150.1, 100.0) for i in range(60)]
        report = engine.evaluate_signal_efficacy(flat)
        self.assertEqual(report.directional_predictions, 0)
        self.assertEqual(report.hit_ratio_pct, 0.0)
        self.assertNotEqual(report.status, STATUS_PREDICTIVE)
        self.assertIn(FINDING_ZERO_VARIANCE_SIGNAL, report.findings)

    def test_inverted_signal_is_not_certified(self):
        """A side-mapping error is caught, not traded inverted.

        Swapping only the queue sizes -- the classic bid/ask column mix-up when loading a
        feed -- leaves the price path and therefore the forward returns untouched while
        flipping the sign of every OFI contribution. The engine must report this as an
        inverted signal rather than certify its magnitude.
        """
        engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=2)
        swapped = [
            OrderBookTick(t.timestamp_ns, t.symbol,
                          t.bid_price, t.ask_qty, t.ask_price, t.bid_qty)
            for t in _rising_bid_series(400)
        ]
        report = engine.evaluate_signal_efficacy(swapped)
        self.assertLess(report.ic_ofi_forward_return, 0.0)
        self.assertEqual(report.status, STATUS_WEAK)
        self.assertIn(FINDING_IC_SIGN_INVERTED, report.findings)

    def test_constant_spread_makes_micro_dev_ic_equal_voi_ic(self):
        """micro_price_dev == (VOI/2)*spread, so a fixed spread makes the two ICs one."""
        engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=2)
        report = engine.evaluate_signal_efficacy(_rising_bid_series(400))
        self.assertAlmostEqual(
            report.ic_micro_price_dev_return, report.ic_voi_forward_return, places=4)
        self.assertIn(FINDING_CONSTANT_SPREAD_COLLINEARITY, report.findings)

    def test_degenerate_depth_is_counted(self):
        engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=2)
        ticks = _rising_bid_series(60)
        ticks[10] = OrderBookTick(ticks[10].timestamp_ns, ticks[10].symbol,
                                  ticks[10].bid_price, 0.0, ticks[10].ask_price, 0.0)
        report = engine.evaluate_signal_efficacy(ticks)
        self.assertEqual(report.degenerate_depth_ticks, 1)
        self.assertIn(FINDING_DEGENERATE_DEPTH, report.findings)

    def test_returns_use_unrounded_endpoints(self):
        """A sub-4dp mid move must register, not round to a zero return."""
        engine = OrderBookMicrostructureSignalResearchEngine(forward_horizon_ticks=1)
        ticks = []
        bid = 1.100000
        for i in range(40):
            bid = bid + 0.000001 * (1 if i % 2 else 2)
            ticks.append(OrderBookTick(
                1000 * i, "EURUSD", round(bid, 6), 100.0 + i, round(bid + 0.00001, 6), 50.0))
        report = engine.evaluate_signal_efficacy(ticks)
        # Every forward mid differs from the current one, so nothing is "flat".
        self.assertEqual(report.flat_or_neutral_ticks, 0)
        self.assertGreater(report.directional_predictions, 0)


class TestStatistics(unittest.TestCase):
    def setUp(self):
        self.engine = OrderBookMicrostructureSignalResearchEngine()

    def test_pearson_matches_a_hand_computed_value(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 5.0, 4.0, 5.0]
        # cov = 6.0, var_x = 10.0, var_y = 6.0  ->  6 / sqrt(60) = 0.7745966692...
        self.assertAlmostEqual(
            self.engine._pearson_correlation(x, y), 6.0 / math.sqrt(60.0), places=12)

    def test_pearson_is_zero_for_a_constant_series(self):
        self.assertEqual(
            self.engine._pearson_correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]), 0.0)

    def test_pearson_is_bounded(self):
        x = [1.0, 2.0, 3.0]
        self.assertLessEqual(abs(self.engine._pearson_correlation(x, x)), 1.0)

    def test_t_statistic_uses_the_effective_sample(self):
        # t = r * sqrt((n - 2) / (1 - r^2)); r = 0.5, n = 38  ->  0.5*sqrt(36/0.75)
        self.assertAlmostEqual(
            self.engine._ic_t_statistic(0.5, 38), 0.5 * math.sqrt(36.0 / 0.75), places=12)

    def test_t_statistic_is_smaller_on_a_smaller_effective_sample(self):
        """The overlap correction must shrink the statistic, not grow it."""
        naive = self.engine._ic_t_statistic(0.2, 500)
        corrected = self.engine._ic_t_statistic(0.2, 100)
        self.assertLess(corrected, naive)

    def test_t_statistic_degenerates_safely(self):
        self.assertEqual(self.engine._ic_t_statistic(0.5, 2), 0.0)
        self.assertEqual(self.engine._ic_t_statistic(1.0, 100), 0.0)


if __name__ == '__main__':
    unittest.main()
