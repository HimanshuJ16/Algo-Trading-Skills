import unittest
from oncall_escalation_manager import OncallEscalationManager, Config

class TestOncallEscalationManager(unittest.TestCase):
    def test_init(self):
        engine = OncallEscalationManager(Config("test"))
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        engine = OncallEscalationManager(Config("test"))
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
