import unittest
from currency_gain_loss_tax_treatment_for_forex_trading import CurrencyGainLossTaxTreatmentForForexTradingEngine, Record

class TestCurrencyGainLossTaxTreatmentForForexTradingEngine(unittest.TestCase):
    def test_initialization(self):
        engine = CurrencyGainLossTaxTreatmentForForexTradingEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = CurrencyGainLossTaxTreatmentForForexTradingEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = CurrencyGainLossTaxTreatmentForForexTradingEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
