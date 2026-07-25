import unittest
from region_dr_failover_executor import Config, MainEngine

class TestRegionDrFailoverExecutor(unittest.TestCase):
    def test_init(self):
        engine = MainEngine(Config(name="test"))
        self.assertIsNotNone(engine)

    def test_run(self):
        engine = MainEngine(Config(name="test"))
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
