import unittest
from fx_forward_and_swap_position_tracking import Configuration, FxForwardAndSwapPositionTrackingEngine

class TestFxForwardAndSwapPositionTracking(unittest.TestCase):
    def test_default_execution(self):
        engine = FxForwardAndSwapPositionTrackingEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution(self):
        engine = FxForwardAndSwapPositionTrackingEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")
        
    def test_empty_data(self):
        engine = FxForwardAndSwapPositionTrackingEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

if __name__ == '__main__':
    unittest.main()
