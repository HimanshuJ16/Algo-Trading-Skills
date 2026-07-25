import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from strategy_research_to_production_pipeline_governance import Config, Engine

class TestEngine(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")
        
    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
