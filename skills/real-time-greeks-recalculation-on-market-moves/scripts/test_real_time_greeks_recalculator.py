import unittest
from real_time_greeks_recalculator import (
    RealTimeGreeksRecalculator, RealTimeGreeksRecalculatorConfig,
    OptionPosition, SingleOptionGreeksResult, RealTimeGreeksReport
)

class TestRealTimeGreeksRecalculator(unittest.TestCase):

    def setUp(self):
        self.config = RealTimeGreeksRecalculatorConfig(enabled=True, full_recalc_threshold_pct=0.005)
        self.instance = RealTimeGreeksRecalculator(self.config)

    def test_legacy_initialization(self):
        self.assertTrue(self.instance.config.enabled)

    def test_legacy_execute(self):
        result = self.instance.execute({"mock": "data"})
        self.assertTrue(result)

    def test_legacy_execute_disabled(self):
        self.instance.config.enabled = False
        result = self.instance.execute({"mock": "data"})
        self.assertFalse(result)

    def test_small_spot_move_uses_taylor_expansion(self):
        # Initial position with pre-set delta=0.50 and gamma=0.03
        pos = OptionPosition(
            symbol="AAPL_CALL_150", underlying_symbol="AAPL", option_type="CALL",
            strike=150.0, expiry_years=1.0, spot=150.0, iv=0.20, position_size=10.0, # 1,000 shares
            last_delta=0.50, last_gamma=0.03, last_vega=0.15
        )

        # Small move: $150.00 -> $150.20 (+0.133% < 0.5% threshold)
        report = self.instance.recalculate_portfolio_greeks("AAPL", 150.20, [pos])
        self.assertEqual(report.recalc_method, "TAYLOR_EXPANSION_UPDATE")
        self.assertEqual(report.status, "GREEKS_RECALCULATED_SUCCESS")
        # Delta = 0.50 + 0.03 * 0.20 = 0.506
        self.assertAlmostEqual(report.option_results[0].delta, 0.506, places=3)
        self.assertEqual(report.portfolio_net_delta, 506.0)

    def test_large_spot_move_triggers_full_black_scholes(self):
        pos = OptionPosition(
            symbol="AAPL_CALL_150", underlying_symbol="AAPL", option_type="CALL",
            strike=150.0, expiry_years=1.0, spot=150.0, iv=0.20, position_size=10.0,
            last_delta=0.50, last_gamma=0.03, last_vega=0.15
        )

        # Large move: $150.00 -> $153.00 (+2.0% > 0.5% threshold)
        report = self.instance.recalculate_portfolio_greeks("AAPL", 153.00, [pos])
        self.assertEqual(report.recalc_method, "FULL_BS_RECALCULATION")
        self.assertEqual(report.status, "GREEKS_RECALCULATED_SUCCESS")
        self.assertGreater(report.option_results[0].delta, 0.50)

if __name__ == '__main__':
    unittest.main()
