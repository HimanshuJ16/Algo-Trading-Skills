import unittest
from smart_contract_audit_requirements_before_defi_integration import Config, Engine

class TestEngine(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertEqual(engine.config.name, "smart-contract-audit-requirements-before-defi-integration")

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
