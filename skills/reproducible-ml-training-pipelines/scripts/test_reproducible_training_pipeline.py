"""Behavioural tests for the reproducible ML training pipeline harness.

Several tests are marked REGRESSION: they fail against an earlier
implementation, which hard-coded ``is_reproducible=True``, excluded
``git_commit_hash`` from the manifest tag, rounded weights to 6 decimals before
hashing, and had no code path capable of producing ``DATA_HASH_MISMATCH``.
"""
import hashlib
import itertools
import logging
import random
import unittest
from dataclasses import replace

from reproducible_training_pipeline import (
    ComparisonStatus,
    EnvironmentFingerprint,
    HAS_NUMPY,
    MAX_SEED,
    MLPipelineSpec,
    ManifestStatus,
    ReproducibilityError,
    ReproducibleMLTrainingPipelineEngine,
    canonical_bytes,
    digest_stream,
    seeded_rng_scope,
    sha256_hex,
)

if HAS_NUMPY:
    import numpy as np

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
COMMIT_SHA256 = "c" * 64
KEY = b"0123456789abcdef-institutional"

# Scoped to this module's logger so discovery of other skills' suites is
# unaffected; logging.disable() is process-wide and would leak.
logging.getLogger("reproducible_training_pipeline").setLevel(logging.CRITICAL)


def seeded_trainer(dataset, hyperparameters):
    """A stochastic but seed-deterministic 'model': initialise, then fit."""
    weights = [random.random() for _ in range(3)]
    scale = float(hyperparameters.get("learning_rate", 0.01))
    return [w + scale * value for w, value in zip(weights, dataset)]


def constant_trainer(dataset, hyperparameters):
    """Closed-form: no RNG dependence at all."""
    return [sum(dataset) / len(dataset)]


def _counter_trainer():
    """Non-deterministic: a different artifact on every call."""
    counter = itertools.count()
    return lambda dataset, hyperparameters: [float(next(counter))]


def make_spec(**overrides):
    base = dict(
        experiment_id="EXP_ALPHA_01",
        git_commit_hash=COMMIT_A,
        seed=12345,
        hyperparameters={"learning_rate": 0.01, "batch_size": 32},
        model_architecture="LinearRegression",
    )
    base.update(overrides)
    return MLPipelineSpec(**base)


class TestCanonicalBytes(unittest.TestCase):

    def test_length_prefix_prevents_concatenation_collision(self):
        self.assertNotEqual(canonical_bytes(["a", "b"]), canonical_bytes(["ab"]))

    def test_bool_is_distinguished_from_int(self):
        # bool subclasses int; without the ordered check True would encode as 1.
        self.assertNotEqual(canonical_bytes(True), canonical_bytes(1))
        self.assertNotEqual(canonical_bytes({"x": False}), canonical_bytes({"x": 0}))

    def test_signed_zero_is_distinguished(self):
        self.assertNotEqual(canonical_bytes(0.0), canonical_bytes(-0.0))

    def test_int_and_float_of_equal_value_are_distinguished(self):
        self.assertNotEqual(canonical_bytes(1), canonical_bytes(1.0))

    def test_last_ulp_difference_survives_encoding(self):
        # REGRESSION: the previous implementation rounded to 6 decimals, which
        # collapsed exactly this pair.
        drifted = sum([0.1] * 10)
        self.assertNotEqual(drifted, 1.0)
        self.assertNotEqual(canonical_bytes([drifted]), canonical_bytes([1.0]))

    def test_dict_key_order_does_not_matter(self):
        self.assertEqual(
            canonical_bytes({"a": 1, "b": 2}), canonical_bytes({"b": 2, "a": 1})
        )

    def test_list_and_tuple_collapse_is_documented_behaviour(self):
        self.assertEqual(canonical_bytes([1, 2]), canonical_bytes((1, 2)))

    def test_nan_and_inf_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ReproducibilityError):
                canonical_bytes([bad])

    def test_unsupported_type_names_the_path(self):
        with self.assertRaises(ReproducibilityError) as ctx:
            canonical_bytes({"outer": [1, {2j}]}, "hyperparameters")
        self.assertIn("hyperparameters.outer[1]", str(ctx.exception))

    def test_non_string_mapping_key_is_rejected(self):
        with self.assertRaises(ReproducibilityError):
            canonical_bytes({1: "a", "b": "c"})

    def test_self_referential_structure_is_rejected(self):
        loop = []
        loop.append(loop)
        with self.assertRaises(ReproducibilityError):
            canonical_bytes(loop)

    def test_bytes_round_trip(self):
        self.assertEqual(canonical_bytes(b"ab"), b"y:2:ab;")


class TestDigestHelpers(unittest.TestCase):

    def test_sha256_hex_matches_hashlib(self):
        self.assertEqual(sha256_hex(b"abc"), hashlib.sha256(b"abc").hexdigest())

    def test_sha256_hex_rejects_str(self):
        with self.assertRaises(ReproducibilityError):
            sha256_hex("abc")

    def test_digest_stream_equals_digest_of_concatenation(self):
        chunks = [b"abc", b"def", b"g"]
        self.assertEqual(
            digest_stream(chunks), hashlib.sha256(b"abcdefg").hexdigest()
        )

    def test_digest_stream_rejects_empty_stream(self):
        with self.assertRaises(ReproducibilityError):
            digest_stream([])

    def test_digest_stream_rejects_non_bytes_chunk(self):
        with self.assertRaises(ReproducibilityError):
            digest_stream([b"ok", "not bytes"])


class TestSeededRngScope(unittest.TestCase):

    def test_seeding_is_applied_inside_the_scope(self):
        with seeded_rng_scope(7):
            first = random.random()
        with seeded_rng_scope(7):
            second = random.random()
        self.assertEqual(first, second)

    def test_caller_python_rng_state_is_restored(self):
        random.seed(999)
        expected = [random.random() for _ in range(3)]

        random.seed(999)
        with seeded_rng_scope(1):
            random.random()
        observed = [random.random() for _ in range(3)]

        self.assertEqual(expected, observed)

    def test_state_is_restored_even_when_the_body_raises(self):
        random.seed(555)
        expected = random.random()
        random.seed(555)
        with self.assertRaises(RuntimeError):
            with seeded_rng_scope(2):
                raise RuntimeError("training blew up")
        self.assertEqual(expected, random.random())

    @unittest.skipUnless(HAS_NUMPY, "NumPy not installed")
    def test_caller_numpy_global_state_is_restored(self):
        np.random.seed(4242)
        expected = np.random.random(3).tolist()

        np.random.seed(4242)
        with seeded_rng_scope(1):
            np.random.random(5)
        observed = np.random.random(3).tolist()

        self.assertEqual(expected, observed)

    def test_invalid_seeds_are_rejected(self):
        for bad in (-1, MAX_SEED + 1, True, 1.5, "7"):
            with self.assertRaises(ReproducibilityError):
                with seeded_rng_scope(bad):
                    pass


class TestEnvironmentFingerprint(unittest.TestCase):

    def test_capture_is_stable_within_a_process(self):
        self.assertEqual(
            EnvironmentFingerprint.capture().canonical_payload(),
            EnvironmentFingerprint.capture().canonical_payload(),
        )

    def test_pythonhashseed_is_recorded_not_invented(self):
        fingerprint = EnvironmentFingerprint.capture()
        self.assertIsInstance(fingerprint.pythonhashseed, str)
        self.assertEqual(
            fingerprint.hash_randomisation_disabled,
            fingerprint.pythonhashseed != "random",
        )

    def test_python_version_change_changes_the_digest(self):
        base = EnvironmentFingerprint.capture()
        other = replace(base, python_version="0.0.0")
        self.assertNotEqual(base.canonical_payload(), other.canonical_payload())


class TestMLPipelineSpecValidation(unittest.TestCase):

    def test_symbolic_ref_is_rejected(self):
        # REGRESSION: 'HEAD' was the previous default and identifies no code.
        with self.assertRaises(ReproducibilityError) as ctx:
            make_spec(git_commit_hash="HEAD")
        self.assertIn("git rev-parse HEAD", str(ctx.exception))

    def test_abbreviated_and_uppercase_hashes_are_rejected(self):
        for bad in ("a" * 7, "A" * 40, "", "z" * 40, "a" * 41):
            with self.assertRaises(ReproducibilityError):
                make_spec(git_commit_hash=bad)

    def test_sha256_object_id_is_accepted(self):
        self.assertEqual(make_spec(git_commit_hash=COMMIT_SHA256).git_commit_hash,
                         COMMIT_SHA256)

    def test_seed_bounds_and_type(self):
        for bad in (-1, MAX_SEED + 1, True, 3.0):
            with self.assertRaises(ReproducibilityError):
                make_spec(seed=bad)
        self.assertEqual(make_spec(seed=MAX_SEED).seed, MAX_SEED)

    def test_empty_experiment_id_is_rejected(self):
        for bad in ("", "   ", None, 5):
            with self.assertRaises(ReproducibilityError):
                make_spec(experiment_id=bad)

    def test_non_mapping_hyperparameters_are_rejected(self):
        with self.assertRaises(ReproducibilityError):
            make_spec(hyperparameters=[("lr", 0.01)])

    def test_unencodable_hyperparameter_fails_at_construction(self):
        # Not mid-training, where the run would already have been paid for.
        with self.assertRaises(ReproducibilityError):
            make_spec(hyperparameters={"optimiser": object()})

    def test_nan_hyperparameter_is_rejected(self):
        with self.assertRaises(ReproducibilityError):
            make_spec(hyperparameters={"learning_rate": float("nan")})

    def test_hyperparameters_cannot_be_mutated_after_construction(self):
        # frozen=True blocks rebinding, not mutation of the mapping behind the
        # attribute; the spec snapshots it into a read-only view.
        source = {"learning_rate": 0.01}
        spec = make_spec(hyperparameters=source)
        source["learning_rate"] = 99.0
        self.assertEqual(spec.hyperparameters["learning_rate"], 0.01)
        with self.assertRaises(TypeError):
            spec.hyperparameters["learning_rate"] = 99.0

    def test_worktree_dirty_must_be_bool(self):
        with self.assertRaises(ReproducibilityError):
            make_spec(worktree_dirty="yes")


class TestTrainModel(unittest.TestCase):

    def setUp(self):
        self.spec = make_spec()
        self.engine = ReproducibleMLTrainingPipelineEngine(self.spec)
        self.data = [10.5, 12.0, 11.2]

    def test_deterministic_trainer_is_reported_reproducible(self):
        manifest = self.engine.train_model(self.data, seeded_trainer, replicate_runs=2)
        self.assertIs(manifest.is_reproducible, True)
        self.assertEqual(manifest.status, ManifestStatus.REPRODUCIBLE_MANIFEST_CREATED.value)
        self.assertEqual(manifest.verification_runs, 2)
        self.assertEqual(len(manifest.replicate_hashes), 2)
        self.assertEqual(set(manifest.replicate_hashes), {manifest.model_weights_hash})

    def test_non_deterministic_trainer_is_reported_non_reproducible(self):
        # REGRESSION: the previous implementation returned is_reproducible=True
        # unconditionally, so this trainer would have been certified.
        manifest = self.engine.train_model(
            self.data, _counter_trainer(), replicate_runs=2
        )
        self.assertIs(manifest.is_reproducible, False)
        self.assertEqual(
            manifest.status, ManifestStatus.NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE.value
        )
        self.assertEqual(len(set(manifest.replicate_hashes)), 2)
        self.assertNotIn(manifest.model_weights_hash, manifest.replicate_hashes)

    def test_unverified_run_reports_none_not_true(self):
        manifest = self.engine.train_model(self.data, seeded_trainer, replicate_runs=0)
        self.assertIsNone(manifest.is_reproducible)
        self.assertEqual(
            manifest.status, ManifestStatus.MANIFEST_RECORDED_UNVERIFIED.value
        )
        self.assertEqual(manifest.replicate_hashes, ())

    def test_seed_sensitivity_probe_detects_a_stochastic_model(self):
        manifest = self.engine.train_model(
            self.data, seeded_trainer, replicate_runs=1, probe_seed_sensitivity=True
        )
        self.assertIs(manifest.seed_sensitivity_verified, True)

    def test_seed_sensitivity_probe_flags_a_seed_independent_model(self):
        manifest = self.engine.train_model(
            self.data, constant_trainer, replicate_runs=1, probe_seed_sensitivity=True
        )
        self.assertIs(manifest.seed_sensitivity_verified, False)
        # Still reproducible - it just was not the seeding that made it so.
        self.assertIs(manifest.is_reproducible, True)

    def test_seed_sensitivity_is_none_when_not_probed(self):
        manifest = self.engine.train_model(self.data, seeded_trainer)
        self.assertIsNone(manifest.seed_sensitivity_verified)

    def test_probe_uses_a_neighbouring_seed_at_the_upper_bound(self):
        engine = ReproducibleMLTrainingPipelineEngine(make_spec(seed=MAX_SEED))
        manifest = engine.train_model(
            self.data, seeded_trainer, replicate_runs=1, probe_seed_sensitivity=True
        )
        self.assertIs(manifest.seed_sensitivity_verified, True)

    def test_train_fn_cannot_mutate_hyperparameters_across_runs(self):
        def mutating_trainer(dataset, hyperparameters):
            hyperparameters["learning_rate"] = hyperparameters.get("learning_rate", 0) * 10
            return [hyperparameters["learning_rate"]]

        manifest = self.engine.train_model(
            self.data, mutating_trainer, replicate_runs=2
        )
        self.assertIs(manifest.is_reproducible, True)
        self.assertEqual(
            self.engine.spec.hyperparameters["learning_rate"], 0.01
        )

    def test_in_place_dataset_mutation_is_detected(self):
        def augmenting_trainer(dataset, hyperparameters):
            dataset.append(0.0)          # in-place augmentation, a real pattern
            return [sum(dataset)]

        with self.assertRaises(ReproducibilityError) as ctx:
            self.engine.train_model(list(self.data), augmenting_trainer, replicate_runs=1)
        self.assertIn("mutated the dataset in place", str(ctx.exception))

    def test_callers_rng_stream_is_not_disturbed(self):
        random.seed(2024)
        expected = [random.random() for _ in range(4)]
        random.seed(2024)
        self.engine.train_model(self.data, seeded_trainer, replicate_runs=1)
        self.assertEqual(expected, [random.random() for _ in range(4)])

    def test_different_data_changes_the_data_hash(self):
        first = self.engine.train_model(self.data, seeded_trainer, replicate_runs=0)
        second = self.engine.train_model(
            [10.5, 12.0, 99.9], seeded_trainer, replicate_runs=0
        )
        self.assertNotEqual(first.data_hash, second.data_hash)

    def test_data_ordering_changes_the_data_hash(self):
        first = self.engine.train_model([1.0, 9.0], seeded_trainer, replicate_runs=0)
        second = self.engine.train_model([9.0, 1.0], seeded_trainer, replicate_runs=0)
        self.assertNotEqual(first.data_hash, second.data_hash)

    def test_hyperparameter_change_changes_the_hyperparameter_hash(self):
        other = ReproducibleMLTrainingPipelineEngine(
            make_spec(hyperparameters={"learning_rate": 0.02, "batch_size": 32})
        )
        a = self.engine.train_model(self.data, seeded_trainer, replicate_runs=0)
        b = other.train_model(self.data, seeded_trainer, replicate_runs=0)
        self.assertNotEqual(a.hyperparameters_hash, b.hyperparameters_hash)

    def test_non_finite_dataset_is_rejected_before_training(self):
        calls = []

        def recording_trainer(dataset, hyperparameters):
            calls.append(1)
            return [0.0]

        with self.assertRaises(ReproducibilityError):
            self.engine.train_model([1.0, float("nan")], recording_trainer)
        self.assertEqual(calls, [])

    def test_non_finite_artifact_is_rejected(self):
        with self.assertRaises(ReproducibilityError):
            self.engine.train_model(
                self.data, lambda d, h: [float("inf")], replicate_runs=0
            )

    def test_unencodable_artifact_names_the_type(self):
        with self.assertRaises(ReproducibilityError) as ctx:
            self.engine.train_model(self.data, lambda d, h: object(), replicate_runs=0)
        self.assertIn("artifact", str(ctx.exception))

    def test_argument_validation(self):
        with self.assertRaises(ReproducibilityError):
            self.engine.train_model(self.data, "not callable")
        with self.assertRaises(ReproducibilityError):
            self.engine.train_model(self.data, seeded_trainer, replicate_runs=-1)
        with self.assertRaises(ReproducibilityError):
            self.engine.train_model(self.data, seeded_trainer, replicate_runs=True)
        with self.assertRaises(ReproducibilityError):
            self.engine.train_model(
                self.data, seeded_trainer, probe_seed_sensitivity="yes"
            )

    def test_engine_rejects_a_non_spec(self):
        with self.assertRaises(ReproducibilityError):
            ReproducibleMLTrainingPipelineEngine({"experiment_id": "X"})

    def test_two_engines_produce_an_identical_manifest(self):
        a = ReproducibleMLTrainingPipelineEngine(make_spec()).train_model(
            self.data, seeded_trainer, replicate_runs=1
        )
        b = ReproducibleMLTrainingPipelineEngine(make_spec()).train_model(
            self.data, seeded_trainer, replicate_runs=1
        )
        self.assertEqual(a, b)

    def test_dataset_may_be_a_precomputed_stream_digest(self):
        digest = digest_stream([b"row-1", b"row-2"])
        manifest = self.engine.train_model(
            digest, lambda d, h: [len(d) * 1.0], replicate_runs=1
        )
        self.assertEqual(manifest.data_hash, sha256_hex(canonical_bytes(digest, "dataset")))


class TestSignatures(unittest.TestCase):

    def setUp(self):
        self.spec = make_spec()
        self.data = [1.0, 2.0, 3.0]

    def test_unkeyed_tag_is_labelled_a_digest_not_a_signature(self):
        engine = ReproducibleMLTrainingPipelineEngine(self.spec)
        manifest = engine.train_model(self.data, seeded_trainer, replicate_runs=1)
        self.assertEqual(manifest.signature_algorithm, "SHA-256")
        self.assertEqual(
            manifest.manifest_signature, sha256_hex(manifest.signing_payload())
        )
        self.assertTrue(engine.verify_signature(manifest))

    def test_keyed_tag_is_hmac_and_depends_on_the_key(self):
        engine = ReproducibleMLTrainingPipelineEngine(self.spec, signing_key=KEY)
        manifest = engine.train_model(self.data, seeded_trainer, replicate_runs=1)
        self.assertEqual(manifest.signature_algorithm, "HMAC-SHA-256")
        self.assertTrue(engine.verify_signature(manifest))

        wrong_key = ReproducibleMLTrainingPipelineEngine(
            self.spec, signing_key=b"a-different-institutional-key"
        )
        self.assertFalse(wrong_key.verify_signature(manifest))

    def test_unkeyed_digest_does_not_match_the_keyed_tag(self):
        unkeyed = ReproducibleMLTrainingPipelineEngine(self.spec)
        keyed = ReproducibleMLTrainingPipelineEngine(self.spec, signing_key=KEY)
        a = unkeyed.train_model(self.data, seeded_trainer, replicate_runs=1)
        b = keyed.train_model(self.data, seeded_trainer, replicate_runs=1)
        self.assertNotEqual(a.manifest_signature, b.manifest_signature)

    def test_algorithm_mismatch_raises_instead_of_reading_as_tampering(self):
        keyed = ReproducibleMLTrainingPipelineEngine(self.spec, signing_key=KEY)
        manifest = keyed.train_model(self.data, seeded_trainer, replicate_runs=1)
        unkeyed = ReproducibleMLTrainingPipelineEngine(self.spec)
        with self.assertRaises(ReproducibilityError):
            unkeyed.verify_signature(manifest)

    def test_short_signing_key_is_rejected(self):
        with self.assertRaises(ReproducibilityError):
            ReproducibleMLTrainingPipelineEngine(self.spec, signing_key=b"short")
        with self.assertRaises(ReproducibilityError):
            ReproducibleMLTrainingPipelineEngine(self.spec, signing_key="not bytes")

    def test_rewriting_the_commit_hash_invalidates_the_tag(self):
        # REGRESSION: the previous signature covered only experiment_id, seed and
        # three hashes, so the recorded code version could be rewritten freely.
        engine = ReproducibleMLTrainingPipelineEngine(self.spec, signing_key=KEY)
        manifest = engine.train_model(self.data, seeded_trainer, replicate_runs=1)
        forged = replace(manifest, git_commit_hash=COMMIT_B)
        self.assertFalse(engine.verify_signature(forged))

    def test_rewriting_the_verdict_invalidates_the_tag(self):
        engine = ReproducibleMLTrainingPipelineEngine(self.spec, signing_key=KEY)
        manifest = engine.train_model(self.data, _counter_trainer(), replicate_runs=2)
        self.assertIs(manifest.is_reproducible, False)
        forged = replace(
            manifest,
            is_reproducible=True,
            status=ManifestStatus.REPRODUCIBLE_MANIFEST_CREATED.value,
        )
        self.assertFalse(engine.verify_signature(forged))

    def test_every_provenance_field_is_covered_by_the_tag(self):
        engine = ReproducibleMLTrainingPipelineEngine(self.spec, signing_key=KEY)
        manifest = engine.train_model(self.data, seeded_trainer, replicate_runs=1)
        mutations = {
            "experiment_id": "EXP_OTHER",
            "seed": 999,
            "git_commit_hash": COMMIT_B,
            "model_architecture": "GradientBoosting",
            "worktree_dirty": True,
            "data_hash": "0" * 64,
            "hyperparameters_hash": "0" * 64,
            "environment_hash": "0" * 64,
            "model_weights_hash": "0" * 64,
            "is_reproducible": None,
            "seed_sensitivity_verified": True,
            "verification_runs": 99,
            "replicate_hashes": ("0" * 64,),
            "status": ManifestStatus.MANIFEST_RECORDED_UNVERIFIED.value,
        }
        for field_name, value in mutations.items():
            with self.subTest(field=field_name):
                forged = replace(manifest, **{field_name: value})
                self.assertFalse(engine.verify_signature(forged))


class TestManifestComparison(unittest.TestCase):

    def setUp(self):
        self.engine = ReproducibleMLTrainingPipelineEngine(make_spec(), signing_key=KEY)
        self.data = [1.0, 2.0, 3.0]
        self.baseline = self.engine.train_model(
            self.data, seeded_trainer, replicate_runs=1
        )

    def test_identical_rerun_matches(self):
        rerun = self.engine.train_model(self.data, seeded_trainer, replicate_runs=1)
        result = self.engine.compare_manifests(rerun, self.baseline)
        self.assertTrue(result.matched)
        self.assertEqual(result.status, ComparisonStatus.MANIFEST_MATCH.value)
        self.assertEqual(result.mismatched_fields, ())

    def test_changed_dataset_is_localised_to_the_data_hash(self):
        # REGRESSION: DATA_HASH_MISMATCH was documented in the previous version
        # but no code path could ever emit it.
        rerun = self.engine.train_model([1.0, 2.0, 4.0], seeded_trainer, replicate_runs=1)
        result = self.engine.compare_manifests(rerun, self.baseline)
        self.assertFalse(result.matched)
        self.assertEqual(result.status, ComparisonStatus.DATA_HASH_MISMATCH.value)
        self.assertIn("data_hash", result.mismatched_fields)

    def test_causal_ordering_reports_the_input_not_the_symptom(self):
        rerun = self.engine.train_model([1.0, 2.0, 4.0], seeded_trainer, replicate_runs=1)
        result = self.engine.compare_manifests(rerun, self.baseline)
        self.assertIn("model_weights_hash", result.mismatched_fields)
        self.assertEqual(result.mismatched_fields[0], "data_hash")
        self.assertEqual(result.status, ComparisonStatus.DATA_HASH_MISMATCH.value)

    def test_changed_hyperparameters_are_localised(self):
        other = ReproducibleMLTrainingPipelineEngine(
            make_spec(hyperparameters={"learning_rate": 0.5, "batch_size": 32}),
            signing_key=KEY,
        )
        rerun = other.train_model(self.data, seeded_trainer, replicate_runs=1)
        result = self.engine.compare_manifests(rerun, self.baseline)
        self.assertEqual(
            result.status, ComparisonStatus.HYPERPARAMETERS_HASH_MISMATCH.value
        )

    def test_changed_code_version_is_localised(self):
        other = ReproducibleMLTrainingPipelineEngine(
            make_spec(git_commit_hash=COMMIT_B), signing_key=KEY
        )
        rerun = other.train_model(self.data, seeded_trainer, replicate_runs=1)
        result = self.engine.compare_manifests(rerun, self.baseline)
        self.assertEqual(result.status, ComparisonStatus.CODE_VERSION_MISMATCH.value)

    def test_changed_seed_is_localised(self):
        other = ReproducibleMLTrainingPipelineEngine(
            make_spec(seed=999), signing_key=KEY
        )
        rerun = other.train_model(self.data, seeded_trainer, replicate_runs=1)
        result = self.engine.compare_manifests(rerun, self.baseline)
        self.assertEqual(result.status, ComparisonStatus.SEED_MISMATCH.value)

    def test_environment_change_is_localised(self):
        # Re-tagged through the engine's own tag function: a hand-edited manifest
        # would trip the signature check first and never reach the field walk.
        rerun = replace(self.baseline, environment_hash="0" * 64)
        rerun = replace(
            rerun, manifest_signature=self.engine._tag(rerun.signing_payload())
        )
        result = self.engine.compare_manifests(rerun, self.baseline)
        self.assertEqual(result.status, ComparisonStatus.ENVIRONMENT_HASH_MISMATCH.value)

    def test_tampered_baseline_is_refused_as_a_reference(self):
        forged = replace(self.baseline, data_hash="0" * 64)
        rerun = self.engine.train_model(self.data, seeded_trainer, replicate_runs=1)
        result = self.engine.compare_manifests(rerun, forged)
        self.assertFalse(result.matched)
        self.assertEqual(result.status, ComparisonStatus.SIGNATURE_INVALID.value)

    def test_tampered_current_manifest_is_also_refused(self):
        forged = replace(self.baseline, model_weights_hash="0" * 64)
        result = self.engine.compare_manifests(forged, self.baseline)
        self.assertFalse(result.matched)
        self.assertEqual(result.status, ComparisonStatus.SIGNATURE_INVALID.value)

    def test_comparison_rejects_non_manifests(self):
        with self.assertRaises(ReproducibilityError):
            self.engine.compare_manifests(self.baseline, {"data_hash": "x"})
        with self.assertRaises(ReproducibilityError):
            self.engine.compare_manifests("x", self.baseline)


class TestAuditNotes(unittest.TestCase):

    def test_notes_state_the_verdict_verbatim(self):
        engine = ReproducibleMLTrainingPipelineEngine(make_spec())
        unverified = engine.train_model([1.0], constant_trainer, replicate_runs=0)
        self.assertIn("unmeasured", unverified.audit_notes)
        self.assertIn(ManifestStatus.MANIFEST_RECORDED_UNVERIFIED.value,
                      unverified.audit_notes)

        failed = engine.train_model([1.0], _counter_trainer(), replicate_runs=1)
        self.assertIn("FAILED", failed.audit_notes)

    def test_dirty_worktree_is_carried_into_the_manifest(self):
        engine = ReproducibleMLTrainingPipelineEngine(make_spec(worktree_dirty=True))
        manifest = engine.train_model([1.0], constant_trainer, replicate_runs=1)
        self.assertTrue(manifest.worktree_dirty)


if __name__ == "__main__":
    unittest.main()
