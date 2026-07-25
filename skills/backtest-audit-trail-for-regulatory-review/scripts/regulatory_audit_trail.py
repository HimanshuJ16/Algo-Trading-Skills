"""
backtest-audit-trail-for-regulatory-review: Cryptographic backtest audit trail,
data lineage recorder, and regulatory compliance manifest generator.
"""
from dataclasses import dataclass, field
import hashlib
import json
import logging
import platform
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class BacktestAuditManifest:
    strategy_id: str
    git_commit_sha: str
    data_checksum_sha256: str
    parameters: Dict[str, Any]
    metrics_summary: Dict[str, float]
    execution_timestamp: float
    platform_info: str
    manifest_signature_sha256: str


class RegulatoryAuditTrailEngine:
    """
    Generates immutable cryptographic audit manifests for backtests to satisfy
    regulatory compliance (SEC / FINRA / MiFID II) and institutional risk committees.
    """

    def __init__(self, organization: str = "QuantDesk Institutional"):
        self.organization = organization

    def compute_file_checksum(self, filepath: str, chunk_size: int = 8192) -> str:
        """
        Computes the SHA256 checksum of a massive dataset (e.g., Parquet/CSV)
        by streaming it in chunks to avoid OOM errors.
        """
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(chunk_size), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def build_manifest(
        self,
        strategy_id: str,
        git_commit_sha: str,
        data_checksum: str,
        parameters: Dict[str, Any],
        metrics_summary: Dict[str, float],
    ) -> BacktestAuditManifest:
        ts = time.time()
        plat_info = f"{platform.system()} {platform.release()} ({platform.machine()})"

        payload = {
            "org": self.organization,
            "strategy_id": strategy_id,
            "git_commit_sha": git_commit_sha,
            "data_checksum": data_checksum,
            "parameters": parameters,
            "metrics": metrics_summary,
            "timestamp": ts,
            "platform": plat_info,
        }

        canonical_json = json.dumps(payload, sort_keys=True)
        sig = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

        logger.info(f"Audit Trail Signed: Strategy '{strategy_id}', Signature={sig[:12]}...")

        return BacktestAuditManifest(
            strategy_id=strategy_id,
            git_commit_sha=git_commit_sha,
            data_checksum_sha256=data_checksum,
            parameters=parameters,
            metrics_summary=metrics_summary,
            execution_timestamp=ts,
            platform_info=plat_info,
            manifest_signature_sha256=sig,
        )

    def verify_manifest(self, manifest: BacktestAuditManifest) -> bool:
        payload = {
            "org": self.organization,
            "strategy_id": manifest.strategy_id,
            "git_commit_sha": manifest.git_commit_sha,
            "data_checksum": manifest.data_checksum_sha256,
            "parameters": manifest.parameters,
            "metrics": manifest.metrics_summary,
            "timestamp": manifest.execution_timestamp,
            "platform": manifest.platform_info,
        }
        canonical_json = json.dumps(payload, sort_keys=True)
        expected_sig = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

        return manifest.manifest_signature_sha256 == expected_sig
