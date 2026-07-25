import unittest
from single_name_concentration_limiter import SingleNameConcentrationLimiter

class TestSingleNameConcentrationLimiter(unittest.TestCase):
    def test_breach(self):
        limiter = SingleNameConcentrationLimiter(0.1)
        res = limiter.check_exposure(100000, 15000)
        self.assertTrue(res.is_breached)
        self.assertEqual(res.max_allowed, 10000.0)

    def test_ok(self):
        limiter = SingleNameConcentrationLimiter(0.1)
        res = limiter.check_exposure(100000, 5000)
        self.assertFalse(res.is_breached)
