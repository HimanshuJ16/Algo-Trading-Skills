import unittest
from crypto_transaction_tax_lot_tracking import CryptoTransactionTaxLotTrackingEngine, Record

class TestCryptoTransactionTaxLotTrackingEngine(unittest.TestCase):
    def test_initialization(self):
        engine = CryptoTransactionTaxLotTrackingEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = CryptoTransactionTaxLotTrackingEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = CryptoTransactionTaxLotTrackingEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
