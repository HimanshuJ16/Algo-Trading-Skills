import unittest
import datetime
from vix_strategies import (
    TermStructureState,
    VIXStrategyType,
    VIXFuturesContract,
    VIXTermStructure,
    VIXStrategySignal,
    VIXStrategyEngine,
    VIXEngineError,
)


class TestVIXStrategyEngine(unittest.TestCase):
    def setUp(self):
        self.engine = VIXStrategyEngine(contango_threshold_pct=2.0, backwardation_threshold_pct=-2.0)
        self.today = datetime.date.today()

        self.f1_contango = VIXFuturesContract(
            symbol="VXF25",
            expiry_date=self.today + datetime.timedelta(days=20),
            days_to_expiry=20,
            price=16.0,
        )
        self.f2_contango = VIXFuturesContract(
            symbol="VXG25",
            expiry_date=self.today + datetime.timedelta(days=50),
            days_to_expiry=50,
            price=17.5,
        )

        self.f1_backwardation = VIXFuturesContract(
            symbol="VXF25",
            expiry_date=self.today + datetime.timedelta(days=20),
            days_to_expiry=20,
            price=32.0,
        )
        self.f2_backwardation = VIXFuturesContract(
            symbol="VXG25",
            expiry_date=self.today + datetime.timedelta(days=50),
            days_to_expiry=50,
            price=28.0,
        )

    def test_contango_term_structure_analysis(self):
        ts = self.engine.analyze_term_structure(spot_vix=15.0, f1=self.f1_contango, f2=self.f2_contango)

        self.assertEqual(ts.state, TermStructureState.CONTANGO)
        self.assertEqual(ts.slope_f2_minus_f1, 1.5)  # 17.5 - 16.0
        self.assertTrue(ts.annualized_roll_yield_pct > 0.0)

    def test_backwardation_term_structure_analysis(self):
        ts = self.engine.analyze_term_structure(
            spot_vix=35.0, f1=self.f1_backwardation, f2=self.f2_backwardation
        )

        self.assertEqual(ts.state, TermStructureState.BACKWARDATION)
        self.assertEqual(ts.slope_f2_minus_f1, -4.0)  # 28.0 - 32.0

    def test_roll_yield_harvest_signal_generation(self):
        ts = self.engine.analyze_term_structure(spot_vix=15.0, f1=self.f1_contango, f2=self.f2_contango)
        signal = self.engine.generate_strategy_signal(ts, portfolio_equity_usd=10_000_000.0)

        self.assertEqual(signal.strategy_type, VIXStrategyType.ROLL_YIELD_HARVEST)
        self.assertEqual(signal.recommended_position, "SHORT_F1_VIX_FUTURE")
        self.assertTrue(signal.target_contracts >= 1)
        self.assertTrue(signal.daily_roll_decay_usd > 0.0)

    def test_tail_risk_hedge_signal_generation(self):
        ts = self.engine.analyze_term_structure(
            spot_vix=35.0, f1=self.f1_backwardation, f2=self.f2_backwardation
        )
        signal = self.engine.generate_strategy_signal(ts, portfolio_equity_usd=10_000_000.0)

        self.assertEqual(signal.strategy_type, VIXStrategyType.TAIL_RISK_HEDGE)
        self.assertEqual(signal.recommended_position, "LONG_VIX_CALL_SPREAD")
        self.assertTrue(signal.tail_hedge_protection_usd > 0.0)

    def test_vix_call_spread_pricing(self):
        max_profit, max_loss = self.engine.price_vix_call_spread(
            f1_futures_price=16.0, strike_lower=20.0, strike_upper=35.0, days_to_expiry=30
        )
        # 15 pt spread * $1000 multiplier = $15,000 max profit
        self.assertEqual(max_profit, 15_000.0)
        self.assertEqual(max_loss, 3_750.0)

    def test_invalid_inputs_raise_error(self):
        with self.assertRaises(VIXEngineError):
            self.engine.analyze_term_structure(spot_vix=-5.0, f1=self.f1_contango, f2=self.f2_contango)

        with self.assertRaises(VIXEngineError):
            self.engine.price_vix_call_spread(16.0, strike_lower=30.0, strike_upper=20.0, days_to_expiry=30)


if __name__ == "__main__":
    unittest.main()

