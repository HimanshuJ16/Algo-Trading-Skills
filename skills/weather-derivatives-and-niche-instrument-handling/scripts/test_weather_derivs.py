import unittest
from weather_derivs import ModelConfig, MainEngine

class TestMainEngine(unittest.TestCase):
    def test_init(self):
        config = ModelConfig("test")
        engine = MainEngine(config)
        self.assertEqual(engine.config.name, "test")
        
    def test_execute(self):
        config = ModelConfig("test")
        engine = MainEngine(config)
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
