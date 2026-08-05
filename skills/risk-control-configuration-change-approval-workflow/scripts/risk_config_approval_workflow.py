"""Reference maker-checker workflow for versioned risk-configuration changes.

The module is deliberately dependency-free.  It demonstrates workflow invariants and an
in-memory compare-and-set store; production users must replace in-memory persistence with a
durable transactional repository and derive :class:`Actor` from trusted IAM claims.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol

logger = logging.getLogger(__name__)

ConfigValidator = Callable[[Mapping[str, Any]], None]


class WorkflowError(Exception):
    """Base class for expected workflow failures."""


class ValidationError(WorkflowError):
    """Input or risk-domain validation failed."""


class AuthorizationError(WorkflowError):
    """The authenticated actor is not permitted to perform the operation."""


class StateTransitionError(WorkflowError):

    """The requested transition is invalid for the current state."""


class IntegrityError(WorkflowError):
    """An identifier, digest, or idempotency invariant was violated."""


class VersionConflict(WorkflowError):

    """The active configuration no longer has the request's base version."""


class ChangeState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    APPLIED = "APPLIED"


@dataclass(frozen=True)
class Actor:
    """Authenticated principal. Populate roles only from a trusted authorization service."""

    actor_id: str
    roles: frozenset[str]

    def __post_init__(self) -> None:
        if not self.actor_id.strip() or not self.roles or any(not role.strip() for role in self.roles):
            raise ValidationError("actor_id and at least one non-empty role are required")


@dataclass(frozen=True)
class ApprovalPolicy:
    """Workflow policy; domain-specific risk validation is supplied separately."""

    policy_version: str = "risk-config-approval-v1"
    submitter_roles: frozenset[str] = field(
        default_factory=lambda: frozenset({"RISK_CONFIG_REQUESTER"})
    )
    approver_roles: frozenset[str] = field(
        default_factory=lambda: frozenset({"RISK_MANAGER"})
    )
    deployer_roles: frozenset[str] = field(
        default_factory=lambda: frozenset({"RISK_CONFIG_DEPLOYER"})
    )
    required_approvals: int = 1
    request_ttl: timedelta = timedelta(hours=24)
    require_independent_applier: bool = False
    max_payload_bytes: int = 262_144
    forbidden_key_names: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "access_token",
                "api_key",
                "api_secret",
                "client_secret",
                "password",
                "private_key",
                "refresh_token",
                "secret_key",
            }
        )
    )

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValidationError("policy_version is required")
        if not self.submitter_roles or not self.approver_roles or not self.deployer_roles:
            raise ValidationError(
                "submitter_roles, approver_roles, and deployer_roles must not be empty"
            )
        if self.required_approvals < 1:
            raise ValidationError("required_approvals must be at least one")
        if self.request_ttl <= timedelta(0):
            raise ValidationError("request_ttl must be positive")
        if self.max_payload_bytes < 1:
            raise ValidationError("max_payload_bytes must be positive")


@dataclass(frozen=True)
class ApprovalRecord:
    approver_id: str
    role: str
    approved_at: datetime
    config_digest: str


@dataclass(frozen=True)
class ChangeRequest:
    request_id: str
    environment: str
    base_version: int
    submitter_id: str
    reason: str
    ticket_id: str
    policy_version: str
    created_at: datetime
    expires_at: datetime
    proposed_config: dict[str, Any]
    config_digest: str
    state: ChangeState
    approvals: tuple[ApprovalRecord, ...]
    rejection_reason: str | None
    applied_version: int | None


@dataclass(frozen=True)
class VersionedConfig:
    environment: str
    version: int
    config: dict[str, Any]
    config_digest: str


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    event_type: str
    request_id: str
    actor_id: str
    occurred_at: datetime
    details: Mapping[str, Any]
    previous_hash: str
    event_hash: str


class VersionedConfigStore(Protocol):
    """Atomic persistence boundary required by the workflow."""

    def read(self, environment: str) -> VersionedConfig:
        """Return the current version and a defensive configuration snapshot."""

    def apply(
        self,
        *,
        request_id: str,
        environment: str,
        expected_version: int,
        config_json: str,
        request_digest: str,
        config_digest: str,
    ) -> VersionedConfig:
        """Atomically compare version and apply once for request_id."""

    def applied_version(self, request_id: str, request_digest: str) -> int | None:
        """Return a prior application result, rejecting digest reuse."""


@dataclass
class _MutableRequest:
    request_id: str
    environment: str
    base_version: int
    submitter_id: str
    reason: str
    ticket_id: str
    policy_version: str
    created_at: datetime
    expires_at: datetime
    config_json: str
    config_digest: str
    state: ChangeState = ChangeState.PENDING
    approvals: list[ApprovalRecord] = field(default_factory=list)
    rejection_reason: str | None = None
    applied_version: int | None = None


class InMemoryVersionedConfigStore:
    """Thread-safe test adapter implementing atomic CAS and apply idempotency."""

    def __init__(self, initial_configs: Mapping[str, Mapping[str, Any]]) -> None:
        if not initial_configs:
            raise ValidationError("at least one environment configuration is required")
        self._lock = threading.RLock()
        self._configs: dict[str, tuple[int, str, str]] = {}
        self._applications: dict[str, tuple[str, str, int]] = {}
        for environment, config in initial_configs.items():
            normalized_environment = _required_text(environment, "environment")
            config_json = _canonical_config(config)
            self._configs[normalized_environment] = (
                1,
                config_json,
                _sha256(config_json),
            )

    def read(self, environment: str) -> VersionedConfig:
        with self._lock:
            try:
                version, config_json, digest = self._configs[environment]
            except KeyError as exc:
                raise ValidationError(f"unknown environment: {environment}") from exc
            return VersionedConfig(
                environment=environment,
                version=version,
                config=json.loads(config_json),
                config_digest=digest,
            )

    def apply(
        self,
        *,
        request_id: str,
        environment: str,
        expected_version: int,
        config_json: str,
        request_digest: str,
        config_digest: str,
    ) -> VersionedConfig:
        with self._lock:
            if _sha256(config_json) != config_digest:
                raise IntegrityError("config_digest does not match config_json")
            prior = self._applications.get(request_id)
            if prior is not None:
                prior_request_digest, prior_config_digest, prior_version = prior
                if (
                    prior_request_digest != request_digest
                    or prior_config_digest != config_digest
                ):
                    raise IntegrityError("request_id was previously applied with another digest")
                current = self.read(environment)
                if current.version < prior_version:
                    raise IntegrityError("configuration store application history is inconsistent")
                return VersionedConfig(
                    environment=environment,
                    version=prior_version,
                    config=json.loads(config_json),
                    config_digest=config_digest,
                )

            current = self.read(environment)
            if current.version != expected_version:
                raise VersionConflict(
                    f"expected version {expected_version}, found {current.version}"
                )

            new_version = current.version + 1
            self._configs[environment] = (new_version, config_json, config_digest)
            self._applications[request_id] = (request_digest, config_digest, new_version)
            return VersionedConfig(
                environment=environment,
                version=new_version,
                config=json.loads(config_json),
                config_digest=config_digest,
            )

    def applied_version(self, request_id: str, request_digest: str) -> int | None:
        with self._lock:
            prior = self._applications.get(request_id)
            if prior is None:
                return None
            prior_request_digest, _, version = prior
            if prior_request_digest != request_digest:
                raise IntegrityError("request_id was previously applied with another digest")
            return version


class RiskConfigApprovalWorkflow:
    """Thread-safe reference aggregate for risk-configuration change control."""

    def __init__(
        self,
        store: VersionedConfigStore,
        validate_config: ConfigValidator,
        policy: ApprovalPolicy | None = None,
    ) -> None:
        self._store = store
        self._validate_config = validate_config
        self._policy = policy or ApprovalPolicy()
        self._lock = threading.RLock()
        self._requests: dict[str, _MutableRequest] = {}
        self._audit_events: list[AuditEvent] = []

    def submit_change(
        self,
        *,
        request_id: str,
        environment: str,
        base_version: int,
        proposed_config: Mapping[str, Any],
        submitter: Actor,
        reason: str,
        ticket_id: str,
        now: datetime | None = None,
    ) -> ChangeRequest:
        occurred_at = _utc(now)
        if not submitter.roles & self._policy.submitter_roles:
            raise AuthorizationError("actor does not hold a submitter role")
        request_id = _required_text(request_id, "request_id")
        environment = _required_text(environment, "environment")
        reason = _required_text(reason, "reason")
        ticket_id = _required_text(ticket_id, "ticket_id")
        if base_version < 1:
            raise ValidationError("base_version must be positive")

        config_json = _canonical_config(proposed_config)
        if len(config_json.encode("utf-8")) > self._policy.max_payload_bytes:
            raise ValidationError("proposed configuration exceeds max_payload_bytes")
        sensitive_path = _find_forbidden_key(
            json.loads(config_json), self._policy.forbidden_key_names
        )
        if sensitive_path is not None:
            raise ValidationError(f"configuration contains forbidden secret field: {sensitive_path}")

        normalized_config = json.loads(config_json)
        try:
            self._validate_config(normalized_config)
        except WorkflowError:
            raise
        except Exception as exc:
            raise ValidationError(f"risk-domain validation failed: {exc}") from exc

        digest = _request_digest(
            request_id=request_id,
            environment=environment,
            base_version=base_version,
            submitter_id=submitter.actor_id,
            reason=reason,
            ticket_id=ticket_id,
            policy_version=self._policy.policy_version,
            config_json=config_json,
        )
        # Return identical retries even after the active version has advanced.
        with self._lock:
            existing = self._requests.get(request_id)
            if existing is not None:
                if existing.config_digest != digest or existing.submitter_id != submitter.actor_id:
                    raise IntegrityError("request_id already identifies another change")
                return self._snapshot(existing)

        current = self._store.read(environment)
        if current.version != base_version:
            raise VersionConflict(
                f"base version {base_version} is stale; current version is {current.version}"
            )
        if current.config_digest == _sha256(config_json):
            raise ValidationError("proposed configuration is identical to the active configuration")

        with self._lock:
            existing = self._requests.get(request_id)
            if existing is not None:
                if existing.config_digest != digest or existing.submitter_id != submitter.actor_id:
                    raise IntegrityError("request_id already identifies another change")
                return self._snapshot(existing)

            request = _MutableRequest(
                request_id=request_id,
                environment=environment,
                base_version=base_version,
                submitter_id=submitter.actor_id,
                reason=reason,
                ticket_id=ticket_id,
                policy_version=self._policy.policy_version,
                created_at=occurred_at,
                expires_at=occurred_at + self._policy.request_ttl,
                config_json=config_json,
                config_digest=digest,
            )
            self._requests[request_id] = request
            self._record_event("CHANGE_SUBMITTED", request, submitter.actor_id, occurred_at)
            return self._snapshot(request)

    def approve(
        self,
        request_id: str,
        *,
        approver: Actor,
        expected_digest: str,
        now: datetime | None = None,
    ) -> ChangeRequest:
        occurred_at = _utc(now)
        eligible_roles = approver.roles & self._policy.approver_roles
        if not eligible_roles:
            raise AuthorizationError("actor does not hold an approver role")
        with self._lock:
            request = self._get(request_id)
            self._expire_if_needed(request, occurred_at)
            self._assert_digest(request, expected_digest)
            if request.state is ChangeState.EXPIRED:
                raise StateTransitionError("expired changes cannot be approved")
            if request.state not in {ChangeState.PENDING, ChangeState.APPROVED}:
                raise StateTransitionError(f"cannot approve a {request.state.value} change")
            if approver.actor_id == request.submitter_id:
                raise AuthorizationError("the change submitter cannot approve the same change")

            if any(record.approver_id == approver.actor_id for record in request.approvals):
                return self._snapshot(request)

            request.approvals.append(
                ApprovalRecord(
                    approver_id=approver.actor_id,
                    role=sorted(eligible_roles)[0],
                    approved_at=occurred_at,
                    config_digest=request.config_digest,
                )
            )
            if len(request.approvals) >= self._policy.required_approvals:
                request.state = ChangeState.APPROVED
            self._record_event("CHANGE_APPROVED", request, approver.actor_id, occurred_at)
            return self._snapshot(request)

    def reject(
        self,
        request_id: str,
        *,
        reviewer: Actor,
        expected_digest: str,
        reason: str,
        now: datetime | None = None,
    ) -> ChangeRequest:
        occurred_at = _utc(now)
        reason = _required_text(reason, "reason")
        if not reviewer.roles & self._policy.approver_roles:
            raise AuthorizationError("actor does not hold an approver role")
        with self._lock:
            request = self._get(request_id)
            self._expire_if_needed(request, occurred_at)
            self._assert_digest(request, expected_digest)
            if request.state not in {ChangeState.PENDING, ChangeState.APPROVED}:
                raise StateTransitionError(f"cannot reject a {request.state.value} change")
            if reviewer.actor_id == request.submitter_id:
                raise AuthorizationError("the change submitter cannot review the same change")
            request.state = ChangeState.REJECTED
            request.rejection_reason = reason
            self._record_event(
                "CHANGE_REJECTED", request, reviewer.actor_id, occurred_at, {"reason": reason}
            )
            return self._snapshot(request)

    def cancel(
        self,
        request_id: str,
        *,
        submitter: Actor,
        expected_digest: str,
        now: datetime | None = None,
    ) -> ChangeRequest:
        occurred_at = _utc(now)
        with self._lock:
            request = self._get(request_id)
            self._expire_if_needed(request, occurred_at)
            self._assert_digest(request, expected_digest)
            if submitter.actor_id != request.submitter_id:
                raise AuthorizationError("only the submitter may cancel the change")
            if request.state not in {ChangeState.PENDING, ChangeState.APPROVED}:
                raise StateTransitionError(f"cannot cancel a {request.state.value} change")
            request.state = ChangeState.CANCELLED
            self._record_event("CHANGE_CANCELLED", request, submitter.actor_id, occurred_at)
            return self._snapshot(request)

    def apply_change(
        self,
        request_id: str,
        *,
        deployer: Actor,
        expected_digest: str,
        now: datetime | None = None,
    ) -> ChangeRequest:
        occurred_at = _utc(now)
        if not deployer.roles & self._policy.deployer_roles:
            raise AuthorizationError("actor does not hold a deployer role")
        with self._lock:
            request = self._get(request_id)
            self._assert_digest(request, expected_digest)
            if self._policy.require_independent_applier and (
                deployer.actor_id == request.submitter_id
                or any(a.approver_id == deployer.actor_id for a in request.approvals)
            ):
                raise AuthorizationError("policy requires an independent applier")
            if request.state is ChangeState.APPLIED:
                return self._snapshot(request)

            self._expire_if_needed(request, occurred_at)
            if request.state is ChangeState.EXPIRED:
                raise StateTransitionError("expired changes cannot be applied")
            if request.state is not ChangeState.APPROVED:
                raise StateTransitionError(f"cannot apply a {request.state.value} change")
            if len(request.approvals) < self._policy.required_approvals:
                raise IntegrityError("approved state does not contain the required quorum")
            if any(a.config_digest != request.config_digest for a in request.approvals):
                raise IntegrityError("an approval is not bound to the requested configuration")

            config = json.loads(request.config_json)
            try:
                self._validate_config(config)
            except WorkflowError:
                raise
            except Exception as exc:
                raise ValidationError(f"risk-domain validation failed before apply: {exc}") from exc

            # The store must atomically compare-and-set and deduplicate request_id.
            applied = self._store.apply(
                request_id=request.request_id,
                environment=request.environment,
                expected_version=request.base_version,
                config_json=request.config_json,
                request_digest=request.config_digest,
                config_digest=_sha256(request.config_json),
            )
            request.applied_version = applied.version
            request.state = ChangeState.APPLIED
            self._record_event(
                "CHANGE_APPLIED",
                request,
                deployer.actor_id,
                occurred_at,
                {"applied_version": applied.version},
            )
            return self._snapshot(request)

    def reconcile(self, request_id: str, *, now: datetime | None = None) -> ChangeRequest:
        """Repair local state after an apply response is lost; emit an audit event on repair."""

        occurred_at = _utc(now)
        with self._lock:
            request = self._get(request_id)
            applied_version = self._store.applied_version(
                request.request_id, request.config_digest
            )
            if applied_version is not None and request.state is not ChangeState.APPLIED:
                request.state = ChangeState.APPLIED
                request.applied_version = applied_version
                self._record_event(
                    "CHANGE_RECONCILED",
                    request,
                    "SYSTEM",
                    occurred_at,
                    {"applied_version": applied_version},
                )
            return self._snapshot(request)

    def get_request(self, request_id: str) -> ChangeRequest:
        with self._lock:
            return self._snapshot(self._get(request_id))

    def audit_events(self) -> tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(self._audit_events)

    def _get(self, request_id: str) -> _MutableRequest:
        try:
            return self._requests[request_id]
        except KeyError as exc:
            raise ValidationError(f"unknown request_id: {request_id}") from exc

    def _expire_if_needed(self, request: _MutableRequest, now: datetime) -> None:
        if request.state in {ChangeState.PENDING, ChangeState.APPROVED} and now >= request.expires_at:
            request.state = ChangeState.EXPIRED
            self._record_event("CHANGE_EXPIRED", request, "SYSTEM", now)

    @staticmethod
    def _assert_digest(request: _MutableRequest, expected_digest: str) -> None:
        if not expected_digest or expected_digest != request.config_digest:
            raise IntegrityError("expected_digest does not match the submitted change")

    @staticmethod
    def _snapshot(request: _MutableRequest) -> ChangeRequest:
        return ChangeRequest(
            request_id=request.request_id,
            environment=request.environment,
            base_version=request.base_version,
            submitter_id=request.submitter_id,
            reason=request.reason,
            ticket_id=request.ticket_id,
            policy_version=request.policy_version,
            created_at=request.created_at,
            expires_at=request.expires_at,
            proposed_config=json.loads(request.config_json),
            config_digest=request.config_digest,
            state=request.state,
            approvals=tuple(request.approvals),
            rejection_reason=request.rejection_reason,
            applied_version=request.applied_version,
        )

    def _record_event(
        self,
        event_type: str,
        request: _MutableRequest,
        actor_id: str,
        occurred_at: datetime,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        sequence = len(self._audit_events) + 1
        previous_hash = self._audit_events[-1].event_hash if self._audit_events else "0" * 64
        safe_details = {
            "environment": request.environment,
            "base_version": request.base_version,
            "config_digest": request.config_digest,
            "policy_version": request.policy_version,
            "state": request.state.value,
            **dict(details or {}),
        }
        event_body = json.dumps(
            {
                "sequence": sequence,
                "event_type": event_type,
                "request_id": request.request_id,
                "actor_id": actor_id,
                "occurred_at": occurred_at.isoformat(),
                "details": safe_details,
                "previous_hash": previous_hash,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        event = AuditEvent(
            sequence=sequence,
            event_type=event_type,
            request_id=request.request_id,
            actor_id=actor_id,
            occurred_at=occurred_at,
            details=MappingProxyType(safe_details),
            previous_hash=previous_hash,
            event_hash=_sha256(event_body),
        )
        self._audit_events.append(event)
        logger.info(
            "risk configuration workflow transition",
            extra={
                "event_type": event_type,
                "request_id": request.request_id,
                "environment": request.environment,
                "actor_id": actor_id,
                "state": request.state.value,
                "config_digest": request.config_digest,
            },
        )


def _canonical_config(config: Mapping[str, Any]) -> str:
    if not isinstance(config, Mapping) or not config:
        raise ValidationError("configuration must be a non-empty mapping")
    try:
        return json.dumps(
            dict(config),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"configuration must contain finite JSON values: {exc}") from exc


def _request_digest(
    *,
    request_id: str,
    environment: str,
    base_version: int,
    submitter_id: str,
    reason: str,
    ticket_id: str,
    policy_version: str,
    config_json: str,
) -> str:
    payload = json.dumps(
        {
            "request_id": request_id,
            "environment": environment,
            "base_version": base_version,
            "submitter_id": submitter_id,
            "reason": reason,
            "ticket_id": ticket_id,
            "policy_version": policy_version,
            "proposed_config": json.loads(config_json),
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _sha256(payload)


def _find_forbidden_key(value: Any, forbidden: frozenset[str], path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            child_path = f"{path}.{key}"
            if key.casefold() in forbidden:
                return child_path
            found = _find_forbidden_key(nested, forbidden, child_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _find_forbidden_key(nested, forbidden, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} is required")
    return value.strip()


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValidationError("timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
