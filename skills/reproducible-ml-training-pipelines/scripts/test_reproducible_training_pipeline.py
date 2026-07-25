import unittest
from reproducible_training_pipeline import Config, ReproducibleTrainingPipeline

class TestReproducibleTrainingPipeline(unittest.TestCase):
    def test_init(self):
        cfg = Config()
        obj = ReproducibleTrainingPipeline(cfg)
        self.assertEqual(obj.config.name, "reproducible-ml-training-pipelines")
        
    def test_process(self):
        cfg = Config()
        obj = ReproducibleTrainingPipeline(cfg)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
