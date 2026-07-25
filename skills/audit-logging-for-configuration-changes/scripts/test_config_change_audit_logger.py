import unittest
from config_change_audit_logger import ConfigChangeAuditLogger, Config

class TestConfigChangeAuditLogger(unittest.TestCase):
    def test_init(self):
        engine = ConfigChangeAuditLogger(Config("test"))
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        engine = ConfigChangeAuditLogger(Config("test"))
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
