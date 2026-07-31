import unittest
from portfolio_stress_test_including_liquidity_crunch_scenarios import (
    Config, Engine, PortfolioStressTestEngine,
    PortfolioPosition, StressScenario, StressTestReport
)

class TestPortfolioStressTestIncludingLiquidityCrunchScenarios(unittest.TestCase):

    def setUp(self):
        self.config = Config(name="test_stress", max_allowed_dtl_days=5.0)
        self.legacy_engine = Engine(self.config)
        self.stress_engine = PortfolioStressTestEngine(self.config)

    def test_legacy_init_and_run(self):
        self.assertEqual(self.legacy_engine.config.name, "test_stress")
        self.assertTrue(self.legacy_engine.run())

    def test_liquid_portfolio_stress_pass(self):
        # Liquid portfolio holding 1,000 shares @ $150 (ADV 1,000,000 shares)
        positions = [
            PortfolioPosition("AAPL", 1000.0, 150.0, adv_shares=1000000.0, spread_bps=5.0)
        ]
        scenario = StressScenario("2020_COVID_SHOCK", price_shock_pct={"AAPL": -0.20}, liquidity_drop_pct=0.50, spread_expansion_factor=5.0)

        report = self.stress_engine.run_stress_test(positions, scenario)
        self.assertEqual(report.status, "STRESS_TEST_PASSED")
        self.assertEqual(report.price_shock_loss_usd, 30000.0) # $150,000 * 20% = $30,000
        self.assertLess(report.max_dtl_days, 1.0)

    def test_illiquid_crunch_warning_dtl(self):
        # Illiquid position holding 100,000 shares @ $10 (ADV 50,000 shares => Stressed ADV 25,000 => Cap 2,500/day => DTL 40 days!)
        positions = [
            PortfolioPosition("SMALL_CAP", 100000.0, 10.0, adv_shares=50000.0, spread_bps=20.0)
        ]
        scenario = StressScenario("LIQUIDITY_CRUNCH_2022", price_shock_pct={"SMALL_CAP": -0.30}, liquidity_drop_pct=0.50)

        report = self.stress_engine.run_stress_test(positions, scenario)
        self.assertEqual(report.status, "LIQUIDITY_CRUNCH_ILLIQUID_WARNING")
        self.assertIn("SMALL_CAP", report.illiquid_symbols)
        self.assertGreater(report.max_dtl_days, 5.0)

if __name__ == '__main__':
    unittest.main()
