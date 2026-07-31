import random
import unittest
from microstructure_noise_filtering_for_hf_signals import (
    MicrostructureNoiseFilterEngine, FilterConfig, RawTick, MicrostructureFilterReport
)

class TestMicrostructureNoiseFilterEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MicrostructureNoiseFilterEngine()

    def test_kalman_filter_noise_reduction(self):
        # Generate 200 ticks oscillating around 100.0 with random noise (+/- 0.20)
        random.seed(42)
        ticks = []
        base_p = 100.0
        for i in range(200):
            noise = random.gauss(0.0, 0.15)
            bid = base_p + noise - 0.02
            ask = base_p + noise + 0.02
            ticks.append(RawTick(timestamp_epoch=float(i), bid_price=bid, ask_price=ask, last_price=base_p+noise, bid_volume=100, ask_volume=100))

        cfg = FilterConfig(filter_type="KALMAN", kalman_process_noise_q=1e-5, kalman_obs_noise_r=1e-2, symbol="AAPL")
        report = self.engine.filter_tick_stream(cfg, ticks)

        self.assertEqual(report.status, "NOISE_FILTERING_SUCCESS")
        self.assertEqual(report.total_ticks_processed, 200)
        self.assertLess(report.filtered_price_std_dev, report.raw_price_std_dev)
        self.assertGreater(report.noise_reduction_pct, 15.0) # > 15% noise variance reduction!

    def test_micro_price_volume_weighting(self):
        # Bid = 100.0, Ask = 100.20 -> Mid = 100.10
        # Bid Volume = 900, Ask Volume = 100 (heavy bid pressure)
        # Micro Price = (100 * 100.0 + 900 * 100.20) / 1000 = 100.18 -> Shifted towards Ask!
        ticks = [RawTick(timestamp_epoch=1.0, bid_price=100.0, ask_price=100.20, last_price=100.10, bid_volume=900.0, ask_volume=100.0)]
        cfg = FilterConfig(filter_type="MICRO_PRICE", symbol="BTC-USD")
        report = self.engine.filter_tick_stream(cfg, ticks)

        filt_tick = report.filtered_ticks[0]
        self.assertEqual(filt_tick.raw_mid_price, 100.10)
        self.assertEqual(filt_tick.filtered_price, 100.18) # Shifted towards ask price 100.20

if __name__ == '__main__':
    unittest.main()
