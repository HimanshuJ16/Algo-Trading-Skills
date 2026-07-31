import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ModelVersion:
    model_id: str
    version: str                        # e.g. 'v1.0.0', 'v1.1.0'
    sha256_hash: str                    # SHA-256 fingerprint of artifact
    training_dataset_id: str
    sharpe_ratio: float
    max_drawdown_pct: float
    status: str                         # 'PRODUCTION', 'STAGING', 'ARCHIVED', 'DEACTIVATED_ROLLBACK'
    is_active: bool = False
    registered_at_epoch: float = 0.0

@dataclass
class RollbackTriggerConfig:
    max_allowed_drawdown_pct: float = 15.0 # Max drawdown before triggering rollback (15.0%)
    max_allowed_error_rate_pct: float = 5.0 # Max inference error rate before rollback (5.0%)

@dataclass
class LivePerformanceTelemetry:
    model_id: str
    current_version: str
    live_drawdown_pct: float
    live_error_rate_pct: float
    recent_sharpe: float

@dataclass
class ModelVersionReport:
    model_id: str
    active_version: str
    previous_version: Optional[str]
    sha256_hash: str
    is_rollback_executed: bool
    status: str                         # 'MODEL_VERSION_HEALTHY', 'ROLLBACK_SUCCESSFUL', 'ROLLBACK_FAILED_NO_HEALTHY_VERSION'
    audit_notes: str

class ModelVersionManagerEngine:
    """
    Quantitative model versioning and automated rollback engine managing immutable SHA-256 model registries,
    semantic versioning, and instant circuit-breaker rollbacks.
    """
    def __init__(self):
        self.registry: Dict[str, Dict[str, ModelVersion]] = {} # {model_id: {version: ModelVersion}}

    @staticmethod
    def compute_sha256(content: bytes) -> str:
        """Computes SHA-256 fingerprint for model artifact payload."""
        return hashlib.sha256(content).hexdigest()

    def register_version(self, model_version: ModelVersion):
        """Registers a new model version into the registry catalog."""
        if not model_version.sha256_hash or len(model_version.sha256_hash) != 64:
            raise ValueError("Invalid SHA-256 hash format. Must be 64-character hex string.")

        if model_version.model_id not in self.registry:
            self.registry[model_version.model_id] = {}

        # If marking as production/active, deactivate existing active versions
        if model_version.is_active or model_version.status == "PRODUCTION":
            for ver in self.registry[model_version.model_id].values():
                ver.is_active = False

        self.registry[model_version.model_id][model_version.version] = model_version
        logger.info(f"REGISTERED MODEL [{model_version.model_id}]: Version {model_version.version} (SHA-256: {model_version.sha256_hash[:8]}...).")

    def audit_telemetry_and_rollback(
        self,
        config: RollbackTriggerConfig,
        telemetry: LivePerformanceTelemetry
    ) -> ModelVersionReport:
        """
        Audits live performance telemetry against drawdown and error rate limits.
        Triggers instant atomic rollback to last known healthy production model if limits are breached.
        """
        model_id = telemetry.model_id
        if model_id not in self.registry:
            raise ValueError(f"Model ID '{model_id}' not found in registry catalog.")

        versions = self.registry[model_id]
        curr_ver_obj = versions.get(telemetry.current_version)
        if not curr_ver_obj:
            raise ValueError(f"Current version '{telemetry.current_version}' not registered.")

        # Check for Breaches
        drawdown_breach = telemetry.live_drawdown_pct > config.max_allowed_drawdown_pct
        error_breach = telemetry.live_error_rate_pct > config.max_allowed_error_rate_pct

        if not drawdown_breach and not error_breach:
            notes = (
                f"MODEL HEALTHY [{model_id}]: Version {telemetry.current_version} operating normally. "
                f"Drawdown = {telemetry.live_drawdown_pct:.1f}%, Error Rate = {telemetry.live_error_rate_pct:.1f}%."
            )
            logger.info(notes)
            return ModelVersionReport(
                model_id=model_id,
                active_version=telemetry.current_version,
                previous_version=None,
                sha256_hash=curr_ver_obj.sha256_hash,
                is_rollback_executed=False,
                status="MODEL_VERSION_HEALTHY",
                audit_notes=notes
            )

        # BREACH DETECTED -> Execute Rollback
        logger.warning(
            f"CIRCUIT BREAKER TRIGGERED [{model_id}]: Version {telemetry.current_version} breached thresholds! "
            f"Drawdown={telemetry.live_drawdown_pct:.1f}% (Limit={config.max_allowed_drawdown_pct:.1f}%), "
            f"Error Rate={telemetry.live_error_rate_pct:.1f}% (Limit={config.max_allowed_error_rate_pct:.1f}%)."
        )

        # Deactivate failing version
        curr_ver_obj.is_active = False
        curr_ver_obj.status = "DEACTIVATED_ROLLBACK"

        # Search for last known healthy version (excluding failing version)
        candidate_versions = [
            v for v in versions.values()
            if v.version != telemetry.current_version and v.status in ("PRODUCTION", "STAGING", "ARCHIVED")
        ]

        if not candidate_versions:
            notes = f"ROLLBACK FAILED [{model_id}]: No secondary healthy production version available in registry!"
            logger.critical(notes)
            return ModelVersionReport(
                model_id=model_id,
                active_version=telemetry.current_version,
                previous_version=None,
                sha256_hash=curr_ver_obj.sha256_hash,
                is_rollback_executed=False,
                status="ROLLBACK_FAILED_NO_HEALTHY_VERSION",
                audit_notes=notes
            )

        # Sort by registration timestamp or version string descending
        sorted_candidates = sorted(candidate_versions, key=lambda v: v.registered_at_epoch, reverse=True)
        fallback_ver = sorted_candidates[0]

        # Hot-swap pointer
        fallback_ver.is_active = True
        fallback_ver.status = "PRODUCTION"

        notes = (
            f"ROLLBACK SUCCESSFUL [{model_id}]: Deactivated failing version {telemetry.current_version}. "
            f"Hot-swapped active pointer to fallback version {fallback_ver.version} (SHA-256: {fallback_ver.sha256_hash[:8]}...)."
        )
        logger.info(notes)

        return ModelVersionReport(
            model_id=model_id,
            active_version=fallback_ver.version,
            previous_version=telemetry.current_version,
            sha256_hash=fallback_ver.sha256_hash,
            is_rollback_executed=True,
            status="ROLLBACK_SUCCESSFUL",
            audit_notes=notes
        )
