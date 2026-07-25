import unittest
from exchange_for_physical_efp_transactions import InputData, ExchangeForPhysicalEfpTransactionsEngine

class TestExchangeForPhysicalEfpTransactions(unittest.TestCase):
    def test_process(self):
        engine = ExchangeForPhysicalEfpTransactionsEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = ExchangeForPhysicalEfpTransactionsEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
