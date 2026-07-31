import unittest
from real_time_liquidity_monitor import (
    Config, MainEngine, RealTimeLiquidityMonitorEngine,
    PortfolioPositionLiquidity, RealTimeLiquidityReport
)

class TestRealTimeLiquidityMonitor(unittest.TestCase):

    def setUp(self):
        self.config = Config(param=2.0)
        self.legacy_engine = MainEngine(self.config)
        self.liquidity_engine = RealTimeLiquidityMonitorEngine(self.config)

    def test_legacy_default(self):
        engine = MainEngine(Config())
        self.assertEqual(engine.process(2.0), 2.0)

    def test_legacy_custom(self):
        self.assertEqual(self.legacy_engine.process(2.0), 4.0)

    def test_healthy_liquidity_profile(self):
        # 10,000 shares @ $100, ADV = 500,000. DTL = 10,000 / (500,000 * 0.10) = 0.2 days <= 2.0
        # Spread = 0.05 vs normal 0.05 (1.0x). L2 Depth = 10,000 vs normal 10,000 (0% drop)
        positions = [
            PortfolioPositionLiquidity(
                symbol="AAPL", position_size=10000.0, current_price=100.0,
                adv=500000.0, bid_ask_spread=0.05, l2_depth_top3=10000.0,
                normal_spread=0.05, normal_l2_depth=10000.0
            )
        ]

        report = self.liquidity_engine.audit_portfolio_liquidity(positions)
        self.assertEqual(report.status, "LIQUIDITY_HEALTHY")
        self.assertEqual(report.total_dtl_breaches, 0)
        self.assertEqual(report.total_spread_spikes, 0)
        self.assertEqual(report.total_depth_collapses, 0)

    def test_liquidity_risk_alert_dtl_and_spread_spike(self):
        # 100,000 shares @ $100, ADV = 200,000. DTL = 100,000 / 20,000 = 5.0 days (> 2.0 breach!)
        # Spread = 0.25 vs normal 0.05 (5.0x spike!)
        positions = [
            PortfolioPositionLiquidity(
                symbol="ILLIQ", position_size=100000.0, current_price=100.0,
                adv=200000.0, bid_ask_spread=0.25, l2_depth_top3=2000.0,
                normal_spread=0.05, normal_l2_depth=10000.0
            )
        ]

        report = self.liquidity_engine.audit_portfolio_liquidity(positions)
        self.assertEqual(report.status, "LIQUIDITY_RISK_ALERT")
        self.assertEqual(report.total_dtl_breaches, 1)
        self.assertEqual(report.total_spread_spikes, 1)
        self.assertGreater(report.portfolio_l_var_usd, 100000.0)

if __name__ == '__main__':
    unittest.main()
