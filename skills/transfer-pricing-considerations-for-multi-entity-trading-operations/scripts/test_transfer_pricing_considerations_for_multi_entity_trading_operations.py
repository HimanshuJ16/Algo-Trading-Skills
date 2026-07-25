import unittest
from transfer_pricing_considerations_for_multi_entity_trading_operations import TransferPricingConsiderationsForMultiEntityTradingOperationsEngine, Record

class TestTransferPricingConsiderationsForMultiEntityTradingOperationsEngine(unittest.TestCase):
    def test_initialization(self):
        engine = TransferPricingConsiderationsForMultiEntityTradingOperationsEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = TransferPricingConsiderationsForMultiEntityTradingOperationsEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = TransferPricingConsiderationsForMultiEntityTradingOperationsEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
