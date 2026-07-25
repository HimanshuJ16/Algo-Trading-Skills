import unittest
from physical_vs_cash_settlement_handling import InputData, PhysicalVsCashSettlementHandlingEngine

class TestPhysicalVsCashSettlementHandling(unittest.TestCase):
    def test_process(self):
        engine = PhysicalVsCashSettlementHandlingEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = PhysicalVsCashSettlementHandlingEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
