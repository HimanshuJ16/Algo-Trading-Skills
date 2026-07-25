import unittest
from custodial_vs_non_custodial_tradeoff_assessment import Config, Engine

class TestEngine(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertEqual(engine.config.name, "custodial-vs-non-custodial-tradeoff-assessment")

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
