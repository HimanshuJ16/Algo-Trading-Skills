"""
backtest-audit-trail-for-regulatory-review: Backtest audit trail, data lineage
recorder, and compliance manifest generator.

Threat model — read before relying on this for anything
--------------------------------------------------------
An *unkeyed* hash (plain SHA256) over a manifest is NOT tamper-evidence. Anyone
who edits the manifest can recompute the hash and write it back, and every
verification will pass. It detects accidental corruption only.

This module therefore produces two distinct values:

* ``content_digest_sha256`` -- unkeyed SHA256 of the canonical payload. Anyone
  can recompute it. Detects corruption in transit or at rest. NOT a signature.
* ``manifest_hmac_sha256``  -- HMAC-SHA256 over the same canonical payload,
  keyed with a secret held outside the manifest. Detects modification by any
  party that does not hold the key.

Residual limitation, stated plainly: HMAC is symmetric, so whoever generates a
manifest can also forge one. It does not provide non-repudiation against the
issuing firm itself. A firm cannot use this to prove to a regulator that it did
not alter its own backtest results. Achieving that requires a trust anchor
outside the firm's control, such as:

* asymmetric signatures whose private key is held by an independent party;
* an RFC 3161 trusted timestamp authority; or
* write-once storage administered by a third party.

The regulatory durability of these records comes from the storage layer
(17 CFR 240.17a-4(f) WORM or audit-trail system), not from any hash computed
here. Treat this module as lineage capture plus integrity checking, not as a
substitute for controlled recordkeeping infrastructure.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import math
import platform
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "AuditManifestError",
    "ManifestVerificationError",
    "BacktestAuditManifest",
    "RegulatoryAuditTrailEngine",
]

# HMAC keys shorter than the hash block offer no benefit and usually indicate a
# passphrase was passed where a generated key was intended.
_MIN_SIGNING_KEY_BYTES = 32


class AuditManifestError(ValueError):
    """Raised when a manifest is malformed or its inputs are invalid."""


class ManifestVerificationError(AuditManifestError):
    """Raised when a manifest fails integrity or authenticity verification."""


@dataclass
class BacktestAuditManifest:
    """Immutable-by-convention record of one backtest execution.

    Note the field rename from earlier versions: the value formerly called
    ``manifest_signature_sha256`` was an unkeyed digest, not a signature. It is
    now ``content_digest_sha256``, and authenticity lives in
    ``manifest_hmac_sha256``.
    """

    strategy_id: str
    git_commit_sha: str
    data_checksums_sha256: Dict[str, str]  # filepath -> checksum
    parameters: Dict[str, Any]
    metrics_summary: Dict[str, float]
    execution_timestamp_ns: int  # integer ns since epoch; see canonicalisation note
    platform_info: str
    python_version: str
    content_digest_sha256: str
    manifest_hmac_sha256: str
    signing_key_id: str
    organization: str
    data_sources: List[str] = field(default_factory=list)
    notes: str = ""

    @property
    def execution_timestamp_iso(self) -> str:
        """UTC ISO 8601 with microsecond granularity."""
        seconds, nanos = divmod(self.execution_timestamp_ns, 1_000_000_000)
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{nanos // 1000:06d}Z"


class RegulatoryAuditTrailEngine:
    """Builds and verifies backtest audit manifests.

    Args:
        signing_key: Secret HMAC key, at least 32 bytes. Load from a secrets
            manager or environment variable; never hard-code it and never store
            it alongside the manifests it authenticates.
        organization: Legal entity the manifest is attributed to.
        signing_key_id: Identifier for the key in use, recorded in the signed
            payload so keys can be rotated without invalidating old manifests.
    """

    def __init__(
        self,
        signing_key: bytes,
        organization: str = "QuantDesk Institutional",
        signing_key_id: str = "default",
    ) -> None:
        if not isinstance(signing_key, (bytes, bytearray)):
            raise AuditManifestError(
                "signing_key must be bytes; pass a generated key, not a str passphrase"
            )
        if len(signing_key) < _MIN_SIGNING_KEY_BYTES:
            raise AuditManifestError(
                f"signing_key must be at least {_MIN_SIGNING_KEY_BYTES} bytes "
                f"(got {len(signing_key)}); use secrets.token_bytes(32) or longer"
            )
        if not isinstance(organization, str) or not organization.strip():
            raise AuditManifestError("organization must be a non-empty string")
        if not isinstance(signing_key_id, str) or not signing_key_id.strip():
            raise AuditManifestError("signing_key_id must be a non-empty string")

        self._signing_key = bytes(signing_key)
        self.organization = organization
        self.signing_key_id = signing_key_id

    # ---------------------------------------------------------------- checksums

    def compute_file_checksum(self, filepath: str, chunk_size: int = 65536) -> str:
        """Streams a file to compute its SHA256 without loading it into memory."""
        if chunk_size <= 0:
            raise AuditManifestError("chunk_size must be positive")
        sha256_hash = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                for byte_block in iter(lambda: f.read(chunk_size), b""):
                    sha256_hash.update(byte_block)
        except OSError as exc:
            raise AuditManifestError(f"cannot checksum {filepath!r}: {exc}") from exc
        return sha256_hash.hexdigest()

    def compute_data_checksums(self, filepaths: List[str]) -> Dict[str, str]:
        """Computes SHA256 checksums for multiple data files.

        Raises:
            AuditManifestError: If the same path appears twice, which would
                silently collapse two entries and understate the input set.
        """
        checksums: Dict[str, str] = {}
        for fp in filepaths:
            if fp in checksums:
                raise AuditManifestError(f"duplicate input file path: {fp!r}")
            checksums[fp] = self.compute_file_checksum(fp)
        return checksums

    # ------------------------------------------------------------ canonical form

    @staticmethod
    def _canonical_payload(manifest: BacktestAuditManifest) -> Dict[str, Any]:
        """The exact structure that gets hashed and HMAC'd.

        Everything an auditor would care about must appear here — any field
        omitted is a field an attacker can change without detection.
        """
        return {
            "org": manifest.organization,
            "strategy_id": manifest.strategy_id,
            "git_commit_sha": manifest.git_commit_sha,
            "data_checksums": manifest.data_checksums_sha256,
            "parameters": manifest.parameters,
            "metrics": manifest.metrics_summary,
            "timestamp_ns": manifest.execution_timestamp_ns,
            "timestamp_iso": manifest.execution_timestamp_iso,
            "platform": manifest.platform_info,
            "python_version": manifest.python_version,
            "signing_key_id": manifest.signing_key_id,
            "data_sources": manifest.data_sources,
            "notes": manifest.notes,
        }

    @staticmethod
    def _canonical_json(payload: Mapping[str, Any]) -> str:
        """Serialises deterministically, rejecting values that break interoperability.

        ``allow_nan=False`` matters: Python emits bare ``NaN`` / ``Infinity``,
        which are not valid JSON (RFC 8259) and are rejected by strict parsers
        in other languages. A manifest containing them could never be verified
        by an auditor using a different toolchain.
        """
        try:
            return json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except ValueError as exc:
            raise AuditManifestError(
                f"manifest is not canonically serialisable: {exc}. "
                "NaN/Infinity metrics cannot appear in an audit manifest."
            ) from exc

    def _digests(self, manifest: BacktestAuditManifest) -> Tuple[str, str]:
        canonical = self._canonical_json(self._canonical_payload(manifest)).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        mac = hmac.new(self._signing_key, canonical, hashlib.sha256).hexdigest()
        return digest, mac

    # ---------------------------------------------------------------- validation

    @staticmethod
    def _validate_inputs(
        strategy_id: str,
        git_commit_sha: str,
        data_checksums: Dict[str, str],
        metrics_summary: Dict[str, float],
    ) -> None:
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise AuditManifestError("strategy_id must be a non-empty string")
        if not isinstance(git_commit_sha, str) or not git_commit_sha.strip():
            raise AuditManifestError("git_commit_sha must be a non-empty string")
        for path, checksum in data_checksums.items():
            if not isinstance(checksum, str) or len(checksum) != 64:
                raise AuditManifestError(
                    f"data checksum for {path!r} is not a 64-hex-char SHA256: {checksum!r}"
                )
            try:
                int(checksum, 16)
            except ValueError as exc:
                raise AuditManifestError(
                    f"data checksum for {path!r} is not hexadecimal"
                ) from exc
        for name, value in metrics_summary.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AuditManifestError(f"metric {name!r} must be a real number")
            if not math.isfinite(value):
                raise AuditManifestError(
                    f"metric {name!r} is {value!r}; non-finite metrics cannot be "
                    "canonically serialised and would fail cross-tool verification"
                )

    # ------------------------------------------------------------------- build

    def build_manifest(
        self,
        strategy_id: str,
        git_commit_sha: str,
        data_checksums: Dict[str, str],
        parameters: Dict[str, Any],
        metrics_summary: Dict[str, float],
        data_sources: Optional[List[str]] = None,
        notes: str = "",
    ) -> BacktestAuditManifest:
        """Assembles, digests, and authenticates a manifest.

        Caller-supplied containers are deep-copied. Without this the manifest
        would alias the caller's dicts and silently change after signing.
        """
        self._validate_inputs(strategy_id, git_commit_sha, data_checksums, metrics_summary)

        manifest = BacktestAuditManifest(
            strategy_id=strategy_id,
            git_commit_sha=git_commit_sha,
            data_checksums_sha256=deepcopy(data_checksums),
            parameters=deepcopy(parameters),
            metrics_summary=deepcopy(metrics_summary),
            execution_timestamp_ns=time.time_ns(),
            platform_info=f"{platform.system()} {platform.release()} ({platform.machine()})",
            python_version=platform.python_version(),
            content_digest_sha256="",
            manifest_hmac_sha256="",
            signing_key_id=self.signing_key_id,
            organization=self.organization,
            data_sources=list(data_sources or []),
            notes=notes,
        )

        digest, mac = self._digests(manifest)
        manifest.content_digest_sha256 = digest
        manifest.manifest_hmac_sha256 = mac

        logger.info(
            f"Audit manifest built: strategy={strategy_id!r} "
            f"key_id={self.signing_key_id!r} digest={digest[:12]}..."
        )
        return manifest

    # ------------------------------------------------------------------ verify

    def verify_manifest(self, manifest: BacktestAuditManifest) -> bool:
        """Returns True only if the content digest AND the HMAC both match.

        Both comparisons are constant-time. The HMAC is the security-relevant
        check; the digest alone proves nothing about authenticity.
        """
        if manifest.signing_key_id != self.signing_key_id:
            logger.warning(
                f"Key ID mismatch: manifest signed with {manifest.signing_key_id!r}, "
                f"engine holds {self.signing_key_id!r}."
            )
            return False
        try:
            digest, mac = self._digests(manifest)
        except AuditManifestError:
            logger.warning("Manifest could not be canonicalised for verification.")
            return False

        digest_ok = hmac.compare_digest(manifest.content_digest_sha256, digest)
        mac_ok = hmac.compare_digest(manifest.manifest_hmac_sha256, mac)
        if digest_ok and not mac_ok:
            logger.error(
                "Content digest matches but HMAC does not - manifest was re-digested "
                "by a party without the signing key. Treat as forged."
            )
        return digest_ok and mac_ok

    # ------------------------------------------------------------ serialisation

    def export_manifest_json(self, manifest: BacktestAuditManifest) -> str:
        """Exports canonical JSON for archival.

        Built from ``_canonical_payload`` so the signed bytes and the archived
        bytes cannot drift apart.
        """
        payload = dict(self._canonical_payload(manifest))
        payload["content_digest_sha256"] = manifest.content_digest_sha256
        payload["manifest_hmac_sha256"] = manifest.manifest_hmac_sha256
        return self._canonical_json(payload)

    def load_manifest_from_json(self, json_str: str) -> BacktestAuditManifest:
        """Parses and verifies an archived manifest.

        Raises:
            AuditManifestError: If the JSON is malformed or missing fields.
            ManifestVerificationError: If verification fails.
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise AuditManifestError(f"manifest is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise AuditManifestError("manifest JSON must be an object")

        required = (
            "strategy_id", "git_commit_sha", "data_checksums", "parameters",
            "metrics", "timestamp_ns", "platform", "python_version",
            "signing_key_id", "content_digest_sha256", "manifest_hmac_sha256", "org",
        )
        missing = [k for k in required if k not in data]
        if missing:
            raise AuditManifestError(f"manifest missing required field(s): {missing}")

        if not isinstance(data["timestamp_ns"], int) or isinstance(data["timestamp_ns"], bool):
            raise AuditManifestError("timestamp_ns must be an integer nanosecond epoch")

        manifest = BacktestAuditManifest(
            strategy_id=data["strategy_id"],
            git_commit_sha=data["git_commit_sha"],
            data_checksums_sha256=data["data_checksums"],
            parameters=data["parameters"],
            metrics_summary=data["metrics"],
            execution_timestamp_ns=data["timestamp_ns"],
            platform_info=data["platform"],
            python_version=data["python_version"],
            content_digest_sha256=data["content_digest_sha256"],
            manifest_hmac_sha256=data["manifest_hmac_sha256"],
            signing_key_id=data["signing_key_id"],
            organization=data["org"],
            data_sources=data.get("data_sources", []),
            notes=data.get("notes", ""),
        )
        if not self.verify_manifest(manifest):
            raise ManifestVerificationError(
                "Manifest verification failed - content altered, wrong signing key, "
                "or forged digest. Do not treat this record as authentic."
            )
        return manifest
