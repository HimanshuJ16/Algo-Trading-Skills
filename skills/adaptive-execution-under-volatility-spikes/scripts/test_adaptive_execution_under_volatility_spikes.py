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
        self.assertEqual(params.limit_offset_bps, 5)
        self.assertFalse(params.halt_trading)

    def test_negative_volatility_is_normal(self):
        """A z-score far below the baseline is quiet, not a shock."""
        params = self.engine.evaluate({"current_volatility": -4.0})

        self.assertEqual(params.regime, MarketRegime.NORMAL)
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
        self.assertEqual(params.limit_offset_bps, 0)

    def test_high_volatility_child_size_floors_at_one(self):
        """Halving a one-unit base must not produce an unroutable zero size."""
        engine = AdaptiveExecutionUnderVolatilitySpikesEngine(
            AdaptiveVolatilityConfig(base_child_order_size=1)
        )

        params = engine.evaluate({"current_volatility": 3.0})

        self.assertEqual(params.regime, MarketRegime.HIGH_VOLATILITY)
        self.assertEqual(params.child_order_size, 1)

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

    def test_new_engine_has_not_classified_anything(self):
        self.assertEqual(
            AdaptiveExecutionUnderVolatilitySpikesEngine(
                AdaptiveVolatilityConfig()
            ).current_regime,
            MarketRegime.UNKNOWN,
        )

    def test_fault_does_not_leave_a_stale_normal_regime(self):
        """A monitor reading state after a fault must not see the last NORMAL.

        Regression: `current_regime` was only assigned on a completed
        classification, so a feed fault following a normal tick reported
        NORMAL while the caller was halting the parent.
        """
        self.engine.evaluate({"current_volatility": 1.0})
        self.assertEqual(self.engine.current_regime, MarketRegime.NORMAL)

        faults = (
            (MarketDataValidationError, {"symbol": "AAPL"}),
            (MarketDataValidationError, {"current_volatility": math.nan}),
            (TypeError, None),
        )
        for expected_error, payload in faults:
            with self.subTest(payload=payload):
                self.engine.evaluate({"current_volatility": 1.0})
                with self.assertRaises(expected_error):
                    self.engine.evaluate(payload)
                self.assertEqual(
                    self.engine.current_regime, MarketRegime.UNKNOWN
                )

    def test_unknown_regime_is_never_returned_as_a_decision(self):
        for volatility in (1.0, 3.0, 6.0):
            with self.subTest(volatility=volatility):
                params = self.engine.evaluate(
                    {"current_volatility": volatility}
                )
                self.assertNotEqual(params.regime, MarketRegime.UNKNOWN)

    def test_config_mutated_to_invalid_is_rejected_at_evaluate(self):
        """The config is mutable, so every call re-validates it."""
        self.engine.config.volatility_threshold_critical = 1.0

        with self.assertRaises(ValueError):
            self.engine.evaluate({"current_volatility": 1.0})

        self.assertEqual(self.engine.current_regime, MarketRegime.UNKNOWN)

    def test_disabled_engine_does_not_validate_volatility(self):
        """Pins the bypass: disabling the overlay removes the fail-closed check."""
        self.engine.config.enabled = False

        params = self.engine.evaluate({"current_volatility": math.nan})

        self.assertEqual(params.regime, MarketRegime.NORMAL)
        self.assertFalse(params.halt_trading)

    def test_engine_requires_a_config_instance(self):
        for bad_config in (None, {"enabled": True}):
            with self.subTest(bad_config=bad_config):
                with self.assertRaises(TypeError):
                    AdaptiveExecutionUnderVolatilitySpikesEngine(bad_config)


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
