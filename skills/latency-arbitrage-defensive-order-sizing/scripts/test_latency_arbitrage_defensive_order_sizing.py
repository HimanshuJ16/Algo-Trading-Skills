"""
Unit tests for latency-arbitrage-defensive-order-sizing.

Expected probabilities are derived by hand from ``P = 1 - exp(-h*dt)`` and written
as literals rather than recomputed with the module's own expression, so a change to
the formula fails the test instead of moving with it.

Reference values used below:
    exp(-0.10)          = 0.904837418035960  -> P = 0.0951625819...  -> 0.0952 (4 dp)
    exp(-0.69)          = 0.501576069285814  -> P = 0.4984239307...  -> 0.4984 (4 dp)
    exp(-ln 2)          = 0.5                -> P = 0.5 exactly
    exp(-10.0)          = 0.0000453999297625 -> P = 0.9999546000...  -> 1.0    (4 dp)
"""
import logging
import math
import unittest

from latency_arbitrage_defensive_order_sizing import (
    COMPARABLE_SIZE_MAX_DIVERGENCE,
    STATUS_DEFENSIVELY_SIZED,
    STATUS_HIGH_SNIPING_RISK_CANCEL,
    STATUS_INVALID_INPUT_CANCEL,
    STATUS_MIN_LOT_CANCEL,
    LatencyArbitrageDefensiveSizingEngine,
    MarketStateSpec,
    round_lot_for_nms_price,
)

# The engine logs an ERROR on every fail-closed path by design; keep the test output
# readable without suppressing anything the tests actually assert on.
logging.getLogger("latency_arbitrage_defensive_order_sizing").setLevel(logging.CRITICAL)


class TestSnipingProbability(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = LatencyArbitrageDefensiveSizingEngine(
            max_sniping_prob_threshold=0.50, lambda_scaling=0.50
        )

    def test_probability_matches_hand_derived_value(self) -> None:
        # h = 0.5 * 0.20 = 0.10 per ms; dt = 1.0 ms; P = 1 - exp(-0.10) = 0.0951626.
        self.assertAlmostEqual(
            self.engine.compute_sniping_probability(1.0, 0.20), 0.0952, places=4
        )

    def test_probability_is_monotone_in_latency_and_volatility(self) -> None:
        base = self.engine.compute_sniping_probability(2.0, 0.30)
        self.assertGreater(self.engine.compute_sniping_probability(4.0, 0.30), base)
        self.assertGreater(self.engine.compute_sniping_probability(2.0, 0.60), base)

    def test_no_exposure_when_cancel_beats_the_sweep(self) -> None:
        # A non-positive gap means the cancel arrives first: no sniping exposure.
        self.assertEqual(self.engine.compute_sniping_probability(0.0, 0.20), 0.0)
        self.assertEqual(self.engine.compute_sniping_probability(-3.0, 0.20), 0.0)

    def test_zero_volatility_gives_zero_probability(self) -> None:
        self.assertEqual(self.engine.compute_sniping_probability(50.0, 0.0), 0.0)

    def test_probability_is_bounded_at_one(self) -> None:
        self.assertEqual(self.engine.compute_sniping_probability(1.0e6, 5.0), 1.0)

    def test_non_finite_inputs_fail_closed_not_open(self) -> None:
        # Regression: the pre-fix engine returned 0.0 here, because
        # `nan <= 0.0` is False and `max(0.0, nan)` is 0.0 -- so a dropped latency
        # probe read as "no sniping risk" and full size was posted.
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(latency=bad):
                self.assertEqual(self.engine.compute_sniping_probability(bad, 0.20), 1.0)
            with self.subTest(volatility=bad):
                self.assertEqual(self.engine.compute_sniping_probability(5.0, bad), 1.0)

    def test_negative_volatility_fails_closed(self) -> None:
        self.assertEqual(self.engine.compute_sniping_probability(5.0, -0.20), 1.0)

    def test_zero_lambda_disables_the_hazard(self) -> None:
        engine = LatencyArbitrageDefensiveSizingEngine(lambda_scaling=0.0)
        self.assertEqual(engine.compute_sniping_probability(100.0, 0.80), 0.0)


class TestEngineConstruction(unittest.TestCase):
    def test_rejects_out_of_range_threshold(self) -> None:
        for bad in (0.0, -0.1, 1.5, float("nan")):
            with self.subTest(threshold=bad):
                with self.assertRaises(ValueError):
                    LatencyArbitrageDefensiveSizingEngine(max_sniping_prob_threshold=bad)

    def test_rejects_negative_or_non_finite_lambda(self) -> None:
        for bad in (-0.5, float("nan"), float("inf")):
            with self.subTest(lambda_scaling=bad):
                with self.assertRaises(ValueError):
                    LatencyArbitrageDefensiveSizingEngine(lambda_scaling=bad)


class TestMarketStateSpecValidation(unittest.TestCase):
    def _spec(self, **overrides):
        kwargs = dict(
            symbol="AAPL",
            base_quote_qty=1000,
            latency_gap_ms=1.0,
            volatility_annualized=0.20,
            spread_bps=2.0,
        )
        kwargs.update(overrides)
        return MarketStateSpec(**kwargs)

    def test_rejects_non_positive_quantity(self) -> None:
        # Regression: a negative base quantity previously flowed through to an audit
        # note reading "Defensive Qty (-4524)" instead of being rejected.
        for bad in (0, -5000):
            with self.subTest(qty=bad):
                with self.assertRaises(ValueError):
                    self._spec(base_quote_qty=bad)

    def test_rejects_non_integer_quantity(self) -> None:
        with self.assertRaises(ValueError):
            self._spec(base_quote_qty=1000.5)
        with self.assertRaises(ValueError):
            self._spec(base_quote_qty=True)

    def test_rejects_non_positive_min_lot(self) -> None:
        for bad in (0, -100):
            with self.subTest(min_lot=bad):
                with self.assertRaises(ValueError):
                    self._spec(min_lot_size=bad)

    def test_rejects_non_positive_lot_increment(self) -> None:
        with self.assertRaises(ValueError):
            self._spec(lot_increment=0)

    def test_rejects_bad_spread(self) -> None:
        for bad in (-1.0, float("nan"), float("inf")):
            with self.subTest(spread=bad):
                with self.assertRaises(ValueError):
                    self._spec(spread_bps=bad)

    def test_rejects_empty_symbol(self) -> None:
        with self.assertRaises(ValueError):
            self._spec(symbol="   ")

    def test_accepts_measurement_values_that_fail_closed_later(self) -> None:
        # Measurements are NOT validated at construction: a stale telemetry sample is
        # an expected runtime event and must produce an auditable cancel report, not
        # an exception thrown inside the quoting path.
        spec = self._spec(latency_gap_ms=float("nan"))
        self.assertTrue(math.isnan(spec.latency_gap_ms))


class TestDefensiveSizing(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = LatencyArbitrageDefensiveSizingEngine(
            max_sniping_prob_threshold=0.50, lambda_scaling=0.50
        )

    def test_normal_market_sizes_down_and_widens_spread(self) -> None:
        # P = 0.0952 (hand-derived above). 1000 * (1 - 0.0952) = 904.8, floored to 904
        # -- floored, not rounded: rounding up would show more size than the model
        # just authorised. W = 1 + 2 * 0.0952 = 1.1904 -> 1.19. 2.0 bps * 1.19 = 2.38.
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=1000, latency_gap_ms=1.0,
            volatility_annualized=0.20, spread_bps=2.0, min_lot_size=100,
        )
        report = self.engine.calculate_defensive_sizing(spec)

        self.assertEqual(report.status, STATUS_DEFENSIVELY_SIZED)
        self.assertFalse(report.is_quote_canceled)
        self.assertAlmostEqual(report.sniping_probability, 0.0952, places=4)
        self.assertEqual(report.defensive_quote_qty, 904)
        self.assertAlmostEqual(report.spread_multiplier, 1.19, places=2)
        self.assertAlmostEqual(report.defensive_spread_bps, 2.38, places=2)
        self.assertAlmostEqual(report.sniping_hazard_per_ms, 0.10, places=6)

    def test_defensive_spread_is_reported_not_left_to_the_caller(self) -> None:
        # Regression: `spread_bps` used to be an input the engine never read, so the
        # documented "widen the spread" behaviour produced no widened spread.
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=1000, latency_gap_ms=4.0,
            volatility_annualized=0.20, spread_bps=5.0, min_lot_size=100,
        )
        report = self.engine.calculate_defensive_sizing(spec)
        self.assertAlmostEqual(
            report.defensive_spread_bps, 5.0 * report.spread_multiplier, places=4
        )
        self.assertGreater(report.defensive_spread_bps, 5.0)

    def test_zero_exposure_leaves_size_and_spread_untouched(self) -> None:
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=1000, latency_gap_ms=0.0,
            volatility_annualized=0.20, spread_bps=2.0, min_lot_size=100,
        )
        report = self.engine.calculate_defensive_sizing(spec)

        self.assertEqual(report.status, STATUS_DEFENSIVELY_SIZED)
        self.assertEqual(report.defensive_quote_qty, 1000)
        self.assertEqual(report.sniping_probability, 0.0)
        self.assertAlmostEqual(report.spread_multiplier, 1.0, places=6)
        self.assertAlmostEqual(report.defensive_spread_bps, 2.0, places=6)
        self.assertEqual(report.size_divergence_ratio, 0.0)
        self.assertFalse(report.breaches_comparable_size_one_sided)

    def test_high_sniping_risk_pulls_the_quote(self) -> None:
        # h = 0.5 * 0.80 = 0.40 per ms; dt = 25 ms; exponent -10; P rounds to 1.0.
        spec = MarketStateSpec(
            symbol="TSLA", base_quote_qty=1000, latency_gap_ms=25.0,
            volatility_annualized=0.80, spread_bps=5.0, min_lot_size=100,
        )
        report = self.engine.calculate_defensive_sizing(spec)

        self.assertEqual(report.status, STATUS_HIGH_SNIPING_RISK_CANCEL)
        self.assertTrue(report.is_quote_canceled)
        self.assertEqual(report.defensive_quote_qty, 0)
        self.assertEqual(report.sniping_probability, 1.0)
        self.assertEqual(report.size_divergence_ratio, math.inf)

    def test_threshold_is_inclusive_at_the_exact_boundary(self) -> None:
        # dt = ln(2)/h with h = 0.10 puts P at exactly 0.50. The threshold is `>=`,
        # so the boundary cancels: at the point of indifference the safe answer is
        # not being on the book.
        boundary_ms = math.log(2.0) / 0.10
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=1000, latency_gap_ms=boundary_ms,
            volatility_annualized=0.20, spread_bps=2.0, min_lot_size=100,
        )
        report = self.engine.calculate_defensive_sizing(spec)

        self.assertEqual(report.sniping_probability, 0.5)
        self.assertEqual(report.status, STATUS_HIGH_SNIPING_RISK_CANCEL)

    def test_just_below_the_threshold_still_quotes(self) -> None:
        # dt = 6.9 ms, h = 0.10 -> P = 1 - exp(-0.69) = 0.4984239 -> 0.4984.
        # 1000 * (1 - 0.4984) = 501.6 -> 501.
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=1000, latency_gap_ms=6.9,
            volatility_annualized=0.20, spread_bps=2.0, min_lot_size=100,
        )
        report = self.engine.calculate_defensive_sizing(spec)

        self.assertEqual(report.status, STATUS_DEFENSIVELY_SIZED)
        self.assertAlmostEqual(report.sniping_probability, 0.4984, places=4)
        self.assertEqual(report.defensive_quote_qty, 501)

    def test_sub_minimum_lot_residual_is_pulled_not_rested(self) -> None:
        # 200 * (1 - 0.0952) = 180.96 -> 180, below a 200-share minimum.
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=200, latency_gap_ms=1.0,
            volatility_annualized=0.20, spread_bps=2.0, min_lot_size=200,
        )
        report = self.engine.calculate_defensive_sizing(spec)

        self.assertEqual(report.status, STATUS_MIN_LOT_CANCEL)
        self.assertTrue(report.is_quote_canceled)
        self.assertEqual(report.defensive_quote_qty, 0)
        self.assertLess(report.sniping_probability, 0.50)

    def test_high_risk_cancel_takes_precedence_over_min_lot_cancel(self) -> None:
        spec = MarketStateSpec(
            symbol="TSLA", base_quote_qty=150, latency_gap_ms=25.0,
            volatility_annualized=0.80, spread_bps=5.0, min_lot_size=100,
        )
        report = self.engine.calculate_defensive_sizing(spec)
        self.assertEqual(report.status, STATUS_HIGH_SNIPING_RISK_CANCEL)

    def test_lot_increment_floors_the_defensive_size(self) -> None:
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=1000, latency_gap_ms=1.0,
            volatility_annualized=0.20, spread_bps=2.0, min_lot_size=100,
            lot_increment=100,
        )
        report = self.engine.calculate_defensive_sizing(spec)
        self.assertEqual(report.defensive_quote_qty, 900)
        self.assertEqual(report.defensive_quote_qty % 100, 0)

    def test_default_lot_increment_preserves_unrounded_sizing(self) -> None:
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=1000, latency_gap_ms=1.0,
            volatility_annualized=0.20, spread_bps=2.0, min_lot_size=100,
        )
        self.assertEqual(
            self.engine.calculate_defensive_sizing(spec).defensive_quote_qty, 904
        )

    def test_lot_increment_can_push_a_size_under_the_minimum(self) -> None:
        # 1000 -> 904 raw, floored to 500 by a 500-share increment, which is still
        # above a 100-share minimum; raise the minimum past it and the quote is pulled.
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=1000, latency_gap_ms=1.0,
            volatility_annualized=0.20, spread_bps=2.0, min_lot_size=600,
            lot_increment=500,
        )
        report = self.engine.calculate_defensive_sizing(spec)
        self.assertEqual(report.status, STATUS_MIN_LOT_CANCEL)


class TestFailClosedOnDegradedTelemetry(unittest.TestCase):
    """The defect class this engine exists to prevent, applied to the engine itself."""

    def setUp(self) -> None:
        self.engine = LatencyArbitrageDefensiveSizingEngine()

    def _spec_with(self, **overrides) -> MarketStateSpec:
        kwargs = dict(
            symbol="AAPL", base_quote_qty=10000, latency_gap_ms=2.0,
            volatility_annualized=0.25, spread_bps=2.0, min_lot_size=100,
        )
        kwargs.update(overrides)
        return MarketStateSpec(**kwargs)

    def test_nan_latency_cancels_rather_than_posting_full_size(self) -> None:
        # Regression: the pre-fix engine returned QUOTE_DEFENSIVELY_SIZED with the
        # full 10,000 shares here -- maximum size posted on an unreadable probe.
        report = self.engine.calculate_defensive_sizing(
            self._spec_with(latency_gap_ms=float("nan"))
        )
        self.assertEqual(report.status, STATUS_INVALID_INPUT_CANCEL)
        self.assertTrue(report.is_quote_canceled)
        self.assertEqual(report.defensive_quote_qty, 0)
        self.assertEqual(report.sniping_probability, 1.0)
        self.assertIn("not finite", report.audit_notes)

    def test_nan_volatility_cancels(self) -> None:
        report = self.engine.calculate_defensive_sizing(
            self._spec_with(volatility_annualized=float("nan"))
        )
        self.assertEqual(report.status, STATUS_INVALID_INPUT_CANCEL)
        self.assertEqual(report.defensive_quote_qty, 0)
        self.assertTrue(math.isnan(report.sniping_hazard_per_ms))

    def test_infinite_latency_cancels(self) -> None:
        report = self.engine.calculate_defensive_sizing(
            self._spec_with(latency_gap_ms=float("inf"))
        )
        self.assertEqual(report.status, STATUS_INVALID_INPUT_CANCEL)
        self.assertEqual(report.defensive_quote_qty, 0)

    def test_negative_volatility_cancels(self) -> None:
        report = self.engine.calculate_defensive_sizing(
            self._spec_with(volatility_annualized=-0.25)
        )
        self.assertEqual(report.status, STATUS_INVALID_INPUT_CANCEL)
        self.assertEqual(report.defensive_quote_qty, 0)


class TestComparableSizeDivergence(unittest.TestCase):
    """MiFID II RTS 8 Art. 1(2)(c): sizes must not diverge by more than 50%."""

    def setUp(self) -> None:
        self.engine = LatencyArbitrageDefensiveSizingEngine()

    def _report(self, latency_ms: float):
        return self.engine.calculate_defensive_sizing(
            MarketStateSpec(
                symbol="AAPL", base_quote_qty=1000, latency_gap_ms=latency_ms,
                volatility_annualized=0.20, spread_bps=2.0, min_lot_size=100,
            )
        )

    def test_small_reduction_stays_within_comparable_size(self) -> None:
        # 1000 -> 904 diverges by (1000-904)/904 = 0.106 against the smaller quote.
        report = self._report(1.0)
        self.assertAlmostEqual(report.size_divergence_ratio, 96 / 904, places=6)
        self.assertFalse(report.breaches_comparable_size_one_sided)

    def test_large_reduction_flags_a_comparable_size_breach(self) -> None:
        # 1000 -> 501 diverges by (1000-501)/501 = 0.996, past the 50% limit: a firm
        # inside a market making agreement cannot apply this to one side alone.
        report = self._report(6.9)
        self.assertGreater(report.size_divergence_ratio, COMPARABLE_SIZE_MAX_DIVERGENCE)
        self.assertTrue(report.breaches_comparable_size_one_sided)

    def test_a_pulled_quote_diverges_without_bound(self) -> None:
        report = self._report(25.0)
        self.assertEqual(report.size_divergence_ratio, math.inf)
        self.assertTrue(report.breaches_comparable_size_one_sided)


class TestNmsRoundLot(unittest.TestCase):
    """17 CFR 242.600(b)(93) price tiers."""

    def test_tier_boundaries(self) -> None:
        cases = [
            (10.00, 100),
            (250.00, 100),      # inclusive upper bound of the 100-share tier
            (250.01, 40),
            (1000.00, 40),
            (1000.01, 10),
            (10000.00, 10),
            (10000.01, 1),
            (500000.00, 1),
        ]
        for price, expected in cases:
            with self.subTest(price=price):
                self.assertEqual(round_lot_for_nms_price(price), expected)

    def test_rejects_non_positive_or_non_finite_price(self) -> None:
        for bad in (0.0, -10.0, float("nan"), float("inf")):
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    round_lot_for_nms_price(bad)

    def test_high_priced_name_would_be_over_cancelled_by_a_flat_100_minimum(self) -> None:
        # The shipped min_lot_size default of 100 is the NMS round lot only at or
        # below $250.00. On a $1,500 stock the round lot is 10 shares, so a valid
        # 40-share defensive quote would be cancelled by the default.
        self.assertEqual(round_lot_for_nms_price(1500.00), 10)

        engine = LatencyArbitrageDefensiveSizingEngine()
        common = dict(
            symbol="BRKB", base_quote_qty=44, latency_gap_ms=1.0,
            volatility_annualized=0.20, spread_bps=2.0,
        )
        self.assertEqual(
            engine.calculate_defensive_sizing(
                MarketStateSpec(min_lot_size=100, **common)
            ).status,
            STATUS_MIN_LOT_CANCEL,
        )
        correct = engine.calculate_defensive_sizing(
            MarketStateSpec(min_lot_size=round_lot_for_nms_price(1500.00), **common)
        )
        self.assertEqual(correct.status, STATUS_DEFENSIVELY_SIZED)
        self.assertEqual(correct.defensive_quote_qty, 39)  # 44 * (1 - 0.0952) = 39.8


if __name__ == "__main__":
    unittest.main()
