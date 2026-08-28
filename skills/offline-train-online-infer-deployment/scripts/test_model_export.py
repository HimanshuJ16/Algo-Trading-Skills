"""
Behavioural tests for the offline-train-online-infer-deployment artifact exporter
and live inference path.

Most tests here are regressions against specific, measured defects in the 1.x
implementation. Each such test is marked REGRESSION and fails against the old
behaviour:

* the "content hash" covered ``time.time()``, so two exports of an identical model
  produced different digests;
* ``load_and_validate`` carried the recorded digest but never verified it, so an
  artifact edited after export loaded silently;
* a live feature absent from the observation defaulted to raw ``0.0`` (0.072 instead
  of the correct 0.321 on this fixture);
* a feature absent from the exported scaler was served unscaled (0.99999999 instead
  of 0.321);
* a weights list shorter than the feature schema was zero-padded and served;
* a NaN feature produced probability ``1.0`` -- a maximum-confidence long signal --
  because ``min(50.0, nan)`` returns ``50.0``;
* ``verify_train_serve_parity`` reported two all-NaN sequences as verified, and
  reported two empty sequences as verified.

Expected probabilities are derived independently of the implementation: the fixture
is chosen so every standardised feature is exactly 0.5 or 1.0, giving the closed-form
score  z = -0.1 + (0.5)(0.5) + (-1.2)(1.0) + (0.3)(1.0) = -0.75  and probability
1 / (1 + e^0.75) = 0.3208213008246070 (30-digit Decimal evaluation).
"""
import json
import math
import os
import shutil
import tempfile
import unittest
from unittest import mock

from model_export import (
    ArtifactIntegrityError,
    ArtifactValidationError,
    FeatureValidationError,
    ModelArtifact,
    ModelArtifactManager,
    SchemaMismatchError,
    export_model_artifact,
    load_and_validate,
)

# Independently derived: 1 / (1 + exp(0.75)).
EXPECTED_PROBABILITY = 0.3208213008246070


class ArtifactTestBase(unittest.TestCase):
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
        # Standardises to exactly (0.5, 1.0, 1.0).
        self.live_features = {"momentum": 0.5, "volatility": 0.025, "volume_z": 120.0}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def export(self, path=None, **overrides):
        kwargs = dict(
            model_id="signal_clf_v1",
            weights=self.weights,
            intercept=self.intercept,
            preprocessing_params=self.preprocessing,
            feature_names=self.feature_names,
            export_path=path or self.artifact_path,
        )
        kwargs.update(overrides)
        return ModelArtifactManager.export_artifact(**kwargs)

    def read_payload(self, path=None):
        with open(path or self.artifact_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def write_payload(self, payload, path=None):
        with open(path or self.artifact_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)


class TestExportAndIntegrity(ArtifactTestBase):

    def test_export_returns_full_sha256_and_round_trips(self):
        content_hash = self.export()
        self.assertEqual(len(content_hash), 64)
        self.assertRegex(content_hash, r"^[0-9a-f]{64}$")
        self.assertTrue(os.path.exists(self.artifact_path))

        artifact = ModelArtifactManager.load_and_validate(
            self.artifact_path, live_feature_names=self.feature_names
        )
        self.assertEqual(artifact.model_id, "signal_clf_v1")
        self.assertEqual(artifact.feature_schema, self.feature_names)
        self.assertEqual(artifact.content_hash, content_hash)
        self.assertEqual(artifact.link, "logistic")

    def test_digest_is_independent_of_export_timestamp(self):
        """REGRESSION: the 1.x digest covered time.time(), so re-exporting an
        identical model produced a different 'content hash' every call.

        The clock is pinned rather than relying on two real exports landing in
        different timer ticks -- on a platform with a coarse clock the unpinned
        version of this test passes against the old defect by luck.
        """
        path_a = os.path.join(self.temp_dir, "a.json")
        path_b = os.path.join(self.temp_dir, "b.json")
        with mock.patch("model_export.time.time", return_value=1_000_000.0):
            first = self.export(path=path_a)
        with mock.patch("model_export.time.time", return_value=2_000_000.0):
            second = self.export(path=path_b)

        self.assertNotEqual(
            self.read_payload(path_a)["exported_at"], self.read_payload(path_b)["exported_at"]
        )
        self.assertEqual(first, second)

    def test_digest_changes_when_any_model_field_changes(self):
        baseline = self.export(path=os.path.join(self.temp_dir, "a.json"))
        changed = self.export(
            path=os.path.join(self.temp_dir, "b.json"), weights=[0.5, -1.2, 0.30000001]
        )
        self.assertNotEqual(baseline, changed)

    def test_export_timestamp_is_recorded_but_outside_the_digest(self):
        digest = self.export()
        payload = self.read_payload()
        self.assertGreater(payload["exported_at"], 0.0)
        # Rewriting only the timestamp must not invalidate the artifact, since it
        # says nothing about which model the file contains.
        payload["exported_at"] = payload["exported_at"] + 1000.0
        self.write_payload(payload)
        artifact = ModelArtifactManager.load_and_validate(self.artifact_path, self.feature_names)
        self.assertEqual(artifact.content_hash, digest)
        self.assertEqual(artifact.exported_at, payload["exported_at"])

    def test_tampered_weights_are_rejected_at_load(self):
        """REGRESSION: 1.x read content_hash from the file and never verified it, so
        an artifact whose weights had been rewritten loaded and served silently."""
        self.export()
        payload = self.read_payload()
        payload["weights"] = [99.0, 99.0, 99.0]
        self.write_payload(payload)
        with self.assertRaises(ArtifactIntegrityError):
            ModelArtifactManager.load_and_validate(self.artifact_path, self.feature_names)

    def test_truncated_artifact_is_rejected_at_load(self):
        self.export()
        with open(self.artifact_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(self.artifact_path, "w", encoding="utf-8") as handle:
            handle.write(text[: len(text) // 2])
        with self.assertRaises(ArtifactIntegrityError):
            ModelArtifactManager.load_and_validate(self.artifact_path, self.feature_names)

    def test_artifact_without_content_hash_is_rejected(self):
        self.export()
        payload = self.read_payload()
        del payload["content_hash"]
        self.write_payload(payload)
        with self.assertRaises(ArtifactIntegrityError):
            ModelArtifactManager.load_and_validate(self.artifact_path, self.feature_names)

    def test_nan_literal_in_artifact_is_rejected(self):
        """A NaN weight from a diverged training run round-trips through json by
        default; the loader must refuse the non-standard literal."""
        self.export()
        payload = self.read_payload()
        payload["weights"] = [float("nan"), -1.2, 0.3]
        with open(self.artifact_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)  # emits a bare NaN literal
        self.assertIn("NaN", open(self.artifact_path, encoding="utf-8").read())
        with self.assertRaises(ArtifactIntegrityError):
            ModelArtifactManager.load_and_validate(self.artifact_path, self.feature_names)

    def test_failed_export_leaves_previous_artifact_intact(self):
        good_hash = self.export()
        with self.assertRaises(ArtifactValidationError):
            self.export(weights=[0.5, float("inf"), 0.3])
        artifact = ModelArtifactManager.load_and_validate(self.artifact_path, self.feature_names)
        self.assertEqual(artifact.content_hash, good_hash)
        self.assertEqual(artifact.weights, self.weights)

    def test_export_leaves_no_temporary_files_behind(self):
        self.export()
        self.assertEqual(os.listdir(self.temp_dir), ["model_v1.json"])


class TestExportValidation(ArtifactTestBase):

    def test_weight_count_must_match_feature_count(self):
        """REGRESSION: 1.x zero-padded a short weights list and served the model."""
        with self.assertRaises(ArtifactValidationError):
            self.export(weights=[0.5])

    def test_every_feature_must_have_scaling_parameters(self):
        """REGRESSION: 1.x fell back to identity scaling for an unexported feature."""
        incomplete = {k: v for k, v in self.preprocessing.items() if k != "volume_z"}
        with self.assertRaises(ArtifactValidationError):
            self.export(preprocessing_params=incomplete)

    def test_zero_std_is_rejected_with_sklearn_guidance(self):
        degenerate = dict(self.preprocessing)
        degenerate["momentum"] = {"mean": 0.0, "std": 0.0}
        with self.assertRaises(ArtifactValidationError) as ctx:
            self.export(preprocessing_params=degenerate)
        self.assertIn("scale_", str(ctx.exception))

    def test_negative_std_is_rejected(self):
        degenerate = dict(self.preprocessing)
        degenerate["momentum"] = {"mean": 0.0, "std": -1.0}
        with self.assertRaises(ArtifactValidationError):
            self.export(preprocessing_params=degenerate)

    def test_non_finite_parameters_are_rejected(self):
        for bad_weights in ([float("nan"), -1.2, 0.3], [float("inf"), -1.2, 0.3]):
            with self.subTest(weights=bad_weights):
                with self.assertRaises(ArtifactValidationError):
                    self.export(weights=bad_weights)
        with self.assertRaises(ArtifactValidationError):
            self.export(intercept=float("nan"))

    def test_duplicate_feature_names_are_rejected(self):
        with self.assertRaises(ArtifactValidationError):
            self.export(feature_names=["momentum", "momentum", "volume_z"])

    def test_empty_schema_is_rejected(self):
        with self.assertRaises(ArtifactValidationError):
            self.export(feature_names=[], weights=[], preprocessing_params={})

    def test_unsupported_link_is_rejected(self):
        with self.assertRaises(ArtifactValidationError):
            self.export(link="softmax")

    def test_blank_model_id_is_rejected(self):
        with self.assertRaises(ArtifactValidationError):
            self.export(model_id="   ")


class TestSchemaValidation(ArtifactTestBase):

    def test_reordered_live_schema_raises(self):
        self.export()
        with self.assertRaises(SchemaMismatchError):
            ModelArtifactManager.load_and_validate(
                self.artifact_path, live_feature_names=["volatility", "momentum", "volume_z"]
            )

    def test_renamed_live_feature_raises(self):
        self.export()
        with self.assertRaises(SchemaMismatchError):
            ModelArtifactManager.load_and_validate(
                self.artifact_path, live_feature_names=["momentum", "volatility", "volume_zscore"]
            )

    def test_extra_live_feature_raises(self):
        self.export()
        with self.assertRaises(SchemaMismatchError):
            ModelArtifactManager.load_and_validate(
                self.artifact_path, live_feature_names=self.feature_names + ["spread"]
            )

    def test_schema_errors_remain_catchable_as_value_error(self):
        self.export()
        with self.assertRaises(ValueError):
            ModelArtifactManager.load_and_validate(self.artifact_path, ["momentum"])


class TestLiveInference(ArtifactTestBase):

    def load(self, **overrides):
        self.export(**overrides)
        return ModelArtifactManager.load_and_validate(self.artifact_path, self.feature_names)

    def test_logistic_prediction_matches_independent_closed_form(self):
        artifact = self.load()
        probability = ModelArtifactManager.predict_live(artifact, self.live_features)
        self.assertAlmostEqual(probability, EXPECTED_PROBABILITY, places=12)

    def test_identity_link_returns_the_raw_score(self):
        artifact = self.load(link="identity")
        score = ModelArtifactManager.predict_live(artifact, self.live_features)
        self.assertAlmostEqual(score, -0.75, places=12)

    def test_extra_live_keys_are_ignored(self):
        artifact = self.load()
        enriched = dict(self.live_features, unused_feature=42.0)
        self.assertAlmostEqual(
            ModelArtifactManager.predict_live(artifact, enriched),
            EXPECTED_PROBABILITY,
            places=12,
        )

    def test_missing_live_feature_raises_instead_of_defaulting(self):
        """REGRESSION: 1.x defaulted a missing feature to raw 0.0, which standardised
        to -5.0 here and returned 0.072 instead of 0.321."""
        artifact = self.load()
        partial = {k: v for k, v in self.live_features.items() if k != "volume_z"}
        with self.assertRaises(SchemaMismatchError):
            ModelArtifactManager.predict_live(artifact, partial)

    def test_nan_feature_raises_instead_of_returning_certainty(self):
        """REGRESSION: 1.x clamped with max(-50, min(50, nan)) -> 50.0 -> probability
        1.0, turning missing data into a maximum-confidence long signal."""
        artifact = self.load()
        for bad_value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad_value):
                observation = dict(self.live_features, momentum=bad_value)
                with self.assertRaises(FeatureValidationError):
                    ModelArtifactManager.predict_live(artifact, observation)

    def test_non_numeric_feature_raises(self):
        artifact = self.load()
        for bad_value in ("0.5", None, True):
            with self.subTest(value=bad_value):
                observation = dict(self.live_features, momentum=bad_value)
                with self.assertRaises(FeatureValidationError):
                    ModelArtifactManager.predict_live(artifact, observation)

    def test_extreme_scores_saturate_without_raising(self):
        """The stable sigmoid must not raise OverflowError and must stay in [0, 1]."""
        artifact = self.load()
        for momentum, expected in ((1e6, 1.0), (-1e6, 0.0)):
            with self.subTest(momentum=momentum):
                observation = dict(self.live_features, momentum=momentum)
                probability = ModelArtifactManager.predict_live(artifact, observation)
                self.assertEqual(probability, expected)

    def test_moderate_scores_are_not_clamped(self):
        """A score of -60 was previously clamped to -50, distorting the probability by
        four orders of magnitude."""
        artifact = self.load(weights=[-120.0, 0.0, 0.0], intercept=0.0)
        # standardised momentum = 0.5 -> z = -60 exactly.
        probability = ModelArtifactManager.predict_live(artifact, self.live_features)
        self.assertAlmostEqual(probability / math.exp(-60.0), 1.0, places=9)

    def test_hand_built_artifact_raises_a_typed_error(self):
        """ModelArtifact is a public dataclass, so an agent can construct one
        directly and bypass load-time validation. That must still surface as a
        documented ArtifactValidationError, not an untyped KeyError/IndexError a
        caller following the documented contract would fail to catch."""
        common = dict(
            model_id="hand_built",
            version="1.0.0",
            content_hash="unverified",
            exported_at=0.0,
            feature_schema=["a", "b"],
            intercept=0.0,
        )
        short_weights = ModelArtifact(
            preprocessing_params={n: {"mean": 0.0, "std": 1.0} for n in ("a", "b")},
            weights=[1.0],
            **common,
        )
        missing_scaler = ModelArtifact(
            preprocessing_params={"a": {"mean": 0.0, "std": 1.0}},
            weights=[1.0, 1.0],
            **common,
        )
        for artifact in (short_weights, missing_scaler):
            with self.subTest(artifact=artifact.model_id):
                with self.assertRaises(ArtifactValidationError):
                    ModelArtifactManager.predict_live(artifact, {"a": 1.0, "b": 2.0})

    def test_scaling_is_actually_applied(self):
        """Guards against an inference path that ignores the exported scaler: the
        unscaled score for this fixture would be -0.6205, not -0.75."""
        artifact = self.load(link="identity")
        score = ModelArtifactManager.predict_live(artifact, self.live_features)
        unscaled = (
            self.intercept
            + 0.5 * 0.5
            + (-1.2) * 0.025
            + 0.3 * 120.0
        )
        self.assertNotAlmostEqual(score, unscaled, places=6)
        self.assertAlmostEqual(score, -0.75, places=12)


class TestParityGate(ArtifactTestBase):

    def test_matching_predictions_verify(self):
        self.assertTrue(
            ModelArtifactManager.verify_train_serve_parity([0.32, 0.80], [0.32, 0.80])
        )

    def test_difference_within_tolerance_verifies(self):
        self.assertTrue(
            ModelArtifactManager.verify_train_serve_parity([0.32], [0.32 + 9e-7], tolerance=1e-6)
        )

    def test_difference_at_tolerance_boundary_verifies(self):
        self.assertTrue(
            ModelArtifactManager.verify_train_serve_parity([1.0], [1.0 + 1e-6], tolerance=1e-6)
        )

    def test_difference_beyond_tolerance_fails(self):
        self.assertFalse(
            ModelArtifactManager.verify_train_serve_parity([0.32], [0.33], tolerance=1e-6)
        )

    def test_length_mismatch_fails(self):
        self.assertFalse(ModelArtifactManager.verify_train_serve_parity([0.3, 0.4], [0.3]))

    def test_nan_predictions_do_not_verify(self):
        """REGRESSION: abs(nan - nan) > tol is False, so 1.x reported two all-NaN
        prediction sets as parity-verified."""
        nan = float("nan")
        self.assertFalse(ModelArtifactManager.verify_train_serve_parity([nan], [nan]))
        self.assertFalse(ModelArtifactManager.verify_train_serve_parity([0.3], [nan]))

    def test_empty_comparison_does_not_verify(self):
        """REGRESSION: 1.x returned True for two empty lists -- a promotion gate that
        passes having compared nothing."""
        self.assertFalse(ModelArtifactManager.verify_train_serve_parity([], []))

    def test_negative_tolerance_is_rejected(self):
        with self.assertRaises(ValueError):
            ModelArtifactManager.verify_train_serve_parity([0.3], [0.3], tolerance=-1e-6)

    def test_end_to_end_offline_online_parity(self):
        """The offline pipeline's own scores, recomputed from the artifact, must match
        the live serving path within tolerance."""
        self.export()
        artifact = ModelArtifactManager.load_and_validate(self.artifact_path, self.feature_names)
        observations = [
            self.live_features,
            {"momentum": -0.25, "volatility": 0.015, "volume_z": 90.0},
            {"momentum": 1.75, "volatility": 0.030, "volume_z": 140.0},
        ]
        offline = []
        for row in observations:
            z = self.intercept + sum(
                self.weights[i]
                * (row[name] - self.preprocessing[name]["mean"])
                / self.preprocessing[name]["std"]
                for i, name in enumerate(self.feature_names)
            )
            offline.append(1.0 / (1.0 + math.exp(-z)))
        online = [ModelArtifactManager.predict_live(artifact, row) for row in observations]
        self.assertTrue(ModelArtifactManager.verify_train_serve_parity(offline, online))


class TestCompatibilityApi(ArtifactTestBase):

    def test_export_model_artifact_round_trips(self):
        digest = export_model_artifact(
            self.weights, self.preprocessing, self.feature_names, self.artifact_path
        )
        self.assertEqual(len(digest), 64)
        payload = load_and_validate(self.artifact_path, self.feature_names)
        self.assertEqual(payload["feature_schema"], self.feature_names)
        self.assertEqual(payload["intercept"], 0.0)
        self.assertEqual(payload["content_hash"], digest)

    def test_compat_loader_enforces_the_same_integrity_check(self):
        export_model_artifact(
            self.weights, self.preprocessing, self.feature_names, self.artifact_path
        )
        payload = self.read_payload()
        payload["intercept"] = 5.0
        self.write_payload(payload)
        with self.assertRaises(ArtifactIntegrityError):
            load_and_validate(self.artifact_path, self.feature_names)

    def test_compat_loader_schema_mismatch_is_still_a_value_error(self):
        export_model_artifact(
            self.weights, self.preprocessing, self.feature_names, self.artifact_path
        )
        with self.assertRaises(ValueError):
            load_and_validate(self.artifact_path, ["momentum", "volume_z", "volatility"])


if __name__ == "__main__":
    unittest.main()
