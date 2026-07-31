import unittest
from options_greeks_real_time_portfolio_aggregation import (
    Configuration,
    OptionsGreeksRealTimePortfolioAggregationEngine,
    OptionPosition,
    PortfolioGreeksLimits,
    PortfolioGreeksReport
)

class TestOptionsGreeksRealTimePortfolioAggregation(unittest.TestCase):

    def test_default_execution_legacy(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution_legacy(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")

    def test_empty_data_legacy(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

    def test_real_time_greeks_aggregation(self):
        # Position 1: Long 10 AAPL Calls @ $150 spot (Delta=0.60, Gamma=0.02, Theta=-0.10, Vega=0.15)
        # Net Delta = 10 * 100 * 0.60 = +600 shares, Dollar Delta = 600 * 150 = $90,000
        # Position 2: Short 5 AAPL Puts @ $150 spot (Delta=-0.40 -> Short Put Delta = -5 * 100 * (-0.40) = +200 shares)
        # Net Delta AAPL = 600 + 200 = 800 shares ($120,000 Dollar Delta)
        pos1 = OptionPosition("AAPL240119C00150000", "AAPL", 10, 100, 150.0, 0.60, 0.02, -0.10, 0.15)
        pos2 = OptionPosition("AAPL240119P00150000", "AAPL", -5, 100, 150.0, -0.40, 0.01, -0.08, 0.12)

        engine = OptionsGreeksRealTimePortfolioAggregationEngine()
        report = engine.aggregate_portfolio_greeks([pos1, pos2])

        self.assertEqual(report.status, "PORTFOLIO_GREEKS_HEALTHY")
        self.assertEqual(report.net_delta_shares, 800.0)
        self.assertEqual(report.net_dollar_delta_usd, 120000.0)
        self.assertEqual(report.by_underlying["AAPL"]["net_delta"], 800.0)

    def test_dollar_delta_limit_breach(self):
        # 100 long NVDA Calls @ $500 spot (Delta=0.80 -> 100 * 100 * 0.80 = 8,000 shares * $500 = $4,000,000 Dollar Delta > $500k limit)
        pos = OptionPosition("NVDA240621C00500000", "NVDA", 100, 100, 500.0, 0.80, 0.01, -0.20, 0.30)

        limits = PortfolioGreeksLimits(max_dollar_delta_usd=500000.0)
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(limits=limits)

        report = engine.aggregate_portfolio_greeks([pos])
        self.assertEqual(report.status, "DOLLAR_DELTA_BREACH")
        self.assertEqual(report.net_dollar_delta_usd, 4000000.0)

if __name__ == '__main__':
    unittest.main()
