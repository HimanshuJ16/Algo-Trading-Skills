import unittest
from datetime import date
from s_1099_b_and_broker_tax_reporting_reconciliation import (
    S1099BAndBrokerTaxReportingReconciliationEngine, 
    TaxLot
)

class TestS1099BAndBrokerTaxReportingReconciliationEngine(unittest.TestCase):
    
    def setUp(self):
        self.engine = S1099BAndBrokerTaxReportingReconciliationEngine(tolerance_usd=0.05)
        
    def test_exact_match(self):
        lot_int = TaxLot("INT_1", "AAPL", 100, date(2023, 1, 15), date(2023, 2, 15), 15000.0, 14000.0)
        lot_brk = TaxLot("BRK_1", "AAPL", 100, date(2023, 1, 15), date(2023, 2, 15), 15000.0, 14000.0)
        
        self.engine.load_internal_lot(lot_int)
        self.engine.load_broker_lot(lot_brk)
        
        results = self.engine.process_reconciliation()
        self.assertEqual(results["matched"], 1)
        self.assertEqual(len(results["discrepancies"]), 0)

    def test_match_within_tolerance(self):
        # Broker cost basis differs by $0.03 due to fee rounding
        lot_int = TaxLot("INT_1", "TSLA", 50, date(2023, 3, 1), date(2023, 3, 10), 10000.0, 9000.0)
        lot_brk = TaxLot("BRK_1", "TSLA", 50, date(2023, 3, 1), date(2023, 3, 10), 10000.0, 9000.03)
        
        self.engine.load_internal_lot(lot_int)
        self.engine.load_broker_lot(lot_brk)
        
        results = self.engine.process_reconciliation()
        self.assertEqual(results["matched"], 1)

    def test_unmatched_missing_broker_record(self):
        lot_int = TaxLot("INT_1", "MSFT", 10, date(2023, 4, 1), date(2023, 4, 5), 3000.0, 2500.0)
        self.engine.load_internal_lot(lot_int)
        
        results = self.engine.process_reconciliation()
        self.assertEqual(results["matched"], 0)
        self.assertEqual(len(results["discrepancies"]), 1)
        self.assertEqual(results["discrepancies"][0].reason, "Missing in 1099-B")

if __name__ == '__main__':
    unittest.main()
