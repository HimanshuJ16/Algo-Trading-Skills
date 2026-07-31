import unittest
from participation_of_volume_pov_execution import (
    ParticipationOfVolumePovExecutionConfig,
    ParticipationOfVolumePovExecutionEngine,
    POVParentOrder,
    POVExecutionReport
)

class TestParticipationOfVolumePovExecution(unittest.TestCase):

    def setUp(self):
        self.config = ParticipationOfVolumePovExecutionConfig(enabled=True, threshold=100.0, size=50)
        self.engine = ParticipationOfVolumePovExecutionEngine(self.config)

    def test_evaluate_triggers_order_legacy(self):
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["qty"], 50)

    def test_evaluate_no_trigger_legacy(self):
        market_data = {"symbol": "AAPL", "price": 95.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

    def test_disabled_engine_legacy(self):
        self.engine.config.enabled = False
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

    def test_pov_slice_calculation_and_execution(self):
        # Target 15% rate, Parent order 1,000 shares
        parent = POVParentOrder("AAPL", 1000, "BUY", target_rate=0.15, max_slice_qty=500)
        engine = ParticipationOfVolumePovExecutionEngine(parent_order=parent)

        # Interval 1: Market volume 1,000 shares
        # Slice = floor(0.15 / 0.85 * 1000) = floor(176.47) = 176 shares
        slice1, report1 = engine.process_volume_update(1000, 150.0)
        self.assertEqual(slice1, 176)
        self.assertEqual(report1.filled_qty, 176)
        self.assertEqual(report1.status, "EXECUTING")

        # Interval 2: High Market volume 5,000 shares -> raw slice 882 -> clamped to max_slice_qty 500
        slice2, report2 = engine.process_volume_update(5000, 150.50)
        self.assertEqual(slice2, 500)
        self.assertEqual(report2.filled_qty, 676)

if __name__ == '__main__':
    unittest.main()
