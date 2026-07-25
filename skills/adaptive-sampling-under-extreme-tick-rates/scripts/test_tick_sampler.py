"""
Unit tests for adaptive-sampling-under-extreme-tick-rates skill.
"""
import time
import unittest
from tick_sampler import AdaptiveTickSamplerEngine, SamplingMode


class TestAdaptiveTickSamplerEngine(unittest.TestCase):

    def setUp(self):
        # Target max rate: 10 ticks/sec for testing
        self.sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=10)

    def test_passthrough_mode_under_normal_rate(self):
        # 5 ticks <= target 10 -> All 5 emitted in PASSTHROUGH mode
        emitted = []
        t0 = time.time()
        for i in range(5):
            res = self.sampler.ingest_tick("AAPL", i, 150.0 + i, 10.0, timestamp=t0)
            if res:
                emitted.append(res)
            t0 += 0.2  # space them out

        self.assertEqual(len(emitted), 5)
        self.assertEqual(emitted[0].mode, SamplingMode.PASSTHROUGH)
        self.assertEqual(emitted[0].sampling_factor, 1)

    def test_systematic_sampling_under_extreme_rate_and_vwap(self):
        t0 = 1700000000.0 # Fixed timestamp to control sliding window
        emitted = []
        total_input_volume = 0.0
        total_input_notional = 0.0

        # Simulate high rate burst of 40 ticks within 0.1 seconds
        for i in range(40):
            vol = 5.0
            price = 100.0 + i
            total_input_volume += vol
            total_input_notional += (price * vol)
            
            res = self.sampler.ingest_tick("AAPL", i, price, vol, timestamp=t0)
            if res:
                emitted.append(res)
            t0 += 0.002 # 500 ticks/sec rate equivalent

        # High tick rate engages systematic sampling mode
        self.assertEqual(emitted[-1].mode, SamplingMode.SYSTEMATIC_SAMPLING)
        self.assertGreater(emitted[-1].sampling_factor, 1)

        # Flush un-emitted residual volume
        flushed = self.sampler.flush("AAPL")
        if flushed:
            emitted.append(flushed)

        # Total accumulated volume across emitted ticks must match total input volume
        total_emitted_vol = sum(t.volume for t in emitted)
        self.assertAlmostEqual(total_emitted_vol, total_input_volume, places=2)
        
        # Total notional across emitted ticks must match total input notional (VWAP preservation)
        total_emitted_notional = sum(t.price * t.volume for t in emitted)
        self.assertAlmostEqual(total_emitted_notional, total_input_notional, places=2)


if __name__ == "__main__":
    unittest.main()
