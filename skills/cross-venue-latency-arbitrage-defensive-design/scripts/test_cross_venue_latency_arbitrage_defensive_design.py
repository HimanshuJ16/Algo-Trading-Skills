import unittest
from cross_venue_latency_arbitrage_defensive_design import (
    CrossVenueLatencyArbitrageDefense, PrimaryBookState, LatencyProfile, LatencyArbitrageDefenseReport
)

class TestCrossVenueLatencyArbitrageDefense(unittest.TestCase):

    def setUp(self):
        self.defense = CrossVenueLatencyArbitrageDefense(
            toxicity_threshold_ticks=1.0,
            base_spread_ticks=1.0,
            base_quote_size=100
        )

    def test_toxic_imbalance_triggers_cancel_and_spread_widening(self):
        # Heavy bid pressure (1000 bid vs 50 ask) -> Micro-price moves up towards ask
        book = PrimaryBookState(bid_price=4000.00, ask_price=4000.25, bid_volume=1000.0, ask_volume=50.0, tick_size=0.01)
        # Latency margin safe (+50us)
        lat = LatencyProfile(cancel_rtt_us=200.0, hft_sweep_latency_us=300.0, lead_event_timestamp_us=1000.0)
        
        report = self.defense.evaluate_defense(book, lat, cancel_sent_timestamp_us=1050.0)
        
        # P_micro = (50*4000.00 + 1000*4000.25)/1050 = 4000.2381 -> Mid = 4000.125 -> Diff = 0.1131 -> 11.31 ticks
        self.assertGreaterEqual(report.toxicity_index_ticks, 10.0)
        self.assertTrue(report.is_preemptive_cancel_triggered)
        self.assertGreater(report.defensive_bid_spread_ticks, 1.0)
        self.assertLess(report.defensive_quote_size, 100)

    def test_negative_latency_margin_triggers_cancel(self):
        # Balanced book (Toxicity ~ 0)
        book = PrimaryBookState(bid_price=100.00, ask_price=100.02, bid_volume=500.0, ask_volume=500.0, tick_size=0.01)
        # MM cancel arrives late (sent at 1000us + 250us RTT = 1250us) vs HFT sweep (1000us + 100us = 1100us) -> -150us deficit!
        lat = LatencyProfile(cancel_rtt_us=250.0, hft_sweep_latency_us=100.0, lead_event_timestamp_us=1000.0)
        
        report = self.defense.evaluate_defense(book, lat, cancel_sent_timestamp_us=1000.0)
        
        self.assertEqual(report.latency_margin_us, -150.0)
        self.assertTrue(report.is_preemptive_cancel_triggered)

if __name__ == '__main__':
    unittest.main()
