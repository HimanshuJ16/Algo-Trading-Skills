"""environment-parity-dev-staging-production: deployment-gate auditor comparing a
DEV/STAGING/PRODUCTION environment against an approved release specification across
five parity vectors.

The five vectors are: Python runtime release, dependency lockfile SHA-256, database
schema revision head(s), broker endpoint mode, and presence of mandatory environment
variables.

This module is a **gate**, not a report generator. The expensive failure mode is a
false PASS -- approving a promotion that should have been blocked -- so every input is
validated up front and ambiguous or missing evidence raises rather than being audited
as "equal". Two environments that both failed to resolve a lockfile hash must not
compare equal and score 100% parity; that is the specific bug this validation exists
to prevent.

What ``production_baseline`` means
----------------------------------
It is the **release specification** for the deployment being gated -- the Python
release, lockfile hash and schema head that this release is defined to run on. It is
*not* a snapshot of whatever is currently live in production. Reading it the other way
makes the gate block every forward migration, because staging is normally ahead of
production while a migration is being validated.

Limitations (documented, deliberate)
------------------------------------
- **Declared specs only.** Every value is supplied by the caller. Nothing here connects
  to a host, an interpreter, a database or a broker, so the audit is exactly as
  trustworthy as the collection step that produced the ``EnvironmentSpec``. A spec
  hand-written to match the baseline passes.
- **A matching lockfile hash is not a matching installed environment.** The hash proves
  the lockfile *file* is byte-identical. Dependency specifiers carry environment
  markers -- ``python_version``, ``sys_platform``, ``platform_machine`` and others
  (PEP 508 / packaging.python.org "Dependency specifiers") -- so the same file resolves
  to different distributions on different hosts. Verifying the installed set is a
  separate job; see the ``dependency-pinning-and-reproducible-builds`` skill.
- **Environment variable *values* are never compared.** Only presence and
  non-emptiness. ``DATABASE_URL`` and ``BROKER_API_KEY`` are *supposed* to differ
  between environments; comparing them would be wrong, and reporting them would leak
  secrets.
- **Multiple schema heads are supported, but completeness is assumed.** Alembic permits
  multiple simultaneous heads on a branched history and ``alembic heads`` prints each
  one. ``db_schema_revision`` therefore accepts several revisions (comma- or
  whitespace-separated) and compares them order-independently. It cannot detect that
  the caller passed only *one* of two real heads -- pass the full ``alembic heads``
  output, not a truncated first line.
- **No severity weighting.** ``parity_score_pct`` is diagnostic only. The gate is
  ``is_deployment_allowed``, and a single failed vector blocks regardless of score.
"""
import hashlib
import logging
import platform
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence

logger = logging.getLogger(__name__)

ENV_DEV = "DEV"
ENV_STAGING = "STAGING"
ENV_PRODUCTION = "PRODUCTION"

#: The only environment names this gate recognises. A free-form name is rejected
#: rather than guessed at: classifying an unrecognised name as "not production" would
#: let a genuinely live environment wired to a testnet endpoint pass the endpoint
#: check, approving a release that silently trades against paper.
VALID_ENV_NAMES: FrozenSet[str] = frozenset({ENV_DEV, ENV_STAGING, ENV_PRODUCTION})

MODE_TESTNET = "TESTNET"
MODE_MAINNET = "MAINNET"
VALID_ENDPOINT_MODES: FrozenSet[str] = frozenset({MODE_TESTNET, MODE_MAINNET})

#: Which broker endpoint mode each environment is required to be wired to.
EXPECTED_ENDPOINT_MODE_BY_ENV: Dict[str, str] = {
    ENV_DEV: MODE_TESTNET,
    ENV_STAGING: MODE_TESTNET,
    ENV_PRODUCTION: MODE_MAINNET,
}

#: Default mandatory environment variables. A starting point for a trading deployment,
#: not an exhaustive inventory -- override via the constructor to match your own
#: configuration schema.
DEFAULT_REQUIRED_ENV_VAR_KEYS: Sequence[str] = (
    "BROKER_API_KEY",
    "MAX_POSITION_LIMIT",
    "DATABASE_URL",
)

STATUS_PASSED = "PARITY_VERIFIED_PASSED"
STATUS_BLOCKED = "PARITY_VIOLATION_BLOCKED"

#: Number of leading hash characters shown in the report. The full 64-character digest
#: is always what gets compared; only the display is abbreviated.
HASH_DISPLAY_PREFIX_LEN = 12

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

#: Requires major.minor.patch. ``"3.11"`` is rejected on purpose: two environments both
#: declaring ``"3.11"`` would compare equal while running 3.11.2 and 3.11.8, which is
#: precisely the drift this vector exists to catch. Suffixes are allowed
#: (``"3.13.0rc1"``, ``"3.11.8+"``).
_PYTHON_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")

_LOCKFILE_CHUNK_BYTES = 1 << 20


def sha256_of_lockfile(lockfile_path: str) -> str:
    """Return the lowercase hex SHA-256 of a lockfile, read in **binary** mode.

    Binary mode is not incidental. A ``requirements.lock`` checked out on Windows with
    ``core.autocrlf=true`` has CRLF line endings while the same file on a Linux build
    host has LF, so the two hash differently despite identical content. Hashing in text
    mode would paper over that by normalising newlines -- and would then also hide a
    genuine content change that only altered line endings. Keep the binary hash and fix
    the checkout instead (``core.autocrlf=input``, or a ``.gitattributes`` entry marking
    the lockfile ``-text``).

    Args:
        lockfile_path: Path to the lockfile to digest.

    Returns:
        The 64-character lowercase hex digest.

    Raises:
        FileNotFoundError: If the path does not exist.
        OSError: If the file cannot be read.
    """
    digest = hashlib.sha256()
    with open(lockfile_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_LOCKFILE_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_python_version() -> str:
    """Return the running interpreter's release as ``'major.minor.patch'``.

    Matches the format :class:`EnvironmentSpec` requires for ``python_version``.
    """
    return platform.python_version()


def _normalize_schema_revisions(raw: str) -> FrozenSet[str]:
    """Split a schema-revision declaration into an order-independent set.

    Alembic permits multiple simultaneous heads on a branched history and prints one
    per line, in no guaranteed order. Comparing the joined string directly would report
    drift between ``"a, b"`` and ``"b, a"`` -- identical database states.
    """
    return frozenset(token for token in re.split(r"[,\s]+", raw.strip()) if token)


def _require_non_blank(value: str, field_name: str, env_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{env_name or '<unnamed>'}: '{field_name}' is missing or blank. A parity "
            f"gate must not treat absent evidence as a match -- two environments that "
            f"both failed to resolve this field would otherwise compare equal and "
            f"score 100% parity. Resolve the real value, or fail the pipeline step "
            f"that collects it."
        )
    return value.strip()


@dataclass
class EnvironmentSpec:
    """Declared configuration of one environment.

    All string fields are normalised in place on construction: ``env_name`` and
    ``broker_endpoint_mode`` are upper-cased, ``lockfile_sha256`` is lower-cased, and
    surrounding whitespace is stripped throughout.

    Raises:
        ValueError: If any field is blank, or if ``env_name``, ``broker_endpoint_mode``,
            ``python_version`` or ``lockfile_sha256`` does not match its required form.
    """

    env_name: str                        # 'DEV', 'STAGING' or 'PRODUCTION'
    python_version: str                  # 'major.minor.patch', e.g. '3.11.8'
    lockfile_sha256: str                 # 64 hex chars, e.g. sha256_of_lockfile(path)
    db_schema_revision: str              # one head, or several: 'a1b2c3d4, e5f6a7b8'
    broker_endpoint_mode: str            # 'TESTNET' or 'MAINNET'

    #: Environment variable name -> value. Values are read for emptiness only, never
    #: compared across environments and never copied into the report. ``repr`` is
    #: suppressed so a traceback, a CI log line or a bare ``print(spec)`` cannot dump
    #: BROKER_API_KEY somewhere it will be retained.
    env_vars: Dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        name = _require_non_blank(self.env_name, "env_name", self.env_name).upper()
        if name not in VALID_ENV_NAMES:
            raise ValueError(
                f"Unknown env_name {self.env_name!r}. Expected one of "
                f"{sorted(VALID_ENV_NAMES)}. Names are not inferred: treating an "
                f"unrecognised name as non-production would let a live environment "
                f"wired to a testnet endpoint pass the endpoint check."
            )
        self.env_name = name

        version = _require_non_blank(self.python_version, "python_version", name)
        if not _PYTHON_VERSION_RE.match(version):
            raise ValueError(
                f"{name}: python_version {self.python_version!r} is not a full release "
                f"version. Expected 'major.minor.patch' (e.g. '3.11.8'); a minor-only "
                f"value such as '3.11' would compare equal across differing patch "
                f"releases."
            )
        self.python_version = version

        lock_hash = _require_non_blank(
            self.lockfile_sha256, "lockfile_sha256", name).lower()
        if not _SHA256_HEX_RE.match(lock_hash):
            raise ValueError(
                f"{name}: lockfile_sha256 {self.lockfile_sha256!r} is not a "
                f"64-character hex SHA-256 digest. Produce it with "
                f"sha256_of_lockfile(path) so every environment digests the file the "
                f"same way."
            )
        self.lockfile_sha256 = lock_hash

        revision = _require_non_blank(
            self.db_schema_revision, "db_schema_revision", name)
        if not _normalize_schema_revisions(revision):
            raise ValueError(
                f"{name}: db_schema_revision {self.db_schema_revision!r} contains no "
                f"revision ids once separators are removed. Two environments both "
                f"declaring a separator-only value would otherwise compare as equal "
                f"empty head sets and pass. Pass the full 'alembic heads' output."
            )
        self.db_schema_revision = revision

        mode = _require_non_blank(
            self.broker_endpoint_mode, "broker_endpoint_mode", name).upper()
        if mode not in VALID_ENDPOINT_MODES:
            raise ValueError(
                f"{name}: broker_endpoint_mode {self.broker_endpoint_mode!r} is not one "
                f"of {sorted(VALID_ENDPOINT_MODES)}. An unrecognised mode cannot be "
                f"audited."
            )
        self.broker_endpoint_mode = mode

        if not isinstance(self.env_vars, dict):
            raise ValueError(f"{name}: env_vars must be a dict of name -> value.")

    @property
    def schema_revision_set(self) -> FrozenSet[str]:
        """``db_schema_revision`` as an order-independent set of revision ids."""
        return _normalize_schema_revisions(self.db_schema_revision)

    @property
    def expected_endpoint_mode(self) -> str:
        """The broker endpoint mode this environment is required to be wired to."""
        return EXPECTED_ENDPOINT_MODE_BY_ENV[self.env_name]


@dataclass
class ParityVectorCheck:
    vector_name: str                     # 'PYTHON_VERSION', 'LOCKFILE_HASH', 'DB_SCHEMA',
                                         # 'BROKER_ENDPOINT', 'ENV_VARS_PRESENT'
    is_passed: bool
    expected_value: str
    actual_value: str
    details: str


@dataclass
class EnvironmentParityAuditReport:
    target_env: str
    baseline_env: str
    parity_score_pct: float              # Diagnostic only -- see is_deployment_allowed.
    is_deployment_allowed: bool          # The gate. True only when every vector passed.
    audit_status: str                    # STATUS_PASSED or STATUS_BLOCKED
    vector_checks: List[ParityVectorCheck]
    audit_summary: str

    @property
    def failed_vector_names(self) -> List[str]:
        """Names of the vectors that failed, in audit order."""
        return [c.vector_name for c in self.vector_checks if not c.is_passed]


class EnvironmentParityAuditorEngine:
    """Audits one environment against an approved release specification.

    Args:
        required_env_var_keys: Environment variables that must be present and non-empty
            in the audited environment. Duplicates are collapsed -- they would
            otherwise inflate the reported key count. Defaults to
            :data:`DEFAULT_REQUIRED_ENV_VAR_KEYS`. Pass an empty sequence only if you
            deliberately intend the env-var vector to pass vacuously.

    Raises:
        ValueError: If any supplied key is blank.
    """

    def __init__(self, required_env_var_keys: Optional[Sequence[str]] = None):
        if isinstance(required_env_var_keys, str):
            # A bare string is a Sequence[str], so this slips past type checkers and
            # would silently audit for one single-character key per letter.
            raise ValueError(
                "required_env_var_keys must be a sequence of names, not a single "
                "string. Pass ['BROKER_API_KEY'], not 'BROKER_API_KEY'.")

        keys = list(DEFAULT_REQUIRED_ENV_VAR_KEYS if required_env_var_keys is None
                    else required_env_var_keys)
        if any(not isinstance(k, str) or not k.strip() for k in keys):
            raise ValueError("required_env_var_keys must not contain blank entries.")

        deduped: List[str] = []
        for key in (k.strip() for k in keys):
            if key not in deduped:
                deduped.append(key)
        self.required_env_var_keys: List[str] = deduped

        if not deduped:
            logger.warning(
                "EnvironmentParityAuditorEngine constructed with no required env var "
                "keys; the ENV_VARS_PRESENT vector will pass unconditionally.")

    def audit_environment_parity(
        self,
        current_env: EnvironmentSpec,
        production_baseline: EnvironmentSpec,
    ) -> EnvironmentParityAuditReport:
        """Audit ``current_env`` against ``production_baseline`` across five vectors.

        Args:
            current_env: The environment being promoted (DEV, STAGING or PRODUCTION).
            production_baseline: The approved release specification. Must be a spec
                named ``PRODUCTION`` -- the two arguments share a type, so an argument
                swap would otherwise produce a plausible but meaningless report.

        Returns:
            The audit report. ``is_deployment_allowed`` is the gate; block the
            promotion whenever it is ``False``.

        Raises:
            ValueError: If ``production_baseline`` is not a PRODUCTION spec.
        """
        if production_baseline.env_name != ENV_PRODUCTION:
            raise ValueError(
                f"production_baseline must be a {ENV_PRODUCTION} spec, got "
                f"{production_baseline.env_name!r}. Check the argument order: the "
                f"environment under audit comes first, the release baseline second."
            )

        checks: List[ParityVectorCheck] = [
            self._check_python_version(current_env, production_baseline),
            self._check_lockfile_hash(current_env, production_baseline),
            self._check_db_schema(current_env, production_baseline),
            self._check_broker_endpoint(current_env),
            self._check_required_env_vars(current_env),
        ]

        passed_count = sum(1 for c in checks if c.is_passed)
        parity_score = round((passed_count / float(len(checks))) * 100.0, 1)
        is_allowed = (passed_count == len(checks))
        status = STATUS_PASSED if is_allowed else STATUS_BLOCKED

        if is_allowed:
            summary = (f"ENVIRONMENT PARITY PASSED [{current_env.env_name}]: 100% "
                       f"vector alignment with the {production_baseline.env_name} "
                       f"release baseline.")
            logger.info(summary)
        else:
            failed_names = [c.vector_name for c in checks if not c.is_passed]
            summary = (f"DEPLOYMENT BLOCKED [{current_env.env_name}]: parity score "
                       f"{parity_score}% < 100%. Failed vectors: {failed_names}.")
            logger.error(summary)

        return EnvironmentParityAuditReport(
            target_env=current_env.env_name,
            baseline_env=production_baseline.env_name,
            parity_score_pct=parity_score,
            is_deployment_allowed=is_allowed,
            audit_status=status,
            vector_checks=checks,
            audit_summary=summary,
        )

    # -- Individual parity vectors -------------------------------------------------

    @staticmethod
    def _check_python_version(
        current: EnvironmentSpec, baseline: EnvironmentSpec) -> ParityVectorCheck:
        ok = current.python_version == baseline.python_version
        return ParityVectorCheck(
            vector_name="PYTHON_VERSION",
            is_passed=ok,
            expected_value=baseline.python_version,
            actual_value=current.python_version,
            details=("Python runtime release version match."
                     if ok else
                     f"Python version mismatch: {current.python_version} vs "
                     f"{baseline.python_version}."),
        )

    @staticmethod
    def _check_lockfile_hash(
        current: EnvironmentSpec, baseline: EnvironmentSpec) -> ParityVectorCheck:
        ok = current.lockfile_sha256 == baseline.lockfile_sha256
        return ParityVectorCheck(
            vector_name="LOCKFILE_HASH",
            is_passed=ok,
            expected_value=f"{baseline.lockfile_sha256[:HASH_DISPLAY_PREFIX_LEN]}...",
            actual_value=f"{current.lockfile_sha256[:HASH_DISPLAY_PREFIX_LEN]}...",
            details=("Dependency lockfile SHA-256 match. This proves the lockfile file "
                     "is identical, not that the installed distributions are."
                     if ok else
                     "Dependency lockfile mismatch: the declared dependency set differs "
                     "from the release baseline."),
        )

    @staticmethod
    def _check_db_schema(
        current: EnvironmentSpec, baseline: EnvironmentSpec) -> ParityVectorCheck:
        current_heads = current.schema_revision_set
        baseline_heads = baseline.schema_revision_set
        ok = current_heads == baseline_heads
        if ok:
            details = f"Migration head(s) match baseline ({len(current_heads)} head(s))."
        else:
            missing = sorted(baseline_heads - current_heads)
            unexpected = sorted(current_heads - baseline_heads)
            details = (f"DB schema drift: missing {missing}, unexpected {unexpected} "
                       f"(order-independent comparison of migration heads).")
        return ParityVectorCheck(
            vector_name="DB_SCHEMA",
            is_passed=ok,
            expected_value=", ".join(sorted(baseline_heads)),
            actual_value=", ".join(sorted(current_heads)),
            details=details,
        )

    @staticmethod
    def _check_broker_endpoint(current: EnvironmentSpec) -> ParityVectorCheck:
        expected_mode = current.expected_endpoint_mode
        ok = current.broker_endpoint_mode == expected_mode
        return ParityVectorCheck(
            vector_name="BROKER_ENDPOINT",
            is_passed=ok,
            expected_value=expected_mode,
            actual_value=current.broker_endpoint_mode,
            details=("Broker endpoint mode aligned with environment."
                     if ok else
                     f"MISCONFIGURED ENDPOINT: {current.env_name} is wired to "
                     f"{current.broker_endpoint_mode}, expected {expected_mode}."),
        )

    def _check_required_env_vars(self, current: EnvironmentSpec) -> ParityVectorCheck:
        # ``None`` is treated as absent, not as the string "None". A key that arrived
        # from an unset ``os.environ.get()`` or a valueless YAML entry ("KEY:") is
        # missing configuration, and str(None) would otherwise make it look present.
        missing = [k for k in self.required_env_var_keys
                   if current.env_vars.get(k) is None
                   or not str(current.env_vars[k]).strip()]
        ok = not missing
        return ParityVectorCheck(
            vector_name="ENV_VARS_PRESENT",
            is_passed=ok,
            expected_value=f"All {len(self.required_env_var_keys)} keys present",
            actual_value=("All present" if ok else f"Missing: {missing}"),
            details=("Mandatory environment variables present and non-empty."
                     if ok else
                     f"Missing or empty required env vars: {missing}. Values are never "
                     f"compared or reported -- only presence."),
        )
