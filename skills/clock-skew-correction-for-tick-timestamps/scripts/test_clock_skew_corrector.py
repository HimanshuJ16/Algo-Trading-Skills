import unittest
import numpy as np
from clock_skew_corrector import ClockSkewCorrector

class TestClockSkewCorrector(unittest.TestCase):

    def setUp(self):
        np.random.seed(42)
        # Create synthetic time series over 100 seconds
        # True drift rate: 100 microseconds per second (0.000100)
        # Baseline offset: 0.050 seconds (50ms)
        self.n_samples = 1000
        self.exchange_ts = np.sort(np.random.uniform(1000.0, 1100.0, self.n_samples))
        
        rel_t = self.exchange_ts - self.exchange_ts[0]
        true_offset = 0.050 + 0.000100 * rel_t
        
        # Add random positive network jitter (50µs to 2ms)
        jitter = np.random.uniform(0.000050, 0.002000, self.n_samples)
        
        self.local_ts = self.exchange_ts + true_offset + jitter
        self.corrector = ClockSkewCorrector(window_size_sec=5.0)

    def test_fit_skew_rate(self):
        self.corrector.fit(self.exchange_ts, self.local_ts)
        
        # Expected drift rate is ~0.000100
        self.assertAlmostEqual(self.corrector.beta, 0.000100, delta=0.000020)

    def test_monotonicity(self):
        corrected = self.corrector.fit_transform(self.exchange_ts, self.local_ts)
        
        # Differences between consecutive timestamps must be strictly > 0
        diffs = np.diff(corrected)
        self.assertTrue(np.all(diffs > 0))

    def test_correction_reduces_variance(self):
        # Initial raw delay variance has skew trend built in
        raw_delays = self.local_ts - self.exchange_ts
        corrected_ts = self.corrector.fit_transform(self.exchange_ts, self.local_ts)
        corrected_delays = corrected_ts - self.exchange_ts
        
        # Standard deviation of corrected delays should be much lower than raw delays
        self.assertLess(np.std(corrected_delays), np.std(raw_delays))

if __name__ == '__main__':
    unittest.main()
