import unittest
from inference_latency_budgeter import Config, InferenceLatencyBudgeter

class TestInferenceLatencyBudgeter(unittest.TestCase):
    def test_init(self):
        obj = InferenceLatencyBudgeter(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = InferenceLatencyBudgeter(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
