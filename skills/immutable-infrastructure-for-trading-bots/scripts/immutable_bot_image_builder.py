"""Pre-deployment immutability audit for live trading bot container specs.

This module audits a *declared* container deployment spec against the
immutability and anti-hot-patching controls a live trading host should enforce.

Scope and honesty boundary
--------------------------
This engine performs **no** network, registry, or cryptographic operation. It
reads the deployment spec your pipeline is about to apply and reports whether
that spec, as written, still permits code in a live trading container to be
changed without a rebuild. Fields such as ``is_image_signed_cosign`` and
``source_revision_annotation`` are *attestations recorded by the pipeline* --
the engine trusts them and says so. Actual signature verification is a separate
step your pipeline must run, e.g.::

    cosign verify "$IMAGE@$DIGEST"
        --certificate-identity=<expected signer identity>
        --certificate-oidc-issuer=<expected OIDC issuer>

Cosign 2.0 made ``--certificate-identity`` and ``--certificate-oidc-issuer``
mandatory for keyless verification precisely because a signature with no
asserted identity proves nothing about *who* signed. Feeding this engine an
``is_image_signed_cosign=True`` that was never backed by such a command
reproduces that same failure at the spec layer.

Passing this audit is therefore necessary but not sufficient for a safe
deployment. See ``references/standards.md`` for the sources behind each control.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)
# Library convention: emit nothing unless the host application configures
# logging. The full finding set is always on the returned report, so a caller
# that has not set up handlers still gets every violation programmatically.
logger.addHandler(logging.NullHandler())

__all__ = [
    "ImmutableSpecError",
    "ImmutableContainerSpec",
    "ImmutabilityViolation",
    "ImmutableInfrastructureReport",
    "ImmutableInfrastructureAuditEngine",
    "STATUS_APPROVED",
]

STATUS_APPROVED = "IMMUTABLE_SPEC_APPROVED"

# OCI image-spec descriptor grammar: for sha256 the encoded portion MUST match
# /[a-f0-9]{64}/ and for sha512 /[a-f0-9]{128}/. Uppercase hex is not valid.
_DIGEST_PATTERN = re.compile(r"^(?:sha256:[a-f0-9]{64}|sha512:[a-f0-9]{128})$")

# Git object ids are 40 hex chars under SHA-1 and 64 under the SHA-256 object
# format, which repositories may already be using.
_GIT_SHA_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")

# Tag names that conventionally float across rebuilds. This is a hygiene signal
# only -- digest pinning, checked separately, is the control that actually binds
# a deployment to specific image content.
_DEFAULT_MUTABLE_TAGS: Tuple[str, ...] = (
    "latest", "stable", "main", "master", "head",
    "dev", "develop", "staging", "prod", "production", "edge", "nightly",
)

# tmpfs mount options that stop a writable scratch mount from becoming a place
# to drop and execute a payload. Passed through to `mount -t tmpfs -o`.
_DEFAULT_REQUIRED_TMPFS_OPTIONS: Tuple[str, ...] = ("noexec", "nosuid")

_DEFAULT_REQUIRED_TMPFS_PATHS: Tuple[str, ...] = ("/tmp",)

_SEVERITY_RANK: Dict[str, int] = {"CRITICAL": 0, "HIGH": 1}


class ImmutableSpecError(ValueError):
    """Raised when a container spec is structurally unusable.

    Distinct from an audit *violation*: a violation is a well-formed spec that
    fails policy, this is a spec the auditor cannot evaluate at all. A
    deployment gate must never treat the two the same -- an unevaluable spec is
    not an approved spec.
    """


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ImmutableSpecError(
            f"{field_name} must be a str, got {type(value).__name__!r}"
        )
    stripped = value.strip()
    if not stripped:
        raise ImmutableSpecError(f"{field_name} must be a non-empty string")
    return stripped


def _require_bool(value: object, field_name: str) -> bool:
    # Explicit isinstance check, not truthiness: a spec loaded from YAML where
    # `read_only_rootfs: "false"` parsed as the string "false" is truthy, and
    # silently approving that would defeat the control being audited.
    if not isinstance(value, bool):
        raise ImmutableSpecError(
            f"{field_name} must be a bool, got {type(value).__name__!r}"
        )
    return value


def _require_str_list(value: object, field_name: str) -> List[str]:
    # str is deliberately excluded: a bare "/tmp" would otherwise iterate into
    # single characters and silently audit a mount list that does not exist.
    if not isinstance(value, (list, tuple)):
        raise ImmutableSpecError(
            f"{field_name} must be a list or tuple of str, "
            f"got {type(value).__name__!r}"
        )
    return [_require_str(item, f"{field_name}[{i}]") for i, item in enumerate(value)]


@dataclass
class ImmutableContainerSpec:
    """A container deployment spec about to be applied to a trading host.

    Args:
        image_name: Repository name, e.g. ``ghcr.io/acme/trading-bot-execution``.
        image_tag: Human-readable tag. Informational only -- ``image_sha256_digest``
            is what binds the deployment to specific content.
        image_sha256_digest: OCI content digest, ``sha256:<64 hex>`` (``sha512:``
            with 128 hex is also accepted). Lowercase, per the OCI descriptor spec.
        git_commit_sha: Source commit the image was built from, 40 hex (SHA-1) or
            64 hex (SHA-256 object format).
        read_only_rootfs: Whether the container runs with a read-only root
            filesystem (``--read-only`` / ``readOnlyRootFilesystem: true``).
        is_image_signed_cosign: Whether the pipeline has *already run and passed*
            an identity-scoped ``cosign verify`` for this digest. This engine
            takes the claim on trust; see the module docstring.
        tmpfs_mounts: Paths mounted as tmpfs, e.g. ``["/tmp", "/run"]``. Required
            because a read-only rootfs leaves the process with nowhere to write.
        no_new_privileges: Whether ``no-new-privileges`` / the kernel
            ``no_new_privs`` flag is set.
        run_as_non_root_user: Whether the container runs as a non-root UID.
        tmpfs_options: Mount options declared per tmpfs path, e.g.
            ``{"/tmp": ["rw", "noexec", "nosuid", "size=64m"]}``. Omit a path to
            state that its options are unknown -- the auditor then warns rather
            than asserting the mount is safe.
        writable_volumes: Container paths backed by a read-write bind mount or
            volume. Docker's own reference states ``--read-only`` prohibits
            "writes to locations other than the specified volumes", so each entry
            here is a hole in the read-only rootfs.
        source_revision_annotation: Value of the image's
            ``org.opencontainers.image.revision`` annotation as read from the
            manifest, if the pipeline resolved it. Used to check that the commit
            being claimed is the commit the image actually records.
    """

    image_name: str
    image_tag: str
    image_sha256_digest: str
    git_commit_sha: str
    read_only_rootfs: bool
    is_image_signed_cosign: bool
    tmpfs_mounts: List[str]
    no_new_privileges: bool = True
    run_as_non_root_user: bool = True
    tmpfs_options: Dict[str, List[str]] = field(default_factory=dict)
    writable_volumes: List[str] = field(default_factory=list)
    source_revision_annotation: Optional[str] = None

    def __post_init__(self) -> None:
        self.image_name = _require_str(self.image_name, "image_name")
        self.image_tag = _require_str(self.image_tag, "image_tag")
        # Digest and commit sha are *format*-validated by the audit, not here:
        # a malformed digest is a policy violation to be reported alongside the
        # others, not an exception that hides every remaining finding.
        self.image_sha256_digest = _require_str(
            self.image_sha256_digest, "image_sha256_digest")
        self.git_commit_sha = _require_str(self.git_commit_sha, "git_commit_sha")
        self.read_only_rootfs = _require_bool(self.read_only_rootfs, "read_only_rootfs")
        self.is_image_signed_cosign = _require_bool(
            self.is_image_signed_cosign, "is_image_signed_cosign")
        self.tmpfs_mounts = _require_str_list(self.tmpfs_mounts, "tmpfs_mounts")
        self.no_new_privileges = _require_bool(
            self.no_new_privileges, "no_new_privileges")
        self.run_as_non_root_user = _require_bool(
            self.run_as_non_root_user, "run_as_non_root_user")
        self.writable_volumes = _require_str_list(
            self.writable_volumes, "writable_volumes")

        if not isinstance(self.tmpfs_options, dict):
            raise ImmutableSpecError(
                "tmpfs_options must be a dict, got "
                f"{type(self.tmpfs_options).__name__!r}")
        self.tmpfs_options = {
            _require_str(path, "tmpfs_options key"):
                _require_str_list(opts, f"tmpfs_options[{path!r}]")
            for path, opts in self.tmpfs_options.items()
        }

        if self.source_revision_annotation is not None:
            self.source_revision_annotation = _require_str(
                self.source_revision_annotation, "source_revision_annotation")


@dataclass(frozen=True)
class ImmutabilityViolation:
    """One failed control, with the concrete flag that fixes it."""

    code: str
    severity: str  # 'CRITICAL' or 'HIGH'
    detail: str
    remediation: str


@dataclass
class ImmutableInfrastructureReport:
    """Result of auditing one container spec.

    ``violations`` is ordered most severe first, ties broken by evaluation
    order, and ``status`` is the code of the first entry (or
    ``IMMUTABLE_SPEC_APPROVED`` when there are none). Callers gating a
    deployment should branch on ``approved`` and surface the whole
    ``violations`` list -- one spec can breach several controls at once, and
    reporting them one rebuild at a time wastes deployment windows.
    """

    image_uri: str
    git_commit_sha: str
    is_digest_pinned: bool
    is_read_only_rootfs_enforced: bool
    is_signature_attested: bool
    is_source_revision_bound: bool
    is_tmpfs_isolated: bool
    is_security_hardened: bool
    status: str
    violations: List[ImmutabilityViolation]
    warnings: List[str]
    audit_notes: str

    @property
    def approved(self) -> bool:
        return self.status == STATUS_APPROVED

    @property
    def violation_codes(self) -> List[str]:
        return [v.code for v in self.violations]


class ImmutableInfrastructureAuditEngine:
    """Audits a declared container spec for live-trading immutability controls.

    Every control is evaluated on every call; the engine never short-circuits on
    the first failure. Violations are returned most severe first, ties broken by
    a fixed evaluation order, so ``status`` is deterministic for a given spec.
    """

    def __init__(
        self,
        required_tmpfs_paths: Sequence[str] = _DEFAULT_REQUIRED_TMPFS_PATHS,
        required_tmpfs_options: Sequence[str] = _DEFAULT_REQUIRED_TMPFS_OPTIONS,
        mutable_tag_names: Sequence[str] = _DEFAULT_MUTABLE_TAGS,
        require_source_revision_annotation: bool = False,
    ) -> None:
        """
        Args:
            required_tmpfs_paths: Paths that must appear in ``tmpfs_mounts``.
                A missing path is a warning, not a violation: it breaks the
                container at first write rather than weakening immutability.
            required_tmpfs_options: Options every *declared* tmpfs mount must
                carry. A mount with no declared options is warned about, not
                failed -- the engine cannot see the real runtime.
            mutable_tag_names: Tag names treated as floating.
            require_source_revision_annotation: When True, a spec that carries no
                ``org.opencontainers.image.revision`` value is rejected rather
                than warned about. Enable once your builder sets the annotation.
        """
        self.required_tmpfs_paths = tuple(required_tmpfs_paths)
        self.required_tmpfs_options = tuple(required_tmpfs_options)
        self.mutable_tag_names = frozenset(t.lower() for t in mutable_tag_names)
        self.require_source_revision_annotation = require_source_revision_annotation

    def validate_git_sha(self, sha: str) -> bool:
        """True if ``sha`` is a full Git object id (40 hex SHA-1 or 64 hex SHA-256).

        Abbreviated ids are rejected on purpose: a deployment record has to name
        exactly one commit, and short ids are ambiguous by construction.
        """
        if not isinstance(sha, str):
            return False
        return bool(_GIT_SHA_PATTERN.fullmatch(sha.strip()))

    def validate_image_digest(self, digest: str) -> bool:
        """True if ``digest`` is a well-formed lowercase OCI content digest."""
        if not isinstance(digest, str):
            return False
        return bool(_DIGEST_PATTERN.fullmatch(digest.strip()))

    def audit_container_spec(
        self, spec: ImmutableContainerSpec
    ) -> ImmutableInfrastructureReport:
        """Audit ``spec`` against every immutability control and report all findings.

        Raises:
            ImmutableSpecError: if ``spec`` is not an ``ImmutableContainerSpec``.
        """
        if not isinstance(spec, ImmutableContainerSpec):
            raise ImmutableSpecError(
                f"spec must be an ImmutableContainerSpec, got {type(spec).__name__!r}")

        image_uri = f"{spec.image_name}:{spec.image_tag}@{spec.image_sha256_digest}"
        violations: List[ImmutabilityViolation] = []
        warnings: List[str] = []

        # 1. Source commit must name exactly one revision.
        if not self.validate_git_sha(spec.git_commit_sha):
            violations.append(ImmutabilityViolation(
                code="INVALID_GIT_SHA_REJECTED",
                severity="HIGH",
                detail=(
                    f"git_commit_sha {spec.git_commit_sha!r} is not a full Git object "
                    f"id (40 hex for SHA-1, 64 hex for the SHA-256 object format). "
                    f"The deployment cannot be traced back to reviewed source."
                ),
                remediation="Record the output of `git rev-parse HEAD` at build time.",
            ))

        # 2. Digest pinning is the control that binds the deployment to content.
        #    Kubernetes: 'Tags can be moved to point to different images, but
        #    digests are fixed.'
        is_digest_pinned = self.validate_image_digest(spec.image_sha256_digest)
        if not is_digest_pinned:
            violations.append(ImmutabilityViolation(
                code="UNPINNED_DIGEST_REJECTED",
                severity="CRITICAL",
                detail=(
                    f"image_sha256_digest {spec.image_sha256_digest!r} is not a valid "
                    f"OCI content digest (expected lowercase 'sha256:<64 hex>'). "
                    f"Without a digest the runtime resolves the tag at pull time, so "
                    f"two hosts starting minutes apart can run different code."
                ),
                remediation=(
                    "Deploy by digest: `image: name@sha256:<64 hex>`, resolved once in "
                    "CI and passed through unchanged."
                ),
            ))

        # 3. Floating tag names. Hygiene signal only -- check 2 is the real control.
        if spec.image_tag.lower() in self.mutable_tag_names:
            violations.append(ImmutabilityViolation(
                code="MUTABLE_TAG_REJECTED",
                severity="HIGH",
                detail=(
                    f"image_tag {spec.image_tag!r} is a floating tag. NIST SP 800-190 "
                    f"warns such a tag 'is only a label attached to the image and not a "
                    f"guarantee of freshness'. A rollback that re-points the tag "
                    f"silently changes what a restart runs."
                ),
                remediation="Tag each build immutably, e.g. `v1.4.2` or `<short-sha>`.",
            ))

        # 4. The image must record the commit it claims to be built from.
        is_source_revision_bound = False
        if spec.source_revision_annotation is None:
            message = (
                "org.opencontainers.image.revision is absent from the image manifest, "
                "so git_commit_sha is unverified pipeline metadata rather than a "
                "property of the image."
            )
            if self.require_source_revision_annotation:
                violations.append(ImmutabilityViolation(
                    code="SOURCE_REVISION_MISSING_REJECTED",
                    severity="HIGH",
                    detail=message,
                    remediation=(
                        "Set the annotation at build time, e.g. `--label "
                        "org.opencontainers.image.revision=$(git rev-parse HEAD)`."
                    ),
                ))
            else:
                warnings.append(message)
        elif spec.source_revision_annotation.lower() != spec.git_commit_sha.lower():
            # An abbreviated annotation is the common false-positive here, and it
            # needs a different fix from a genuinely different commit.
            if spec.git_commit_sha.lower().startswith(
                    spec.source_revision_annotation.lower()):
                remediation = (
                    "The annotation looks like an abbreviated id. Build with the full "
                    "`git rev-parse HEAD`, not `--short` -- a deployment record must "
                    "name exactly one commit."
                )
            else:
                remediation = (
                    "Rebuild from the reviewed commit; do not re-tag an existing image."
                )
            violations.append(ImmutabilityViolation(
                code="SOURCE_REVISION_MISMATCH_REJECTED",
                severity="CRITICAL",
                detail=(
                    f"Image annotation org.opencontainers.image.revision="
                    f"{spec.source_revision_annotation!r} does not match the declared "
                    f"git_commit_sha={spec.git_commit_sha!r}. The artifact being "
                    f"deployed was not built from the commit under review."
                ),
                remediation=remediation,
            ))
        else:
            is_source_revision_bound = True

        # 5. Read-only root filesystem: the control that stops in-place edits.
        if not spec.read_only_rootfs:
            violations.append(ImmutabilityViolation(
                code="MUTABLE_ROOTFS_REJECTED",
                severity="CRITICAL",
                detail=(
                    "read_only_rootfs is False, so strategy code inside the running "
                    "container can be edited in place. NIST SP 800-190: containers "
                    "'should be operated as stateless entities that are deployed but "
                    "not changed'."
                ),
                remediation=(
                    "Run with `--read-only` (Docker) or "
                    "`securityContext.readOnlyRootFilesystem: true` (Kubernetes)."
                ),
            ))

        # 6. Writable host mounts defeat check 5. Docker's reference is explicit
        #    that --read-only prohibits writes 'to locations other than the
        #    specified volumes'.
        if spec.writable_volumes:
            violations.append(ImmutabilityViolation(
                code="WRITABLE_HOST_MOUNT_REJECTED",
                severity="CRITICAL",
                detail=(
                    f"Read-write mounts {spec.writable_volumes} are exposed inside the "
                    f"container. `--read-only` does not cover mounted volumes, so a "
                    f"bind mount over the code directory restores exactly the "
                    f"hot-patching path this audit exists to close. NIST SP 800-190: "
                    f"'Very rarely should containers mount local file systems on a host.'"
                ),
                remediation=(
                    "Bake code into the image. Mount only genuine state read-write, and "
                    "mount anything else `:ro`."
                ),
            ))

        # 7. Signature attestation (declared by the pipeline, not verified here).
        if not spec.is_image_signed_cosign:
            violations.append(ImmutabilityViolation(
                code="UNSIGNED_IMAGE_REJECTED",
                severity="CRITICAL",
                detail=(
                    "No Cosign signature verification is attested for this digest. "
                    "NIST SP 800-190 calls for 'Validation of image signatures before "
                    "image execution to ensure images are from trusted sources and have "
                    "not been tampered with'."
                ),
                remediation=(
                    "Run `cosign verify <image>@<digest> --certificate-identity=<signer> "
                    "--certificate-oidc-issuer=<issuer>` in the deploy job and record "
                    "the result here."
                ),
            ))

        # 8. Privilege hardening. Evaluated once, and a real gate rather than a
        #    field that silently reads False on an approved spec.
        is_security_hardened = spec.no_new_privileges and spec.run_as_non_root_user
        if not is_security_hardened:
            missing = []
            if not spec.no_new_privileges:
                missing.append("no_new_privileges is False")
            if not spec.run_as_non_root_user:
                missing.append("run_as_non_root_user is False")
            violations.append(ImmutabilityViolation(
                code="PRIVILEGE_HARDENING_REJECTED",
                severity="HIGH",
                detail=(
                    f"{'; '.join(missing)}. A root process that can escalate is able to "
                    f"remount the rootfs read-write, which turns the read-only setting "
                    f"into a formality."
                ),
                remediation=(
                    "Set `--security-opt no-new-privileges` and a non-root `--user`, or "
                    "`allowPrivilegeEscalation: false` with `runAsNonRoot: true`. Note "
                    "that Kubernetes forces allowPrivilegeEscalation true for privileged "
                    "containers and for CAP_SYS_ADMIN, so drop those too."
                ),
            ))

        # 9. Ephemeral scratch space. Warnings, not violations: a missing tmpfs
        #    crashes the bot at first write rather than weakening immutability,
        #    and undeclared options are unknown, not proven unsafe.
        declared_mounts = set(spec.tmpfs_mounts)
        missing_paths = [p for p in self.required_tmpfs_paths if p not in declared_mounts]
        if missing_paths:
            warnings.append(
                f"Required tmpfs path(s) {missing_paths} are not in tmpfs_mounts "
                f"{spec.tmpfs_mounts}. Under a read-only rootfs the process has nowhere "
                f"to write and will fail on its first temp file or log rotation."
            )
        is_tmpfs_isolated = not missing_paths

        # A typo in a tmpfs_options key is silent otherwise: the real mount falls
        # through to "no declared options" while the operator believes it was
        # hardened. Name the orphans explicitly.
        orphan_option_keys = sorted(set(spec.tmpfs_options) - declared_mounts)
        if orphan_option_keys:
            warnings.append(
                f"tmpfs_options declares options for {orphan_option_keys}, which are not "
                f"in tmpfs_mounts {spec.tmpfs_mounts}. Those options harden nothing; "
                f"check for a path typo."
            )

        # Dedupe while preserving order: a repeated path must not produce the same
        # finding twice.
        for mount in dict.fromkeys(spec.tmpfs_mounts):
            declared_options = spec.tmpfs_options.get(mount)
            if declared_options is None:
                if self.required_tmpfs_options:
                    warnings.append(
                        f"tmpfs mount {mount!r} has no declared options, so it cannot be "
                        f"confirmed to be mounted "
                        f"{','.join(self.required_tmpfs_options)}. A writable, "
                        f"executable tmpfs is a place to drop and run a payload."
                    )
                continue
            normalized = {opt.split("=", 1)[0].strip().lower() for opt in declared_options}
            absent = [opt for opt in self.required_tmpfs_options if opt not in normalized]
            if absent:
                violations.append(ImmutabilityViolation(
                    code="EXECUTABLE_TMPFS_REJECTED",
                    severity="HIGH",
                    detail=(
                        f"tmpfs mount {mount!r} declares options {declared_options} and "
                        f"is missing {absent}. Writable and executable scratch space lets "
                        f"an attacker who reaches the container run new code despite the "
                        f"read-only rootfs."
                    ),
                    remediation=(
                        f"Mount it as `--tmpfs {mount}:rw,"
                        f"{','.join(self.required_tmpfs_options)},size=<limit>`."
                    ),
                ))

        # Most severe first, ties broken by evaluation order (stable sort). An
        # alert keyed on `status` must not read HIGH while a CRITICAL breach is
        # sitting further down the list.
        violations.sort(key=lambda v: _SEVERITY_RANK[v.severity])

        if violations:
            status = violations[0].code
            notes = (
                f"IMMUTABLE SPEC REJECTED [{image_uri}]: {len(violations)} violation(s): "
                f"{', '.join(v.code for v in violations)}."
            )
            logger.error(notes)
        else:
            status = STATUS_APPROVED
            notes = (
                f"IMMUTABLE SPEC APPROVED [{image_uri}]: digest pinned, read-only rootfs, "
                f"no read-write host mounts, signature attested, privilege hardening set, "
                f"source revision {spec.git_commit_sha[:7]}. Signature verification itself "
                f"is attested by the pipeline, not performed here."
            )
            logger.info(notes)

        for warning in warnings:
            logger.warning("IMMUTABLE SPEC WARNING [%s]: %s", image_uri, warning)

        return ImmutableInfrastructureReport(
            image_uri=image_uri,
            git_commit_sha=spec.git_commit_sha,
            is_digest_pinned=is_digest_pinned,
            is_read_only_rootfs_enforced=spec.read_only_rootfs,
            is_signature_attested=spec.is_image_signed_cosign,
            is_source_revision_bound=is_source_revision_bound,
            is_tmpfs_isolated=is_tmpfs_isolated,
            is_security_hardened=is_security_hardened,
            status=status,
            violations=violations,
            warnings=warnings,
            audit_notes=notes,
        )
