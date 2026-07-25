import unittest
from capital_gains_vs_business_income_classification import CapitalGainsVsBusinessIncomeClassificationEngine, Record

class TestCapitalGainsVsBusinessIncomeClassificationEngine(unittest.TestCase):
    def test_initialization(self):
        engine = CapitalGainsVsBusinessIncomeClassificationEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = CapitalGainsVsBusinessIncomeClassificationEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = CapitalGainsVsBusinessIncomeClassificationEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
