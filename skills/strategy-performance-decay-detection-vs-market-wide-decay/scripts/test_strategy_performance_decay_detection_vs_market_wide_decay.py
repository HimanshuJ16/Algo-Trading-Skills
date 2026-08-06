import unittest
import numpy as np
import pandas as pd
from strategy_performance_decay_detection_vs_market_wide_decay import (
    Config, Engine,
    StrategyPerformanceDecayDiagnosticEngine, DecayClassification,
    StrategyDecayDiagnosticsReport
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

class TestStrategyPerformanceDecayEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyPerformanceDecayDiagnosticEngine(
            rolling_window_days=60,
            idiosyncratic_z_threshold=-1.96,
            market_wide_sharpe_threshold=0.50
        )

        np.random.seed(42)
        # Peer benchmark remains healthy: mean = 0.001 (~25% ann)
        peer_rets = np.random.normal(0.001, 0.01, 120)

        # Strategy 1: Healthy (matches peers)
        strat_healthy = np.random.normal(0.001, 0.01, 120)

        # Strategy 2: Idiosyncratic Alpha Decay (returns drop to -0.001 in last 60 days)
        strat_decayed = np.concatenate([
            np.random.normal(0.001, 0.01, 60),
            np.random.normal(-0.0015, 0.01, 60)
        ])

        # Peer + Strategy both decaying (Market-Wide Regime Shift)
        peer_regime_shift = np.concatenate([
            np.random.normal(0.001, 0.01, 60),
            np.random.normal(-0.001, 0.01, 60)
        ])

        self.peer_series = pd.Series(peer_rets)
        self.strat_healthy = pd.Series(strat_healthy)
        self.strat_decayed = pd.Series(strat_decayed)
        self.peer_regime_shift = pd.Series(peer_regime_shift)

    def test_idiosyncratic_alpha_decay_detection(self):
        report = self.engine.evaluate_decay_cause(
            strategy_id="DECAYED_ALPHA_STRAT",
            peer_benchmark_id="STAT_ARB_PEER_INDEX",
            strategy_returns=self.strat_decayed,
            peer_returns=self.peer_series
        )

        self.assertEqual(report.classification, DecayClassification.IDIOSYNCRATIC_ALPHA_DECAY)
        self.assertLess(report.relative_sharpe_z_score, -1.96)
        self.assertIn("DECOMMISSION", report.recommended_action)

    def test_market_wide_regime_shift_detection(self):
        report = self.engine.evaluate_decay_cause(
            strategy_id="TREND_STRAT",
            peer_benchmark_id="TREND_PEER_INDEX",
            strategy_returns=self.strat_decayed,
            peer_returns=self.peer_regime_shift
        )

        self.assertEqual(report.classification, DecayClassification.MARKET_WIDE_REGIME_SHIFT)
        self.assertIn("PAUSE_OR_REDUCE_RISK", report.recommended_action)

if __name__ == '__main__':
    unittest.main()
