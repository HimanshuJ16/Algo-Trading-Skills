"""Deterministic dependency and blast-radius analysis for trading risk controls.

Edges point from a dependency to its consumer.  The analyzer is pure and dependency-free;
production inventory collection, authorization, persistence, and alerting remain adapter concerns.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any


class MappingError(Exception):
    """Base class for invalid graph definitions or analysis requests."""


class GraphValidationError(MappingError):
    """The dependency graph is structurally invalid."""


class NodeKind(str, Enum):
    DATA_FEED = "DATA_FEED"
    STATE_STORE = "STATE_STORE"
    SERVICE = "SERVICE"
    CONTROL = "CONTROL"
    ACTUATOR = "ACTUATOR"
    EXTERNAL = "EXTERNAL"


class Criticality(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FailureResponse(str, Enum):
    """Consumer response when a dependency contract is not satisfied."""

    DEGRADE = "DEGRADE"
    FAIL_CLOSED = "FAIL_CLOSED"
    FAIL_OPEN = "FAIL_OPEN"


class ImpactMode(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAIL_CLOSED = "FAIL_CLOSED"
    FAIL_OPEN = "FAIL_OPEN"
    DIRECT_FAILURE = "DIRECT_FAILURE"


class IssueSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


_IMPACT_RANK = {
    ImpactMode.HEALTHY: 0,
    ImpactMode.DEGRADED: 1,
    ImpactMode.FAIL_CLOSED: 2,
    ImpactMode.FAIL_OPEN: 3,
    ImpactMode.DIRECT_FAILURE: 4,
}

_CRITICALITY_RANK = {
    Criticality.LOW: 0,
    Criticality.MEDIUM: 1,
    Criticality.HIGH: 2,
    Criticality.CRITICAL: 3,
}

_RESPONSE_TO_IMPACT = {
    FailureResponse.DEGRADE: ImpactMode.DEGRADED,
    FailureResponse.FAIL_CLOSED: ImpactMode.FAIL_CLOSED,
    FailureResponse.FAIL_OPEN: ImpactMode.FAIL_OPEN,
}


@dataclass(frozen=True)
class RiskNode:
    node_id: str
    kind: NodeKind
    criticality: Criticality
    owner: str
    description: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    stale_after_seconds: float | None = None
    recovery_objective_seconds: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.node_id, "node_id")
        _require_text(self.owner, "owner")
        _require_text(self.description, "description")
        if not isinstance(self.kind, NodeKind) or not isinstance(
            self.criticality, Criticality
        ):
            raise GraphValidationError("kind and criticality must use their enum types")
        if not isinstance(self.scopes, frozenset):
            raise GraphValidationError("scopes must be a frozenset")
        if any(
            not isinstance(scope, str)
            or not scope.strip()
            or scope != scope.strip()
            for scope in self.scopes
        ):
            raise GraphValidationError("scopes must contain normalized non-empty values")
        _validate_positive_number(self.stale_after_seconds, "stale_after_seconds")
        _validate_positive_number(
            self.recovery_objective_seconds, "recovery_objective_seconds"
        )


@dataclass(frozen=True)
class DependencyEdge:
    dependency_id: str
    consumer_id: str
    response: FailureResponse
    rationale: str
    redundancy_group: str | None = None
    monitored: bool = True

    def __post_init__(self) -> None:
        _require_text(self.dependency_id, "dependency_id")
        _require_text(self.consumer_id, "consumer_id")
        _require_text(self.rationale, "rationale")
        if not isinstance(self.response, FailureResponse):
            raise GraphValidationError("response must use FailureResponse")
        if self.redundancy_group is not None:
            _require_text(self.redundancy_group, "redundancy_group")
        if not isinstance(self.monitored, bool):
            raise GraphValidationError("monitored must be a boolean")


@dataclass(frozen=True)
class MappingIssue:
    severity: IssueSeverity
    code: str
    message: str
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImpactRecord:
    node_id: str
    kind: NodeKind
    criticality: Criticality
    mode: ImpactMode
    triggered_by: tuple[str, ...]
    owner: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class BlastRadiusReport:
    failed_nodes: tuple[str, ...]
    impacts: tuple[ImpactRecord, ...]
    affected_controls: tuple[str, ...]
    unsafe_controls: tuple[str, ...]
    fail_closed_controls: tuple[str, ...]
    affected_scopes: tuple[str, ...]
    responsible_owners: tuple[str, ...]
    maximum_criticality: Criticality | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible, deterministically ordered representation."""

        return {
            "failed_nodes": list(self.failed_nodes),
            "impacts": [
                {
                    **asdict(impact),
                    "kind": impact.kind.value,
                    "criticality": impact.criticality.value,
                    "mode": impact.mode.value,
                    "triggered_by": list(impact.triggered_by),
                    "scopes": list(impact.scopes),
                }
                for impact in self.impacts
            ],
            "affected_controls": list(self.affected_controls),
            "unsafe_controls": list(self.unsafe_controls),
            "fail_closed_controls": list(self.fail_closed_controls),
            "affected_scopes": list(self.affected_scopes),
            "responsible_owners": list(self.responsible_owners),
            "maximum_criticality": (
                self.maximum_criticality.value if self.maximum_criticality else None
            ),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class SinglePointOfFailure:
    node_id: str
    affected_high_criticality_controls: tuple[str, ...]
    unsafe_controls: tuple[str, ...]


class RiskDependencyMapper:
    """Immutable graph supporting validation and conservative failure propagation."""

    def __init__(
        self,
        nodes: Sequence[RiskNode],
        edges: Sequence[DependencyEdge],
    ) -> None:
        if not nodes:
            raise GraphValidationError("at least one node is required")

        node_map: dict[str, RiskNode] = {}
        for node in nodes:
            if node.node_id in node_map:
                raise GraphValidationError(f"duplicate node_id: {node.node_id}")
            node_map[node.node_id] = node

        edge_keys: set[tuple[str, str]] = set()
        incoming: dict[str, list[DependencyEdge]] = defaultdict(list)
        outgoing: dict[str, list[DependencyEdge]] = defaultdict(list)
        normalized_edges: list[DependencyEdge] = []
        for edge in edges:
            if edge.dependency_id not in node_map:
                raise GraphValidationError(
                    f"unknown dependency node: {edge.dependency_id}"
                )
            if edge.consumer_id not in node_map:
                raise GraphValidationError(f"unknown consumer node: {edge.consumer_id}")
            if edge.dependency_id == edge.consumer_id:
                raise GraphValidationError(
                    f"self dependency is not permitted: {edge.dependency_id}"
                )
            key = (edge.dependency_id, edge.consumer_id)
            if key in edge_keys:
                raise GraphValidationError(
                    f"duplicate dependency edge: {edge.dependency_id} -> {edge.consumer_id}"
                )
            edge_keys.add(key)
            normalized_edges.append(edge)
            incoming[edge.consumer_id].append(edge)
            outgoing[edge.dependency_id].append(edge)

        self._nodes: Mapping[str, RiskNode] = MappingProxyType(node_map)
        self._edges = tuple(
            sorted(normalized_edges, key=lambda e: (e.dependency_id, e.consumer_id))
        )
        self._incoming: Mapping[str, tuple[DependencyEdge, ...]] = MappingProxyType(
            {
                node_id: tuple(sorted(items, key=lambda e: e.dependency_id))
                for node_id, items in incoming.items()
            }
        )
        self._outgoing: Mapping[str, tuple[DependencyEdge, ...]] = MappingProxyType(
            {
                node_id: tuple(sorted(items, key=lambda e: e.consumer_id))
                for node_id, items in outgoing.items()
            }
        )

    @property
    def nodes(self) -> tuple[RiskNode, ...]:
        return tuple(self._nodes[node_id] for node_id in sorted(self._nodes))

    @property
    def edges(self) -> tuple[DependencyEdge, ...]:
        return self._edges

    def validate(self) -> tuple[MappingIssue, ...]:
        """Return deterministic design issues without rejecting analyzable topology."""

        issues: list[MappingIssue] = []
        for cycle in self._find_cycles():
            issues.append(
                MappingIssue(
                    IssueSeverity.ERROR,
                    "DEPENDENCY_CYCLE",
                    "Dependency cycle requires an explicit bounded failure model",
                    cycle,
                )
            )

        for node in self.nodes:
            incoming = self._incoming.get(node.node_id, ())
            outgoing = self._outgoing.get(node.node_id, ())
            if node.kind is NodeKind.CONTROL and not incoming:
                issues.append(
                    MappingIssue(
                        IssueSeverity.ERROR,
                        "CONTROL_WITHOUT_DEPENDENCIES",
                        "Risk control has no mapped inputs or state dependencies",
                        (node.node_id,),
                    )
                )
            if not incoming and not outgoing:
                issues.append(
                    MappingIssue(
                        IssueSeverity.WARNING,
                        "ORPHAN_NODE",
                        "Node is disconnected from the dependency graph",
                        (node.node_id,),
                    )
                )
            if node.kind is NodeKind.DATA_FEED and node.stale_after_seconds is None:
                issues.append(
                    MappingIssue(
                        IssueSeverity.ERROR,
                        "FEED_WITHOUT_STALENESS_BOUND",
                        "Data feed has no maximum accepted age",
                        (node.node_id,),
                    )
                )

        grouped: dict[tuple[str, str], list[DependencyEdge]] = defaultdict(list)
        for edge in self._edges:
            if edge.response is FailureResponse.FAIL_OPEN:
                issues.append(
                    MappingIssue(
                        IssueSeverity.ERROR,
                        "FAIL_OPEN_DEPENDENCY",
                        "Dependency loss can leave a consumer permissive or unsafe",
                        (edge.dependency_id, edge.consumer_id),
                    )
                )
            if not edge.monitored:
                issues.append(
                    MappingIssue(
                        IssueSeverity.WARNING,
                        "UNMONITORED_DEPENDENCY",
                        "Dependency contract has no health or freshness monitoring",
                        (edge.dependency_id, edge.consumer_id),
                    )
                )
            if edge.redundancy_group is not None:
                grouped[(edge.consumer_id, edge.redundancy_group)].append(edge)

        for (consumer_id, group), group_edges in grouped.items():
            if len(group_edges) < 2:
                issues.append(
                    MappingIssue(
                        IssueSeverity.ERROR,
                        "INVALID_REDUNDANCY_GROUP",
                        f"Redundancy group {group!r} has fewer than two alternatives",
                        (consumer_id, *(edge.dependency_id for edge in group_edges)),
                    )
                )
            responses = {edge.response for edge in group_edges}
            if len(responses) > 1:
                issues.append(
                    MappingIssue(
                        IssueSeverity.WARNING,
                        "INCONSISTENT_REDUNDANCY_RESPONSE",
                        f"Redundancy group {group!r} has inconsistent failure responses",
                        (consumer_id, *(edge.dependency_id for edge in group_edges)),
                    )
                )

        return tuple(
            sorted(
                issues,
                key=lambda issue: (
                    _issue_rank(issue.severity),
                    issue.code,
                    issue.node_ids,
                ),
            )
        )

    def analyze(self, failed_nodes: Iterable[str]) -> BlastRadiusReport:
        """Propagate simultaneous failures to a deterministic fixed point.

        A partially lost redundancy group degrades its consumer.  Once a dependency is not
        healthy, an ungrouped edge applies its declared response.  This is intentionally
        conservative and avoids treating degraded risk data as fully trustworthy.
        """

        requested_failures = tuple(failed_nodes)
        if any(
            not isinstance(node_id, str)
            or not node_id.strip()
            or node_id != node_id.strip()
            for node_id in requested_failures
        ):
            raise GraphValidationError("failed node IDs must be normalized non-empty strings")
        failed = tuple(sorted(set(requested_failures)))
        if not failed:
            raise GraphValidationError("at least one failed node is required")
        unknown = tuple(node_id for node_id in failed if node_id not in self._nodes)
        if unknown:
            raise GraphValidationError(f"unknown failed nodes: {', '.join(unknown)}")

        modes: dict[str, ImpactMode] = {
            node_id: ImpactMode.DIRECT_FAILURE for node_id in failed
        }
        triggers: dict[str, set[str]] = {node_id: {node_id} for node_id in failed}

        changed = True
        while changed:
            changed = False
            for consumer_id in sorted(self._nodes):
                if consumer_id in failed:
                    continue
                candidate_mode, candidate_triggers = self._consumer_impact(
                    consumer_id, modes, triggers
                )
                existing = modes.get(consumer_id, ImpactMode.HEALTHY)
                if _IMPACT_RANK[candidate_mode] > _IMPACT_RANK[existing]:
                    modes[consumer_id] = candidate_mode
                    triggers[consumer_id] = candidate_triggers
                    changed = True
                elif candidate_mode is existing and candidate_mode is not ImpactMode.HEALTHY:
                    prior = triggers.setdefault(consumer_id, set())
                    size_before = len(prior)
                    prior.update(candidate_triggers)
                    changed = changed or len(prior) != size_before

        impacts = tuple(
            ImpactRecord(
                node_id=node_id,
                kind=self._nodes[node_id].kind,
                criticality=self._nodes[node_id].criticality,
                mode=modes[node_id],
                triggered_by=tuple(sorted(triggers[node_id])),
                owner=self._nodes[node_id].owner,
                scopes=tuple(sorted(self._nodes[node_id].scopes)),
            )
            for node_id in sorted(modes)
        )
        affected_controls = tuple(
            impact.node_id for impact in impacts if impact.kind is NodeKind.CONTROL
        )
        unsafe_controls = tuple(
            impact.node_id
            for impact in impacts
            if impact.kind is NodeKind.CONTROL and impact.mode is ImpactMode.FAIL_OPEN
        )
        fail_closed_controls = tuple(
            impact.node_id
            for impact in impacts
            if impact.kind is NodeKind.CONTROL and impact.mode is ImpactMode.FAIL_CLOSED
        )
        maximum_criticality = max(
            (impact.criticality for impact in impacts),
            key=lambda value: _CRITICALITY_RANK[value],
            default=None,
        )
        return BlastRadiusReport(
            failed_nodes=failed,
            impacts=impacts,
            affected_controls=affected_controls,
            unsafe_controls=unsafe_controls,
            fail_closed_controls=fail_closed_controls,
            affected_scopes=tuple(
                sorted({scope for impact in impacts for scope in impact.scopes})
            ),
            responsible_owners=tuple(sorted({impact.owner for impact in impacts})),
            maximum_criticality=maximum_criticality,
        )

    def single_points_of_failure(self) -> tuple[SinglePointOfFailure, ...]:
        """Find nodes whose individual failure affects HIGH/CRITICAL controls."""

        results: list[SinglePointOfFailure] = []
        for node_id in sorted(self._nodes):
            if self._nodes[node_id].kind is NodeKind.CONTROL:
                continue
            report = self.analyze((node_id,))
            high_controls = tuple(
                impact.node_id
                for impact in report.impacts
                if impact.kind is NodeKind.CONTROL
                and impact.mode is not ImpactMode.DEGRADED
                and _CRITICALITY_RANK[impact.criticality]
                >= _CRITICALITY_RANK[Criticality.HIGH]
            )
            if high_controls:
                results.append(
                    SinglePointOfFailure(
                        node_id=node_id,
                        affected_high_criticality_controls=high_controls,
                        unsafe_controls=report.unsafe_controls,
                    )
                )
        return tuple(results)

    def to_dot(self, report: BlastRadiusReport | None = None) -> str:
        """Render a deterministic Graphviz DOT representation for review artifacts."""

        impact_by_node = (
            {impact.node_id: impact.mode for impact in report.impacts} if report else {}
        )
        colors = {
            ImpactMode.DEGRADED: "gold",
            ImpactMode.FAIL_CLOSED: "orange",
            ImpactMode.FAIL_OPEN: "red",
            ImpactMode.DIRECT_FAILURE: "black",
        }
        lines = ["digraph risk_dependencies {", "  rankdir=LR;"]
        for node in self.nodes:
            attributes = [f'label="{_dot_escape(node.node_id)}\\n{node.kind.value}"']
            mode = impact_by_node.get(node.node_id)
            if mode is not None:
                attributes.extend(
                    [
                        'style="filled"',
                        f'fillcolor="{colors[mode]}"',
                        f'fontcolor="{"white" if mode is ImpactMode.DIRECT_FAILURE else "black"}"',
                    ]
                )
            lines.append(f'  "{_dot_escape(node.node_id)}" [{", ".join(attributes)}];')
        for edge in self._edges:
            label = edge.response.value
            if edge.redundancy_group:
                label += f"/{edge.redundancy_group}"
            lines.append(
                f'  "{_dot_escape(edge.dependency_id)}" -> '
                f'"{_dot_escape(edge.consumer_id)}" [label="{_dot_escape(label)}"];'
            )
        lines.append("}")
        return "\n".join(lines)

    def _consumer_impact(
        self,
        consumer_id: str,
        modes: Mapping[str, ImpactMode],
        triggers: Mapping[str, set[str]],
    ) -> tuple[ImpactMode, set[str]]:
        incoming = self._incoming.get(consumer_id, ())
        candidates: list[tuple[ImpactMode, set[str]]] = []
        redundancy_groups: dict[str, list[DependencyEdge]] = defaultdict(list)

        for edge in incoming:
            if edge.redundancy_group is not None:
                redundancy_groups[edge.redundancy_group].append(edge)
                continue
            dependency_mode = modes.get(edge.dependency_id, ImpactMode.HEALTHY)
            if dependency_mode is not ImpactMode.HEALTHY:
                candidates.append(
                    (
                        ImpactMode.DEGRADED
                        if dependency_mode is ImpactMode.DEGRADED
                        else _RESPONSE_TO_IMPACT[edge.response],
                        set(triggers.get(edge.dependency_id, {edge.dependency_id})),
                    )
                )

        for group_edges in redundancy_groups.values():
            affected = [
                edge
                for edge in group_edges
                if modes.get(edge.dependency_id, ImpactMode.HEALTHY)
                is not ImpactMode.HEALTHY
            ]
            if not affected:
                continue
            if len(affected) < len(group_edges):
                candidates.append(
                    (
                        ImpactMode.DEGRADED,
                        set().union(
                            *(
                                triggers.get(edge.dependency_id, {edge.dependency_id})
                                for edge in affected
                            )
                        ),
                    )
                )
            else:
                worst = max(
                    (_RESPONSE_TO_IMPACT[edge.response] for edge in group_edges),
                    key=lambda mode: _IMPACT_RANK[mode],
                )
                candidates.append(
                    (
                        worst,
                        set().union(
                            *(
                                triggers.get(edge.dependency_id, {edge.dependency_id})
                                for edge in affected
                            )
                        ),
                    )
                )

        if not candidates:
            return ImpactMode.HEALTHY, set()
        highest_rank = max(_IMPACT_RANK[mode] for mode, _ in candidates)
        highest = [item for item in candidates if _IMPACT_RANK[item[0]] == highest_rank]
        return highest[0][0], set().union(*(trigger_set for _, trigger_set in highest))

    def _find_cycles(self) -> tuple[tuple[str, ...], ...]:
        """Return canonical detected cycles; duplicate rotations are removed."""

        cycles: set[tuple[str, ...]] = set()
        color: dict[str, int] = {node_id: 0 for node_id in self._nodes}
        for root in sorted(self._nodes):
            if color[root] != 0:
                continue
            path: list[str] = [root]
            positions = {root: 0}
            color[root] = 1
            stack: list[tuple[str, Any]] = [
                (root, iter(self._outgoing.get(root, ())))
            ]
            while stack:
                node_id, outgoing = stack[-1]
                try:
                    target = next(outgoing).consumer_id
                except StopIteration:
                    color[node_id] = 2
                    stack.pop()
                    positions.pop(node_id)
                    path.pop()
                    continue
                if color[target] == 0:
                    color[target] = 1
                    positions[target] = len(path)
                    path.append(target)
                    stack.append((target, iter(self._outgoing.get(target, ()))))
                elif color[target] == 1:
                    cycle = tuple(path[positions[target] :])
                    rotations = [
                        cycle[index:] + cycle[:index] for index in range(len(cycle))
                    ]
                    cycles.add(min(rotations))
        return tuple(sorted(cycles))


def _issue_rank(severity: IssueSeverity) -> int:
    return {
        IssueSeverity.ERROR: 0,
        IssueSeverity.WARNING: 1,
        IssueSeverity.INFO: 2,
    }[severity]


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise GraphValidationError(f"{name} is required")
    if value != value.strip():
        raise GraphValidationError(f"{name} must not contain surrounding whitespace")


def _validate_positive_number(value: float | None, name: str) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise GraphValidationError(f"{name} must be a finite positive number")


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
