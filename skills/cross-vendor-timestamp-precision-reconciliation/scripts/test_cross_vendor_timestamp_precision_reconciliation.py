
import unittest
from cross_vendor_timestamp_precision_reconciliation import CrossVendorTimestampPrecisionReconciliationEngine, CrossVendorTimestampPrecisionReconciliationConfig

class TestCrossVendorTimestampPrecisionReconciliation(unittest.TestCase):
    def setUp(self):
        self.engine = CrossVendorTimestampPrecisionReconciliationEngine(CrossVendorTimestampPrecisionReconciliationConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = CrossVendorTimestampPrecisionReconciliationEngine(CrossVendorTimestampPrecisionReconciliationConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
