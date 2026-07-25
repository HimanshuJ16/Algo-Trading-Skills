import unittest
from credit_default_swap_basics_for_algo_context import Configuration, CreditDefaultSwapBasicsForAlgoContextEngine

class TestCreditDefaultSwapBasicsForAlgoContext(unittest.TestCase):
    def test_default_execution(self):
        engine = CreditDefaultSwapBasicsForAlgoContextEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution(self):
        engine = CreditDefaultSwapBasicsForAlgoContextEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")
        
    def test_empty_data(self):
        engine = CreditDefaultSwapBasicsForAlgoContextEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

if __name__ == '__main__':
    unittest.main()
