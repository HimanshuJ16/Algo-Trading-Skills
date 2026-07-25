import unittest
from zero_downtime_migration_engine import ZeroDowntimeMigrationEngine, Config

class TestZeroDowntimeMigrationEngine(unittest.TestCase):
    def test_init(self):
        engine = ZeroDowntimeMigrationEngine(Config("test"))
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        engine = ZeroDowntimeMigrationEngine(Config("test"))
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
