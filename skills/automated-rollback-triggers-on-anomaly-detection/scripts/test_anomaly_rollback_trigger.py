import unittest
from anomaly_rollback_trigger import AnomalyRollbackTrigger, Config

class TestAnomalyRollbackTrigger(unittest.TestCase):
    def test_init(self):
        engine = AnomalyRollbackTrigger(Config("test"))
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        engine = AnomalyRollbackTrigger(Config("test"))
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
