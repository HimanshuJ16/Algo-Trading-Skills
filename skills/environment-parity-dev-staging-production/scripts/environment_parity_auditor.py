import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class EnvironmentSpec:
    env_name: str                        # 'DEV', 'STAGING', 'PRODUCTION'
    python_version: str                  # e.g. '3.11.8'
    lockfile_sha256: str                 # e.g. 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    db_schema_revision: str              # e.g. 'a1b2c3d4'
    broker_endpoint_mode: str            # 'TESTNET' for Staging, 'MAINNET' for Production
    env_vars: Dict[str, str] = field(default_factory=dict)

@dataclass
class ParityVectorCheck:
    vector_name: str                     # e.g. 'PYTHON_VERSION', 'LOCKFILE_HASH', 'DB_SCHEMA', 'BROKER_ENDPOINT'
    is_passed: bool
    expected_value: str
    actual_value: str
    details: str

@dataclass
class EnvironmentParityAuditReport:
    target_env: str
    baseline_env: str
    parity_score_pct: float              # 0.0% to 100.0%
    is_deployment_allowed: bool
    audit_status: str                    # 'PARITY_VERIFIED_PASSED', 'PARITY_VIOLATION_BLOCKED'
    vector_checks: List[ParityVectorCheck]
    audit_summary: str

class EnvironmentParityAuditorEngine:
    """
    Quantitative DevOps engine for auditing 5-factor environment parity
    (Python runtime, dependency lockfile hashes, env vars, DB schema revisions, broker endpoint modes) across Dev, Staging, and Production.
    """
    def __init__(self, required_env_var_keys: Optional[List[str]] = None):
        self.required_env_var_keys = required_env_var_keys or ["BROKER_API_KEY", "MAX_POSITION_LIMIT", "DATABASE_URL"]

    def audit_environment_parity(
        self,
        current_env: EnvironmentSpec,
        production_baseline: EnvironmentSpec
    ) -> EnvironmentParityAuditReport:
        """
        Audits current environment spec against production baseline across 5 core parity vectors.
        """
        checks: List[ParityVectorCheck] = []

        # Vector 1: Python Version
        v1_ok = (current_env.python_version == production_baseline.python_version)
        checks.append(ParityVectorCheck(
            vector_name="PYTHON_VERSION", is_passed=v1_ok,
            expected_value=production_baseline.python_version, actual_value=current_env.python_version,
            details="Python runtime release version match." if v1_ok else f"Python version mismatch: {current_env.python_version} vs {production_baseline.python_version}."
        ))

        # Vector 2: Dependency Lockfile Hash
        v2_ok = (current_env.lockfile_sha256 == production_baseline.lockfile_sha256)
        checks.append(ParityVectorCheck(
            vector_name="LOCKFILE_HASH", is_passed=v2_ok,
            expected_value=production_baseline.lockfile_sha256[:12], actual_value=current_env.lockfile_sha256[:12],
            details="Package lockfile SHA-256 hash match." if v2_ok else "Dependency lockfile mismatch! Packages differ from production."
        ))

        # Vector 3: Database Schema Revision
        v3_ok = (current_env.db_schema_revision == production_baseline.db_schema_revision)
        checks.append(ParityVectorCheck(
            vector_name="DB_SCHEMA", is_passed=v3_ok,
            expected_value=production_baseline.db_schema_revision, actual_value=current_env.db_schema_revision,
            details="Alembic DB migration head match." if v3_ok else f"DB schema drift: {current_env.db_schema_revision} vs {production_baseline.db_schema_revision}."
        ))

        # Vector 4: Broker Endpoint Mode Alignment
        expected_mode = "MAINNET" if current_env.env_name.upper() == "PRODUCTION" else "TESTNET"
        v4_ok = (current_env.broker_endpoint_mode.upper() == expected_mode)
        checks.append(ParityVectorCheck(
            vector_name="BROKER_ENDPOINT", is_passed=v4_ok,
            expected_value=expected_mode, actual_value=current_env.broker_endpoint_mode.upper(),
            details="Broker endpoint mode aligned." if v4_ok else f"MISCONFIGURED ENDPOINT: {current_env.env_name} set to {current_env.broker_endpoint_mode} (expected {expected_mode})!"
        ))

        # Vector 5: Mandatory Environment Variables
        missing_vars = [k for k in self.required_env_var_keys if k not in current_env.env_vars or not current_env.env_vars[k]]
        v5_ok = (len(missing_vars) == 0)
        checks.append(ParityVectorCheck(
            vector_name="ENV_VARS_PRESENT", is_passed=v5_ok,
            expected_value=f"All {len(self.required_env_var_keys)} keys present", actual_value=f"Missing: {missing_vars}" if missing_vars else "All present",
            details="Mandatory environment variables verified." if v5_ok else f"Missing required env vars: {missing_vars}."
        ))

        passed_count = sum(1 for c in checks if c.is_passed)
        parity_score = round((passed_count / float(len(checks))) * 100.0, 1)

        is_allowed = (passed_count == len(checks))
        status = "PARITY_VERIFIED_PASSED" if is_allowed else "PARITY_VIOLATION_BLOCKED"

        if is_allowed:
            summary = f"ENVIRONMENT PARITY PASSED [{current_env.env_name}]: 100% vector alignment with Production baseline."
            logger.info(summary)
        else:
            failed_names = [c.vector_name for c in checks if not c.is_passed]
            summary = f"DEPLOYMENT BLOCKED [{current_env.env_name}]: Parity score {parity_score}% < 100%. Failed vectors: {failed_names}."
            logger.error(summary)

        return EnvironmentParityAuditReport(
            target_env=current_env.env_name,
            baseline_env=production_baseline.env_name,
            parity_score_pct=parity_score,
            is_deployment_allowed=is_allowed,
            audit_status=status,
            vector_checks=checks,
            audit_summary=summary
        )
