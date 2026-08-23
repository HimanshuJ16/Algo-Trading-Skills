import unittest

from cross_venue_latency_arbitrage_defensive_design import (
    REASON_CROSSED_BOOK,
    REASON_LOCKED_BOOK,
    REASON_RACE_LOST,
    REASON_TOXICITY_NORMALIZED,
    REASON_TOXICITY_TICKS,
    SIDE_ASK,
    SIDE_BID,
    SIDE_NONE,
    CrossVenueLatencyArbitrageDefense,
    LatencyArbitrageDefenseReport,
    LatencyProfile,
    PrimaryBookState,
)

# Race is comfortably won: sweep arrives at t=1000+300=1300us, cancel at
# t=1050+200=1250us, margin +50us.
SAFE_LATENCY = LatencyProfile(
    cancel_rtt_us=200.0, hft_sweep_latency_us=300.0, lead_event_timestamp_us=1000.0)
SAFE_CANCEL_SENT = 1050.0


class TestWeightedMid(unittest.TestCase):

    def setUp(self):
        self.defense = CrossVenueLatencyArbitrageDefense()

    def test_weighted_mid_matches_hand_computed_value(self):
        book = PrimaryBookState(4000.00, 4000.25, 1000.0, 50.0, 0.01)
        weighted_mid, mid = self.defense.calculate_micro_price(book)
        # (50*4000.00 + 1000*4000.25)/1050 = 4200250/1050 = 4000.238095238...
        self.assertAlmostEqual(weighted_mid, 4200250.0 / 1050.0, places=10)
        self.assertAlmostEqual(mid, 4000.125, places=10)

    def test_weighted_mid_leans_toward_the_pressured_side(self):
        heavy_bid = PrimaryBookState(100.00, 100.01, 10000.0, 1.0, 0.01)
        heavy_ask = PrimaryBookState(100.00, 100.01, 1.0, 10000.0, 0.01)
        wm_bid, mid = self.defense.calculate_micro_price(heavy_bid)
        wm_ask, _ = self.defense.calculate_micro_price(heavy_ask)
        self.assertGreater(wm_bid, mid)   # buying pressure -> toward the ask
        self.assertLess(wm_ask, mid)      # selling pressure -> toward the bid

    def test_weighted_mid_always_inside_the_touch(self):
        for bid_vol, ask_vol in ((1e9, 1.0), (1.0, 1e9), (7.0, 3.0), (0.0, 5.0), (5.0, 0.0)):
            with self.subTest(bid_vol=bid_vol, ask_vol=ask_vol):
                book = PrimaryBookState(100.00, 100.05, bid_vol, ask_vol, 0.01)
                weighted_mid, _ = self.defense.calculate_micro_price(book)
                self.assertGreaterEqual(weighted_mid, book.bid_price)
                self.assertLessEqual(weighted_mid, book.ask_price)

    def test_empty_book_has_no_imbalance_information(self):
        book = PrimaryBookState(100.00, 100.02, 0.0, 0.0, 0.01)
        weighted_mid, mid = self.defense.calculate_micro_price(book)
        self.assertEqual(weighted_mid, mid)
        self.assertAlmostEqual(mid, 100.01, places=10)

    def test_fine_tick_instrument_keeps_its_signal(self):
        # Regression: rounding the weighted mid to 4 decimals reported zero
        # toxicity for a 100:1 imbalance on a 0.00001 tick.
        book = PrimaryBookState(1.10500, 1.10501, 1000.0, 10.0, 0.00001)
        report = self.defense.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertGreater(report.toxicity_index_ticks, 0.0)
        self.assertGreater(report.toxicity_index_normalized, 0.9)
        self.assertEqual(report.toxic_side, SIDE_ASK)

    def test_corrupt_or_malformed_book_rejected(self):
        for kwargs in (
            {"tick_size": 0.0},                 # would divide by zero
            {"tick_size": -0.01},
            {"bid_volume": -100.0},             # can push the weighted mid outside the touch
            {"bid_price": float("nan")},
            {"ask_price": float("inf")},
        ):
            with self.subTest(kwargs=kwargs):
                params = dict(bid_price=100.0, ask_price=100.02,
                              bid_volume=5.0, ask_volume=5.0, tick_size=0.01)
                params.update(kwargs)
                with self.assertRaises((ValueError, TypeError)):
                    self.defense.evaluate_defense(
                        PrimaryBookState(**params), SAFE_LATENCY, SAFE_CANCEL_SENT)


class TestToxicityScoring(unittest.TestCase):

    def setUp(self):
        self.defense = CrossVenueLatencyArbitrageDefense(
            toxicity_threshold_ticks=1.0, base_spread_ticks=1.0, base_quote_size=100)

    def test_tick_toxicity_is_bounded_by_the_half_spread(self):
        # A one-tick-wide market can never exceed 0.5 ticks of deviation, which
        # is why the old "tau >= 2.0 ticks MUST trigger" standard could not fire
        # there. The engine now reports the bound explicitly.
        book = PrimaryBookState(100.00, 100.01, 1e9, 1.0, 0.01)
        report = self.defense.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertLessEqual(report.toxicity_index_ticks, report.half_spread_ticks)
        self.assertAlmostEqual(report.half_spread_ticks, 0.5, places=10)
        self.assertLess(report.toxicity_index_ticks, 0.5)
        # ...while the normalized score still saturates, exposing the danger.
        self.assertGreater(report.toxicity_index_normalized, 0.99)

    def test_normalized_toxicity_is_scale_free_across_spreads(self):
        # Same 10:1 imbalance, tight and wide books: the tick score differs by
        # the spread ratio, the normalized score does not.
        tight = PrimaryBookState(100.00, 100.01, 1000.0, 100.0, 0.01)
        wide = PrimaryBookState(100.00, 100.50, 1000.0, 100.0, 0.01)
        r_tight = self.defense.evaluate_defense(tight, SAFE_LATENCY, SAFE_CANCEL_SENT)
        r_wide = self.defense.evaluate_defense(wide, SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertAlmostEqual(
            r_tight.toxicity_index_normalized, r_wide.toxicity_index_normalized, places=10)
        self.assertGreater(r_wide.toxicity_index_ticks, r_tight.toxicity_index_ticks * 10)

    def test_normalized_toxicity_equals_the_imbalance_skew(self):
        # Hand derivation: weighted mid - mid = (Vb - Va)/(Vb + Va) * spread/2,
        # so the normalized score is |Vb - Va| / (Vb + Va) = 900/1100 here.
        book = PrimaryBookState(100.00, 100.02, 1000.0, 100.0, 0.01)
        report = self.defense.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertAlmostEqual(report.toxicity_index_normalized, 900.0 / 1100.0, places=10)

    def test_normalized_score_agrees_with_the_reported_prices(self):
        # Independent cross-check: the score computed from the volumes must match
        # the one implied by the weighted mid and mid the report carries.
        book = PrimaryBookState(4000.00, 4000.25, 1000.0, 50.0, 0.01)
        report = self.defense.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT)
        implied = abs(report.micro_price - report.mid_price) / ((4000.25 - 4000.00) / 2.0)
        self.assertAlmostEqual(report.toxicity_index_normalized, implied, places=9)
        self.assertAlmostEqual(
            report.toxicity_index_ticks,
            abs(report.micro_price - report.mid_price) / 0.01, places=6)

    def test_toxic_side_follows_the_pressure_direction(self):
        buy_pressure = PrimaryBookState(100.00, 100.02, 1000.0, 10.0, 0.01)
        sell_pressure = PrimaryBookState(100.00, 100.02, 10.0, 1000.0, 0.01)
        balanced = PrimaryBookState(100.00, 100.02, 500.0, 500.0, 0.01)
        self.assertEqual(
            self.defense.evaluate_defense(buy_pressure, SAFE_LATENCY, SAFE_CANCEL_SENT).toxic_side,
            SIDE_ASK)
        self.assertEqual(
            self.defense.evaluate_defense(sell_pressure, SAFE_LATENCY, SAFE_CANCEL_SENT).toxic_side,
            SIDE_BID)
        self.assertEqual(
            self.defense.evaluate_defense(balanced, SAFE_LATENCY, SAFE_CANCEL_SENT).toxic_side,
            SIDE_NONE)

    def test_widening_is_asymmetric_and_bounded(self):
        # Regression: both sides used to be widened identically, and by an
        # unbounded multiple of the spread-dependent tick score (a 25-tick book
        # produced 13.5x the base spread).
        book = PrimaryBookState(4000.00, 4000.25, 1000.0, 50.0, 0.01)
        defense = CrossVenueLatencyArbitrageDefense(
            base_spread_ticks=1.0, spread_widening_factor=1.0)
        report = defense.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertEqual(report.toxic_side, SIDE_ASK)
        self.assertEqual(report.defensive_bid_spread_ticks, 1.0)
        self.assertGreater(report.defensive_ask_spread_ticks, 1.0)
        self.assertLessEqual(report.defensive_ask_spread_ticks, 2.0)

    def test_size_tapers_to_zero_at_maximum_toxicity(self):
        defense = CrossVenueLatencyArbitrageDefense(base_quote_size=100)
        near_max = defense.evaluate_defense(
            PrimaryBookState(100.00, 100.02, 1e9, 1.0, 0.01), SAFE_LATENCY, SAFE_CANCEL_SENT)
        balanced = defense.evaluate_defense(
            PrimaryBookState(100.00, 100.02, 500.0, 500.0, 0.01), SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertEqual(near_max.defensive_quote_size, 0)
        self.assertEqual(balanced.defensive_quote_size, 100)

    def test_balanced_safe_book_is_left_alone(self):
        book = PrimaryBookState(100.00, 100.02, 500.0, 500.0, 0.01)
        report = self.defense.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertIsInstance(report, LatencyArbitrageDefenseReport)
        self.assertFalse(report.is_preemptive_cancel_triggered)
        self.assertEqual(report.trigger_reasons, [])
        self.assertEqual(report.defensive_bid_spread_ticks, 1.0)
        self.assertEqual(report.defensive_ask_spread_ticks, 1.0)
        self.assertEqual(report.defensive_quote_size, 100)

    def test_normalized_threshold_is_opt_in(self):
        book = PrimaryBookState(100.00, 100.01, 1000.0, 100.0, 0.01)  # 0.818 normalized
        lenient = CrossVenueLatencyArbitrageDefense(toxicity_threshold_ticks=5.0)
        self.assertNotIn(
            REASON_TOXICITY_NORMALIZED,
            lenient.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT).trigger_reasons)
        calibrated = CrossVenueLatencyArbitrageDefense(
            toxicity_threshold_ticks=5.0, toxicity_threshold_normalized=0.8)
        report = calibrated.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertIn(REASON_TOXICITY_NORMALIZED, report.trigger_reasons)
        self.assertTrue(report.is_preemptive_cancel_triggered)

    def test_invalid_configuration_rejected(self):
        for kwargs in (
            {"base_quote_size": 0},
            {"base_quote_size": -10},
            {"base_quote_size": 10.5},
            {"base_spread_ticks": 0.0},
            {"toxicity_threshold_ticks": -1.0},
            {"toxicity_threshold_normalized": 1.5},
            {"spread_widening_factor": float("nan")},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises((ValueError, TypeError)):
                    CrossVenueLatencyArbitrageDefense(**kwargs)


class TestLatencyRace(unittest.TestCase):

    def setUp(self):
        self.defense = CrossVenueLatencyArbitrageDefense(toxicity_threshold_ticks=1.0)
        self.balanced = PrimaryBookState(100.00, 100.02, 500.0, 500.0, 0.01)

    def test_negative_latency_margin_triggers_cancel(self):
        # Cancel lands at 1000+250=1250us, sweep at 1000+100=1100us -> -150us.
        lat = LatencyProfile(
            cancel_rtt_us=250.0, hft_sweep_latency_us=100.0, lead_event_timestamp_us=1000.0)
        report = self.defense.evaluate_defense(self.balanced, lat, cancel_sent_timestamp_us=1000.0)
        self.assertEqual(report.latency_margin_us, -150.0)
        self.assertTrue(report.is_preemptive_cancel_triggered)
        self.assertIn(REASON_RACE_LOST, report.trigger_reasons)

    def test_dead_heat_counts_as_a_loss(self):
        # Regression: a margin of exactly 0.0 left every defense disarmed, even
        # though the venue processes messages in arrival order.
        lat = LatencyProfile(
            cancel_rtt_us=100.0, hft_sweep_latency_us=100.0, lead_event_timestamp_us=0.0)
        report = self.defense.evaluate_defense(self.balanced, lat, cancel_sent_timestamp_us=0.0)
        self.assertEqual(report.latency_margin_us, 0.0)
        self.assertTrue(report.is_preemptive_cancel_triggered)
        self.assertIn(REASON_RACE_LOST, report.trigger_reasons)

    def test_safety_margin_arms_defenses_before_the_dead_heat(self):
        lat = LatencyProfile(
            cancel_rtt_us=100.0, hft_sweep_latency_us=130.0, lead_event_timestamp_us=0.0)
        tight = CrossVenueLatencyArbitrageDefense(latency_safety_margin_us=0.0)
        buffered = CrossVenueLatencyArbitrageDefense(latency_safety_margin_us=50.0)
        self.assertFalse(
            tight.evaluate_defense(self.balanced, lat, 0.0).is_preemptive_cancel_triggered)
        self.assertTrue(
            buffered.evaluate_defense(self.balanced, lat, 0.0).is_preemptive_cancel_triggered)

    def test_lost_race_recommends_no_quote_at_all(self):
        # Regression: the engine announced "pulling quotes" while returning a
        # positive size, so a caller following the size stayed on the book.
        lat = LatencyProfile(
            cancel_rtt_us=500.0, hft_sweep_latency_us=10.0, lead_event_timestamp_us=0.0)
        report = self.defense.evaluate_defense(self.balanced, lat, cancel_sent_timestamp_us=0.0)
        self.assertTrue(report.is_preemptive_cancel_triggered)
        self.assertEqual(report.defensive_quote_size, 0)

    def test_cancel_sent_before_the_lead_event_is_rejected(self):
        # Mixed clock domains would otherwise inflate the margin and report a
        # lost race as won.
        lat = LatencyProfile(
            cancel_rtt_us=10.0, hft_sweep_latency_us=10.0, lead_event_timestamp_us=1000.0)
        with self.assertRaises(ValueError):
            self.defense.evaluate_defense(self.balanced, lat, cancel_sent_timestamp_us=999.0)

    def test_malformed_latency_inputs_rejected(self):
        for kwargs in (
            {"cancel_rtt_us": -10.0},
            {"hft_sweep_latency_us": float("nan")},
            {"lead_event_timestamp_us": float("inf")},
        ):
            with self.subTest(kwargs=kwargs):
                params = dict(cancel_rtt_us=100.0, hft_sweep_latency_us=100.0,
                              lead_event_timestamp_us=0.0)
                params.update(kwargs)
                with self.assertRaises((ValueError, TypeError)):
                    self.defense.evaluate_defense(self.balanced, LatencyProfile(**params), 1e9)
        with self.assertRaises(TypeError):
            self.defense.evaluate_defense(self.balanced, "not-a-latency-profile", 0.0)


class TestLockedAndCrossedBooks(unittest.TestCase):

    def setUp(self):
        self.defense = CrossVenueLatencyArbitrageDefense()

    def test_crossed_book_is_maximum_danger_not_zero_toxicity(self):
        # Regression: bid 100.05 > ask 100.00 gave weighted mid == mid, hence
        # toxicity 0.0 and "normal market making" on the most dangerous book.
        book = PrimaryBookState(100.05, 100.00, 500.0, 500.0, 0.01)
        report = self.defense.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertTrue(report.is_preemptive_cancel_triggered)
        self.assertIn(REASON_CROSSED_BOOK, report.trigger_reasons)
        self.assertEqual(report.defensive_quote_size, 0)
        self.assertEqual(report.toxicity_index_normalized, 1.0)

    def test_locked_book_also_pulls_quotes(self):
        book = PrimaryBookState(100.00, 100.00, 500.0, 500.0, 0.01)
        report = self.defense.evaluate_defense(book, SAFE_LATENCY, SAFE_CANCEL_SENT)
        self.assertTrue(report.is_preemptive_cancel_triggered)
        self.assertIn(REASON_LOCKED_BOOK, report.trigger_reasons)
        self.assertEqual(report.defensive_quote_size, 0)

    def test_crossed_book_still_reports_the_latency_race(self):
        lat = LatencyProfile(
            cancel_rtt_us=500.0, hft_sweep_latency_us=10.0, lead_event_timestamp_us=0.0)
        report = self.defense.evaluate_defense(
            PrimaryBookState(100.05, 100.00, 5.0, 5.0, 0.01), lat, 0.0)
        self.assertEqual(report.latency_margin_us, -490.0)
        self.assertIn(REASON_RACE_LOST, report.trigger_reasons)


if __name__ == '__main__':
    unittest.main()
