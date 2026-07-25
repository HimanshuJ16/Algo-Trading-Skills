import unittest
from environment_parity_auditor import EnvironmentParityAuditor, Config

class TestEnvironmentParityAuditor(unittest.TestCase):
    def test_init(self):
        engine = EnvironmentParityAuditor(Config("test"))
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        engine = EnvironmentParityAuditor(Config("test"))
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
