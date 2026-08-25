import hashlib
import os
import tempfile
import unittest

from environment_parity_auditor import (
    ENV_PRODUCTION,
    MODE_MAINNET,
    MODE_TESTNET,
    STATUS_BLOCKED,
    STATUS_PASSED,
    EnvironmentParityAuditorEngine,
    EnvironmentSpec,
    current_python_version,
    sha256_of_lockfile,
)

# Two distinct, well-formed 64-character hex SHA-256 digests.
LOCK_A = hashlib.sha256(b"requirements.lock release A").hexdigest()
LOCK_B = hashlib.sha256(b"requirements.lock release B").hexdigest()

BASE_ENV_VARS = {
    "BROKER_API_KEY": "secret_test",
    "MAX_POSITION_LIMIT": "100000",
    "DATABASE_URL": "postgres://test",
}


def make_spec(**overrides):
    """Build a STAGING spec that is at full parity with PROD_BASELINE unless overridden."""
    kwargs = dict(
        env_name="STAGING",
        python_version="3.11.8",
        lockfile_sha256=LOCK_A,
        db_schema_revision="rev_a1b2c3d4",
        broker_endpoint_mode=MODE_TESTNET,
        env_vars=dict(BASE_ENV_VARS),
    )
    kwargs.update(overrides)
    return EnvironmentSpec(**kwargs)


class TestEnvironmentParityAuditorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EnvironmentParityAuditorEngine()
        self.prod_baseline = EnvironmentSpec(
            env_name=ENV_PRODUCTION,
            python_version="3.11.8",
            lockfile_sha256=LOCK_A,
            db_schema_revision="rev_a1b2c3d4",
            broker_endpoint_mode=MODE_MAINNET,
            env_vars={"BROKER_API_KEY": "secret_prod",
                      "MAX_POSITION_LIMIT": "1000000",
                      "DATABASE_URL": "postgres://prod"},
        )

    # -- Happy path ----------------------------------------------------------------

    def test_staging_environment_parity_passed(self):
        report = self.engine.audit_environment_parity(make_spec(), self.prod_baseline)
        self.assertTrue(report.is_deployment_allowed)
        self.assertEqual(report.parity_score_pct, 100.0)
        self.assertEqual(report.audit_status, STATUS_PASSED)
        self.assertEqual(report.failed_vector_names, [])
        self.assertEqual(len(report.vector_checks), 5)

    def test_production_auditing_itself_passes(self):
        report = self.engine.audit_environment_parity(
            self.prod_baseline, self.prod_baseline)
        self.assertTrue(report.is_deployment_allowed)
        self.assertEqual(report.target_env, ENV_PRODUCTION)

    # -- One test per parity vector -------------------------------------------------

    def test_python_patch_version_drift_blocks_deployment(self):
        report = self.engine.audit_environment_parity(
            make_spec(python_version="3.11.2"), self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["PYTHON_VERSION"])
        self.assertEqual(report.parity_score_pct, 80.0)

    def test_lockfile_hash_mismatch_blocks_deployment(self):
        report = self.engine.audit_environment_parity(
            make_spec(lockfile_sha256=LOCK_B), self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["LOCKFILE_HASH"])

    def test_db_schema_drift_blocks_deployment(self):
        report = self.engine.audit_environment_parity(
            make_spec(db_schema_revision="rev_old_0000"), self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.audit_status, STATUS_BLOCKED)
        self.assertEqual(report.failed_vector_names, ["DB_SCHEMA"])

    def test_missing_env_var_blocks_deployment(self):
        env_vars = dict(BASE_ENV_VARS)
        del env_vars["DATABASE_URL"]
        report = self.engine.audit_environment_parity(
            make_spec(env_vars=env_vars), self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["ENV_VARS_PRESENT"])

    def test_empty_env_var_value_counts_as_missing(self):
        env_vars = dict(BASE_ENV_VARS, BROKER_API_KEY="   ")
        report = self.engine.audit_environment_parity(
            make_spec(env_vars=env_vars), self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["ENV_VARS_PRESENT"])

    # -- The headline safety case ---------------------------------------------------

    def test_staging_wired_to_mainnet_blocks_deployment(self):
        """Staging pointed at the live broker: paper strategies would send real orders."""
        report = self.engine.audit_environment_parity(
            make_spec(broker_endpoint_mode=MODE_MAINNET), self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["BROKER_ENDPOINT"])
        endpoint = next(c for c in report.vector_checks
                        if c.vector_name == "BROKER_ENDPOINT")
        self.assertEqual(endpoint.expected_value, MODE_TESTNET)
        self.assertEqual(endpoint.actual_value, MODE_MAINNET)

    def test_dev_wired_to_mainnet_blocks_deployment(self):
        report = self.engine.audit_environment_parity(
            make_spec(env_name="DEV", broker_endpoint_mode=MODE_MAINNET),
            self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["BROKER_ENDPOINT"])

    def test_production_wired_to_testnet_blocks_deployment(self):
        """A live release silently pointed at paper is a failure, not a safe default."""
        report = self.engine.audit_environment_parity(
            make_spec(env_name=ENV_PRODUCTION, broker_endpoint_mode=MODE_TESTNET),
            self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["BROKER_ENDPOINT"])

    # -- Regression: blank fields must not be audited as "equal" --------------------

    def test_blank_field_is_rejected_not_treated_as_match(self):
        """Two environments that both failed to resolve a value must never score 100%.

        Under the previous string-equality implementation, ``"" == ""`` made every such
        vector pass, so an audit run with no evidence at all reported PARITY_VERIFIED.
        """
        for blank_field in ("python_version", "lockfile_sha256",
                            "db_schema_revision", "broker_endpoint_mode"):
            for blank_value in ("", "   "):
                with self.subTest(field=blank_field, value=repr(blank_value)):
                    with self.assertRaises(ValueError):
                        make_spec(**{blank_field: blank_value})

    def test_separator_only_schema_revision_is_rejected(self):
        """',' on both sides would yield two equal empty head sets and pass."""
        for bad in (",", " , , ", "\n\n"):
            with self.subTest(value=repr(bad)):
                with self.assertRaises(ValueError):
                    make_spec(db_schema_revision=bad)

    def test_none_env_var_value_counts_as_missing(self):
        """str(None) is 'None' -- a truthy string that must not read as present."""
        env_vars = dict(BASE_ENV_VARS)
        env_vars["BROKER_API_KEY"] = None
        report = self.engine.audit_environment_parity(
            make_spec(env_vars=env_vars), self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["ENV_VARS_PRESENT"])

    def test_bare_string_required_env_var_keys_is_rejected(self):
        """A str is a Sequence[str]; unguarded it would audit one key per letter."""
        with self.assertRaises(ValueError):
            EnvironmentParityAuditorEngine(required_env_var_keys="BROKER_API_KEY")

    def test_minor_only_python_version_is_rejected(self):
        """'3.11' on both sides would compare equal across 3.11.2 and 3.11.8."""
        with self.assertRaises(ValueError):
            make_spec(python_version="3.11")

    def test_short_or_non_hex_lockfile_hash_is_rejected(self):
        for bad in ("abc123def4567890", "not-a-hash", LOCK_A[:-1], LOCK_A + "0"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    make_spec(lockfile_sha256=bad)

    # -- Input validation ------------------------------------------------------------

    def test_unrecognised_env_name_is_rejected(self):
        """'PROD' must not be silently classified as a non-production environment."""
        for bad_name in ("PROD", "prod-eu-1", "production-1", "UAT", "Staging2"):
            with self.subTest(name=bad_name):
                with self.assertRaises(ValueError):
                    make_spec(env_name=bad_name)

    def test_env_name_and_mode_are_case_and_whitespace_normalised(self):
        spec = make_spec(env_name="  staging ", broker_endpoint_mode="testnet")
        self.assertEqual(spec.env_name, "STAGING")
        self.assertEqual(spec.broker_endpoint_mode, MODE_TESTNET)

    def test_lockfile_hash_comparison_is_case_insensitive(self):
        """Hex digest case is not semantic; an upper-case digest is the same digest."""
        report = self.engine.audit_environment_parity(
            make_spec(lockfile_sha256=LOCK_A.upper()), self.prod_baseline)
        self.assertTrue(report.is_deployment_allowed)

    def test_unrecognised_endpoint_mode_is_rejected(self):
        for bad in ("TESNET", "SANDBOX", "LIVE"):
            with self.subTest(mode=bad):
                with self.assertRaises(ValueError):
                    make_spec(broker_endpoint_mode=bad)

    def test_non_production_baseline_is_rejected(self):
        """Guards against swapping the two same-typed arguments."""
        with self.assertRaises(ValueError):
            self.engine.audit_environment_parity(self.prod_baseline, make_spec())

    def test_blank_required_env_var_key_is_rejected(self):
        with self.assertRaises(ValueError):
            EnvironmentParityAuditorEngine(required_env_var_keys=["BROKER_API_KEY", " "])

    def test_duplicate_required_env_var_keys_do_not_inflate_the_count(self):
        engine = EnvironmentParityAuditorEngine(
            required_env_var_keys=["BROKER_API_KEY", "BROKER_API_KEY", "DATABASE_URL"])
        self.assertEqual(engine.required_env_var_keys,
                         ["BROKER_API_KEY", "DATABASE_URL"])
        report = engine.audit_environment_parity(make_spec(), self.prod_baseline)
        env_check = next(c for c in report.vector_checks
                         if c.vector_name == "ENV_VARS_PRESENT")
        self.assertEqual(env_check.expected_value, "All 2 keys present")

    # -- Multi-head Alembic histories -----------------------------------------------

    def test_multiple_schema_heads_compare_order_independently(self):
        baseline = EnvironmentSpec(
            env_name=ENV_PRODUCTION,
            python_version="3.11.8",
            lockfile_sha256=LOCK_A,
            db_schema_revision="27c6a30d7c24, ae1027a6acf",
            broker_endpoint_mode=MODE_MAINNET,
            env_vars=dict(BASE_ENV_VARS),
        )
        report = self.engine.audit_environment_parity(
            make_spec(db_schema_revision="ae1027a6acf\n27c6a30d7c24"), baseline)
        self.assertTrue(report.is_deployment_allowed)

    def test_partially_migrated_branch_blocks_deployment(self):
        """One head applied out of two required is drift, not a match."""
        baseline = EnvironmentSpec(
            env_name=ENV_PRODUCTION,
            python_version="3.11.8",
            lockfile_sha256=LOCK_A,
            db_schema_revision="27c6a30d7c24 ae1027a6acf",
            broker_endpoint_mode=MODE_MAINNET,
            env_vars=dict(BASE_ENV_VARS),
        )
        report = self.engine.audit_environment_parity(
            make_spec(db_schema_revision="27c6a30d7c24"), baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["DB_SCHEMA"])
        schema = next(c for c in report.vector_checks if c.vector_name == "DB_SCHEMA")
        self.assertIn("ae1027a6acf", schema.details)

    # -- Scoring and reporting -------------------------------------------------------

    def test_multiple_failures_lower_the_score_but_the_gate_is_binary(self):
        report = self.engine.audit_environment_parity(
            make_spec(python_version="3.10.14", lockfile_sha256=LOCK_B,
                      broker_endpoint_mode=MODE_MAINNET),
            self.prod_baseline)
        self.assertEqual(report.parity_score_pct, 40.0)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names,
                         ["PYTHON_VERSION", "LOCKFILE_HASH", "BROKER_ENDPOINT"])

    def test_secrets_are_not_exposed_in_spec_repr_or_report(self):
        spec = make_spec(env_vars=dict(BASE_ENV_VARS, BROKER_API_KEY="hunter2"))
        self.assertNotIn("hunter2", repr(spec))
        report = self.engine.audit_environment_parity(spec, self.prod_baseline)
        rendered = repr(report)
        self.assertNotIn("hunter2", rendered)
        self.assertNotIn("secret_prod", rendered)

    def test_full_lockfile_hash_is_compared_not_the_displayed_prefix(self):
        """Two digests sharing a 12-character prefix must still be reported as drift."""
        prefix = LOCK_A[:12]
        collided = prefix + LOCK_B[12:]
        self.assertNotEqual(collided, LOCK_A)
        report = self.engine.audit_environment_parity(
            make_spec(lockfile_sha256=collided), self.prod_baseline)
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.failed_vector_names, ["LOCKFILE_HASH"])


class TestLockfileHashing(unittest.TestCase):

    def test_sha256_of_lockfile_matches_independent_digest(self):
        payload = b"numpy==1.26.4\npandas==2.2.2\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "requirements.lock")
            with open(path, "wb") as handle:
                handle.write(payload)
            self.assertEqual(sha256_of_lockfile(path),
                             hashlib.sha256(payload).hexdigest())

    def test_crlf_and_lf_lockfiles_hash_differently(self):
        """Documents the Windows checkout trap: binary hashing is newline-sensitive."""
        with tempfile.TemporaryDirectory() as tmp:
            lf_path = os.path.join(tmp, "lf.lock")
            crlf_path = os.path.join(tmp, "crlf.lock")
            with open(lf_path, "wb") as handle:
                handle.write(b"numpy==1.26.4\n")
            with open(crlf_path, "wb") as handle:
                handle.write(b"numpy==1.26.4\r\n")
            self.assertNotEqual(sha256_of_lockfile(lf_path),
                                sha256_of_lockfile(crlf_path))

    def test_missing_lockfile_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                sha256_of_lockfile(os.path.join(tmp, "nope.lock"))

    def test_current_python_version_is_accepted_by_environment_spec(self):
        spec = make_spec(python_version=current_python_version())
        self.assertEqual(spec.python_version, current_python_version())


if __name__ == '__main__':
    unittest.main()
