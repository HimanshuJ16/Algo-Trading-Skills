import unittest
import numpy as np
import pandas as pd
from strategy_performance_attribution_vs_market_beta import (
    StrategyPerformanceAttributionVsMarketBeta,
    StrategyPerformanceAttributionVsMarketBetaConfig,
    StrategyPerformanceAttributionEngine, PerformanceAttributionReport, FactorAttributionBreakdown
)

class TestPerformanceAttributionLegacy(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyPerformanceAttributionVsMarketBetaConfig(enabled=True)
        engine = StrategyPerformanceAttributionVsMarketBeta(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyPerformanceAttributionVsMarketBetaConfig(enabled=False)
        engine = StrategyPerformanceAttributionVsMarketBeta(config)
        self.assertFalse(engine.execute())

class TestStrategyPerformanceAttributionEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyPerformanceAttributionEngine()
        np.random.seed(42)
        mkt = np.random.normal(0.0005, 0.01, 252) # 12.6% annual mkt return
        # Generate strategy return: 1.2 * MKT + 5% annual Alpha + noise
        strat = 1.2 * mkt + (0.05 / 252) + np.random.normal(0, 0.002, 252)

        self.mkt_series = pd.Series(mkt)
        self.strat_series = pd.Series(strat)

    def test_capm_single_factor_attribution(self):
        report = self.engine.analyze_attribution(
            strategy_id="BETA_TILT_ALPHA_STRAT",
            strategy_returns=self.strat_series,
            market_returns=self.mkt_series,
            risk_free_rate_annual_pct=2.0
        )

        self.assertGreater(report.market_beta, 1.1)
        self.assertLess(report.market_beta, 1.3)
        self.assertGreater(report.annualized_jensens_alpha_pct, 4.0)
        self.assertGreater(report.r_squared, 0.90)
        self.assertEqual(len(report.factor_breakdown), 1)

    def test_fama_french_multi_factor_attribution(self):
        smb = np.random.normal(0.0002, 0.005, 252)
        hml = np.random.normal(-0.0001, 0.005, 252)

        # Strategy with Size (SMB) tilt
        strat_ff = 1.0 * self.mkt_series + 0.5 * smb + (0.04 / 252) + np.random.normal(0, 0.001, 252)

        report = self.engine.analyze_attribution(
            strategy_id="SMALL_CAP_TILT_STRAT",
            strategy_returns=strat_ff,
            market_returns=self.mkt_series,
            smb_returns=pd.Series(smb),
            hml_returns=pd.Series(hml),
            risk_free_rate_annual_pct=2.0
        )

        self.assertEqual(len(report.factor_breakdown), 3) # MKT, SMB, HML
        smb_breakdown = [f for f in report.factor_breakdown if f.factor_name == "Size (SMB)"][0]
        self.assertGreater(smb_breakdown.beta_exposure, 0.4)

if __name__ == '__main__':
    unittest.main()
