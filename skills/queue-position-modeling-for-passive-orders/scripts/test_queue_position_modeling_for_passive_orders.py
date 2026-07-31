import unittest
from queue_position_modeling_for_passive_orders import (
    Config, MainEngine, QueuePositionModelEngine,
    PassiveOrderTracker, QueuePositionReport
)

class TestQueuePositionModelingForPassiveOrders(unittest.TestCase):

    def setUp(self):
        self.config = Config(param1=2.0)
        self.legacy_engine = MainEngine(self.config)
        self.model_engine = QueuePositionModelEngine(self.config)

    def test_legacy_execute(self):
        self.assertTrue(self.legacy_engine.execute())

    def test_legacy_process_data(self):
        self.assertEqual(self.legacy_engine.process_data(10.0), 20.0)

    def test_queue_position_tracking_and_fills(self):
        tracker = PassiveOrderTracker(
            order_id="ORD_PASSIVE_01", side="BUY", price=100.0,
            our_quantity=100.0, initial_queue_ahead=1000.0, total_level_volume=2000.0
        )

        # 500 fills, 200 cancellations
        report = self.model_engine.update_queue_position(tracker, accumulated_fills=500.0, accumulated_cancellations=200.0)
        self.assertEqual(report.status, "QUEUE_PRIORITY_TRACKING")
        self.assertFalse(report.is_front_of_queue)
        self.assertLess(report.current_queue_ahead, 1000.0)

    def test_front_of_queue_reached(self):
        tracker = PassiveOrderTracker(
            order_id="ORD_PASSIVE_02", side="BUY", price=100.0,
            our_quantity=100.0, initial_queue_ahead=500.0, total_level_volume=1000.0
        )

        # 600 fills (exceeds 500 ahead) -> front of queue reached
        report = self.model_engine.update_queue_position(tracker, accumulated_fills=600.0, accumulated_cancellations=0.0)
        self.assertEqual(report.status, "FRONT_OF_QUEUE")
        self.assertTrue(report.is_front_of_queue)
        self.assertEqual(report.current_queue_ahead, 0.0)
        self.assertEqual(report.estimated_queue_rank, 1)

if __name__ == '__main__':
    unittest.main()
