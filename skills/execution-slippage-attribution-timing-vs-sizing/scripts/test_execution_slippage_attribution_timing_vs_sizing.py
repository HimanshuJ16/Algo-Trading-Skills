import unittest
from execution_slippage_attribution_timing_vs_sizing import (
    ExecutionSlippageAttributionEngine, TradeExecutionSummary, SlippageAttributionAuditReport
)

class TestExecutionSlippageAttributionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionSlippageAttributionEngine()

    def test_timing_driven_buy_slippage(self):
        # BUY order: Decision=$100.00, Arrival=$100.50 (+50bps timing delay), Exec=$100.70 (+20bps sizing impact)
        # Total IS = +70.0 bps (Timing = +50.0 bps / 71.4%, Sizing = +20.0 bps / 28.6%) -> TIMING DRIVEN!
        trade = TradeExecutionSummary(
            trade_id="TR_BUY_01", symbol="AAPL", side="BUY", order_qty=10_000,
            decision_price=100.00, arrival_price=100.50, average_exec_price=100.70,
            decision_time_iso="2026-07-30T10:00:00Z", arrival_time_iso="2026-07-30T10:02:00Z", completion_time_iso="2026-07-30T10:15:00Z"
        )
        report = self.engine.attribute_execution_slippage(trade)

        self.assertEqual(report.total_is_slippage_bps, 70.0)
        self.assertEqual(report.timing_delay_slippage_bps, 50.0)
        self.assertEqual(report.sizing_impact_slippage_bps, 20.0)
        self.assertEqual(report.primary_slippage_driver, "TIMING_DRIVEN_SLIPPAGE")
        self.assertEqual(report.strategy_action_recommendation, "ACCELERATE_ORDER_DISPATCH")

    def test_sizing_driven_sell_slippage(self):
        # SELL order: Decision=$100.00, Arrival=$99.90 (+10bps timing loss), Exec=$99.20 (+70bps sizing impact loss)
        # Total IS = +80.0 bps loss (Timing = +10.0 bps, Sizing = +70.0 bps) -> SIZING DRIVEN!
        trade = TradeExecutionSummary(
            trade_id="TR_SELL_02", symbol="MSFT", side="SELL", order_qty=25_000,
            decision_price=100.00, arrival_price=99.90, average_exec_price=99.20,
            decision_time_iso="2026-07-30T10:00:00Z", arrival_time_iso="2026-07-30T10:00:05Z", completion_time_iso="2026-07-30T10:30:00Z"
        )
        report = self.engine.attribute_execution_slippage(trade)

        self.assertEqual(report.total_is_slippage_bps, 80.0)
        self.assertEqual(report.timing_delay_slippage_bps, 10.0)
        self.assertEqual(report.sizing_impact_slippage_bps, 70.0)
        self.assertEqual(report.primary_slippage_driver, "SIZING_DRIVEN_SLIPPAGE")
        self.assertEqual(report.strategy_action_recommendation, "REDUCE_PARTICIPATION_RATE_CEILING")

if __name__ == '__main__':
    unittest.main()
