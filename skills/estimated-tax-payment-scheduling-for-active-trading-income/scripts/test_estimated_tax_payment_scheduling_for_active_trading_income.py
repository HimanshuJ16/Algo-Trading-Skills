import unittest
from estimated_tax_payment_scheduling_for_active_trading_income import EstimatedTaxPaymentSchedulingForActiveTradingIncomeEngine, Record

class TestEstimatedTaxPaymentSchedulingForActiveTradingIncomeEngine(unittest.TestCase):
    def test_initialization(self):
        engine = EstimatedTaxPaymentSchedulingForActiveTradingIncomeEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = EstimatedTaxPaymentSchedulingForActiveTradingIncomeEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = EstimatedTaxPaymentSchedulingForActiveTradingIncomeEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
