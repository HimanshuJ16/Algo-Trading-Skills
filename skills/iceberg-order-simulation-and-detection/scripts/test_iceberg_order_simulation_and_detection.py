import unittest

from iceberg_order_simulation_and_detection import (
    MAX_CONFIDENCE_SCORE,
    IcebergDetectionReport,
    IcebergDetectorEngine,
    Level2DepthSnapshot,
    TradePrint,
)


def build_iceberg_sequence(detector, price, book_side, aggressor, baseline, peak,
                           trade_qty, n_trades, start_ts=1_000, step=1):
    """
    Drives one price level through ``n_trades`` executions with a depth refill between
    each, which is the canonical iceberg pattern: peak consumed, peak restored.

    Returns the last report emitted (or None). Timestamps advance by ``step`` nanos so
    every message is strictly ordered.
    """
    ts = start_ts
    detector.process_l2_depth(Level2DepthSnapshot(price, book_side, baseline, ts))
    last_report = None
    for i in range(n_trades):
        ts += step
        detector.process_l2_depth(Level2DepthSnapshot(price, book_side, 1, ts))
        ts += step
        last_report = detector.process_trade_print(
            TradePrint(f"T{i}", price, trade_qty, aggressor, ts))
        if i < n_trades - 1:
            ts += step
            detector.process_l2_depth(Level2DepthSnapshot(price, book_side, peak, ts))
    return last_report


class TestDocumentedScenario(unittest.TestCase):
    """The scenario SKILL.md's Verification section promises."""

    def test_skill_md_verification_scenario(self):
        # $100.00 bid, Q0 = 500, four 400-share SELL-aggressor prints (1,600 traded)
        # with the level refilling between them.
        detector = IcebergDetectorEngine(symbol="AAPL", tick_size=0.01)
        report = build_iceberg_sequence(detector, 100.00, "BID", "SELL",
                                        baseline=500, peak=500, trade_qty=400, n_trades=4)

        self.assertIsNotNone(report)
        self.assertEqual(report.signal_classification, "BULLISH_HIDDEN_BUY")
        self.assertEqual(report.iceberg_side, "BUY")
        self.assertEqual(report.cumulative_traded_quantity, 1600)
        # Hidden estimate is V_cum - Q0, computed independently: 1600 - 500 = 1100.
        self.assertEqual(report.estimated_hidden_quantity, 1100)
        self.assertEqual(report.refill_count, 3)
        # Score: 0.50 + 0.10*min(3.2, 3.0) + 0.10*min(3, 5) = 1.10, clamped to the cap.
        self.assertEqual(report.confidence_score, MAX_CONFIDENCE_SCORE)
        self.assertTrue(report.refill_peaks_consistent)
        self.assertEqual(report.contra_side_traded_quantity, 0)


class TestDetectionCore(unittest.TestCase):

    def setUp(self):
        self.detector = IcebergDetectorEngine(symbol="AAPL", min_volume_ratio=1.5,
                                              min_refill_count=2, tick_size=0.01)

    def test_bullish_hidden_buy_iceberg_detection(self):
        report = build_iceberg_sequence(self.detector, 100.00, "BID", "SELL",
                                        baseline=500, peak=500, trade_qty=400, n_trades=3)
        self.assertIsNotNone(report)
        self.assertEqual(report.signal_classification, "BULLISH_HIDDEN_BUY")
        self.assertEqual(report.cumulative_traded_quantity, 1200)
        self.assertEqual(report.estimated_hidden_quantity, 700)
        # 0.50 + 0.10*2.4 + 0.10*2 = 0.94, no penalties.
        self.assertAlmostEqual(report.confidence_score, 0.94, places=2)
        self.assertAlmostEqual(report.volume_ratio, 2.4, places=4)

    def test_bearish_hidden_sell_iceberg_on_ask_side(self):
        report = build_iceberg_sequence(self.detector, 100.05, "ASK", "BUY",
                                        baseline=500, peak=500, trade_qty=400, n_trades=3)
        self.assertIsNotNone(report)
        self.assertEqual(report.signal_classification, "BEARISH_HIDDEN_SELL")
        self.assertEqual(report.iceberg_side, "SELL")

    def test_no_iceberg_under_low_volume(self):
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1000))
        self.assertIsNone(
            self.detector.process_trade_print(TradePrint("T1", 100.00, 100, "SELL", 1001)))

    def test_no_iceberg_without_enough_refills(self):
        """A single aggressive sweep clears the volume bar but must not flag."""
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1000))
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 1, 1001))
        report = self.detector.process_trade_print(TradePrint("S1", 100.00, 5000, "SELL", 1002))
        self.assertIsNone(report)
        state = self.detector.get_level_state(100.00)
        self.assertGreaterEqual(state["cumulative_traded"] / state["initial_display"], 1.5)
        self.assertEqual(state["refill_count"], 0)

    def test_threshold_is_inclusive_at_the_boundary(self):
        """Exactly 1.5x with exactly 2 refills flags; a hair under does not."""
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        # Q0 = 100; 3 x 50 = 150 traded = exactly 1.50x, with 2 refills.
        report = build_iceberg_sequence(detector, 10.00, "BID", "SELL",
                                        baseline=100, peak=100, trade_qty=50, n_trades=3)
        self.assertIsNotNone(report)
        self.assertAlmostEqual(report.volume_ratio, 1.5, places=6)

        detector2 = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        # 149 / 100 = 1.49x -> below the bar despite the same 2 refills.
        r2 = build_iceberg_sequence(detector2, 10.00, "BID", "SELL",
                                    baseline=100, peak=100, trade_qty=49, n_trades=3)
        self.assertIsNone(r2)

    def test_is_initial_detection_only_on_first_emission(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        first = build_iceberg_sequence(detector, 100.00, "BID", "SELL",
                                       baseline=500, peak=500, trade_qty=400, n_trades=3)
        self.assertTrue(first.is_initial_detection)
        again = detector.process_trade_print(TradePrint("T99", 100.00, 400, "SELL", 9_999))
        self.assertFalse(again.is_initial_detection)


class TestSideClassificationRegression(unittest.TestCase):
    """
    Regression: classification used ``book_side == 'BID' or aggressor == 'SELL'``, so a
    single SELL-aggressor print at an ASK level flipped a resting sell iceberg into a
    BULLISH_HIDDEN_BUY signal -- an inverted directional call on institutional flow.
    """

    def test_contra_side_print_cannot_invert_an_ask_side_iceberg(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        build_iceberg_sequence(detector, 100.05, "ASK", "BUY",
                               baseline=500, peak=500, trade_qty=400, n_trades=3)
        report = detector.process_trade_print(TradePrint("X1", 100.05, 400, "SELL", 9_000))
        self.assertEqual(report.signal_classification, "BEARISH_HIDDEN_SELL")
        self.assertEqual(report.iceberg_side, "SELL")

    def test_contra_side_volume_excluded_from_hidden_estimate(self):
        """
        Regression: volume from both aggressor sides accumulated into V_cum, so 5,000
        shares lifted from the ask at a level tracked as a hidden bid were reported as
        hidden bid size.
        """
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        build_iceberg_sequence(detector, 100.00, "BID", "SELL",
                               baseline=500, peak=500, trade_qty=400, n_trades=3)
        report = detector.process_trade_print(TradePrint("C1", 100.00, 5000, "BUY", 9_000))
        self.assertEqual(report.cumulative_traded_quantity, 1200)
        self.assertEqual(report.estimated_hidden_quantity, 700)   # not 5,300
        self.assertEqual(report.contra_side_traded_quantity, 5000)
        # Two-sided flow is penalized: 0.94 - 0.15 = 0.79.
        self.assertAlmostEqual(report.confidence_score, 0.79, places=2)


class TestFeedIntegrityRegressions(unittest.TestCase):

    def test_duplicate_trade_id_is_not_double_counted(self):
        """Regression: a replayed print after a reconnect inflated V_cum."""
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        build_iceberg_sequence(detector, 100.00, "BID", "SELL",
                               baseline=500, peak=500, trade_qty=400, n_trades=3)
        self.assertEqual(detector.get_level_state(100.00)["cumulative_traded"], 1200)
        self.assertIsNone(detector.process_trade_print(
            TradePrint("T2", 100.00, 400, "SELL", 1_006)))
        self.assertEqual(detector.get_level_state(100.00)["cumulative_traded"], 1200)

    def test_dedup_can_be_disabled(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01, dedup_capacity=0)
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1))
        detector.process_trade_print(TradePrint("T1", 100.00, 100, "SELL", 2))
        detector.process_trade_print(TradePrint("T1", 100.00, 100, "SELL", 3))
        self.assertEqual(detector.get_level_state(100.00)["cumulative_traded"], 200)

    def test_stale_snapshot_does_not_book_a_phantom_refill(self):
        """Regression: a late-arriving old snapshot read as depth increasing."""
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 10))
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 100, 11))
        detector.process_trade_print(TradePrint("T1", 100.00, 400, "SELL", 11))
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 5))  # stale
        self.assertEqual(detector.get_level_state(100.00)["refill_count"], 0)
        self.assertEqual(detector.get_level_state(100.00)["current_display"], 100)

    def test_float_representation_noise_maps_to_one_level(self):
        """Regression: 0.1 + 0.2 != 0.3 split one level into two trackers."""
        detector = IcebergDetectorEngine("BTC-USD", 1.5, 2)   # no tick_size
        detector.process_l2_depth(Level2DepthSnapshot(0.1 + 0.2, "BID", 500, 1))
        detector.process_trade_print(TradePrint("T1", 0.3, 400, "SELL", 2))
        self.assertEqual(len(detector.price_trackers), 1)
        self.assertEqual(detector.get_level_state(0.3)["cumulative_traded"], 400)

    def test_tick_size_bins_prices_to_integer_ticks(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        detector.process_l2_depth(Level2DepthSnapshot(100.30, "BID", 500, 1))
        detector.process_trade_print(TradePrint("T1", 100.3000000001, 400, "SELL", 2))
        self.assertEqual(len(detector.price_trackers), 1)


class TestLevelLifecycle(unittest.TestCase):

    def test_level_rebaselines_after_dwelling_empty(self):
        """
        A level that empties and stays empty past the dwell window is a different
        resting order when it returns; carrying the old volume forward is the
        cross-session false positive SKILL.md warns about.
        """
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01,
                                         level_reset_dwell_nanos=1_000)
        build_iceberg_sequence(detector, 100.00, "BID", "SELL",
                               baseline=500, peak=500, trade_qty=400, n_trades=3)
        self.assertEqual(detector.get_level_state(100.00)["cumulative_traded"], 1200)
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 0, 20_000))
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 300, 40_000))
        state = detector.get_level_state(100.00)
        self.assertEqual(state["cumulative_traded"], 0)
        self.assertEqual(state["initial_display"], 300)
        self.assertEqual(state["refill_count"], 0)

    def test_immediate_refill_through_zero_is_not_a_rebaseline(self):
        """A venue refresh is immediate: a momentary zero must still count as a refill."""
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01,
                                         level_reset_dwell_nanos=1_000_000)
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1))
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 0, 2))
        detector.process_trade_print(TradePrint("T1", 100.00, 500, "SELL", 3))
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 4))
        state = detector.get_level_state(100.00)
        self.assertEqual(state["refill_count"], 1)
        self.assertEqual(state["initial_display"], 500)

    def test_empty_heartbeats_do_not_restart_the_dwell_clock(self):
        """
        Regression: re-stamping ``emptied_at_nanos`` on every zero-depth snapshot
        restarted the dwell clock, so a level held empty by a stream of empty
        heartbeats never accumulated dwell and never re-baselined.
        """
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01,
                                         level_reset_dwell_nanos=1_000)
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 0))
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 1, 1))
        detector.process_trade_print(TradePrint("t", 100.00, 499, "SELL", 2))
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 0, 10))
        for ts in range(100, 100_000, 100):
            detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 0, ts))
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 400, 100_100))
        state = detector.get_level_state(100.00)
        self.assertEqual(state["cumulative_traded"], 0)
        self.assertEqual(state["initial_display"], 400)

    def test_side_flip_rebaselines_the_level(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1))
        detector.process_trade_print(TradePrint("T1", 100.00, 400, "SELL", 2))
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "ASK", 700, 3))
        state = detector.get_level_state(100.00)
        self.assertEqual(state["side"], "ASK")
        self.assertEqual(state["initial_display"], 700)
        self.assertEqual(state["cumulative_traded"], 0)

    def test_zero_display_baseline_never_reports_all_volume_as_hidden(self):
        """
        Regression: a level baselined at 0 displayed shares divided by max(1, 0), so
        ordinary displayed volume was reported as 100% hidden.
        """
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01,
                                         level_reset_dwell_nanos=10**12)
        detector.process_l2_depth(Level2DepthSnapshot(50.00, "BID", 0, 1))
        ts = 1
        for i in range(5):
            ts += 1
            detector.process_l2_depth(Level2DepthSnapshot(50.00, "BID", 10, ts))
            ts += 1
            report = detector.process_trade_print(TradePrint(f"Z{i}", 50.00, 10, "SELL", ts))
            ts += 1
            detector.process_l2_depth(Level2DepthSnapshot(50.00, "BID", 0, ts))
            self.assertIsNone(report)
        self.assertEqual(detector.get_level_state(50.00)["initial_display"], 0)

    def test_tracked_levels_are_bounded(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01, max_tracked_levels=8)
        for i in range(200):
            detector.process_l2_depth(Level2DepthSnapshot(100.00 + i * 0.01, "BID", 100, i + 1))
        self.assertEqual(len(detector.price_trackers), 8)
        self.assertIsNone(detector.get_level_state(100.00))          # coldest evicted
        self.assertIsNotNone(detector.get_level_state(100.00 + 199 * 0.01))

    def test_reset_clears_all_state(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1))
        detector.process_trade_print(TradePrint("T1", 100.00, 400, "SELL", 2))
        detector.reset()
        self.assertEqual(len(detector.price_trackers), 0)
        detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 500, 1))
        detector.process_trade_print(TradePrint("T1", 100.00, 400, "SELL", 2))
        self.assertEqual(detector.get_level_state(100.00)["cumulative_traded"], 400)


class TestScoring(unittest.TestCase):

    def test_score_never_reaches_certainty(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        report = build_iceberg_sequence(detector, 100.00, "BID", "SELL",
                                        baseline=100, peak=100, trade_qty=900, n_trades=6)
        self.assertLessEqual(report.confidence_score, MAX_CONFIDENCE_SCORE)
        self.assertLess(report.confidence_score, 1.0)

    def test_inconsistent_refill_peaks_are_penalized(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        ts, price = 100, 100.00
        detector.process_l2_depth(Level2DepthSnapshot(price, "BID", 500, ts))
        report = None
        for i, peak in enumerate((500, 120, 830)):
            ts += 1
            detector.process_l2_depth(Level2DepthSnapshot(price, "BID", 1, ts))
            ts += 1
            report = detector.process_trade_print(TradePrint(f"P{i}", price, 400, "SELL", ts))
            ts += 1
            detector.process_l2_depth(Level2DepthSnapshot(price, "BID", peak, ts))
        self.assertIsNotNone(report)
        self.assertFalse(report.refill_peaks_consistent)
        # The report is emitted on the third trade, before that trade's own refill
        # snapshot arrives, so only the first two peaks are in evidence.
        self.assertEqual(report.observed_refill_peaks, (500, 120))
        # 0.50 + 0.10*2.4 + 0.10*2 - 0.10 = 0.84.
        self.assertAlmostEqual(report.confidence_score, 0.84, places=2)

    def test_consistent_peaks_within_tolerance(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        ts, price = 100, 100.00
        detector.process_l2_depth(Level2DepthSnapshot(price, "BID", 500, ts))
        report = None
        for i, peak in enumerate((500, 505, 495)):   # all within 10%
            ts += 1
            detector.process_l2_depth(Level2DepthSnapshot(price, "BID", 1, ts))
            ts += 1
            report = detector.process_trade_print(TradePrint(f"Q{i}", price, 400, "SELL", ts))
            ts += 1
            detector.process_l2_depth(Level2DepthSnapshot(price, "BID", peak, ts))
        self.assertTrue(report.refill_peaks_consistent)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)

    def test_rejects_non_finite_price(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                self.detector.process_l2_depth(Level2DepthSnapshot(bad, "BID", 100, 1))
            with self.assertRaises(ValueError):
                self.detector.process_trade_print(TradePrint("T", bad, 100, "SELL", 1))

    def test_rejects_negative_quantities(self):
        with self.assertRaises(ValueError):
            self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", -100, 1))
        with self.assertRaises(ValueError):
            self.detector.process_trade_print(TradePrint("T", 100.00, -400, "SELL", 1))

    def test_rejects_zero_trade_quantity(self):
        with self.assertRaises(ValueError):
            self.detector.process_trade_print(TradePrint("T", 100.00, 0, "SELL", 1))

    def test_allows_zero_displayed_quantity(self):
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 0, 1))
        self.assertEqual(self.detector.get_level_state(100.00)["initial_display"], 0)

    def test_rejects_unknown_sides(self):
        with self.assertRaises(ValueError):
            self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BUY", 100, 1))
        with self.assertRaises(ValueError):
            self.detector.process_trade_print(TradePrint("T", 100.00, 100, "BID", 1))

    def test_normalizes_side_case_and_whitespace(self):
        self.detector.process_l2_depth(Level2DepthSnapshot(100.00, " bid ", 500, 1))
        self.assertEqual(self.detector.get_level_state(100.00)["side"], "BID")
        self.detector.process_trade_print(TradePrint("T1", 100.00, 400, "sell", 2))
        self.assertEqual(self.detector.get_level_state(100.00)["cumulative_traded"], 400)

    def test_rejects_non_integer_quantity(self):
        with self.assertRaises(TypeError):
            self.detector.process_trade_print(TradePrint("T", 100.00, 400.5, "SELL", 1))

    def test_rejects_non_integer_timestamp(self):
        with self.assertRaises(TypeError):
            self.detector.process_l2_depth(Level2DepthSnapshot(100.00, "BID", 100, 1.5))

    def test_rejects_invalid_constructor_arguments(self):
        for kwargs in (
            {"min_volume_ratio": 1.0},
            {"min_volume_ratio": 0.5},
            {"min_refill_count": 0},
            {"tick_size": 0},
            {"tick_size": -0.01},
            {"max_tracked_levels": 0},
            {"dedup_capacity": -1},
            {"level_reset_dwell_nanos": -1},
            {"symbol": "   "},
        ):
            with self.assertRaises(ValueError, msg=f"expected rejection for {kwargs}"):
                IcebergDetectorEngine(**kwargs)

    def test_extreme_price_raises_rather_than_overflowing(self):
        """A corrupt price near the float ceiling must not surface as OverflowError."""
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        with self.assertRaises(ValueError):
            detector.process_l2_depth(Level2DepthSnapshot(1e308, "BID", 100, 1))

    def test_negative_prices_are_accepted(self):
        """Negative prices are real (CME WTI, 2020-04-20) and must not be rejected."""
        detector = IcebergDetectorEngine("CL", 1.5, 2, tick_size=0.01)
        report = build_iceberg_sequence(detector, -37.63, "BID", "SELL",
                                        baseline=100, peak=100, trade_qty=100, n_trades=3)
        self.assertIsNotNone(report)
        self.assertAlmostEqual(report.detected_price, -37.63, places=2)


class TestReportContract(unittest.TestCase):

    def test_report_is_constructible_from_required_fields_only(self):
        """The 2.0.0 diagnostic fields are defaulted, so older construction still works."""
        report = IcebergDetectionReport(
            symbol="AAPL", detected_price=100.0, iceberg_side="BUY",
            initial_display_quantity=500, cumulative_traded_quantity=1200,
            estimated_hidden_quantity=700, refill_count=2, confidence_score=0.94,
            signal_classification="BULLISH_HIDDEN_BUY", audit_notes="",
        )
        self.assertEqual(report.contra_side_traded_quantity, 0)
        self.assertTrue(report.is_initial_detection)

    def test_audit_notes_do_not_claim_confirmation(self):
        detector = IcebergDetectorEngine("AAPL", 1.5, 2, tick_size=0.01)
        report = build_iceberg_sequence(detector, 100.00, "BID", "SELL",
                                        baseline=500, peak=500, trade_qty=400, n_trades=3)
        self.assertIn("CANDIDATE", report.audit_notes)
        self.assertIn("Screen only", report.audit_notes)


if __name__ == "__main__":
    unittest.main()
