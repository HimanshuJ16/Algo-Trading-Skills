import unittest
from s_1099_b_and_broker_tax_reporting_reconciliation import S1099BAndBrokerTaxReportingReconciliationEngine, Record

class TestS1099BAndBrokerTaxReportingReconciliationEngine(unittest.TestCase):
    def test_initialization(self):
        engine = S1099BAndBrokerTaxReportingReconciliationEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = S1099BAndBrokerTaxReportingReconciliationEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = S1099BAndBrokerTaxReportingReconciliationEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
