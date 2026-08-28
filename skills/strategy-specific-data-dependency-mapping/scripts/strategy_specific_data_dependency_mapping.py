"""Strategy-specific data dependency mapping, freshness auditing, and trading readiness gating.

The engine models one strategy's data dependencies as a directed acyclic graph of feeds.
Each feed declares an ordered vendor preference list, an explicit freshness bound, an
optional schema contract, and the response required when no vendor can serve it.

Readiness is derived only from *observed* vendor state.  A fallback vendor earns credit
only when an observation shows that vendor is itself fresh, healthy, and on-contract; the
engine never assumes an unobserved alternative is alive.

The module is pure and dependency-free.  Vendor polling, persistence, alert routing, and
the decision to act on a report remain adapter concerns.  Scoring weights, fallback and
degraded credit, and the readiness cut-off are operator-chosen policy with no external
regulatory basis - read references/standards.md before adopting the defaults.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

logger = logging.getLogger(__name__)

__all__ = [
    "DependencyMappingError",
    "DependencyValidationError",
    "ObservationValidationError",
    "DependencyCriticality",
    "FailureResponse",
    "FeedState",
    "FaultCode",
    "DataDependencyNode",
    "FeedObservation",
    "ReadinessPolicy",
    "DependencyAssessment",
    "StrategyDataDependencyReport",
    "VendorExposure",
    "StrategyDataDependencyEngine",
    "DataDependencyPortfolio",
]


class DependencyMappingError(Exception):
    """Base class for dependency-mapping configuration and evaluation errors."""


class DependencyValidationError(DependencyMappingError):
    """A dependency node, policy, or dependency graph is structurally invalid."""


class ObservationValidationError(DependencyMappingError):
    """A runtime feed observation or evaluation timestamp is invalid."""


class DependencyCriticality(str, Enum):
    """How much the strategy's correctness depends on the feed."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FailureResponse(str, Enum):
    """Required behaviour when no vendor can serve a feed."""

    BLOCK = "BLOCK"      # the strategy must not trade
    DEGRADE = "DEGRADE"  # the strategy may proceed on cached/imputed values at reduced credit


class FeedState(str, Enum):
    """Resolved serving state of a feed after observation and upstream propagation."""

    PRIMARY_ACTIVE = "PRIMARY_ACTIVE"
    FALLBACK_ACTIVE = "FALLBACK_ACTIVE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class FaultCode(str, Enum):
    """Why the highest-preference vendor was not usable."""

    NONE = "NONE"
    STALE = "STALE"
    UNHEALTHY = "UNHEALTHY"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    CLOCK_SKEW = "CLOCK_SKEW"
    NO_OBSERVATION = "NO_OBSERVATION"
    UPSTREAM_IMPAIRED = "UPSTREAM_IMPAIRED"


_STATE_RANK: Mapping[FeedState, int] = MappingProxyType({
    FeedState.UNAVAILABLE: 0,
    FeedState.DEGRADED: 1,
    FeedState.FALLBACK_ACTIVE: 2,
    FeedState.PRIMARY_ACTIVE: 3,
})

_CRITICALITY_RANK: Mapping[DependencyCriticality, int] = MappingProxyType({
    DependencyCriticality.LOW: 0,
    DependencyCriticality.MEDIUM: 1,
    DependencyCriticality.HIGH: 2,
    DependencyCriticality.CRITICAL: 3,
})

_DEFAULT_CRITICALITY_WEIGHTS: Mapping[DependencyCriticality, float] = MappingProxyType({
    DependencyCriticality.CRITICAL: 4.0,
    DependencyCriticality.HIGH: 3.0,
    DependencyCriticality.MEDIUM: 2.0,
    DependencyCriticality.LOW: 1.0,
})


def _require_text(value: object, field_name: str, error: type[DependencyMappingError]) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise error(f"{field_name} must be a non-empty string without leading/trailing whitespace")
    return value


def _require_finite(value: object, field_name: str, error: type[DependencyMappingError]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error(f"{field_name} must be a real number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise error(f"{field_name} must be finite (got {value!r})")
    return numeric


@dataclass(frozen=True)
class DataDependencyNode:
    """One data dependency of a strategy.

    ``vendors`` is an ordered preference list: ``vendors[0]`` is the primary source and each
    later entry is a fallback tried in order.  ``max_acceptable_lag_seconds`` is mandatory and
    deliberately has no default - a silent default lets a CRITICAL feed inherit a bound that
    was never chosen for it.

    ``upstream_feed_ids`` records the feeds this one is derived from.  A derived feed that
    publishes a fresh timestamp computed from a dead input is not healthy, so upstream loss is
    propagated downstream by ``StrategyDataDependencyEngine``.

    ``failure_response`` may be left ``None``, in which case ``effective_failure_response``
    resolves to ``BLOCK`` for CRITICAL feeds and ``DEGRADE`` otherwise.
    """

    feed_id: str
    feed_name: str
    criticality: DependencyCriticality
    vendors: tuple[str, ...]
    max_acceptable_lag_seconds: float
    schema_contract_version: str = ""
    upstream_feed_ids: frozenset[str] = frozenset()
    failure_response: FailureResponse | None = None

    def __post_init__(self) -> None:
        _require_text(self.feed_id, "feed_id", DependencyValidationError)
        _require_text(self.feed_name, "feed_name", DependencyValidationError)
        if not isinstance(self.criticality, DependencyCriticality):
            raise DependencyValidationError("criticality must be a DependencyCriticality member")
        if self.failure_response is not None and not isinstance(self.failure_response, FailureResponse):
            raise DependencyValidationError("failure_response must be a FailureResponse member or None")

        if isinstance(self.vendors, (str, bytes)) or not isinstance(self.vendors, Sequence):
            raise DependencyValidationError("vendors must be a sequence of vendor identifiers")
        vendors = tuple(
            _require_text(vendor, f"vendors[{index}]", DependencyValidationError)
            for index, vendor in enumerate(self.vendors)
        )
        if not vendors:
            raise DependencyValidationError(f"{self.feed_id}: at least one vendor is required")
        if len(set(vendors)) != len(vendors):
            raise DependencyValidationError(f"{self.feed_id}: vendors must not repeat")
        object.__setattr__(self, "vendors", vendors)

        lag = _require_finite(
            self.max_acceptable_lag_seconds, "max_acceptable_lag_seconds", DependencyValidationError
        )
        if lag <= 0.0:
            raise DependencyValidationError("max_acceptable_lag_seconds must be > 0")
        object.__setattr__(self, "max_acceptable_lag_seconds", lag)

        if not isinstance(self.schema_contract_version, str):
            raise DependencyValidationError("schema_contract_version must be a string")
        if self.schema_contract_version:
            _require_text(
                self.schema_contract_version, "schema_contract_version", DependencyValidationError
            )

        if isinstance(self.upstream_feed_ids, (str, bytes)) or not isinstance(
            self.upstream_feed_ids, Iterable
        ):
            raise DependencyValidationError("upstream_feed_ids must be an iterable of feed ids")
        upstreams = frozenset(
            _require_text(upstream, "upstream_feed_ids entry", DependencyValidationError)
            for upstream in self.upstream_feed_ids
        )
        if self.feed_id in upstreams:
            raise DependencyValidationError(f"{self.feed_id}: a feed cannot be its own upstream")
        object.__setattr__(self, "upstream_feed_ids", upstreams)

    @property
    def primary_vendor(self) -> str:
        """Highest-preference vendor."""
        return self.vendors[0]

    @property
    def secondary_vendor(self) -> str | None:
        """Next vendor after the primary, or ``None`` when the feed is single-sourced."""
        return self.vendors[1] if len(self.vendors) > 1 else None

    @property
    def effective_failure_response(self) -> FailureResponse:
        """Declared response, defaulting to BLOCK for CRITICAL feeds and DEGRADE otherwise."""
        if self.failure_response is not None:
            return self.failure_response
        if self.criticality is DependencyCriticality.CRITICAL:
            return FailureResponse.BLOCK
        return FailureResponse.DEGRADE


@dataclass(frozen=True)
class FeedObservation:
    """Observed state of one vendor's stream for one feed.

    One observation describes exactly one ``(feed_id, vendor_id)`` pair.  A fallback vendor is
    credited only when an observation proves that vendor is itself serving; the engine has no
    way to infer the health of a vendor nobody looked at.
    """

    feed_id: str
    vendor_id: str
    last_updated_epoch: float
    is_healthy: bool
    schema_version: str = ""
    schema_error: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.feed_id, "feed_id", ObservationValidationError)
        _require_text(self.vendor_id, "vendor_id", ObservationValidationError)
        epoch = _require_finite(
            self.last_updated_epoch, "last_updated_epoch", ObservationValidationError
        )
        object.__setattr__(self, "last_updated_epoch", epoch)
        if not isinstance(self.is_healthy, bool):
            raise ObservationValidationError("is_healthy must be a bool")
        if not isinstance(self.schema_version, str):
            raise ObservationValidationError("schema_version must be a string")
        if self.schema_error is not None:
            _require_text(self.schema_error, "schema_error", ObservationValidationError)


@dataclass(frozen=True)
class ReadinessPolicy:
    """Operator-chosen scoring policy.

    None of these values derive from a regulator, exchange, or vendor standard.  They are
    defaults for a scoring convention; calibrate them against your own incident history and
    record the rationale.  ``future_timestamp_tolerance_seconds`` bounds how far ahead of the
    evaluation clock a vendor timestamp may sit before it is treated as a clock-integrity
    fault instead of as fresh data.
    """

    criticality_weights: Mapping[DependencyCriticality, float] = field(
        default_factory=lambda: _DEFAULT_CRITICALITY_WEIGHTS
    )
    fallback_credit: float = 0.8
    degraded_credit: float = 0.5
    minimum_readiness_pct: float = 70.0
    future_timestamp_tolerance_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.criticality_weights, Mapping):
            raise DependencyValidationError("criticality_weights must be a mapping")
        weights: dict[DependencyCriticality, float] = {}
        for criticality in DependencyCriticality:
            if criticality not in self.criticality_weights:
                raise DependencyValidationError(
                    f"criticality_weights is missing {criticality.value}"
                )
            weight = _require_finite(
                self.criticality_weights[criticality],
                f"criticality_weights[{criticality.value}]",
                DependencyValidationError,
            )
            if weight <= 0.0:
                raise DependencyValidationError(
                    f"criticality_weights[{criticality.value}] must be > 0"
                )
            weights[criticality] = weight
        object.__setattr__(self, "criticality_weights", MappingProxyType(weights))

        fallback = _require_finite(self.fallback_credit, "fallback_credit", DependencyValidationError)
        degraded = _require_finite(self.degraded_credit, "degraded_credit", DependencyValidationError)
        if not 0.0 <= degraded <= fallback <= 1.0:
            raise DependencyValidationError(
                "credits must satisfy 0 <= degraded_credit <= fallback_credit <= 1"
            )
        object.__setattr__(self, "fallback_credit", fallback)
        object.__setattr__(self, "degraded_credit", degraded)

        minimum = _require_finite(
            self.minimum_readiness_pct, "minimum_readiness_pct", DependencyValidationError
        )
        if not 0.0 <= minimum <= 100.0:
            raise DependencyValidationError("minimum_readiness_pct must be within [0, 100]")
        object.__setattr__(self, "minimum_readiness_pct", minimum)

        tolerance = _require_finite(
            self.future_timestamp_tolerance_seconds,
            "future_timestamp_tolerance_seconds",
            DependencyValidationError,
        )
        if tolerance < 0.0:
            raise DependencyValidationError("future_timestamp_tolerance_seconds must be >= 0")
        object.__setattr__(self, "future_timestamp_tolerance_seconds", tolerance)

    def credit_for(self, state: FeedState) -> float:
        """Fraction of a feed's weight earned in ``state``."""
        if state is FeedState.PRIMARY_ACTIVE:
            return 1.0
        if state is FeedState.FALLBACK_ACTIVE:
            return self.fallback_credit
        if state is FeedState.DEGRADED:
            return self.degraded_credit
        return 0.0


@dataclass(frozen=True)
class DependencyAssessment:
    """Per-feed outcome of one readiness evaluation.

    ``fault`` explains why the primary vendor was not used; for a FALLBACK_ACTIVE feed it is
    the primary's rejection reason, not a fault of the serving vendor.  ``observed_lag_seconds``
    is the serving vendor's lag, or the primary's lag when nothing is serving.
    """

    feed_id: str
    criticality: DependencyCriticality
    state: FeedState
    active_vendor: str | None
    fault: FaultCode
    observed_lag_seconds: float | None
    readiness_credit: float
    detail: str


@dataclass(frozen=True)
class StrategyDataDependencyReport:
    """Deterministic readiness verdict for one strategy at one evaluation timestamp."""

    strategy_id: str
    evaluated_at_epoch: float
    readiness_score_pct: float
    is_strategy_ready_to_trade: bool
    assessments: tuple[DependencyAssessment, ...]
    active_feed_sources: Mapping[str, str]
    blocked_dependencies: tuple[str, ...]
    fallback_dependencies: tuple[str, ...]
    degraded_dependencies: tuple[str, ...]
    unobserved_dependencies: tuple[str, ...]
    warnings: tuple[str, ...]
    audit_notes: str


@dataclass(frozen=True)
class VendorExposure:
    """Static blast radius of losing one vendor for one strategy.

    This is a best-case projection: every remaining vendor is assumed healthy.  It therefore
    understates impact during a correlated outage and must not replace a live evaluation.
    """

    vendor_id: str
    strategy_id: str
    dependent_feed_ids: tuple[str, ...]
    sole_source_feed_ids: tuple[str, ...]
    blocking_feed_ids: tuple[str, ...]
    would_block_strategy: bool
    projected_readiness_pct: float
    max_criticality: DependencyCriticality | None


class StrategyDataDependencyEngine:
    """Maps one strategy's data dependencies and gates trading on observed feed health.

    Evaluation is deterministic: the same nodes, policy, timestamp, and observations always
    produce the same report.  The engine performs no I/O and never mutates its configuration.
    """

    def __init__(
        self,
        strategy_id: str,
        dependencies: Sequence[DataDependencyNode],
        policy: ReadinessPolicy | None = None,
    ) -> None:
        _require_text(strategy_id, "strategy_id", DependencyValidationError)
        if isinstance(dependencies, (str, bytes)) or not isinstance(dependencies, Sequence):
            raise DependencyValidationError("dependencies must be a sequence of DataDependencyNode")
        if not dependencies:
            raise DependencyValidationError(
                f"{strategy_id}: at least one data dependency must be mapped"
            )

        nodes: dict[str, DataDependencyNode] = {}
        for dep in dependencies:
            if not isinstance(dep, DataDependencyNode):
                raise DependencyValidationError(
                    "dependencies must contain DataDependencyNode instances"
                )
            if dep.feed_id in nodes:
                raise DependencyValidationError(f"duplicate feed_id in dependencies: {dep.feed_id}")
            nodes[dep.feed_id] = dep

        for dep in nodes.values():
            unknown = sorted(dep.upstream_feed_ids - nodes.keys())
            if unknown:
                raise DependencyValidationError(
                    f"{dep.feed_id}: unknown upstream feed id(s): {', '.join(unknown)}"
                )

        if policy is None:
            policy = ReadinessPolicy()
        elif not isinstance(policy, ReadinessPolicy):
            raise DependencyValidationError("policy must be a ReadinessPolicy instance")

        self.strategy_id = strategy_id
        self.dependencies: Mapping[str, DataDependencyNode] = MappingProxyType(nodes)
        self.policy = policy
        self._topological_order = _topological_order(nodes)

    # ---------------------------------------------------------------- evaluation

    def evaluate_strategy_readiness(
        self,
        current_time_epoch: float,
        observations: Sequence[FeedObservation],
    ) -> StrategyDataDependencyReport:
        """Audit every dependency against its freshness bound, schema contract, and vendors.

        A vendor is usable only when an observation for that exact vendor is healthy, carries no
        schema error, matches the declared schema contract, is not future-dated beyond the policy
        tolerance, and lags by no more than ``max_acceptable_lag_seconds`` (inclusive).  The
        highest-preference usable vendor serves the feed; if none is usable the feed's
        ``effective_failure_response`` decides between blocking and degraded operation.
        """
        now = _require_finite(current_time_epoch, "current_time_epoch", ObservationValidationError)
        if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
            raise ObservationValidationError("observations must be a sequence of FeedObservation")

        warnings: list[str] = []
        by_key = self._index_observations(observations, warnings)

        own_state: dict[str, FeedState] = {}
        own_fault: dict[str, FaultCode] = {}
        selected_vendor: dict[str, str | None] = {}
        observed_lag: dict[str, float | None] = {}

        for feed_id, dep in self.dependencies.items():
            state, fault, vendor, lag = self._resolve_own_state(dep, now, by_key)
            own_state[feed_id] = state
            own_fault[feed_id] = fault
            selected_vendor[feed_id] = vendor
            observed_lag[feed_id] = lag

        resolved_state, resolved_fault = self._propagate(own_state, own_fault)

        assessments: list[DependencyAssessment] = []
        active_sources: dict[str, str] = {}
        blocked: list[str] = []
        fallback: list[str] = []
        degraded: list[str] = []
        unobserved: list[str] = []
        total_weight = 0.0
        earned_weight = 0.0

        for feed_id, dep in self.dependencies.items():
            state = resolved_state[feed_id]
            fault = resolved_fault[feed_id]
            weight = self.policy.criticality_weights[dep.criticality]
            credit = self.policy.credit_for(state)
            total_weight += weight
            earned_weight += weight * credit

            vendor = selected_vendor[feed_id]
            if vendor is not None and state in (FeedState.PRIMARY_ACTIVE, FeedState.FALLBACK_ACTIVE):
                active_sources[feed_id] = vendor

            if state is FeedState.UNAVAILABLE:
                blocked.append(feed_id)
            elif state is FeedState.FALLBACK_ACTIVE:
                fallback.append(feed_id)
            elif state is FeedState.DEGRADED:
                degraded.append(feed_id)
            if not any((feed_id, vendor_id) in by_key for vendor_id in dep.vendors):
                unobserved.append(feed_id)

            assessments.append(
                DependencyAssessment(
                    feed_id=feed_id,
                    criticality=dep.criticality,
                    state=state,
                    active_vendor=vendor,
                    fault=fault,
                    observed_lag_seconds=observed_lag[feed_id],
                    readiness_credit=round(weight * credit, 6),
                    detail=self._describe(dep, state, fault, vendor, observed_lag[feed_id]),
                )
            )

            if state is FeedState.UNAVAILABLE:
                logger.error(
                    "DEPENDENCY BLOCKED [%s] feed=%s criticality=%s fault=%s",
                    self.strategy_id, feed_id, dep.criticality.value, fault.value,
                )
            elif state is not FeedState.PRIMARY_ACTIVE:
                logger.warning(
                    "DEPENDENCY IMPAIRED [%s] feed=%s state=%s vendor=%s fault=%s",
                    self.strategy_id, feed_id, state.value, vendor, fault.value,
                )

        readiness = round(earned_weight / total_weight * 100.0, 6)
        is_ready = not blocked and readiness >= self.policy.minimum_readiness_pct

        notes = (
            f"DATA DEPENDENCY REPORT [{self.strategy_id}] @ {now:.3f}: "
            f"readiness={readiness:.1f}% (min {self.policy.minimum_readiness_pct:.1f}%), "
            f"ready={is_ready}, blocked={len(blocked)}, fallback={len(fallback)}, "
            f"degraded={len(degraded)}, unobserved={len(unobserved)}."
        )
        if is_ready:
            logger.info(notes)
        else:
            logger.error(notes)

        return StrategyDataDependencyReport(
            strategy_id=self.strategy_id,
            evaluated_at_epoch=now,
            readiness_score_pct=readiness,
            is_strategy_ready_to_trade=is_ready,
            assessments=tuple(assessments),
            active_feed_sources=MappingProxyType(dict(active_sources)),
            blocked_dependencies=tuple(blocked),
            fallback_dependencies=tuple(fallback),
            degraded_dependencies=tuple(degraded),
            unobserved_dependencies=tuple(unobserved),
            warnings=tuple(warnings),
            audit_notes=notes,
        )

    # ---------------------------------------------------------------- blast radius

    def assess_vendor_outage(self, vendor_id: str) -> VendorExposure:
        """Project the impact of losing ``vendor_id`` entirely, assuming all other vendors are healthy.

        Use this for outage triage and single-point-of-failure review.  Because every remaining
        vendor is assumed healthy, the projection is an upper bound on resilience: it will not
        reveal a correlated outage in which the fallback is down as well.
        """
        _require_text(vendor_id, "vendor_id", DependencyValidationError)

        own_state: dict[str, FeedState] = {}
        own_fault: dict[str, FaultCode] = {}
        dependent: list[str] = []
        sole_source: list[str] = []

        for feed_id, dep in self.dependencies.items():
            if vendor_id not in dep.vendors:
                own_state[feed_id] = FeedState.PRIMARY_ACTIVE
                own_fault[feed_id] = FaultCode.NONE
                continue
            dependent.append(feed_id)
            if len(dep.vendors) == 1:
                sole_source.append(feed_id)
                own_state[feed_id] = FeedState.UNAVAILABLE
                own_fault[feed_id] = FaultCode.NO_OBSERVATION
            elif dep.vendors[0] == vendor_id:
                own_state[feed_id] = FeedState.FALLBACK_ACTIVE
                own_fault[feed_id] = FaultCode.NONE
            else:
                own_state[feed_id] = FeedState.PRIMARY_ACTIVE
                own_fault[feed_id] = FaultCode.NONE

        resolved_state, _ = self._propagate(own_state, own_fault)

        blocking: list[str] = []
        total_weight = 0.0
        earned_weight = 0.0
        for feed_id, dep in self.dependencies.items():
            state = resolved_state[feed_id]
            weight = self.policy.criticality_weights[dep.criticality]
            total_weight += weight
            earned_weight += weight * self.policy.credit_for(state)
            if state is FeedState.UNAVAILABLE:
                blocking.append(feed_id)

        criticalities = [self.dependencies[feed_id].criticality for feed_id in dependent]
        max_criticality = (
            max(criticalities, key=lambda c: _CRITICALITY_RANK[c]) if criticalities else None
        )
        projected = round(earned_weight / total_weight * 100.0, 6)

        return VendorExposure(
            vendor_id=vendor_id,
            strategy_id=self.strategy_id,
            dependent_feed_ids=tuple(dependent),
            sole_source_feed_ids=tuple(sole_source),
            blocking_feed_ids=tuple(blocking),
            would_block_strategy=bool(blocking) or projected < self.policy.minimum_readiness_pct,
            projected_readiness_pct=projected,
            max_criticality=max_criticality,
        )

    def single_source_feeds(self) -> tuple[str, ...]:
        """Feed ids with no configured fallback vendor, in registration order."""
        return tuple(
            feed_id for feed_id, dep in self.dependencies.items() if len(dep.vendors) == 1
        )

    # ---------------------------------------------------------------- internals

    def _index_observations(
        self,
        observations: Sequence[FeedObservation],
        warnings: list[str],
    ) -> dict[tuple[str, str], FeedObservation]:
        """Index observations by ``(feed_id, vendor_id)``, resolving conflicts conservatively.

        Unmapped feeds and vendors outside a feed's configured hierarchy are recorded as
        warnings rather than raised, so configuration drift degrades the report instead of
        crashing the gate out of the trading path.  Duplicate observations for the same pair
        keep the least favourable one: a duplicate is a caller defect and must never silently
        raise readiness.
        """
        indexed: dict[tuple[str, str], FeedObservation] = {}
        for obs in observations:
            if not isinstance(obs, FeedObservation):
                raise ObservationValidationError(
                    "observations must contain FeedObservation instances"
                )
            dep = self.dependencies.get(obs.feed_id)
            if dep is None:
                warnings.append(f"observation for unmapped feed ignored: {obs.feed_id}")
                logger.warning(
                    "UNMAPPED OBSERVATION [%s] feed=%s vendor=%s",
                    self.strategy_id, obs.feed_id, obs.vendor_id,
                )
                continue
            if obs.vendor_id not in dep.vendors:
                warnings.append(
                    f"observation from vendor outside hierarchy ignored: "
                    f"{obs.feed_id}/{obs.vendor_id}"
                )
                logger.warning(
                    "UNKNOWN VENDOR OBSERVATION [%s] feed=%s vendor=%s",
                    self.strategy_id, obs.feed_id, obs.vendor_id,
                )
                continue
            key = (obs.feed_id, obs.vendor_id)
            existing = indexed.get(key)
            if existing is None:
                indexed[key] = obs
                continue
            warnings.append(
                f"duplicate observation resolved conservatively: {obs.feed_id}/{obs.vendor_id}"
            )
            logger.warning(
                "DUPLICATE OBSERVATION [%s] feed=%s vendor=%s",
                self.strategy_id, obs.feed_id, obs.vendor_id,
            )
            indexed[key] = _least_favourable(existing, obs)
        return indexed

    def _resolve_own_state(
        self,
        dep: DataDependencyNode,
        now: float,
        by_key: Mapping[tuple[str, str], FeedObservation],
    ) -> tuple[FeedState, FaultCode, str | None, float | None]:
        """Pick the highest-preference usable vendor for one feed from observed evidence."""
        primary_fault = FaultCode.NO_OBSERVATION
        primary_lag: float | None = None

        for rank, vendor in enumerate(dep.vendors):
            obs = by_key.get((dep.feed_id, vendor))
            if obs is None:
                fault = FaultCode.NO_OBSERVATION
                lag: float | None = None
            else:
                lag = now - obs.last_updated_epoch
                fault = self._classify(dep, obs, lag)
            if rank == 0:
                primary_fault, primary_lag = fault, lag
            if fault is FaultCode.NONE:
                if rank == 0:
                    return FeedState.PRIMARY_ACTIVE, FaultCode.NONE, vendor, lag
                return FeedState.FALLBACK_ACTIVE, primary_fault, vendor, lag

        return FeedState.UNAVAILABLE, primary_fault, None, primary_lag

    def _classify(self, dep: DataDependencyNode, obs: FeedObservation, lag: float) -> FaultCode:
        """Classify one vendor observation, or confirm it is usable.

        A timestamp further ahead of the evaluation clock than the policy tolerance is a
        clock-integrity fault, never fresh data: without this check a vendor whose clock runs
        fast reports a negative lag and stays permanently inside its freshness bound.
        """
        if lag < -self.policy.future_timestamp_tolerance_seconds:
            return FaultCode.CLOCK_SKEW
        if not obs.is_healthy:
            return FaultCode.UNHEALTHY
        if obs.schema_error is not None:
            return FaultCode.SCHEMA_ERROR
        if dep.schema_contract_version and obs.schema_version != dep.schema_contract_version:
            return FaultCode.SCHEMA_MISMATCH
        if lag > dep.max_acceptable_lag_seconds:
            return FaultCode.STALE
        return FaultCode.NONE

    def _propagate(
        self,
        own_state: Mapping[str, FeedState],
        own_fault: Mapping[str, FaultCode],
    ) -> tuple[dict[str, FeedState], dict[str, FaultCode]]:
        """Fold upstream states into downstream states in topological order.

        A feed is never healthier than its worst upstream: a derived feed publishing a fresh
        timestamp off a dead input is stale data wearing a fresh label.  A feed left UNAVAILABLE
        whose response is DEGRADE is then clamped to DEGRADED, so UNAVAILABLE in the resolved
        map always means "the strategy must not trade on this".
        """
        resolved_state: dict[str, FeedState] = {}
        resolved_fault: dict[str, FaultCode] = {}

        for feed_id in self._topological_order:
            dep = self.dependencies[feed_id]
            state = own_state[feed_id]
            fault = own_fault[feed_id]
            for upstream in sorted(dep.upstream_feed_ids):
                upstream_state = resolved_state[upstream]
                if _STATE_RANK[upstream_state] < _STATE_RANK[state]:
                    state = upstream_state
                    fault = FaultCode.UPSTREAM_IMPAIRED
            if (
                state is FeedState.UNAVAILABLE
                and dep.effective_failure_response is FailureResponse.DEGRADE
            ):
                state = FeedState.DEGRADED
            resolved_state[feed_id] = state
            resolved_fault[feed_id] = fault

        return resolved_state, resolved_fault

    def _describe(
        self,
        dep: DataDependencyNode,
        state: FeedState,
        fault: FaultCode,
        vendor: str | None,
        lag: float | None,
    ) -> str:
        lag_text = "n/a" if lag is None else f"{lag:.3f}s"
        return (
            f"{dep.feed_name} [{dep.criticality.value}] state={state.value} "
            f"vendor={vendor or 'NONE'} fault={fault.value} "
            f"lag={lag_text} sla={dep.max_acceptable_lag_seconds:.3f}s "
            f"on_failure={dep.effective_failure_response.value}"
        )


class DataDependencyPortfolio:
    """Aggregates per-strategy dependency maps for portfolio-wide vendor outage triage."""

    def __init__(self, engines: Sequence[StrategyDataDependencyEngine]) -> None:
        if isinstance(engines, (str, bytes)) or not isinstance(engines, Sequence):
            raise DependencyValidationError(
                "engines must be a sequence of StrategyDataDependencyEngine"
            )
        if not engines:
            raise DependencyValidationError("at least one strategy engine is required")
        registry: dict[str, StrategyDataDependencyEngine] = {}
        for engine in engines:
            if not isinstance(engine, StrategyDataDependencyEngine):
                raise DependencyValidationError(
                    "engines must contain StrategyDataDependencyEngine instances"
                )
            if engine.strategy_id in registry:
                raise DependencyValidationError(f"duplicate strategy_id: {engine.strategy_id}")
            registry[engine.strategy_id] = engine
        self.engines: Mapping[str, StrategyDataDependencyEngine] = MappingProxyType(registry)

    def assess_vendor_outage(self, vendor_id: str) -> tuple[VendorExposure, ...]:
        """Per-strategy exposure to losing ``vendor_id``, in registration order."""
        return tuple(engine.assess_vendor_outage(vendor_id) for engine in self.engines.values())

    def strategies_blocked_by(self, vendor_id: str) -> tuple[str, ...]:
        """Strategy ids that a total loss of ``vendor_id`` would stop, in registration order."""
        return tuple(
            exposure.strategy_id
            for exposure in self.assess_vendor_outage(vendor_id)
            if exposure.would_block_strategy
        )

    def vendor_ids(self) -> tuple[str, ...]:
        """Every vendor referenced by any registered strategy, sorted."""
        vendors: set[str] = set()
        for engine in self.engines.values():
            for dep in engine.dependencies.values():
                vendors.update(dep.vendors)
        return tuple(sorted(vendors))


def _least_favourable(left: FeedObservation, right: FeedObservation) -> FeedObservation:
    """Pick the observation that can only lower readiness, for conflicting duplicates."""
    left_bad = (not left.is_healthy) or left.schema_error is not None
    right_bad = (not right.is_healthy) or right.schema_error is not None
    if left_bad != right_bad:
        return left if left_bad else right
    return left if left.last_updated_epoch <= right.last_updated_epoch else right


def _topological_order(nodes: Mapping[str, DataDependencyNode]) -> tuple[str, ...]:
    """Order feeds upstream-first, rejecting cycles.  Deterministic given registration order."""
    indegree = {feed_id: len(node.upstream_feed_ids) for feed_id, node in nodes.items()}
    downstream: dict[str, list[str]] = {feed_id: [] for feed_id in nodes}
    for feed_id, node in nodes.items():
        for upstream in sorted(node.upstream_feed_ids):
            downstream[upstream].append(feed_id)

    ready = [feed_id for feed_id in nodes if indegree[feed_id] == 0]
    order: list[str] = []
    while ready:
        feed_id = ready.pop(0)
        order.append(feed_id)
        for child in downstream[feed_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)

    if len(order) != len(nodes):
        remaining = sorted(set(nodes) - set(order))
        raise DependencyValidationError(
            "dependency graph contains a cycle involving: " + ", ".join(remaining)
        )
    return tuple(order)
