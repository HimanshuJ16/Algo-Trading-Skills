import unittest
from kupiec_var_backtester import KupiecVaRBacktester

class TestKupiecVaRBacktester(unittest.TestCase):
    def test_accept_model(self):
        tester = KupiecVaRBacktester(0.99)
        res = tester.run_test(1000, 10)
        self.assertFalse(res.is_rejected)
        self.assertEqual(res.exceptions, 10)

    def test_reject_model(self):
        tester = KupiecVaRBacktester(0.99)
        res = tester.run_test(1000, 25)
        self.assertTrue(res.is_rejected)
