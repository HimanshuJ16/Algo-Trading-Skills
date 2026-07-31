import unittest
from multi_horizon_forecaster import (
    MultiHorizonForecasterEngine, HorizonPrediction, MultiHorizonConfig, MultiHorizonForecastReport
)

class TestMultiHorizonForecasterEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MultiHorizonForecasterEngine()

    def test_ic_weighted_multi_horizon_synthesis(self):
        # 4 horizons: 5m, 15m, 60m, 240m
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0020, ic_score=0.08, confidence=1.0),
            HorizonPrediction(horizon_steps=15, predicted_return=0.0015, ic_score=0.06, confidence=1.0),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0010, ic_score=0.04, confidence=1.0),
            HorizonPrediction(horizon_steps=240, predicted_return=0.0005, ic_score=0.02, confidence=1.0),
        ]
        cfg = MultiHorizonConfig(symbol="BTC/USD", weighting_scheme="IC_WEIGHTED")

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertEqual(report.status, "FORECAST_SYNTHESIZED_SUCCESS")
        self.assertGreater(report.composite_alpha, 0.0)
        self.assertEqual(report.directional_consensus_pct, 100.0)
        self.assertFalse(report.has_directional_conflict)

        # Total weights sum to 1.0
        self.assertAlmostEqual(sum(report.horizon_weights.values()), 1.0, places=3)

    def test_directional_conflict_detection(self):
        # Short horizon (5m) = BUY (+0.0050), Long horizon (240m) = SELL (-0.0040) -> CONFLICT!
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0050, ic_score=0.05),
            HorizonPrediction(horizon_steps=240, predicted_return=-0.0040, ic_score=0.05),
        ]
        cfg = MultiHorizonConfig(symbol="ETH/USD", conflict_threshold=0.001)

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertTrue(report.has_directional_conflict)
        self.assertEqual(report.directional_consensus_pct, 50.0)

if __name__ == '__main__':
    unittest.main()
