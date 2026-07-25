import unittest
from exchange_self_match_prevention_configuration import Config, Engine

class TestEngine(unittest.TestCase):
    def test_process_true(self):
        engine = Engine(Config(threshold=1.0))
        self.assertTrue(engine.process(2.0))
        
    def test_process_false(self):
        engine = Engine(Config(threshold=1.0))
        self.assertFalse(engine.process(0.5))

if __name__ == '__main__':
    unittest.main()
