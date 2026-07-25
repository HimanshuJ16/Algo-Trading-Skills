import unittest
from fifo_vs_specific_lot_tax_accounting_methods import FifoVsSpecificLotTaxAccountingMethodsEngine, Record

class TestFifoVsSpecificLotTaxAccountingMethodsEngine(unittest.TestCase):
    def test_initialization(self):
        engine = FifoVsSpecificLotTaxAccountingMethodsEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = FifoVsSpecificLotTaxAccountingMethodsEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = FifoVsSpecificLotTaxAccountingMethodsEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
