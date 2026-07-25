import unittest
from capital_reallocation_based_on_live_performance import CapitalReallocationBasedOnLivePerformance, CapitalReallocationBasedOnLivePerformanceConfig

class TestCapitalReallocationBasedOnLivePerformance(unittest.TestCase):
    def test_execute_true(self):
        config = CapitalReallocationBasedOnLivePerformanceConfig(enabled=True)
        engine = CapitalReallocationBasedOnLivePerformance(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = CapitalReallocationBasedOnLivePerformanceConfig(enabled=False)
        engine = CapitalReallocationBasedOnLivePerformance(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
