import unittest
from adverse_selection_measurement_for_passive_orders import Config, Engine

class TestAdverseSelectionMeasurementForPassiveOrders(unittest.TestCase):
    def test_init(self):
        config = Config()
        instance = Engine(config)
        self.assertTrue(instance.config.enabled)
        
    def test_execute(self):
        config = Config()
        instance = Engine(config)
        self.assertTrue(instance.execute())

if __name__ == '__main__':
    unittest.main()
