import unittest
from market_data_simulator import (
    MarketDataSimulatorEngine, SimulationConfig, SimulationReport
)

class TestMarketDataSimulatorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MarketDataSimulatorEngine()

    def test_gbm_tick_generation_and_reproducibility(self):
        cfg = SimulationConfig(
            symbol="AAPL", initial_price=100.0, drift_mu=0.05, volatility_sigma=0.20,
            time_step_sec=1.0, num_steps=500, spread_bps=10.0, random_seed=42
        )

        report1 = self.engine.generate_synthetic_tick_stream(cfg)
        report2 = self.engine.generate_synthetic_tick_stream(cfg)

        self.assertEqual(report1.status, "SIMULATION_COMPLETED")
        self.assertEqual(report1.total_ticks_generated, 500)
        self.assertGreater(report1.final_price, 0.0)

        # 100% Seed Reproducibility
        self.assertEqual(report1.final_price, report2.final_price)
        self.assertEqual(report1.ticks[0].bid_price, report2.ticks[0].bid_price)

    def test_bid_ask_spread_ordering(self):
        cfg = SimulationConfig(
            symbol="BTC-USD", initial_price=50000.0, num_steps=100, spread_bps=20.0, random_seed=123
        )
        report = self.engine.generate_synthetic_tick_stream(cfg)

        for tick in report.ticks:
            self.assertLess(tick.bid_price, tick.ask_price)
            self.assertEqual(tick.symbol, "BTC-USD")

if __name__ == '__main__':
    unittest.main()
