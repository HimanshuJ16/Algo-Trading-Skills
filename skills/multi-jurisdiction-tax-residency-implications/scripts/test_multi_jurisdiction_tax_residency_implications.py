import unittest
from multi_jurisdiction_tax_residency_implications import MultiJurisdictionTaxResidencyImplicationsEngine, Record

class TestMultiJurisdictionTaxResidencyImplicationsEngine(unittest.TestCase):
    def test_initialization(self):
        engine = MultiJurisdictionTaxResidencyImplicationsEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = MultiJurisdictionTaxResidencyImplicationsEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = MultiJurisdictionTaxResidencyImplicationsEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
