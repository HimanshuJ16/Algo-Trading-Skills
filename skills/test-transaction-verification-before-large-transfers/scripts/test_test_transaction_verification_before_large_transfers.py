import unittest
from test_transaction_verification_before_large_transfers import Analyzer

class TestAnalyzer(unittest.TestCase):
    def test_success(self):
        analyzer = Analyzer({"key": "value"})
        res = analyzer.execute()
        self.assertTrue(res.success)
        
    def test_failure(self):
        analyzer = Analyzer({})
        res = analyzer.execute()
        self.assertFalse(res.success)

if __name__ == '__main__':
    unittest.main()
