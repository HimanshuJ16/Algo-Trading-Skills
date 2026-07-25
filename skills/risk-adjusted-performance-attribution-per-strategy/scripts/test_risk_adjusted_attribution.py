import unittest
from risk_adjusted_attribution import Config, MainEngine

class TestEngine(unittest.TestCase):
    def test_default(self):
        engine = MainEngine(Config())
        self.assertEqual(engine.process(2.0), 2.0)
        
    def test_custom(self):
        engine = MainEngine(Config(param=3.0))
        self.assertEqual(engine.process(2.0), 6.0)

if __name__ == '__main__':
    unittest.main()
