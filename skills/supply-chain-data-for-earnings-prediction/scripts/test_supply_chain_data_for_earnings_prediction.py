import unittest
import pandas as pd
from supply_chain_data_for_earnings_prediction import (
    Config, Engine,
    SupplyChainDataForEarningsPredictionEngine, SignalResult
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")

    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())

class TestSupplyChainDataForEarningsPrediction(unittest.TestCase):
    def setUp(self):
        self.engine = SupplyChainDataForEarningsPredictionEngine()

    def test_empty_data(self):
        df = pd.DataFrame()
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 0)

    def test_valid_data_legacy(self):
        df = pd.DataFrame({
            "raw_val": [10.0, 20.0],
            "asset": ["AAPL", "MSFT"]
        })
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].signal_value, 15.0)
        self.assertEqual(signals[0].asset_id, "AAPL")

    def test_evaluate_supply_chain_lead_signal_buy_surprise(self):
        # High supplier growth (25%) vs consensus (10%) -> Bullish surprise
        res = self.engine.evaluate_supply_chain_lead_signal(
            target_asset="NVDA",
            supplier_revenue_growth_pct=25.0,
            customer_inventory_growth_pct=5.0,
            consensus_eps_growth_pct=10.0
        )
        # Implied growth = 0.7*25 - 0.3*5 = 17.5 - 1.5 = 16.0%
        # Consensus gap = 16.0 - 10.0 = 6.0%
        # Signal val = 6.0 / 5.0 = 1.2 >= 1.0 -> BUY_EARNINGS_SURPRISE
        self.assertEqual(res.directional_signal, "BUY_EARNINGS_SURPRISE")
        self.assertEqual(res.estimated_revenue_growth_pct, 16.0)
        self.assertGreater(res.signal_value, 1.0)

    def test_evaluate_supply_chain_lead_signal_sell_disappointment(self):
        # Weak supplier growth (-10%) & high customer inventory build-up (20%) -> Bearish disappointment
        res = self.engine.evaluate_supply_chain_lead_signal(
            target_asset="TSLA",
            supplier_revenue_growth_pct=-10.0,
            customer_inventory_growth_pct=20.0,
            consensus_eps_growth_pct=5.0
        )
        # Implied growth = 0.7*(-10) - 0.3*20 = -7 - 6 = -13.0%
        # Consensus gap = -13.0 - 5.0 = -18.0%
        # Signal val = -18.0 / 5.0 = -3.6 <= -1.0 -> SELL_EARNINGS_DISAPPOINTMENT
        self.assertEqual(res.directional_signal, "SELL_EARNINGS_DISAPPOINTMENT")
        self.assertLess(res.signal_value, -1.0)

if __name__ == '__main__':
    unittest.main()
