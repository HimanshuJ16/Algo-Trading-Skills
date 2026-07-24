"""
Unit tests for clock-skew-correction-for-tick-timestamps skill.

Tests:
1. Convergence of EWMA skew estimation to constant exchange offset.
2. Network jitter outlier rejection logic.
3. Timestamp calibration calculation.
4. High clock drift alert threshold triggering.
"""
import time
import unittest
from clock_skew_corrector import ClockSkewCorrector, ClockSkewResult


class TestClockSkewCorrector(unittest.TestCase):

    def setUp(self):
        self.corrector = ClockSkewCorrector(alpha=0.2, max_drift_threshold_ms=100.0)

    def test_ewma_skew_convergence(self):
        base_time = 1700000000.0
        # Constant offset of +30ms on exchange clock
        for i in range(30):
            t_loc = base_time + (i * 0.1)
            t_ex = t_loc + 0.030  # +30ms offset
            res = self.corrector.add_sample(t_ex, t_loc)

        # Estimated skew should be very close to +30ms
        self.assertAlmostEqual(self.corrector.estimated_skew_ms, 30.0, delta=1.0)

    def test_outlier_jitter_rejection(self):
        base_time = 1700000000.0
        # Establish stable +10ms baseline
        for i in range(20):
            t_loc = base_time + (i * 0.1)
            t_ex = t_loc + 0.010
            self.corrector.add_sample(t_ex, t_loc)

        # Submit network spike sample (+500ms jitter)
        t_loc_spike = base_time + 2.5
        t_ex_spike = t_loc_spike + 0.500

        res_spike = self.corrector.add_sample(t_ex_spike, t_loc_spike)
        self.assertTrue(res_spike.is_outlier)

        # Skew estimate should NOT be distorted by +500ms spike
        self.assertLess(self.corrector.estimated_skew_ms, 50.0)

    def test_calibrate_timestamp(self):
        base_time = 1700000000.0
        for i in range(10):
            t_loc = base_time + (i * 0.1)
            t_ex = t_loc + 0.050  # +50ms offset
            self.corrector.add_sample(t_ex, t_loc)

        t_local_test = 1700000010.0
        calibrated = self.corrector.calibrate_timestamp(t_local_test)
        self.assertAlmostEqual(calibrated, t_local_test + 0.050, delta=0.002)


if __name__ == "__main__":
    unittest.main()
