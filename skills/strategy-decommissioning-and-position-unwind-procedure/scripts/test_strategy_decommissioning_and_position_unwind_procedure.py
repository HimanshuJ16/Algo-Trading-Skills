import unittest
from strategy_decommissioning_and_position_unwind_procedure import (
    Config, Engine,
    StrategyDecommissioningEngine, StrategyPosition, DecommissionState,
    LiquidationSliceOrder, UnwindProcedureReport
)

class TestStrategyDecommissioningLegacy(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

class TestStrategyDecommissioningEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyDecommissioningEngine("STRAT_MOMENTUM_ALPHA")
        self.positions = [
            StrategyPosition("AAPL", quantity=1000.0, market_price=150.0, avg_daily_volume=5000.0, max_adv_slice_pct=10.0), # Slice = 500
            StrategyPosition("MSFT", quantity=-500.0, market_price=300.0, avg_daily_volume=2000.0, max_adv_slice_pct=10.0), # Slice = 200
        ]
        self.engine.load_positions(self.positions)

    def test_initiate_decommissioning_blocks_new_entries(self):
        report = self.engine.initiate_decommissioning("Sharpe ratio decay below 0.5")
        self.assertEqual(report.state, DecommissionState.ORDER_ENTRY_BLOCKED)
        self.assertFalse(report.new_entries_allowed)

    def test_orderly_unwind_liquidation_slicing(self):
        self.engine.initiate_decommissioning()
        report = self.engine.generate_unwind_liquidation_slices()

        self.assertEqual(report.state, DecommissionState.UNWIND_IN_PROGRESS)
        self.assertEqual(len(report.slices_generated), 2)

        # AAPL Long 1000 -> Liquidation SELL slice of 500 shares (10% of 5000 ADV)
        aapl_slice = [s for s in report.slices_generated if s.symbol == "AAPL"][0]
        self.assertEqual(aapl_slice.side, "SELL")
        self.assertEqual(aapl_slice.slice_quantity, 500.0)

        # MSFT Short -500 -> Liquidation BUY slice of 200 shares (10% of 2000 ADV)
        msft_slice = [s for s in report.slices_generated if s.symbol == "MSFT"][0]
        self.assertEqual(msft_slice.side, "BUY")
        self.assertEqual(msft_slice.slice_quantity, 200.0)

    def test_record_execution_and_complete_unwind(self):
        self.engine.initiate_decommissioning()
        # Record full liquidation execution
        self.engine.record_slice_execution("S1", "AAPL", 1000.0, 150.0, realized_pnl=5000.0)
        self.engine.record_slice_execution("S2", "MSFT", 500.0, 300.0, realized_pnl=-1000.0)

        self.assertEqual(self.engine.state, DecommissionState.FULLY_UNWOUND)
        self.assertEqual(self.engine.liquidated_pnl_usd, 4000.0)

if __name__ == '__main__':
    unittest.main()
