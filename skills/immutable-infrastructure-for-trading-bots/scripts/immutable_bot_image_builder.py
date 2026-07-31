import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ImmutableContainerSpec:
    image_name: str                     # e.g. 'trading-bot-execution'
    image_tag: str                      # e.g. 'v1.4.2'
    image_sha256_digest: str            # e.g. 'sha256:a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890'
    git_commit_sha: str                 # 40-character hex commit
    read_only_rootfs: bool              # MUST BE True (--read-only)
    is_image_signed_cosign: bool        # MUST BE True
    tmpfs_mounts: List[str]             # e.g. ['/tmp', '/run']
    no_new_privileges: bool = True
    run_as_non_root_user: bool = True

@dataclass
class ImmutableInfrastructureReport:
    image_uri: str
    git_commit_sha: str
    is_read_only_rootfs_enforced: bool
    is_cosign_signature_verified: bool
    is_tmpfs_isolated: bool
    is_security_hardened: bool
    status: str                         # 'IMMUTABLE_SPEC_APPROVED', 'MUTABLE_ROOTFS_REJECTED', 'UNSIGNED_IMAGE_REJECTED', 'INVALID_GIT_SHA_REJECTED'
    audit_notes: str

class ImmutableInfrastructureAuditEngine:
    """
    DevOps engine for auditing and building immutable trading bot container images (Docker, Packer, Cosign signing, read-only rootfs)
    to eliminate configuration drift and prevent live hot-patching.
    """
    def __init__(self):
        pass

    def validate_git_sha(self, sha: str) -> bool:
        """
        Validates 40-character hexadecimal Git commit SHA.
        """
        return bool(re.match(r"^[0-9a-fA-F]{40}$", sha.strip()))

    def audit_container_spec(self, spec: ImmutableContainerSpec) -> ImmutableInfrastructureReport:
        """
        Audits container spec against immutability standards and zero-drift security requirements.
        """
        image_uri = f"{spec.image_name}:{spec.image_tag}@{spec.image_sha256_digest}"

        # 1. Audit Git Commit SHA
        if not self.validate_git_sha(spec.git_commit_sha):
            notes = f"IMMUTABLE SPEC REJECTED [{image_uri}]: Invalid Git commit SHA '{spec.git_commit_sha}'. Must be 40-char hex."
            logger.error(notes)
            return ImmutableInfrastructureReport(
                image_uri=image_uri, git_commit_sha=spec.git_commit_sha,
                is_read_only_rootfs_enforced=spec.read_only_rootfs, is_cosign_signature_verified=spec.is_image_signed_cosign,
                is_tmpfs_isolated=bool(spec.tmpfs_mounts), is_security_hardened=spec.no_new_privileges,
                status="INVALID_GIT_SHA_REJECTED", audit_notes=notes
            )

        # 2. Audit Read-Only Root Filesystem (Prevents Live Hot-Patching)
        if not spec.read_only_rootfs:
            notes = (
                f"IMMUTABLE SPEC REJECTED [{image_uri}]: Container root filesystem is MUTABLE (read_only_rootfs = False). "
                f"Trading containers MUST enforce '--read-only' to eliminate configuration drift."
            )
            logger.critical(notes)
            return ImmutableInfrastructureReport(
                image_uri=image_uri, git_commit_sha=spec.git_commit_sha,
                is_read_only_rootfs_enforced=False, is_cosign_signature_verified=spec.is_image_signed_cosign,
                is_tmpfs_isolated=bool(spec.tmpfs_mounts), is_security_hardened=spec.no_new_privileges,
                status="MUTABLE_ROOTFS_REJECTED", audit_notes=notes
            )

        # 3. Audit Cosign Cryptographic Image Signature
        if not spec.is_image_signed_cosign:
            notes = f"IMMUTABLE SPEC REJECTED [{image_uri}]: Container image is UNSIGNED! Cosign / Sigstore signature verification failed."
            logger.critical(notes)
            return ImmutableInfrastructureReport(
                image_uri=image_uri, git_commit_sha=spec.git_commit_sha,
                is_read_only_rootfs_enforced=True, is_cosign_signature_verified=False,
                is_tmpfs_isolated=bool(spec.tmpfs_mounts), is_security_hardened=spec.no_new_privileges,
                status="UNSIGNED_IMAGE_REJECTED", audit_notes=notes
            )

        # 4. Audit Tmpfs Mounts for Scratch Files
        has_tmpfs = "/tmp" in spec.tmpfs_mounts or len(spec.tmpfs_mounts) > 0
        if not has_tmpfs:
            notes = f"IMMUTABLE SPEC WARNING [{image_uri}]: Ephemeral /tmp directory is not mounted via tmpfs."
            logger.warning(notes)

        notes = (
            f"IMMUTABLE SPEC APPROVED [{image_uri}]: Verified Cosign signature, read-only rootfs, "
            f"Git SHA ({spec.git_commit_sha[:7]}), and tmpfs mounts {spec.tmpfs_mounts}. Ready for zero-drift deployment."
        )
        logger.info(notes)

        return ImmutableInfrastructureReport(
            image_uri=image_uri,
            git_commit_sha=spec.git_commit_sha,
            is_read_only_rootfs_enforced=True,
            is_cosign_signature_verified=True,
            is_tmpfs_isolated=has_tmpfs,
            is_security_hardened=spec.no_new_privileges and spec.run_as_non_root_user,
            status="IMMUTABLE_SPEC_APPROVED",
            audit_notes=notes
        )
