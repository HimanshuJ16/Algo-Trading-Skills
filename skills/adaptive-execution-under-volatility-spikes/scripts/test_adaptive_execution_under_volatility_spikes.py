import unittest
from adaptive_execution_under_volatility_spikes import (
    AdaptiveVolatilityConfig, 
    AdaptiveExecutionUnderVolatilitySpikesEngine,
    MarketRegime
)

class TestAdaptiveExecutionUnderVolatilitySpikes(unittest.TestCase):
    def setUp(self):
        self.config = AdaptiveVolatilityConfig(
            enabled=True, 
            base_participation_rate=0.20, 
            base_child_order_size=200,
            volatility_threshold_high=2.0,
            volatility_threshold_critical=5.0
        )
        self.engine = AdaptiveExecutionUnderVolatilitySpikesEngine(self.config)

    def test_normal_regime(self):
        market_data = {"symbol": "AAPL", "current_volatility": 1.0}
        params = self.engine.evaluate(market_data)
        
        self.assertEqual(self.engine.current_regime, MarketRegime.NORMAL)
        self.assertEqual(params.participation_rate, 0.20)
        self.assertEqual(params.child_order_size, 200)
        self.assertFalse(params.halt_trading)
        
    def test_high_volatility_regime(self):
        market_data = {"symbol": "AAPL", "current_volatility": 3.0}
        params = self.engine.evaluate(market_data)
        
        # Should halve participation and order size
        self.assertEqual(self.engine.current_regime, MarketRegime.HIGH_VOLATILITY)
        self.assertEqual(params.participation_rate, 0.10)
        self.assertEqual(params.child_order_size, 100)
        self.assertEqual(params.limit_offset_bps, 15)
        self.assertFalse(params.halt_trading)

    def test_critical_shock_regime(self):
        market_data = {"symbol": "AAPL", "current_volatility": 6.0}
        params = self.engine.evaluate(market_data)
        
        # Should halt trading
        self.assertEqual(self.engine.current_regime, MarketRegime.CRITICAL_SHOCK)
        self.assertTrue(params.halt_trading)
        self.assertEqual(params.participation_rate, 0.0)

    def test_disabled_engine(self):
        self.engine.config.enabled = False
        market_data = {"symbol": "AAPL", "current_volatility": 6.0}
        params = self.engine.evaluate(market_data)
        
        # Even with high vol, if disabled it should return normal params
        self.assertFalse(params.halt_trading)
        self.assertEqual(params.participation_rate, 0.20)

if __name__ == '__main__':
    unittest.main()
