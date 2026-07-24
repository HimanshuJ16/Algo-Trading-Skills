"""
Unit tests for offline-train-online-infer-deployment skill.

Tests:
1. Model artifact serialization and SHA-256 hash generation.
2. Feature schema mismatch detection and error raising (SchemaMismatchError).
3. Live prediction with feature scaling.
4. Train/Serve parity verification.
5. Backward compatibility with export_model_artifact and load_and_validate.
"""
import os
import shutil
import tempfile
import unittest
from model_export import (
    ModelArtifactManager,
    SchemaMismatchError,
    export_model_artifact,
    load_and_validate,
)


class TestOfflineTrainOnlineInferDeployment(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.artifact_path = os.path.join(self.temp_dir, "model_v1.json")
        self.feature_names = ["momentum", "volatility", "volume_z"]
        self.preprocessing = {
            "momentum": {"mean": 0.0, "std": 1.0},
            "volatility": {"mean": 0.02, "std": 0.005},
            "volume_z": {"mean": 100.0, "std": 20.0},
        }
        self.weights = [0.5, -1.2, 0.3]
        self.intercept = -0.1

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_export_and_load_artifact(self):
        content_hash = ModelArtifactManager.export_artifact(
            model_id="signal_clf_v1",
            weights=self.weights,
            intercept=self.intercept,
            preprocessing_params=self.preprocessing,
            feature_names=self.feature_names,
            export_path=self.artifact_path,
        )

        self.assertEqual(len(content_hash), 16)
        self.assertTrue(os.path.exists(self.artifact_path))

        artifact = ModelArtifactManager.load_and_validate(
            self.artifact_path, live_feature_names=self.feature_names
        )
        self.assertEqual(artifact.model_id, "signal_clf_v1")
        self.assertEqual(artifact.feature_schema, self.feature_names)

    def test_schema_mismatch_raises_error(self):
        ModelArtifactManager.export_artifact(
            model_id="signal_clf_v1",
            weights=self.weights,
            intercept=self.intercept,
            preprocessing_params=self.preprocessing,
            feature_names=self.feature_names,
            export_path=self.artifact_path,
        )

        # Mismatched live schema order or names
        wrong_schema = ["volatility", "momentum", "volume_z"]
        with self.assertRaises(SchemaMismatchError):
            ModelArtifactManager.load_and_validate(self.artifact_path, live_feature_names=wrong_schema)

    def test_live_prediction_and_parity(self):
        ModelArtifactManager.export_artifact(
            model_id="signal_clf_v1",
            weights=self.weights,
            intercept=self.intercept,
            preprocessing_params=self.preprocessing,
            feature_names=self.feature_names,
            export_path=self.artifact_path,
        )

        artifact = ModelArtifactManager.load_and_validate(
            self.artifact_path, live_feature_names=self.feature_names
        )

        live_feats = {"momentum": 0.5, "volatility": 0.025, "volume_z": 120.0}
        prob = ModelArtifactManager.predict_live(artifact, live_feats)
        self.assertTrue(0.0 <= prob <= 1.0)

        # Train/Serve Parity Test
        self.assertTrue(
            ModelArtifactManager.verify_train_serve_parity([prob, 0.8], [prob, 0.8])
        )

    def test_backward_compatibility(self):
        h = export_model_artifact(self.weights, self.preprocessing, self.feature_names, self.artifact_path)
        self.assertEqual(len(h), 16)

        art = load_and_validate(self.artifact_path, self.feature_names)
        self.assertEqual(art["feature_schema"], self.feature_names)


if __name__ == "__main__":
    unittest.main()
