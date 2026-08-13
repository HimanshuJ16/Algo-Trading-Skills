import math
import unittest

from adaptive_execution_under_volatility_spikes import (
    AdaptiveExecutionUnderVolatilitySpikesEngine,
    AdaptiveVolatilityConfig,
    MarketDataValidationError,
    MarketRegime,
)


class TestAdaptiveExecutionUnderVolatilitySpikes(unittest.TestCase):
    def setUp(self):
        self.config = AdaptiveVolatilityConfig(
            enabled=True,
            base_participation_rate=0.20,
            base_child_order_size=200,
            volatility_threshold_high=2.0,
            volatility_threshold_critical=5.0,
        )
        self.engine = AdaptiveExecutionUnderVolatilitySpikesEngine(self.config)

    def test_normal_regime(self):
        params = self.engine.evaluate(
            {"symbol": "AAPL", "current_volatility": 1.0}
        )

        self.assertEqual(self.engine.current_regime, MarketRegime.NORMAL)
        self.assertEqual(params.regime, MarketRegime.NORMAL)
        self.assertEqual(params.participation_rate, 0.20)
        self.assertEqual(params.child_order_size, 200)
        self.assertFalse(params.halt_trading)

    def test_high_volatility_regime(self):
        params = self.engine.evaluate(
            {"symbol": "AAPL", "current_volatility": 3.0}
        )

        self.assertEqual(self.engine.current_regime, MarketRegime.HIGH_VOLATILITY)
        self.assertEqual(params.regime, MarketRegime.HIGH_VOLATILITY)
        self.assertEqual(params.participation_rate, 0.10)
        self.assertEqual(params.child_order_size, 100)
        self.assertEqual(params.limit_offset_bps, 15)
        self.assertFalse(params.halt_trading)

    def test_critical_shock_regime(self):
        params = self.engine.evaluate(
            {"symbol": "AAPL", "current_volatility": 6.0}
        )

        self.assertEqual(self.engine.current_regime, MarketRegime.CRITICAL_SHOCK)
        self.assertEqual(params.regime, MarketRegime.CRITICAL_SHOCK)
        self.assertTrue(params.halt_trading)
        self.assertEqual(params.participation_rate, 0.0)
        self.assertEqual(params.child_order_size, 0)

    def test_threshold_boundaries_are_inclusive(self):
        high_params = self.engine.evaluate({"current_volatility": 2.0})
        critical_params = self.engine.evaluate({"current_volatility": 5.0})

        self.assertEqual(high_params.regime, MarketRegime.HIGH_VOLATILITY)
        self.assertEqual(critical_params.regime, MarketRegime.CRITICAL_SHOCK)

    def test_disabled_engine_returns_normal_parameters_without_market_data(self):
        self.engine.config.enabled = False

        params = self.engine.evaluate({})

        self.assertEqual(self.engine.current_regime, MarketRegime.NORMAL)
        self.assertFalse(params.halt_trading)
        self.assertEqual(params.participation_rate, 0.20)
        self.assertEqual(params.child_order_size, 200)

    def test_missing_volatility_fails_closed(self):
        with self.assertRaises(MarketDataValidationError):
            self.engine.evaluate({"symbol": "AAPL"})

    def test_invalid_volatility_fails_closed(self):
        for invalid_value in (None, "3.0", math.nan, math.inf, -math.inf, True):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaises(MarketDataValidationError):
                    self.engine.evaluate({"current_volatility": invalid_value})

    def test_market_data_must_be_mapping(self):
        with self.assertRaises(TypeError):
            self.engine.evaluate(None)


class TestAdaptiveVolatilityConfig(unittest.TestCase):
    def test_invalid_configurations_are_rejected(self):
        invalid_configurations = (
            {"base_participation_rate": -0.1},
            {"base_participation_rate": 1.1},
            {"base_child_order_size": 0},
            {"volatility_threshold_high": -1.0},
            {
                "volatility_threshold_high": 5.0,
                "volatility_threshold_critical": 2.0,
            },
            {"limit_offset_bps_normal": -1},
        )

        for overrides in invalid_configurations:
            with self.subTest(overrides=overrides):
                with self.assertRaises((TypeError, ValueError)):
                    AdaptiveVolatilityConfig(**overrides)


if __name__ == "__main__":
    unittest.main()
