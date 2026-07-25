"""
Unit tests for adaptive-sampling-under-extreme-tick-rates skill.
"""
import time
import unittest
from tick_sampler import AdaptiveTickSamplerEngine, SamplingMode


class TestAdaptiveTickSamplerEngine(unittest.TestCase):

    def setUp(self):
        # Target max rate: 10 ticks/sec for testing
        self.sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=10, window_sec=1.0)

    def test_passthrough_mode_under_normal_rate(self):
        # 5 ticks <= target 10 -> All 5 emitted in PASSTHROUGH mode
        emitted = []
        t0 = time.time()
        for i in range(5):
            res = self.sampler.ingest_tick("AAPL", i, 150.0 + i, 10.0, timestamp=t0)
            if res:
                emitted.append(res)

        self.assertEqual(len(emitted), 5)
        self.assertEqual(emitted[0].mode, SamplingMode.PASSTHROUGH)
        self.assertEqual(emitted[0].sampling_factor, 1)

    def test_systematic_sampling_under_extreme_rate(self):
        t0 = time.time()
        emitted = []
        total_input_volume = 0.0

        # Simulate high rate burst of 40 ticks
        for i in range(40):
            vol = 5.0
            total_input_volume += vol
            res = self.sampler.ingest_tick("AAPL", i, 150.0, vol, timestamp=t0)
            if res:
                emitted.append(res)

        # High tick rate engages systematic sampling mode
        self.assertEqual(emitted[-1].mode, SamplingMode.SYSTEMATIC_SAMPLING)
        self.assertGreater(emitted[-1].sampling_factor, 1)

        # Flush un-emitted residual volume
        flushed = self.sampler.flush("AAPL")
        if flushed:
            emitted.append(flushed)

        # Total accumulated volume across emitted ticks must match total input volume (200.0)
        total_emitted_vol = sum(t.accumulated_volume for t in emitted)
        self.assertAlmostEqual(total_emitted_vol, total_input_volume, places=2)


if __name__ == "__main__":
    unittest.main()
