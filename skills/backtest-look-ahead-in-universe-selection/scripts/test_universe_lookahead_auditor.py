import unittest
from datetime import datetime, timedelta
from universe_lookahead_auditor import UniverseSelectionRule, UniverseLookaheadAuditor

class TestUniverseLookaheadAuditor(unittest.TestCase):
    def test_no_lookahead_bias(self):
        rule = UniverseSelectionRule("Top 50 MktCap", 30, "Fundamental Data")
        auditor = UniverseLookaheadAuditor([rule])
        snapshot = datetime(2023, 1, 1)
        data_up_to = datetime(2023, 1, 1)
        results = auditor.audit(snapshot, data_up_to)
        
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].has_lookahead_bias)

    def test_lookahead_bias_detected(self):
        rule = UniverseSelectionRule("Future Earnings", 0, "Forward Estimates")
        auditor = UniverseLookaheadAuditor([rule])
        snapshot = datetime(2023, 1, 1)
        # Data available from the future
        data_up_to = datetime(2023, 1, 10) 
        results = auditor.audit(snapshot, data_up_to)
        
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].has_lookahead_bias)
        self.assertIn("Lookahead Bias Detected", results[0].description)

if __name__ == '__main__':
    unittest.main()
