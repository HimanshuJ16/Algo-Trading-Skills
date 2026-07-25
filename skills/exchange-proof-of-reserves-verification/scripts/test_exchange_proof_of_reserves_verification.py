import unittest
from exchange_proof_of_reserves_verification import Config, Engine

class TestEngine(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertEqual(engine.config.name, "exchange-proof-of-reserves-verification")

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
