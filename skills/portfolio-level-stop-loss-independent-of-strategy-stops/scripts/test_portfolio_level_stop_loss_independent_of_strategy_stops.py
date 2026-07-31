import unittest
from portfolio_level_stop_loss_independent_of_strategy_stops import (
    PortfolioLevelStopLossIndependentOfStrategyStops,
    PortfolioLevelStopLossIndependentOfStrategyStopsConfig,
    StrategyPosition,
    PortfolioState,
    PortfolioStopReport
)

class TestPortfolioLevelStopLossIndependentOfStrategyStops(unittest.TestCase):

    def test_execute_true_legacy(self):
        config = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(enabled=True)
        engine = PortfolioLevelStopLossIndependentOfStrategyStops(config)
        self.assertTrue(engine.execute())

    def test_execute_false_legacy(self):
        config = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(enabled=False)
        engine = PortfolioLevelStopLossIndependentOfStrategyStops(config)
        self.assertFalse(engine.execute())

    def test_healthy_portfolio_no_breach(self):
        engine = PortfolioLevelStopLossIndependentOfStrategyStops()
        positions = [
            StrategyPosition("Trend_1", "AAPL", 100, 150.0, 500.0),
            StrategyPosition("MeanRev_2", "MSFT", 50, 300.0, 200.0)
        ]
        # Cash $970,000 + Pos Val $30,000 = $1,000,000 NAV (SOD Equity $1,000,000) -> 0% DD
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1000000.0, current_cash=970000.0, open_positions=positions)

        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.status, "PORTFOLIO_NAV_HEALTHY")
        self.assertFalse(report.is_trading_locked)
        self.assertEqual(report.positions_to_flatten_count, 0)

    def test_daily_drawdown_breach_triggers_flattening(self):
        # Max daily drawdown 5%.
        # SOD Equity $1,000,000 -> Max acceptable NAV $950,000.
        # Current NAV $940,000 (Daily DD 6.0%) -> BREACH!
        config = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(max_daily_drawdown_pct=0.05)
        engine = PortfolioLevelStopLossIndependentOfStrategyStops(config)

        positions = [
            StrategyPosition("Trend_1", "AAPL", 100, 100.0, -10000.0),
            StrategyPosition("MeanRev_2", "TSLA", 200, 150.0, -50000.0)
        ]
        state = PortfolioState(start_of_day_equity=1000000.0, peak_equity=1050000.0, current_cash=900000.0, open_positions=positions)

        report = engine.evaluate_portfolio_stop(state)
        self.assertEqual(report.status, "DAILY_DRAWDOWN_BREACH_FLATTEN")
        self.assertTrue(report.is_daily_breached)
        self.assertTrue(report.is_trading_locked)
        self.assertEqual(report.positions_to_flatten_count, 2)

if __name__ == '__main__':
    unittest.main()
