import unittest
import pandas as pd
from satellite_imagery_based_signal_research import (
    SatelliteImageryBasedSignalResearchEngine, SignalResult,
    SatelliteObservation, ImagerySignalType, QuantitativeSatelliteSignal
)

class TestSatelliteImageryLegacy(unittest.TestCase):
    def setUp(self):
        self.engine = SatelliteImageryBasedSignalResearchEngine()

    def test_empty_data(self):
        df = pd.DataFrame()
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 0)

    def test_valid_data(self):
        df = pd.DataFrame({
            "raw_val": [10.0, 20.0],
            "asset": ["AAPL", "MSFT"]
        })
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].signal_value, 15.0)
        self.assertEqual(signals[0].asset_id, "AAPL")

class TestSatelliteImageryEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = SatelliteImageryBasedSignalResearchEngine(z_score_threshold=1.5)

    def test_retail_parking_lot_bullish_signal(self):
        # 12,000 cars vs baseline 10,000 +- 1,000 -> Z-score = +2.0 -> Bullish (Direction = +1.0)
        obs = SatelliteObservation(
            timestamp_iso="2026-08-05T12:00:00Z",
            asset_id="WMT",
            signal_type=ImagerySignalType.RETAIL_PARKING_OCCUPANCY,
            observed_metric=12000.0,
            baseline_historical_mean=10000.0,
            baseline_historical_std=1000.0
        )
        sig = self.engine.compute_satellite_signal(obs)
        self.assertEqual(sig.z_score, 2.0)
        self.assertEqual(sig.trading_signal_direction, 1.0)
        self.assertGreater(sig.confidence_pct, 60.0)

    def test_floating_roof_oil_bearish_signal(self):
        # High oil inventory -> Bearish oil price (Direction = -1.0)
        obs = SatelliteObservation(
            timestamp_iso="2026-08-05T12:00:00Z",
            asset_id="CL_FUTURES",
            signal_type=ImagerySignalType.FLOATING_ROOF_OIL_STORAGE,
            observed_metric=85.0,  # 85% full
            baseline_historical_mean=60.0,
            baseline_historical_std=10.0
        )
        sig = self.engine.compute_satellite_signal(obs)
        self.assertEqual(sig.z_score, 2.5)
        self.assertEqual(sig.trading_signal_direction, -1.0)

    def test_neutral_signal(self):
        obs = SatelliteObservation(
            timestamp_iso="2026-08-05T12:00:00Z",
            asset_id="TGT",
            signal_type=ImagerySignalType.RETAIL_PARKING_OCCUPANCY,
            observed_metric=10200.0,
            baseline_historical_mean=10000.0,
            baseline_historical_std=1000.0  # Z-score = +0.2 < 1.5 -> Neutral (0.0)
        )
        sig = self.engine.compute_satellite_signal(obs)
        self.assertEqual(sig.z_score, 0.2)
        self.assertEqual(sig.trading_signal_direction, 0.0)

if __name__ == '__main__':
    unittest.main()
