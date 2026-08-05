import unittest
from ssf_handling import (
    ModelConfig, MainEngine,
    SingleStockFuturesEngine, SSFContractSpec, SSFSettlementType,
    DividendEvent, SSFArbitrageSignal, SSFFairValueResult
)

class TestMainEngineLegacy(unittest.TestCase):
    def test_init(self):
        config = ModelConfig("test")
        engine = MainEngine(config)
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        config = ModelConfig("test")
        engine = MainEngine(config)
        self.assertTrue(engine.execute())

class TestSingleStockFuturesEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SingleStockFuturesEngine(arbitrage_cost_threshold_pct=0.3)
        self.spec = SSFContractSpec(
            symbol="RELIANCE.NS-FUT",
            underlying_spot_symbol="RELIANCE.NS",
            exchange="NSE",
            lot_size=250,
            days_to_expiry=30,
            settlement_type=SSFSettlementType.CASH_SETTLED,
            risk_free_rate_annual=0.06
        )

    def test_fair_value_and_cash_and_carry_arbitrage(self):
        # Spot = $2,500. Fair Value ~ 2500 * e^(0.055 * 30/365) = 2511.31
        # Market SSF = $2,530.00 (Overvalued by > 0.3% -> CASH_AND_CARRY signal)
        res = self.engine.compute_fair_value_and_arbitrage(
            spec=self.spec,
            spot_price=2500.0,
            market_ssf_price=2530.0
        )
        self.assertAlmostEqual(res.theoretical_fair_value, 2511.31, delta=0.5)
        self.assertEqual(res.arbitrage_signal, SSFArbitrageSignal.CASH_AND_CARRY)
        self.assertGreater(res.leverage_multiplier, 3.0)  # 50% spot margin vs 15% SSF margin = 3.33x

    def test_dividend_adjusted_fair_value(self):
        # Upcoming dividend of $20 in 15 days
        divs = [DividendEvent(ex_date_days=15, amount_per_share=20.0)]
        res = self.engine.compute_fair_value_and_arbitrage(
            spec=self.spec,
            spot_price=2500.0,
            market_ssf_price=2490.0,
            dividends=divs
        )
        # Spot adjusted for div PV ~ 2500 - 19.95 = 2480.05 -> Fair Value ~ 2491.27
        self.assertLess(res.theoretical_fair_value, 2500.0)

    def test_ex_dividend_base_price_adjustment(self):
        # Prev settlement 2500 - 20 div = 2480 adjusted base price
        adj = self.engine.calculate_ex_dividend_price_adjustment(2500.0, 20.0)
        self.assertEqual(adj, 2480.0)

if __name__ == '__main__':
    unittest.main()
