import unittest
from reproducible_training_pipeline import (
    Config, ReproducibleTrainingPipeline,
    ReproducibleMLTrainingPipelineEngine, MLPipelineSpec, MLReproducibilityManifest
)

class TestReproducibleTrainingPipeline(unittest.TestCase):

    def test_legacy_init(self):
        cfg = Config()
        obj = ReproducibleTrainingPipeline(cfg)
        self.assertEqual(obj.config.name, "reproducible-ml-training-pipelines")

    def test_legacy_process(self):
        cfg = Config()
        obj = ReproducibleTrainingPipeline(cfg)
        self.assertTrue(obj.process())

class TestReproducibleMLTrainingPipelineEngine(unittest.TestCase):

    def setUp(self):
        self.spec = MLPipelineSpec(
            experiment_id="EXP_ALPHA_01",
            seed=12345,
            hyperparameters={"learning_rate": 0.01, "batch_size": 32}
        )
        self.engine = ReproducibleMLTrainingPipelineEngine(self.spec)

    def test_identical_runs_produce_identical_weights_and_manifest(self):
        data = [10.5, 12.0, 11.2, 14.8, 13.5]

        # Run 1
        manifest1 = self.engine.train_model(data)

        # Run 2 with separate engine instance but identical seed & spec
        engine2 = ReproducibleMLTrainingPipelineEngine(self.spec)
        manifest2 = engine2.train_model(data)

        self.assertEqual(manifest1.data_hash, manifest2.data_hash)
        self.assertEqual(manifest1.hyperparameters_hash, manifest2.hyperparameters_hash)
        self.assertEqual(manifest1.model_weights_hash, manifest2.model_weights_hash)
        self.assertEqual(manifest1.manifest_signature, manifest2.manifest_signature)
        self.assertTrue(manifest1.is_reproducible)

    def test_different_data_produces_different_hash(self):
        data1 = [10.5, 12.0, 11.2]
        data2 = [10.5, 12.0, 99.9]

        manifest1 = self.engine.train_model(data1)
        manifest2 = self.engine.train_model(data2)

        self.assertNotEqual(manifest1.data_hash, manifest2.data_hash)
        self.assertNotEqual(manifest1.model_weights_hash, manifest2.model_weights_hash)

if __name__ == '__main__':
    unittest.main()
