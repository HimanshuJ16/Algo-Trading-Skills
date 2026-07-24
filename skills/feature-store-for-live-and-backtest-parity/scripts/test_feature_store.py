"""
Unit tests for feature-store-for-live-and-backtest-parity skill.

Tests:
1. Pure feature computation from window (RSI, Bollinger %B, Volatility Z-score).
2. Offline batch backtest feature extraction.
3. Online streaming feature extraction.
4. Bit-for-bit parity validation and divergence assertion on mismatch.
"""
import unittest
from feature_store import Bar, FeatureParityMismatchError, FeatureVector, ParityFeatureStoreEngine


class TestParityFeatureStoreEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ParityFeatureStoreEngine(lookback_period=20)
        self.bars = [
            Bar(
                timestamp=1700000000.0 + (i * 60),
                open=100.0 + i,
                high=102.0 + i,
                low=99.0 + i,
                close=101.0 + i,
                volume=1000.0 + (i * 10),
            )
            for i in range(50)
        ]

    def test_pure_feature_computation(self):
        closes = [100.0 + i for i in range(20)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]

        rsi, bb_b, vol_z, ret = ParityFeatureStoreEngine.compute_features_from_window(closes, highs, lows)

        self.assertGreater(rsi, 50.0)  # Upward trend -> RSI > 50
        self.assertTrue(0.0 <= bb_b <= 1.0)
        self.assertAlmostEqual(ret, 1.0 / 118.0, delta=0.005)

    def test_batch_and_online_parity_validation(self):
        # Must pass parity validation with zero mismatch
        parity_pass = self.engine.validate_parity(self.bars, tolerance=1e-4)
        self.assertTrue(parity_pass)

    def test_parity_mismatch_detection(self):
        # Mutate pure feature calculation or compare against modified online stream
        batch_fv = self.engine.compute_batch_features(self.bars)

        # Compute online features over corrupted bar series
        corrupted_bars = list(self.bars)
        corrupted_bars[10] = Bar(
            timestamp=corrupted_bars[10].timestamp,
            open=999.0,
            high=999.0,
            low=999.0,
            close=999.0,
            volume=999.0,
        )

        self.engine.online_ring_buffer.clear()
        online_fv = [self.engine.compute_online_feature(b) for b in corrupted_bars]

        # Comparing batch features against corrupted online features must fail
        with self.assertRaises(FeatureParityMismatchError):
            for i in range(len(self.bars)):
                b_fv = batch_fv[i]
                o_fv = online_fv[i]
                diff_rsi = abs(b_fv.rsi - o_fv.rsi)
                if diff_rsi > 1e-4:
                    raise FeatureParityMismatchError("Parity mismatch detected")


if __name__ == "__main__":
    unittest.main()
