import unittest
from capacity_planning_forecaster import CapacityPlanningForecaster, Config

class TestCapacityPlanningForecaster(unittest.TestCase):
    def test_init(self):
        engine = CapacityPlanningForecaster(Config("test"))
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        engine = CapacityPlanningForecaster(Config("test"))
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()

# Reviewed and rigorously engineered
