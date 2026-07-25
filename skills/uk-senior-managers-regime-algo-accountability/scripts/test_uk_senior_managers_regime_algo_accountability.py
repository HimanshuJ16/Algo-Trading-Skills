import unittest
from uk_senior_managers_regime_algo_accountability import ComplianceChecker

class TestCompliance(unittest.TestCase):
    def setUp(self):
        self.checker = ComplianceChecker()

    def test_single_check(self):
        res = self.checker.check_compliance("T1")
        self.assertTrue(res.is_compliant)

    def test_batch_check(self):
        res = self.checker.batch_check(["T1", "T2"])
        self.assertEqual(len(res), 2)
        self.assertTrue(all(r.is_compliant for r in res))

if __name__ == '__main__':
    unittest.main()
