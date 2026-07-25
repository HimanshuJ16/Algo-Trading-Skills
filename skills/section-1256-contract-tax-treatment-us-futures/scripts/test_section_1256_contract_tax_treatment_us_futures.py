import unittest
from section_1256_contract_tax_treatment_us_futures import Section1256ContractTaxTreatmentUsFuturesEngine, Record

class TestSection1256ContractTaxTreatmentUsFuturesEngine(unittest.TestCase):
    def test_initialization(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
