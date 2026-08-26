"""
model-versioning-and-rollback: an immutable model registry with a deterministic,
atomic rollback path for live trading models.

Design notes
------------
* **The registry is append-only.** A ``(model_id, version)`` pair may be written
  once. Re-registering the same version with different content raises rather
  than silently overwriting it, because the alternative destroys the property
  the whole skill exists to provide: that a version string identifies exactly
  one artifact forever. This mirrors Semantic Versioning 2.0.0 rule 3 -- "Once
  a versioned package has been released, the contents of that version MUST NOT
  be modified." Re-registering byte-identical metadata is a no-op, so a crash
  loop that replays registrations is safe.

* **Version strings are validated, not assumed.** ``X.Y.Z`` with an optional
  ``v`` prefix (semver FAQ: the ``v`` is a common English prefix, not part of
  the version) and an optional pre-release. Leading zeroes are rejected
  (semver rule 2) and build metadata is rejected because rule 10 excludes it
  from precedence, which would make two registry keys compare equal.

* **Rollback target selection is deterministic and ranked, not "whatever sorts
  first".** Candidates are ranked by (last time the engine made them active,
  semver precedence, registration epoch), all descending, so a version that has
  actually served outranks one that has only been registered. Semver precedence
  is computed numerically per rule 11 -- a string sort puts ``v1.10.0`` *below*
  ``v1.9.0``, which is what the previous implementation did whenever
  registration timestamps were absent or tied. The activation counter is a
  monotonic sequence, not a wall clock, so the engine has no hidden time
  dependency and two identical call sequences produce identical results.

* **A rollback is planned before anything mutates.** The failing version is
  only deactivated once a fallback has been selected. The previous
  implementation deactivated first and then discovered it had no target,
  leaving the registry with no active version while the report claimed the
  failing version was still serving.

* **Unusable telemetry raises; it never reads as healthy.** ``NaN > 15.0`` is
  ``False`` in IEEE 754, so a missing-data NaN reaching a naive threshold
  comparison silently disables the circuit breaker. So does a drawdown reported
  as ``-18.5`` under a signed convention. Both are rejected at the boundary.
  A monitoring loop must treat ``ModelRegistryError`` as a failed check, not as
  a healthy sample -- see the module docstring of the test suite for the
  fail-safe wiring.

* **Stale telemetry does not re-trigger.** Once a version has been rolled back
  it is quarantined, and telemetry still naming it returns a no-action status.
  Without this, every subsequent poll from a monitoring loop that has not caught
  up re-reports ``ROLLBACK_SUCCESSFUL`` with ``is_rollback_executed=True`` --
  measured against the previous implementation -- so anything the caller does on
  a rollback (page on-call, reload the artifact, restart the serving process,
  write an audit record) fires again on every poll.

* **Scope.** This engine executes a *confirmed* rollback decision. It applies no
  debouncing, confirmation streak, cooldown or per-deployment cap -- a single
  breaching sample acts. Feed it a confirmed trigger; see
  `automated-rollback-triggers-on-anomaly-detection` for that layer.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import math
import re
import threading
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Statuses a caller may register. ``DEACTIVATED_ROLLBACK`` is engine-managed.
REGISTRABLE_STATUSES = ("PRODUCTION", "STAGING", "ARCHIVED")

#: Status stamped on a version the engine has taken out of service.
STATUS_DEACTIVATED_ROLLBACK = "DEACTIVATED_ROLLBACK"

#: SHA-256 emits a 256-bit digest -> exactly 64 hexadecimal characters
#: (NIST FIPS 180-4, *Secure Hash Standard*).
_HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")

#: Semantic Versioning 2.0.0 normal version + optional pre-release, with the
#: conventional (non-normative) ``v`` prefix. Build metadata is deliberately
#: excluded -- see the module docstring.
_SEMVER_RE = re.compile(
    r"\Av?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?\Z"
)

#: Report statuses.
STATUS_HEALTHY = "MODEL_VERSION_HEALTHY"
STATUS_ROLLBACK_SUCCESSFUL = "ROLLBACK_SUCCESSFUL"
STATUS_ROLLBACK_FAILED = "ROLLBACK_FAILED_NO_HEALTHY_VERSION"
STATUS_TELEMETRY_STALE = "TELEMETRY_STALE_NO_ACTION"


class ModelRegistryError(ValueError):
    """
    Raised on malformed registry input, an immutability violation, or telemetry
    the circuit breaker cannot evaluate.

    Subclasses ``ValueError`` so callers that already catch ``ValueError``
    around registration and audit calls keep working.
    """


def parse_semver(version: str) -> Tuple[int, int, int, Optional[str]]:
    """
    Parse a Semantic Versioning 2.0.0 normal version with an optional
    pre-release and an optional conventional ``v`` prefix.

    Returns ``(major, minor, patch, prerelease_or_None)``.
    Raises :class:`ModelRegistryError` on anything else -- including
    ``latest``, ``v1.0``, ``v01.0.0`` (leading zero, rule 2) and any string
    carrying ``+build`` metadata (rule 10 excludes it from precedence).
    """
    if not isinstance(version, str) or not version:
        raise ModelRegistryError("Version must be a non-empty string.")
    match = _SEMVER_RE.match(version)
    if match is None:
        raise ModelRegistryError(
            f"Version {version!r} is not a valid semantic version. Expected "
            f"'vX.Y.Z' or 'X.Y.Z' with non-negative integers, no leading "
            f"zeroes, an optional '-prerelease', and no '+build' metadata."
        )
    major, minor, patch, prerelease = match.groups()
    return int(major), int(minor), int(patch), prerelease


def semver_precedence_key(version: str) -> Tuple:
    """
    Build a sort key implementing Semantic Versioning 2.0.0 rule 11 precedence.

    Major, minor and patch compare numerically; a pre-release version has lower
    precedence than the corresponding normal version; among pre-releases,
    numeric identifiers compare numerically and rank below alphanumeric ones,
    and a shorter set of identifiers ranks below a longer one when all
    preceding identifiers are equal.
    """
    major, minor, patch, prerelease = parse_semver(version)
    if prerelease is None:
        # 1 outranks the 0 stamped on every pre-release.
        return (major, minor, patch, 1, ())
    identifiers = []
    for token in prerelease.split("."):
        if token.isdigit():
            identifiers.append((0, int(token), ""))
        else:
            identifiers.append((1, 0, token))
    return (major, minor, patch, 0, tuple(identifiers))


def _require_finite_non_negative(value: float, label: str) -> float:
    """Reject NaN, +/-Inf, negative and non-numeric values for a percentage."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelRegistryError(f"{label} must be a real number, got {value!r}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ModelRegistryError(
            f"{label} is {value!r}. A non-finite value silently defeats every "
            f"threshold comparison (NaN > limit is False) and is rejected."
        )
    if numeric < 0.0:
        raise ModelRegistryError(
            f"{label} is {numeric}. This engine uses positive-magnitude "
            f"percentages; a signed value would never breach its limit."
        )
    return numeric


@dataclass
class ModelVersion:
    """
    One immutable registry entry plus its engine-managed serving state.

    Identity fields (``model_id``, ``version``, ``sha256_hash``,
    ``training_dataset_id``, ``sharpe_ratio``, ``max_drawdown_pct``) are frozen
    by policy: the registry stores a defensive copy and refuses to overwrite an
    existing version with different identity. ``status``, ``is_active`` and
    ``last_activated_seq`` are serving state and are mutated by the engine.

    ``sharpe_ratio`` and ``max_drawdown_pct`` are the *validated, pre-deployment*
    figures for this artifact, not live numbers. ``max_drawdown_pct`` is a
    positive magnitude.
    """

    model_id: str
    version: str                        # e.g. 'v1.0.0', 'v1.1.0'
    sha256_hash: str                    # SHA-256 fingerprint of artifact
    training_dataset_id: str
    sharpe_ratio: float
    max_drawdown_pct: float
    status: str                         # see REGISTRABLE_STATUSES
    is_active: bool = False
    registered_at_epoch: float = 0.0
    approved_by: Optional[str] = None   # who signed off this material change
    last_activated_seq: int = 0         # engine-managed promotion ordering token


@dataclass(frozen=True)
class RollbackTriggerConfig:
    """
    Circuit-breaker limits and rollback policy.

    A breach is strict: ``live > limit``. A reading exactly equal to the limit
    is not a breach, so set the limit to the last value you are willing to
    tolerate.
    """

    max_allowed_drawdown_pct: float = 15.0   # Max drawdown before rollback (15.0%)
    max_allowed_error_rate_pct: float = 5.0  # Max inference error rate before rollback (5.0%)
    halt_on_missing_rollback_target: bool = True
    """
    Fail-safe default. When no healthy fallback exists, the breaching version is
    quarantined and model serving stops, leaving the registry with no active
    version. Set ``False`` only with an explicit, recorded decision that
    continuing to serve a breaching model is preferable to halting.
    """
    allow_staging_fallback: bool = False
    """
    When ``True``, a ``STAGING`` version may be selected as the rollback target.
    Off by default: promoting an unvalidated candidate during a live incident
    replaces a known-bad model with an unknown one. ``ARCHIVED`` versions are
    never eligible -- archival is a deliberate retirement decision.
    """


@dataclass(frozen=True)
class LivePerformanceTelemetry:
    """
    One post-deployment observation for the currently serving version.

    ``live_drawdown_pct`` and ``live_error_rate_pct`` are positive-magnitude
    percentages (``18.5`` means 18.5%, not 0.185 and not -18.5).

    ``recent_sharpe`` is carried for the caller's own reporting and is **not**
    a trigger: no Sharpe threshold exists in :class:`RollbackTriggerConfig`, so
    a collapsing Sharpe alone will not roll anything back.
    """

    model_id: str
    current_version: str
    live_drawdown_pct: float
    live_error_rate_pct: float
    recent_sharpe: float


@dataclass(frozen=True)
class ModelVersionReport:
    """
    Outcome of one telemetry audit.

    ``active_version`` is ``None`` when the audit left no version serving --
    that is a halt, not a rollback, and ``is_serving_halted`` says so.
    """

    model_id: str
    active_version: Optional[str]
    previous_version: Optional[str]
    sha256_hash: str
    is_rollback_executed: bool
    status: str
    audit_notes: str
    is_serving_halted: bool = False


@dataclass(frozen=True)
class RegistryAuditEvent:
    """
    One recorded material change to the registry.

    ``sequence`` is a monotonic ordering token assigned by the engine, not a
    timestamp; ``at_epoch`` carries the caller-supplied registration epoch where
    one exists (``0.0`` otherwise). Keeping who/what/which-version together is
    what makes a promotion or rollback reconstructable after the fact.
    """

    sequence: int
    event: str                          # REGISTER | PROMOTE | ROLLBACK | ROLLBACK_FAILED | HALT
    model_id: str
    version: str
    detail: str
    approved_by: Optional[str] = None
    at_epoch: float = 0.0


class ModelVersionManagerEngine:
    """
    Immutable SHA-256 model registry with a deterministic, atomic rollback path.

    Thread-safe: every registry read and mutation holds a re-entrant lock, so a
    monitoring thread auditing telemetry cannot observe a half-completed
    pointer swap performed by a deployment thread.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.registry: Dict[str, Dict[str, ModelVersion]] = {}  # {model_id: {version: ModelVersion}}
        self._audit_log: List[RegistryAuditEvent] = []
        self._sequence = 0

    # ------------------------------------------------------------------ utils

    @staticmethod
    def compute_sha256(content: bytes) -> str:
        """
        Compute the SHA-256 fingerprint of a model artifact payload
        (NIST FIPS 180-4). Returns 64 lowercase hexadecimal characters.

        A digest detects corruption and accidental substitution. It establishes
        *authenticity* only if the registry holding it is itself protected --
        an attacker who can rewrite the artifact can rewrite an unprotected
        hash beside it. Persist the registry to append-only or signed storage.
        """
        if not isinstance(content, (bytes, bytearray, memoryview)):
            raise ModelRegistryError("Artifact content must be bytes-like.")
        return hashlib.sha256(bytes(content)).hexdigest()

    @staticmethod
    def _normalise_hash(sha256_hash: str) -> str:
        """Lowercase and validate a 64-character hexadecimal SHA-256 digest."""
        if not isinstance(sha256_hash, str):
            raise ModelRegistryError("SHA-256 hash must be a string.")
        normalised = sha256_hash.strip().lower()
        if not _HEX64_RE.match(normalised):
            raise ModelRegistryError(
                f"Invalid SHA-256 hash {sha256_hash!r}. Must be 64 hexadecimal "
                f"characters; a 64-character non-hex string is not a digest."
            )
        return normalised

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _record(
        self,
        event: str,
        model_id: str,
        version: str,
        detail: str,
        approved_by: Optional[str] = None,
        at_epoch: float = 0.0,
    ) -> None:
        self._audit_log.append(
            RegistryAuditEvent(
                sequence=self._next_sequence(),
                event=event,
                model_id=model_id,
                version=version,
                detail=detail,
                approved_by=approved_by,
                at_epoch=at_epoch,
            )
        )

    @property
    def audit_log(self) -> Tuple[RegistryAuditEvent, ...]:
        """Immutable view of every recorded material change, in order."""
        with self._lock:
            return tuple(self._audit_log)

    # --------------------------------------------------------------- registry

    def register_version(self, model_version: ModelVersion) -> ModelVersion:
        """
        Register a model version into the immutable registry catalog.

        Validates the semantic version, the SHA-256 digest and the status, then
        stores a defensive copy so later mutation of the caller's object cannot
        rewrite registry history.

        Re-registering an existing version is a no-op when the identity fields
        are byte-identical and raises :class:`ModelRegistryError` otherwise --
        a version string must keep pointing at one artifact (semver rule 3).

        Only ``is_active=True`` claims the serving pointer, and that requires
        ``status='PRODUCTION'``. Registering a ``PRODUCTION`` artifact with
        ``is_active=False`` stages it for a later :meth:`promote_version` call
        and leaves the incumbent serving.

        Returns the stored copy.
        """
        if not isinstance(model_version, ModelVersion):
            raise ModelRegistryError("register_version expects a ModelVersion.")
        if not model_version.model_id or not isinstance(model_version.model_id, str):
            raise ModelRegistryError("model_id must be a non-empty string.")
        if not model_version.training_dataset_id:
            raise ModelRegistryError(
                "training_dataset_id is required: a version whose training data "
                "cannot be identified cannot be reproduced or audited."
            )
        if model_version.status not in REGISTRABLE_STATUSES:
            raise ModelRegistryError(
                f"status {model_version.status!r} is not registrable. Use one of "
                f"{REGISTRABLE_STATUSES}; {STATUS_DEACTIVATED_ROLLBACK!r} is set "
                f"by the engine."
            )
        if model_version.is_active and model_version.status != "PRODUCTION":
            raise ModelRegistryError(
                f"Version {model_version.version} is marked active with status "
                f"{model_version.status!r}. Only a PRODUCTION version may hold "
                f"the serving pointer."
            )

        parse_semver(model_version.version)
        normalised_hash = self._normalise_hash(model_version.sha256_hash)
        max_drawdown = _require_finite_non_negative(
            model_version.max_drawdown_pct, "max_drawdown_pct"
        )
        registered_at = _require_finite_non_negative(
            model_version.registered_at_epoch, "registered_at_epoch"
        )
        if isinstance(model_version.sharpe_ratio, bool) or not isinstance(
            model_version.sharpe_ratio, (int, float)
        ):
            raise ModelRegistryError("sharpe_ratio must be a real number.")
        if not math.isfinite(float(model_version.sharpe_ratio)):
            raise ModelRegistryError("sharpe_ratio must be finite.")

        stored = replace(
            model_version,
            sha256_hash=normalised_hash,
            max_drawdown_pct=max_drawdown,
            registered_at_epoch=registered_at,
            sharpe_ratio=float(model_version.sharpe_ratio),
            last_activated_seq=0,
        )

        with self._lock:
            versions = self.registry.setdefault(stored.model_id, {})
            existing = versions.get(stored.version)
            if existing is not None:
                self._assert_identical_identity(existing, stored)
                logger.info(
                    "REGISTER NO-OP [%s]: version %s already registered with an "
                    "identical artifact identity.", stored.model_id, stored.version,
                )
                if stored.is_active and not existing.is_active:
                    # A replayed registration still carries the intent to
                    # deploy. Route it through promote_version so the pointer
                    # moves, the change is recorded, and a quarantined version
                    # is refused rather than quietly resurrected.
                    return self.promote_version(
                        stored.model_id, stored.version, approved_by=stored.approved_by
                    )
                return replace(existing)

            if stored.is_active:
                self._deactivate_all(stored.model_id)
                stored.last_activated_seq = self._next_sequence()

            versions[stored.version] = stored
            if stored.approved_by is None:
                logger.warning(
                    "REGISTERED WITHOUT APPROVER [%s]: version %s has no "
                    "approved_by. A deployment record that cannot say who "
                    "approved the change is not an audit trail.",
                    stored.model_id, stored.version,
                )
            self._record(
                "REGISTER",
                stored.model_id,
                stored.version,
                f"status={stored.status} active={stored.is_active} "
                f"sha256={stored.sha256_hash[:8]}... dataset={stored.training_dataset_id}",
                approved_by=stored.approved_by,
                at_epoch=stored.registered_at_epoch,
            )
            logger.info(
                "REGISTERED MODEL [%s]: Version %s (SHA-256: %s...), status=%s, active=%s.",
                stored.model_id, stored.version, stored.sha256_hash[:8],
                stored.status, stored.is_active,
            )
            return replace(stored)

    @staticmethod
    def _assert_identical_identity(existing: ModelVersion, incoming: ModelVersion) -> None:
        """Raise unless the incoming registration is identity-identical."""
        identity_fields = (
            "sha256_hash", "training_dataset_id", "sharpe_ratio", "max_drawdown_pct",
        )
        differing = [
            f for f in identity_fields
            if getattr(existing, f) != getattr(incoming, f)
        ]
        if differing:
            raise ModelRegistryError(
                f"Version {incoming.version} of {incoming.model_id} is already "
                f"registered with different {', '.join(differing)}. A registered "
                f"version is immutable (Semantic Versioning 2.0.0 rule 3); "
                f"publish a new version instead of rewriting this one."
            )

    def _deactivate_all(self, model_id: str) -> Optional[str]:
        """Clear the serving pointer for ``model_id``; return the version cleared."""
        cleared = None
        for version in self.registry.get(model_id, {}).values():
            if version.is_active:
                cleared = version.version
            version.is_active = False
        return cleared

    def promote_version(
        self,
        model_id: str,
        version: str,
        approved_by: Optional[str] = None,
    ) -> ModelVersion:
        """
        Make ``version`` the single active (serving) version of ``model_id``.

        Refuses to promote a version the engine has quarantined by rollback:
        re-promoting a version that just breached its limits is a rollback loop,
        not a deployment. Register a new version instead.
        """
        with self._lock:
            target = self._require_version(model_id, version)
            if target.status == STATUS_DEACTIVATED_ROLLBACK:
                raise ModelRegistryError(
                    f"Version {version} of {model_id} was quarantined by a "
                    f"rollback and cannot be re-promoted. Register a fixed "
                    f"version instead."
                )
            if target.status != "PRODUCTION":
                logger.warning(
                    "PROMOTING NON-PRODUCTION VERSION [%s]: %s has status %s.",
                    model_id, version, target.status,
                )
            previous = self._deactivate_all(model_id)
            target.is_active = True
            target.status = "PRODUCTION"
            target.last_activated_seq = self._next_sequence()
            self._record(
                "PROMOTE", model_id, version,
                f"previous_active={previous}",
                approved_by=approved_by,
                at_epoch=target.registered_at_epoch,
            )
            logger.info(
                "PROMOTED [%s]: %s is now the active version (previous=%s).",
                model_id, version, previous,
            )
            return replace(target)

    def get_active_version(self, model_id: str) -> Optional[ModelVersion]:
        """Return a copy of the currently serving version, or ``None``."""
        with self._lock:
            for version in self.registry.get(model_id, {}).values():
                if version.is_active:
                    return replace(version)
            return None

    def verify_artifact(self, model_id: str, version: str, content: bytes) -> bool:
        """
        Verify that ``content`` is the artifact registered for this version.

        Call this on every load from disk or object storage before the model is
        allowed to produce a live signal. Comparison is constant-time; the
        integrity guarantee is only as strong as the storage protecting the
        registry itself (see :meth:`compute_sha256`).
        """
        with self._lock:
            registered = self._require_version(model_id, version)
            expected = registered.sha256_hash
        actual = self.compute_sha256(content)
        matched = hmac.compare_digest(expected, actual)
        if not matched:
            logger.error(
                "ARTIFACT HASH MISMATCH [%s] version %s: registered %s..., "
                "loaded %s.... Do not serve this artifact.",
                model_id, version, expected[:8], actual[:8],
            )
        return matched

    def _require_version(self, model_id: str, version: str) -> ModelVersion:
        versions = self.registry.get(model_id)
        if versions is None:
            raise ModelRegistryError(f"Model ID '{model_id}' not found in registry catalog.")
        found = versions.get(version)
        if found is None:
            raise ModelRegistryError(f"Current version '{version}' not registered.")
        return found

    # ------------------------------------------------------- circuit breaker

    def audit_telemetry_and_rollback(
        self,
        config: RollbackTriggerConfig,
        telemetry: LivePerformanceTelemetry,
    ) -> ModelVersionReport:
        """
        Audit one live telemetry sample against the drawdown and error-rate
        limits and, on a breach, execute an atomic rollback to the last healthy
        production version.

        Acts on a *single* sample. Debouncing, confirmation streaks, cooldowns
        and per-deployment rollback caps belong to the trigger layer
        (`automated-rollback-triggers-on-anomaly-detection`); wiring raw
        telemetry straight into this call will flap.

        Raises :class:`ModelRegistryError` on unknown identifiers or telemetry
        that cannot be evaluated. An exception is a *failed check*, never a
        healthy one -- a monitoring loop that swallows it has silently disabled
        the circuit breaker.
        """
        if not isinstance(config, RollbackTriggerConfig):
            raise ModelRegistryError("config must be a RollbackTriggerConfig.")
        if not isinstance(telemetry, LivePerformanceTelemetry):
            raise ModelRegistryError("telemetry must be a LivePerformanceTelemetry.")

        drawdown_limit = _require_finite_non_negative(
            config.max_allowed_drawdown_pct, "max_allowed_drawdown_pct"
        )
        error_limit = _require_finite_non_negative(
            config.max_allowed_error_rate_pct, "max_allowed_error_rate_pct"
        )
        live_drawdown = _require_finite_non_negative(
            telemetry.live_drawdown_pct, "live_drawdown_pct"
        )
        live_error_rate = _require_finite_non_negative(
            telemetry.live_error_rate_pct, "live_error_rate_pct"
        )

        model_id = telemetry.model_id
        with self._lock:
            current = self._require_version(model_id, telemetry.current_version)

            # Telemetry describing a version the engine already took out of
            # service is stale. Acting on it walks back through the history.
            if current.status == STATUS_DEACTIVATED_ROLLBACK or not current.is_active:
                notes = (
                    f"NO ACTION [{model_id}]: telemetry names version "
                    f"{telemetry.current_version}, which is not the active "
                    f"version (status={current.status}, is_active={current.is_active}). "
                    f"Sample discarded as stale; no rollback attempted."
                )
                logger.warning(notes)
                active = self.get_active_version(model_id)
                return ModelVersionReport(
                    model_id=model_id,
                    active_version=active.version if active else None,
                    previous_version=None,
                    sha256_hash=current.sha256_hash,
                    is_rollback_executed=False,
                    status=STATUS_TELEMETRY_STALE,
                    audit_notes=notes,
                    is_serving_halted=active is None,
                )

            drawdown_breach = live_drawdown > drawdown_limit
            error_breach = live_error_rate > error_limit

            if not drawdown_breach and not error_breach:
                notes = (
                    f"MODEL HEALTHY [{model_id}]: Version {telemetry.current_version} "
                    f"operating normally. Drawdown = {live_drawdown:.1f}%, "
                    f"Error Rate = {live_error_rate:.1f}%."
                )
                logger.info(notes)
                return ModelVersionReport(
                    model_id=model_id,
                    active_version=telemetry.current_version,
                    previous_version=None,
                    sha256_hash=current.sha256_hash,
                    is_rollback_executed=False,
                    status=STATUS_HEALTHY,
                    audit_notes=notes,
                )

            logger.warning(
                "CIRCUIT BREAKER TRIGGERED [%s]: Version %s breached thresholds! "
                "Drawdown=%.1f%% (Limit=%.1f%%), Error Rate=%.1f%% (Limit=%.1f%%).",
                model_id, telemetry.current_version, live_drawdown, drawdown_limit,
                live_error_rate, error_limit,
            )

            # Plan before mutating: select the fallback first, so a failed
            # search cannot leave the registry with nothing serving by accident.
            fallback = self._select_rollback_target(
                model_id, telemetry.current_version, config, drawdown_limit
            )

            if fallback is None:
                return self._handle_missing_target(model_id, current, config)

            current.is_active = False
            current.status = STATUS_DEACTIVATED_ROLLBACK
            fallback.is_active = True
            fallback.status = "PRODUCTION"
            fallback.last_activated_seq = self._next_sequence()

            notes = (
                f"ROLLBACK SUCCESSFUL [{model_id}]: Deactivated failing version "
                f"{telemetry.current_version}. Hot-swapped active pointer to fallback "
                f"version {fallback.version} (SHA-256: {fallback.sha256_hash[:8]}...)."
            )
            self._record(
                "ROLLBACK", model_id, fallback.version,
                f"from={telemetry.current_version} "
                f"drawdown={live_drawdown:.4f} error_rate={live_error_rate:.4f}",
                at_epoch=fallback.registered_at_epoch,
            )
            logger.info(notes)
            return ModelVersionReport(
                model_id=model_id,
                active_version=fallback.version,
                previous_version=telemetry.current_version,
                sha256_hash=fallback.sha256_hash,
                is_rollback_executed=True,
                status=STATUS_ROLLBACK_SUCCESSFUL,
                audit_notes=notes,
            )

    def _select_rollback_target(
        self,
        model_id: str,
        failing_version: str,
        config: RollbackTriggerConfig,
        drawdown_limit: float,
    ) -> Optional[ModelVersion]:
        """
        Pick the last known healthy version, deterministically.

        Eligible: a registered version other than the failing one, with status
        ``PRODUCTION`` (plus ``STAGING`` when ``allow_staging_fallback``), never
        quarantined by a previous rollback, and whose *validated* max drawdown
        does not already exceed the live limit -- rolling into a model already
        known to breach the limit only re-trips the breaker.

        A version that has **never** served and whose semver precedence is
        *above* the failing one is excluded outright: promoting it would be a
        roll-forward onto an unproven artifact during a live incident, not a
        rollback.

        Ranked by last activation, then semver precedence, then registration
        epoch, all descending -- so a version that has actually served in
        production outranks one that has only ever been registered. Ranking on
        the version *string* would place ``v1.10.0`` below ``v1.9.0``.
        """
        eligible_statuses = ["PRODUCTION"]
        if config.allow_staging_fallback:
            eligible_statuses.append("STAGING")
        failing_key = semver_precedence_key(failing_version)

        candidates: List[ModelVersion] = []
        for version in self.registry.get(model_id, {}).values():
            if version.version == failing_version:
                continue
            if version.status not in eligible_statuses:
                continue
            if version.max_drawdown_pct > drawdown_limit:
                logger.warning(
                    "ROLLBACK CANDIDATE SKIPPED [%s]: version %s has a validated "
                    "max drawdown of %.1f%%, above the %.1f%% live limit.",
                    model_id, version.version, version.max_drawdown_pct, drawdown_limit,
                )
                continue
            if (
                version.last_activated_seq == 0
                and semver_precedence_key(version.version) > failing_key
            ):
                logger.warning(
                    "ROLLBACK CANDIDATE SKIPPED [%s]: version %s ranks above the "
                    "failing version %s and has never served. Rolling onto it "
                    "would be an unproven roll-forward during an incident.",
                    model_id, version.version, failing_version,
                )
                continue
            candidates.append(version)

        if not candidates:
            return None

        candidates.sort(
            key=lambda v: (
                v.last_activated_seq,
                semver_precedence_key(v.version),
                v.registered_at_epoch,
            ),
            reverse=True,
        )
        return candidates[0]

    def _handle_missing_target(
        self,
        model_id: str,
        current: ModelVersion,
        config: RollbackTriggerConfig,
    ) -> ModelVersionReport:
        """Apply the no-fallback policy: halt serving (default) or keep serving."""
        if not config.halt_on_missing_rollback_target:
            notes = (
                f"ROLLBACK FAILED [{model_id}]: No healthy fallback version available. "
                f"halt_on_missing_rollback_target=False, so breaching version "
                f"{current.version} REMAINS ACTIVE by explicit configuration."
            )
            logger.critical(notes)
            self._record(
                "ROLLBACK_FAILED", model_id, current.version,
                "no_fallback_available; continued serving by configuration",
                at_epoch=current.registered_at_epoch,
            )
            return ModelVersionReport(
                model_id=model_id,
                active_version=current.version,
                previous_version=None,
                sha256_hash=current.sha256_hash,
                is_rollback_executed=False,
                status=STATUS_ROLLBACK_FAILED,
                audit_notes=notes,
                is_serving_halted=False,
            )

        current.is_active = False
        current.status = STATUS_DEACTIVATED_ROLLBACK
        notes = (
            f"ROLLBACK FAILED [{model_id}]: No healthy fallback version available. "
            f"Version {current.version} quarantined and MODEL SERVING IS HALTED - "
            f"no active version remains. Escalate and invoke the trading kill switch."
        )
        logger.critical(notes)
        self._record(
            "HALT", model_id, current.version,
            "no_fallback_available; serving halted",
            at_epoch=current.registered_at_epoch,
        )
        return ModelVersionReport(
            model_id=model_id,
            active_version=None,
            previous_version=current.version,
            sha256_hash=current.sha256_hash,
            is_rollback_executed=False,
            status=STATUS_ROLLBACK_FAILED,
            audit_notes=notes,
            is_serving_halted=True,
        )
