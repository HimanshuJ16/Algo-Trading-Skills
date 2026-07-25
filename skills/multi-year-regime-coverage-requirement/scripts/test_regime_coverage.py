"""
Unit tests for multi-year-regime-coverage-requirement skill.
"""
import unittest
from regime_coverage import MarketRegime, MarketRegimeCoverageEngine


class TestMarketRegimeCoverageEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MarketRegimeCoverageEngine(
            min_required_years=1.0,  # 1 year for test brevity
            min_required_regimes=3,
            max_allowed_regime_drawdown_pct=20.0,
        )

    def test_regime_classification_and_coverage(self):
        # Generate 300 bars of multi-regime prices (Bull, Bear, Volatile)
        prices = []
        p = 100.0
        # Bull regime (100 bars)
        for _ in range(100):
            p *= 1.005
            prices.append(p)
        # Bear regime (100 bars)
        for _ in range(100):
            p *= 0.995
            prices.append(p)
        # High vol regime (100 bars)
        for i in range(100):
            p += 10.0 if i % 2 == 0 else -9.5
            prices.append(p)

        strat_rets = [0.001] * 300  # Constant positive returns

        report = self.engine.audit_coverage(prices, strat_rets)

        self.assertTrue(report.is_coverage_sufficient)
        self.assertGreaterEqual(len(report.unique_regimes_covered), 3)
        self.assertTrue(report.is_promotable)

    def test_regime_drawdown_veto(self):
        prices = [100.0 + i for i in range(300)]
        # Inject -30% drawdown into strategy returns during part of backtest
        strat_rets = [0.01] * 150 + [-0.05] * 8 + [0.01] * 142

        report = self.engine.audit_coverage(prices, strat_rets)

        self.assertFalse(report.is_promotable)
        self.assertIn("REGIME VETO", report.message)


if __name__ == "__main__":
    unittest.main()
