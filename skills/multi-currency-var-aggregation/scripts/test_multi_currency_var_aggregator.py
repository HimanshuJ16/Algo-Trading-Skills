import unittest
from multi_currency_var_aggregator import (
    MultiCurrencyVarAggregatorEngine, MultiCurrencyPosition, VarConfig, MultiCurrencyVarReport
)

class TestMultiCurrencyVarAggregatorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MultiCurrencyVarAggregatorEngine()

    def test_multi_currency_var_and_cvar_calculation(self):
        # Position 1: $100k AAPL (USD native) | Position 2: €100k SAP (EUR native @ 1.10 = $110k USD)
        # Total Base Portfolio Value = $210,000 USD
        p1 = MultiCurrencyPosition("AAPL", "USD", quantity=1000.0, current_price_native=100.0, fx_rate_to_base=1.0)
        p2 = MultiCurrencyPosition("SAP", "EUR", quantity=1000.0, current_price_native=100.0, fx_rate_to_base=1.10)

        # 100 periods of simulated returns
        r_aapl = [0.01 if i % 2 == 0 else -0.01 for i in range(100)]
        r_sap = [0.015 if i % 3 == 0 else -0.008 for i in range(100)]
        r_eur = [0.002 if i % 4 == 0 else -0.002 for i in range(100)]

        native_returns = {"AAPL": r_aapl, "SAP": r_sap}
        fx_returns = {"USD": [0.0] * 100, "EUR": r_eur}

        cfg = VarConfig(confidence_level=0.95, holding_period_days=1, base_currency="USD")
        report = self.engine.calculate_multi_currency_var(cfg, [p1, p2], native_returns, fx_returns)

        self.assertEqual(report.status, "VAR_CALCULATION_SUCCESS")
        self.assertAlmostEqual(report.total_portfolio_value_base, 210000.0, places=2)
        self.assertGreater(report.parametric_var_base, 0.0)
        self.assertGreater(report.historical_var_base, 0.0)
        self.assertGreaterEqual(report.expected_shortfall_cvar_base, report.historical_var_base)
        self.assertAlmostEqual(report.currency_risk_breakdown["USD"], 100000.0, places=2)
        self.assertAlmostEqual(report.currency_risk_breakdown["EUR"], 110000.0, places=2)

if __name__ == '__main__':
    unittest.main()
