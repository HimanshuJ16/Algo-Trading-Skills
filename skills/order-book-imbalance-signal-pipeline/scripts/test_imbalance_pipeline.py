"""Unit tests for order-book-imbalance-signal-pipeline.

Expected values are derived independently of the engine's own expressions:

* The imbalance is checked against ratios worked out by hand.
* The weighted mid is checked against the identity ``W = M + I_top * s / 2``,
  which follows algebraically from the definition but shares no code path with
  it -- so a sign flip or a swapped price/volume pairing in the implementation
  fails here rather than being restated.
"""

import logging
import math
import unittest

from imbalance_pipeline import (
    FastPathOBIPipelineEngine,
    ImbalanceSignalType,
    L2OrderBookTop,
    OBIConfigurationError,
    OBIValidationError,
)


def book(**overrides) -> L2OrderBookTop:
    """A valid, balanced book; override only the field under test."""
    defaults = dict(
        symbol="TEST",
        bid_price=100.0,
        bid_volume=500.0,
        ask_price=101.0,
        ask_volume=500.0,
        timestamp_ns=1_000,
    )
    defaults.update(overrides)
    return L2OrderBookTop(**defaults)


class SilencedLogMixin:
    """Rejections log at WARNING; silence them for the expected-failure cases."""

    def silence_logs(self) -> None:
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)


class TestConfigurationValidation(unittest.TestCase):
    """The threshold and depth settings decide whether a signal can ever mean anything."""

    def test_zero_threshold_is_rejected(self):
        # I >= 0.0 holds for a perfectly balanced book, so a zero threshold would
        # emit HIGH_BUY_PRESSURE on literally every update.
        with self.assertRaises(OBIConfigurationError):
            FastPathOBIPipelineEngine(imbalance_threshold=0.0)

    def test_negative_threshold_is_rejected(self):
        with self.assertRaises(OBIConfigurationError):
            FastPathOBIPipelineEngine(imbalance_threshold=-0.5)

    def test_threshold_above_one_is_rejected(self):
        # |I| <= 1 by construction, so a threshold above 1.0 can never trigger.
        with self.assertRaises(OBIConfigurationError):
            FastPathOBIPipelineEngine(imbalance_threshold=1.5)

    def test_non_finite_threshold_is_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(threshold=bad):
                with self.assertRaises(OBIConfigurationError):
                    FastPathOBIPipelineEngine(imbalance_threshold=bad)

    def test_threshold_of_one_is_accepted(self):
        engine = FastPathOBIPipelineEngine(imbalance_threshold=1.0)
        self.assertEqual(engine.imbalance_threshold, 1.0)

    def test_bool_threshold_is_rejected(self):
        # True == 1 numerically; accepting it would silently configure a
        # never-triggering engine from an obvious caller mistake.
        with self.assertRaises(OBIConfigurationError):
            FastPathOBIPipelineEngine(imbalance_threshold=True)

    def test_invalid_depth_levels_rejected(self):
        for bad in (0, -1, 1.5, True):
            with self.subTest(depth_levels=bad):
                with self.assertRaises(OBIConfigurationError):
                    FastPathOBIPipelineEngine(depth_levels=bad)


class TestImbalanceAndWeightedMid(unittest.TestCase):
    """Core quantitative behaviour on well-formed books."""

    def setUp(self):
        self.engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)

    def assert_weighted_mid_identity(self, result):
        """W - M must equal I_top * spread / 2 (derived, not copied from the code)."""
        self.assertIsNotNone(result.weighted_mid_price)
        expected = result.mid_price + result.imbalance * result.spread / 2.0
        self.assertAlmostEqual(result.weighted_mid_price, expected, places=12)

    def test_buy_pressure_at_exact_threshold(self):
        # 800 vs 200 over 1000 -> I = 600/1000 = +0.60 exactly, and >= is inclusive.
        result = self.engine.process_l2_update(
            book(bid_volume=800.0, ask_volume=200.0)
        )
        self.assertEqual(result.signal_type, ImbalanceSignalType.HIGH_BUY_PRESSURE)
        self.assertAlmostEqual(result.imbalance, 0.60, places=12)
        # Hand-computed: (800*101 + 200*100) / 1000 = (80800 + 20000)/1000 = 100.80.
        self.assertAlmostEqual(result.weighted_mid_price, 100.80, places=10)
        self.assertAlmostEqual(result.mid_price, 100.50, places=10)
        self.assertAlmostEqual(result.spread, 1.0, places=10)
        self.assert_weighted_mid_identity(result)
        self.assertTrue(result.is_actionable)
        self.assertEqual(result.levels_used, 1)
        self.assertIsNone(result.rejection_reason)

    def test_sell_pressure(self):
        # 100 vs 900 over 1000 -> I = -800/1000 = -0.80.
        result = self.engine.process_l2_update(
            book(symbol="BTCUSDT", bid_price=50_000.0, bid_volume=100.0,
                 ask_price=50_001.0, ask_volume=900.0)
        )
        self.assertEqual(result.signal_type, ImbalanceSignalType.HIGH_SELL_PRESSURE)
        self.assertAlmostEqual(result.imbalance, -0.80, places=12)
        self.assert_weighted_mid_identity(result)

    def test_heavy_bid_pulls_weighted_mid_towards_the_ask(self):
        # Direction check independent of the formula: buying pressure must move
        # the fair price up, never down. A swapped price/volume pairing fails here.
        result = self.engine.process_l2_update(
            book(bid_volume=900.0, ask_volume=100.0)
        )
        self.assertGreater(result.weighted_mid_price, result.mid_price)
        self.assertLess(result.weighted_mid_price, 101.0)

    def test_heavy_ask_pulls_weighted_mid_towards_the_bid(self):
        result = self.engine.process_l2_update(
            book(bid_volume=100.0, ask_volume=900.0)
        )
        self.assertLess(result.weighted_mid_price, result.mid_price)
        self.assertGreater(result.weighted_mid_price, 100.0)

    def test_balanced_book_is_neutral_and_weighted_mid_equals_mid(self):
        result = self.engine.process_l2_update(book())
        self.assertEqual(result.signal_type, ImbalanceSignalType.NEUTRAL)
        self.assertAlmostEqual(result.imbalance, 0.0, places=12)
        self.assertAlmostEqual(result.weighted_mid_price, result.mid_price, places=12)
        self.assertFalse(result.is_actionable)

    def test_one_sided_book_saturates_at_plus_one(self):
        result = self.engine.process_l2_update(book(ask_volume=0.0))
        self.assertAlmostEqual(result.imbalance, 1.0, places=12)
        # With no ask size the weighted mid collapses onto the ask price itself.
        self.assertAlmostEqual(result.weighted_mid_price, 101.0, places=10)
        self.assertEqual(result.signal_type, ImbalanceSignalType.HIGH_BUY_PRESSURE)

    def test_one_sided_book_saturates_at_minus_one(self):
        result = self.engine.process_l2_update(book(bid_volume=0.0))
        self.assertAlmostEqual(result.imbalance, -1.0, places=12)
        self.assertAlmostEqual(result.weighted_mid_price, 100.0, places=10)
        self.assertEqual(result.signal_type, ImbalanceSignalType.HIGH_SELL_PRESSURE)

    def test_reported_imbalance_and_classification_agree_just_below_threshold(self):
        # Regression: the engine used to report round(I, 4) while classifying the
        # raw value, so this book reported imbalance 0.6 alongside NEUTRAL.
        result = self.engine.process_l2_update(
            book(bid_volume=79_999.0, ask_volume=20_001.0)
        )
        self.assertEqual(result.signal_type, ImbalanceSignalType.NEUTRAL)
        self.assertLess(result.imbalance, 0.60)
        self.assertAlmostEqual(result.imbalance, 59_998.0 / 100_000.0, places=12)

    def test_imbalance_always_within_unit_interval(self):
        cases = [(0.0, 7.5), (7.5, 0.0), (1e-9, 1e9), (1e9, 1e-9), (3.0, 4.0)]
        for v_bid, v_ask in cases:
            with self.subTest(bid=v_bid, ask=v_ask):
                result = self.engine.process_l2_update(
                    book(bid_volume=v_bid, ask_volume=v_ask)
                )
                self.assertGreaterEqual(result.imbalance, -1.0)
                self.assertLessEqual(result.imbalance, 1.0)


class TestInputValidation(SilencedLogMixin, unittest.TestCase):
    """Bad data must never leave the engine wearing a directional signal."""

    def setUp(self):
        self.silence_logs()
        self.engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)

    def assert_unreliable(self, result, kind):
        self.assertEqual(result.signal_type, ImbalanceSignalType.UNRELIABLE)
        self.assertEqual(result.rejection_reason, kind)
        self.assertIsNone(result.imbalance)
        self.assertIsNone(result.weighted_mid_price)
        self.assertIsNone(result.mid_price)
        self.assertIsNone(result.spread)
        self.assertFalse(result.is_actionable)

    def test_negative_bid_volume_rejected(self):
        # Regression: bid -100 against ask 200 gave I = (-100-200)/100 = -3.0 and
        # a full HIGH_SELL_PRESSURE signal from an impossible book.
        result = self.engine.process_l2_update(
            book(bid_volume=-100.0, ask_volume=200.0)
        )
        self.assert_unreliable(result, "INVALID_VOLUME")

    def test_negative_volumes_summing_below_zero_rejected(self):
        # Regression: this pair used to be absorbed as I = 0.0 / NEUTRAL, i.e. a
        # corrupt book silently reported as balanced.
        result = self.engine.process_l2_update(
            book(bid_volume=-500.0, ask_volume=200.0)
        )
        self.assert_unreliable(result, "INVALID_VOLUME")

    def test_nan_volume_rejected(self):
        # Regression: NaN survives a `total <= 0.0` guard, giving a NaN imbalance
        # and a NaN weighted mid classified as NEUTRAL.
        result = self.engine.process_l2_update(book(bid_volume=float("nan")))
        self.assert_unreliable(result, "INVALID_VOLUME")

    def test_infinite_volume_rejected(self):
        result = self.engine.process_l2_update(book(ask_volume=float("inf")))
        self.assert_unreliable(result, "INVALID_VOLUME")

    def test_non_numeric_volume_rejected(self):
        result = self.engine.process_l2_update(book(bid_volume="500"))
        self.assert_unreliable(result, "INVALID_VOLUME")

    def test_zero_price_rejected(self):
        # Regression: zero prices used to yield weighted_mid 0.0 / mid 0.0 while
        # still emitting HIGH_BUY_PRESSURE -- a price of zero into an execution worker.
        result = self.engine.process_l2_update(
            book(bid_price=0.0, ask_price=0.0, bid_volume=800.0, ask_volume=200.0)
        )
        self.assert_unreliable(result, "INVALID_PRICE")

    def test_negative_price_rejected(self):
        result = self.engine.process_l2_update(book(bid_price=-1.0))
        self.assert_unreliable(result, "INVALID_PRICE")

    def test_nan_price_rejected(self):
        result = self.engine.process_l2_update(book(ask_price=float("nan")))
        self.assert_unreliable(result, "INVALID_PRICE")

    def test_crossed_book_rejected(self):
        # Regression: bid 101 / ask 100 with 800 vs 200 emitted HIGH_BUY_PRESSURE.
        result = self.engine.process_l2_update(
            book(bid_price=101.0, ask_price=100.0, bid_volume=800.0, ask_volume=200.0)
        )
        self.assert_unreliable(result, "CROSSED_OR_LOCKED_BOOK")

    def test_locked_book_rejected_by_default(self):
        result = self.engine.process_l2_update(book(bid_price=100.0, ask_price=100.0))
        self.assert_unreliable(result, "CROSSED_OR_LOCKED_BOOK")

    def test_locked_book_accepted_when_opted_in(self):
        engine = FastPathOBIPipelineEngine(allow_locked_book=True)
        result = engine.process_l2_update(
            book(bid_price=100.0, ask_price=100.0, bid_volume=800.0, ask_volume=200.0)
        )
        self.assertEqual(result.signal_type, ImbalanceSignalType.HIGH_BUY_PRESSURE)
        self.assertAlmostEqual(result.spread, 0.0, places=12)
        # A zero spread collapses the weighted mid onto the mid, by the identity.
        self.assertAlmostEqual(result.weighted_mid_price, result.mid_price, places=12)

    def test_crossed_book_still_rejected_when_locked_is_allowed(self):
        engine = FastPathOBIPipelineEngine(allow_locked_book=True)
        result = engine.process_l2_update(book(bid_price=101.0, ask_price=100.0))
        self.assertEqual(result.rejection_reason, "CROSSED_OR_LOCKED_BOOK")

    def test_empty_book_is_unreliable_not_neutral(self):
        # Regression: zero volume on both sides used to report I = 0.0 / NEUTRAL,
        # conflating "measured and balanced" with "no data at all".
        result = self.engine.process_l2_update(
            book(bid_volume=0.0, ask_volume=0.0)
        )
        self.assert_unreliable(result, "EMPTY_BOOK")

    def test_nan_timestamp_rejected(self):
        # Regression: a float NaN timestamp was accepted (NaN comparisons are all
        # False), stored, and then disabled the ordering check for the next update.
        result = self.engine.process_l2_update(book(timestamp_ns=float("nan")))
        self.assert_unreliable(result, "INVALID_TIMESTAMP")

    def test_non_integer_timestamp_rejected(self):
        for bad in (1_000.5, "1000", None, True):
            with self.subTest(timestamp_ns=bad):
                result = self.engine.process_l2_update(book(timestamp_ns=bad))
                self.assert_unreliable(result, "INVALID_TIMESTAMP")

    def test_negative_timestamp_rejected(self):
        result = self.engine.process_l2_update(book(timestamp_ns=-1))
        self.assert_unreliable(result, "INVALID_TIMESTAMP")

    def test_bad_timestamp_does_not_disable_the_ordering_guard(self):
        self.engine.process_l2_update(book(timestamp_ns=5_000))
        self.engine.process_l2_update(book(timestamp_ns=float("nan")))
        result = self.engine.process_l2_update(book(timestamp_ns=4_000))
        self.assert_unreliable(result, "TIMESTAMP_REGRESSION")

    def test_rejections_are_counted_by_kind(self):
        self.engine.process_l2_update(book(bid_volume=float("nan")))
        self.engine.process_l2_update(book(bid_price=101.0, ask_price=100.0))
        self.engine.process_l2_update(book(bid_volume=0.0, ask_volume=0.0))
        self.assertEqual(self.engine.rejected_update_count, 3)
        self.assertEqual(self.engine.rejections_by_kind["INVALID_VOLUME"], 1)
        self.assertEqual(self.engine.rejections_by_kind["CROSSED_OR_LOCKED_BOOK"], 1)
        self.assertEqual(self.engine.rejections_by_kind["EMPTY_BOOK"], 1)
        self.assertEqual(self.engine.total_signals_emitted, 0)

    def test_strict_mode_raises_instead_of_returning(self):
        engine = FastPathOBIPipelineEngine(strict=True)
        with self.assertRaises(OBIValidationError):
            engine.process_l2_update(book(bid_volume=-1.0))
        self.assertEqual(engine.rejections_by_kind["INVALID_VOLUME"], 1)

    def test_engine_recovers_after_a_rejected_update(self):
        self.engine.process_l2_update(book(bid_volume=float("nan")))
        result = self.engine.process_l2_update(
            book(bid_volume=800.0, ask_volume=200.0, timestamp_ns=2_000)
        )
        self.assertEqual(result.signal_type, ImbalanceSignalType.HIGH_BUY_PRESSURE)


class TestTimestampOrdering(SilencedLogMixin, unittest.TestCase):
    """Out-of-order delivery must not produce a signal from a superseded book."""

    def setUp(self):
        self.silence_logs()
        self.engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)

    def test_regressing_timestamp_rejected(self):
        self.engine.process_l2_update(book(timestamp_ns=5_000))
        result = self.engine.process_l2_update(book(timestamp_ns=4_999))
        self.assertEqual(result.signal_type, ImbalanceSignalType.UNRELIABLE)
        self.assertEqual(result.rejection_reason, "TIMESTAMP_REGRESSION")

    def test_equal_timestamp_accepted(self):
        # Venues legitimately stamp several book events within one clock tick.
        self.engine.process_l2_update(book(timestamp_ns=5_000))
        result = self.engine.process_l2_update(
            book(timestamp_ns=5_000, bid_volume=800.0, ask_volume=200.0)
        )
        self.assertEqual(result.signal_type, ImbalanceSignalType.HIGH_BUY_PRESSURE)

    def test_timestamp_state_is_tracked_per_symbol(self):
        # Two symbols interleaved on one feed have independent clocks; a lower
        # timestamp on a different symbol is not a regression.
        self.engine.process_l2_update(book(symbol="AAA", timestamp_ns=9_000))
        result = self.engine.process_l2_update(book(symbol="BBB", timestamp_ns=1_000))
        self.assertNotEqual(result.signal_type, ImbalanceSignalType.UNRELIABLE)

    def test_rejected_update_does_not_advance_the_clock(self):
        self.engine.process_l2_update(book(timestamp_ns=5_000))
        self.engine.process_l2_update(book(timestamp_ns=9_000, bid_volume=-1.0))
        result = self.engine.process_l2_update(book(timestamp_ns=6_000))
        self.assertNotEqual(result.signal_type, ImbalanceSignalType.UNRELIABLE)


class TestDepthAggregation(SilencedLogMixin, unittest.TestCase):
    """Multi-level imbalance, and the double-counting trap it invites."""

    def setUp(self):
        self.silence_logs()

    def test_depth_two_aggregates_the_second_level(self):
        engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60, depth_levels=2)
        # Top of book alone is +0.60; adding 200 behind the bid and 600 behind the
        # ask gives (800+200 - (200+600)) / 1800 = 200/1800 = 0.1111...
        result = engine.process_l2_update(
            book(
                bid_volume=800.0, ask_volume=200.0,
                bid_depth=[(99.0, 200.0)],
                ask_depth=[(102.0, 600.0)],
            )
        )
        self.assertAlmostEqual(result.imbalance, 200.0 / 1800.0, places=12)
        self.assertEqual(result.signal_type, ImbalanceSignalType.NEUTRAL)
        self.assertEqual(result.levels_used, 2)

    def test_weighted_mid_stays_top_of_book_under_depth_aggregation(self):
        engine = FastPathOBIPipelineEngine(depth_levels=2)
        result = engine.process_l2_update(
            book(
                bid_volume=800.0, ask_volume=200.0,
                bid_depth=[(99.0, 5_000.0)],
                ask_depth=[(102.0, 5_000.0)],
            )
        )
        # Unchanged by the depth behind it: (800*101 + 200*100)/1000 = 100.80.
        self.assertAlmostEqual(result.weighted_mid_price, 100.80, places=10)

    def test_insufficient_depth_is_rejected_not_downgraded(self):
        engine = FastPathOBIPipelineEngine(depth_levels=3)
        result = engine.process_l2_update(
            book(bid_depth=[(99.0, 100.0)], ask_depth=[(102.0, 100.0)])
        )
        self.assertEqual(result.rejection_reason, "INSUFFICIENT_DEPTH")

    def test_depth_repeating_level_one_is_rejected(self):
        # The classic double-count: passing the whole ladder, touch included.
        engine = FastPathOBIPipelineEngine(depth_levels=2)
        result = engine.process_l2_update(
            book(
                bid_depth=[(100.0, 500.0), (99.0, 100.0)],
                ask_depth=[(101.0, 500.0), (102.0, 100.0)],
            )
        )
        self.assertEqual(result.rejection_reason, "MALFORMED_DEPTH")

    def test_generator_depth_rejected_rather_than_crashing(self):
        # Regression: a generator was consumed by the ladder scan and then hit an
        # unhandled `len()` TypeError, taking the feed loop down with it.
        engine = FastPathOBIPipelineEngine(depth_levels=2)
        result = engine.process_l2_update(
            book(
                bid_depth=((price, 100.0) for price in (99.0,)),
                ask_depth=[(102.0, 100.0)],
            )
        )
        self.assertEqual(result.rejection_reason, "MALFORMED_DEPTH")

    def test_out_of_order_depth_ladder_rejected(self):
        engine = FastPathOBIPipelineEngine(depth_levels=2)
        result = engine.process_l2_update(
            book(bid_depth=[(101.5, 100.0)], ask_depth=[(102.0, 100.0)])
        )
        self.assertEqual(result.rejection_reason, "MALFORMED_DEPTH")

    def test_malformed_depth_entry_rejected(self):
        engine = FastPathOBIPipelineEngine(depth_levels=2)
        result = engine.process_l2_update(
            book(bid_depth=[(99.0,)], ask_depth=[(102.0, 100.0)])
        )
        self.assertEqual(result.rejection_reason, "MALFORMED_DEPTH")

    def test_nan_volume_in_depth_rejected(self):
        engine = FastPathOBIPipelineEngine(depth_levels=2)
        result = engine.process_l2_update(
            book(bid_depth=[(99.0, float("nan"))], ask_depth=[(102.0, 100.0)])
        )
        self.assertEqual(result.rejection_reason, "INVALID_VOLUME")

    def test_depth_beyond_configured_levels_is_ignored(self):
        engine = FastPathOBIPipelineEngine(depth_levels=2)
        result = engine.process_l2_update(
            book(
                bid_volume=800.0, ask_volume=200.0,
                bid_depth=[(99.0, 200.0), (98.0, 1e6)],
                ask_depth=[(102.0, 600.0), (103.0, 1e6)],
            )
        )
        self.assertAlmostEqual(result.imbalance, 200.0 / 1800.0, places=12)

    def test_empty_touch_with_depth_yields_imbalance_but_no_weighted_mid(self):
        engine = FastPathOBIPipelineEngine(depth_levels=2)
        result = engine.process_l2_update(
            book(
                bid_volume=0.0, ask_volume=0.0,
                bid_depth=[(99.0, 900.0)],
                ask_depth=[(102.0, 100.0)],
            )
        )
        self.assertAlmostEqual(result.imbalance, 0.80, places=12)
        self.assertIsNone(result.weighted_mid_price)
        self.assertEqual(result.signal_type, ImbalanceSignalType.HIGH_BUY_PRESSURE)


class TestSignalDispatch(SilencedLogMixin, unittest.TestCase):
    """The strategy callback must not be able to stop the feed loop."""

    def setUp(self):
        self.silence_logs()

    def test_callback_receives_only_actionable_signals(self):
        received = []
        engine = FastPathOBIPipelineEngine(
            imbalance_threshold=0.60, signal_callback=received.append
        )
        engine.process_l2_update(book(timestamp_ns=1))
        engine.process_l2_update(
            book(bid_volume=800.0, ask_volume=200.0, timestamp_ns=2)
        )
        engine.process_l2_update(book(bid_volume=float("nan"), timestamp_ns=3))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].signal_type, ImbalanceSignalType.HIGH_BUY_PRESSURE)

    def test_raising_callback_does_not_propagate(self):
        # Regression: a strategy exception used to escape into the feed handler.
        def explode(_result):
            raise RuntimeError("strategy blew up")

        engine = FastPathOBIPipelineEngine(
            imbalance_threshold=0.60, signal_callback=explode
        )
        logging.disable(logging.NOTSET)
        with self.assertLogs("imbalance_pipeline", level=logging.ERROR):
            result = engine.process_l2_update(
                book(bid_volume=800.0, ask_volume=200.0)
            )
        self.assertEqual(result.signal_type, ImbalanceSignalType.HIGH_BUY_PRESSURE)
        self.assertEqual(engine.callback_error_count, 1)

    def test_feed_loop_survives_a_failing_callback(self):
        calls = []

        def flaky(result):
            calls.append(result)
            raise ValueError("transient")

        engine = FastPathOBIPipelineEngine(
            imbalance_threshold=0.60, signal_callback=flaky
        )
        logging.disable(logging.NOTSET)
        with self.assertLogs("imbalance_pipeline", level=logging.ERROR):
            for tick in range(3):
                engine.process_l2_update(
                    book(bid_volume=800.0, ask_volume=200.0, timestamp_ns=tick)
                )
        self.assertEqual(len(calls), 3)
        self.assertEqual(engine.callback_error_count, 3)
        self.assertEqual(engine.total_signals_emitted, 3)

    def test_keyboard_interrupt_from_callback_still_propagates(self):
        def interrupt(_result):
            raise KeyboardInterrupt

        engine = FastPathOBIPipelineEngine(
            imbalance_threshold=0.60, signal_callback=interrupt
        )
        with self.assertRaises(KeyboardInterrupt):
            engine.process_l2_update(book(bid_volume=800.0, ask_volume=200.0))


class TestReporting(SilencedLogMixin, unittest.TestCase):
    """A run is only clean if nothing was dropped along the way."""

    def setUp(self):
        self.silence_logs()

    def test_clean_run_reports_clean(self):
        engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)
        engine.process_l2_update(book(bid_volume=800.0, ask_volume=200.0))
        report = engine.generate_report()
        self.assertEqual(report.status, "OBI_PIPELINE_CLEAN")
        self.assertEqual(report.updates_processed, 1)
        self.assertEqual(report.signals_emitted, 1)
        self.assertEqual(report.rejected_updates, 0)

    def test_rejected_update_degrades_the_report(self):
        engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)
        engine.process_l2_update(book(bid_volume=800.0, ask_volume=200.0))
        engine.process_l2_update(book(bid_price=101.0, ask_price=100.0))
        report = engine.generate_report()
        self.assertEqual(report.status, "OBI_PIPELINE_DEGRADED")
        self.assertEqual(report.rejected_updates, 1)
        self.assertEqual(report.rejections_by_kind["CROSSED_OR_LOCKED_BOOK"], 1)
        self.assertIn("partial sample", report.notes)

    def test_callback_failure_degrades_the_report(self):
        def explode(_result):
            raise RuntimeError("boom")

        engine = FastPathOBIPipelineEngine(
            imbalance_threshold=0.60, signal_callback=explode
        )
        logging.disable(logging.NOTSET)
        with self.assertLogs("imbalance_pipeline", level=logging.ERROR):
            engine.process_l2_update(book(bid_volume=800.0, ask_volume=200.0))
        report = engine.generate_report()
        self.assertEqual(report.status, "OBI_PIPELINE_DEGRADED")
        self.assertEqual(report.callback_errors, 1)

    def test_report_snapshot_is_decoupled_from_engine_state(self):
        engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)
        engine.process_l2_update(book(bid_volume=-1.0))
        report = engine.generate_report()
        engine.process_l2_update(book(bid_volume=-1.0, timestamp_ns=2))
        self.assertEqual(report.rejections_by_kind["INVALID_VOLUME"], 1)


class TestLatencyInstrumentation(SilencedLogMixin, unittest.TestCase):
    """The latency field must be honest about what it measures."""

    def setUp(self):
        self.silence_logs()

    def test_latency_is_reported_and_non_negative(self):
        engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)
        result = engine.process_l2_update(book(bid_volume=800.0, ask_volume=200.0))
        self.assertIsInstance(result.calculation_latency_ns, int)
        self.assertGreaterEqual(result.calculation_latency_ns, 0)

    def test_rejected_updates_are_also_timed(self):
        engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)
        result = engine.process_l2_update(book(bid_volume=float("nan")))
        self.assertGreaterEqual(result.calculation_latency_ns, 0)

    def test_no_nan_ever_reaches_a_consumer(self):
        engine = FastPathOBIPipelineEngine(imbalance_threshold=0.60)
        for bad in (float("nan"), float("inf"), -1.0):
            with self.subTest(volume=bad):
                result = engine.process_l2_update(book(bid_volume=bad))
                for value in (result.imbalance, result.weighted_mid_price,
                              result.mid_price, result.spread):
                    self.assertFalse(
                        isinstance(value, float) and math.isnan(value),
                        "a NaN escaped to the consumer",
                    )


if __name__ == "__main__":
    unittest.main()
