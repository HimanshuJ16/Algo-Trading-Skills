import unittest
from environment_parity_auditor import (
    EnvironmentParityAuditorEngine, EnvironmentSpec, EnvironmentParityAuditReport
)

class TestEnvironmentParityAuditorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EnvironmentParityAuditorEngine()
        self.prod_baseline = EnvironmentSpec(
            env_name="PRODUCTION",
            python_version="3.11.8",
            lockfile_sha256="abc123def4567890",
            db_schema_revision="rev_a1b2c3d4",
            broker_endpoint_mode="MAINNET",
            env_vars={"BROKER_API_KEY": "secret_prod", "MAX_POSITION_LIMIT": "1000000", "DATABASE_URL": "postgres://prod"}
        )

    def test_staging_environment_parity_passed(self):
        staging_env = EnvironmentSpec(
            env_name="STAGING",
            python_version="3.11.8",
            lockfile_sha256="abc123def4567890",
            db_schema_revision="rev_a1b2c3d4",
            broker_endpoint_mode="TESTNET",       # Correct for Staging!
            env_vars={"BROKER_API_KEY": "secret_test", "MAX_POSITION_LIMIT": "100000", "DATABASE_URL": "postgres://test"}
        )
        report = self.engine.audit_environment_parity(staging_env, self.prod_baseline)
        
        self.assertTrue(report.is_deployment_allowed)
        self.assertEqual(report.parity_score_pct, 100.0)
        self.assertEqual(report.audit_status, "PARITY_VERIFIED_PASSED")

    def test_db_schema_drift_blocks_deployment(self):
        staging_env = EnvironmentSpec(
            env_name="STAGING",
            python_version="3.11.8",
            lockfile_sha256="abc123def4567890",
            db_schema_revision="rev_old_0000",   # Mismatched DB schema!
            broker_endpoint_mode="TESTNET",
            env_vars={"BROKER_API_KEY": "secret_test", "MAX_POSITION_LIMIT": "100000", "DATABASE_URL": "postgres://test"}
        )
        report = self.engine.audit_environment_parity(staging_env, self.prod_baseline)
        
        self.assertFalse(report.is_deployment_allowed)
        self.assertEqual(report.audit_status, "PARITY_VIOLATION_BLOCKED")
        self.assertIn("DB_SCHEMA", [c.vector_name for c in report.vector_checks if not c.is_passed])

if __name__ == '__main__':
    unittest.main()
