import unittest
import numpy as np
from model_architecture_selector import (
    ModelArchitectureSelectorEngine, ArchitectureRecommendation, SignalTransformationResult
)

class TestModelArchitectureSelectorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ModelArchitectureSelectorEngine(target_volatility_annual=0.15)

    def test_architecture_recommendation(self):
        rec_cs = self.engine.recommend_architecture(universe_size=50, require_market_neutrality=True, is_single_asset_trend=False)
        self.assertEqual(rec_cs.selected_architecture, "CROSS_SECTIONAL")
        self.assertTrue(rec_cs.is_market_neutral_enforced)

        rec_ts = self.engine.recommend_architecture(universe_size=1, require_market_neutrality=False, is_single_asset_trend=True)
        self.assertEqual(rec_ts.selected_architecture, "TIME_SERIES")
        self.assertTrue(rec_ts.is_volatility_targeting_enforced)

    def test_cross_sectional_transformation(self):
        # 5 assets with raw factors
        factors = np.array([10.0, 50.0, -20.0, 30.0, -10.0])
        res = self.engine.transform_cross_sectional(factors)
        
        self.assertEqual(res.architecture, "CROSS_SECTIONAL")
        self.assertAlmostEqual(res.net_exposure, 0.0, places=4) # Net Dollar Neutral!
        self.assertEqual(res.gross_exposure, 1.0)              # Normalized Gross Exposure = 1.0

    def test_time_series_volatility_scaling(self):
        hist_returns = np.random.normal(0.001, 0.02, 50)
        current_factor = 0.05 # Positive trend
        
        # High Volatility Asset (30% realized vol) -> Vol scalar = 15% / 30% = 0.5x weight
        w_high_vol = self.engine.transform_time_series(hist_returns, current_factor, asset_realized_vol_annual=0.30)
        
        # Low Volatility Asset (10% realized vol) -> Vol scalar = 15% / 10% = 1.5x weight
        w_low_vol = self.engine.transform_time_series(hist_returns, current_factor, asset_realized_vol_annual=0.10)
        
        self.assertEqual(w_high_vol, 0.5)
        self.assertEqual(w_low_vol, 1.5)

if __name__ == '__main__':
    unittest.main()
