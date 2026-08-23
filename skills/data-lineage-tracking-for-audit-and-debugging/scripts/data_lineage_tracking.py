"""Data lineage tracking engine for audit and debugging of market data pipelines.

Models a pipeline as an append-only directed acyclic graph (DAG) of content-
fingerprinted artifacts:

    DATA_SOURCE -> TRANSFORMATION -> FEATURE_STORE -> MODEL_INFERENCE -> ORDER_DECISION

and supports two audit traversals over it:

- **Upstream root cause**: from an anomalous decision back to the raw
  ``DATA_SOURCE`` nodes (and to any *orphan* root that is not a declared data
  source, which signals broken lineage rather than a clean audit).
- **Downstream impact**: from a corrupted source forward to every affected
  ``MODEL_INFERENCE`` / ``ORDER_DECISION`` node (the blast radius).

Design constraints that the implementation enforces rather than assumes:

- **Append-only nodes.** Re-registering an existing ``node_id`` with different
  content raises instead of silently overwriting it. Overwriting a lineage
  record destroys the very history the graph exists to prove; where the graph
  forms part of a US broker-dealer's electronic records, 17 CFR
  240.17a-4(f)(2)(i) requires either a complete time-stamped audit trail that
  "will permit re-creation of the original record if it is modified or
  deleted" (paragraph (A)) or WORM storage (paragraph (B)). Record a revised
  artifact as a NEW node id (e.g. ``FEAT_MOMENTUM@v2``) linked to its
  predecessor, never by mutating the old one.
- **Acyclicity by construction.** ``add_dependency`` refuses an edge that would
  close a cycle. A cycle makes "the root cause" and "the blast radius"
  ill-defined, and reporting ``is_dag_valid=True`` unconditionally would assert
  a property that was never checked.
- **Declared node types only.** A mistyped ``node_type`` (``"MODEL"`` instead of
  ``"MODEL_INFERENCE"``) would silently produce an empty impacted-models list --
  a false all-clear from an audit tool -- so unknown types are rejected.
- **Deterministic output.** Traversal results are emitted in breadth-first
  visit order, not in set-iteration order, so two runs over the same graph
  produce identical audit reports.

Limitations (documented, deliberate):

- This engine is conceptually aligned with the OpenLineage dataset/job model
  but is **not** an OpenLineage implementation: it emits no ``RunEvent`` /
  ``JobEvent`` / ``DatasetEvent`` payloads, no run UUIDs, and no facets. Do not
  present its output as OpenLineage-compliant lineage.
- Lineage is held in memory only. Durable retention, access control, and the
  operator-identity element of an audit trail are the caller's responsibility.
- ``data_hash_sha256`` fingerprints whatever payload the caller supplies. It
  detects mutation of that payload only; it cannot attest that the payload
  actually corresponds to the artifact named by ``node_id``.
- Edge identity is the ``(parent, child)`` pair: exactly one dependency edge is
  held per ordered pair.
- The cycle check costs one O(V+E) reachability search per ``add_dependency``.
  Appending a leaf (the common ingestion pattern) short-circuits immediately;
  wiring an edge into the middle of a large existing graph does not.
"""

import hashlib
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Union

logger = logging.getLogger(__name__)

#: Node classifications recognised by the traversals. ``DATA_SOURCE`` terminates
#: an upstream root-cause trace; ``MODEL_INFERENCE`` / ``ORDER_DECISION`` are
#: collected by a downstream impact trace.
VALID_NODE_TYPES = frozenset({
    "DATA_SOURCE",
    "TRANSFORMATION",
    "FEATURE_STORE",
    "MODEL_INFERENCE",
    "ORDER_DECISION",
})

#: Node types treated as an actionable downstream impact (live trading surface).
IMPACT_NODE_TYPES = frozenset({"MODEL_INFERENCE", "ORDER_DECISION"})


@dataclass
class LineageNode:
    node_id: str                       # Unique node identifier (e.g. 'SRC_BLOOMBERG_AAPL')
    node_type: str                     # One of VALID_NODE_TYPES
    pipeline_version: str
    data_hash_sha256: str
    timestamp_utc: str                 # Canonical ISO-8601 UTC instant ('...+00:00')
    description: str


@dataclass
class LineageEdge:
    parent_node_id: str
    child_node_id: str
    transformation_name: str


@dataclass
class DataLineageAuditReport:
    target_node_id: str
    traversal_direction: str            # 'UPSTREAM_ROOT_CAUSE' or 'DOWNSTREAM_IMPACT'
    total_nodes_traversed: int          # Includes the target/source node itself
    traversed_node_ids: List[str]       # Breadth-first visit order (deterministic)
    root_cause_sources: List[str]
    impacted_downstream_models: List[str]
    is_dag_valid: bool                  # Computed over the traversed subgraph
    orphan_root_nodes: List[str] = field(default_factory=list)
    node_fingerprints: Dict[str, str] = field(default_factory=dict)


def _validate_identifier(value: str, label: str) -> str:
    """Rejects non-string or blank identifiers, which would corrupt the graph keys."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string, got {value!r}.")
    return value


def _canonical_utc_timestamp(timestamp_utc: str) -> str:
    """
    Parses an ISO-8601 timestamp and returns it canonicalised to UTC.

    A lineage record whose timestamp is unparseable or timezone-naive cannot be
    ordered against other records or against exchange session boundaries, so it
    is rejected at registration rather than discovered during an incident.
    Trailing 'Z' is accepted and normalised to '+00:00'.
    """
    _validate_identifier(timestamp_utc, "timestamp_utc")
    text = timestamp_utc.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"timestamp_utc {timestamp_utc!r} is not a valid ISO-8601 timestamp: {exc}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"timestamp_utc {timestamp_utc!r} is timezone-naive. Supply an explicit "
            "UTC offset (e.g. '2026-03-01T10:00:00Z') so lineage records are orderable."
        )
    return parsed.astimezone(timezone.utc).isoformat()


class DataLineageTrackerEngine:
    """
    Append-only DAG lineage tracker for market data pipelines, feature stores, and
    model decision graphs, supporting upstream root-cause and downstream impact audits.
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, LineageNode] = {}
        self.parent_to_children: Dict[str, List[LineageEdge]] = {}
        self.child_to_parents: Dict[str, List[LineageEdge]] = {}

    def register_node(
        self,
        node_id: str,
        node_type: str,
        pipeline_version: str,
        raw_payload: Union[str, bytes],
        timestamp_utc: str,
        description: str = ""
    ) -> LineageNode:
        """
        Registers a content-fingerprinted lineage artifact.

        ``raw_payload`` is SHA-256 hashed (``str`` is encoded as UTF-8; ``bytes``
        is hashed as-is, so binary artifacts such as Parquet blocks can be
        fingerprinted without a lossy decode).

        Re-registering an existing ``node_id`` is idempotent only if every field
        matches; a conflicting re-registration raises ``ValueError``. To record a
        revised artifact, register a new node id and link it to its predecessor.
        """
        _validate_identifier(node_id, "node_id")
        _validate_identifier(node_type, "node_type")
        _validate_identifier(pipeline_version, "pipeline_version")

        normalized_type = node_type.strip().upper()
        if normalized_type not in VALID_NODE_TYPES:
            raise ValueError(
                f"Unknown node_type {node_type!r} for node {node_id!r}. "
                f"Expected one of {sorted(VALID_NODE_TYPES)}. An unrecognised type is "
                "never matched by the traversals and would yield a silent false all-clear."
            )

        if isinstance(raw_payload, bytes):
            payload_bytes = raw_payload
        elif isinstance(raw_payload, str):
            payload_bytes = raw_payload.encode("utf-8")
        else:
            raise TypeError(
                f"raw_payload for node {node_id!r} must be str or bytes, "
                f"got {type(raw_payload).__name__}."
            )

        node = LineageNode(
            node_id=node_id,
            node_type=normalized_type,
            pipeline_version=pipeline_version,
            data_hash_sha256=hashlib.sha256(payload_bytes).hexdigest(),
            timestamp_utc=_canonical_utc_timestamp(timestamp_utc),
            description=description
        )

        existing = self.nodes.get(node_id)
        if existing is not None:
            if existing != node:
                raise ValueError(
                    f"Node {node_id!r} is already registered with different content "
                    f"(existing hash {existing.data_hash_sha256[:12]}..., new hash "
                    f"{node.data_hash_sha256[:12]}...). Lineage records are append-only: "
                    "register the revised artifact under a new node id instead of "
                    "overwriting the original."
                )
            return existing

        self.nodes[node_id] = node
        self.parent_to_children.setdefault(node_id, [])
        self.child_to_parents.setdefault(node_id, [])
        return node

    def add_dependency(
        self,
        parent_node_id: str,
        child_node_id: str,
        transformation_name: str
    ) -> LineageEdge:
        """
        Records a directed dependency ``parent -> child``.

        Refuses self-loops and any edge that would close a cycle: with a cycle in
        the graph, "the root cause" and "the blast radius" are no longer
        well-defined. Re-adding an identical edge is a no-op; re-adding the same
        ``(parent, child)`` pair under a different ``transformation_name`` raises,
        because two conflicting provenance claims cannot both be true.
        """
        if parent_node_id not in self.nodes or child_node_id not in self.nodes:
            raise ValueError(f"Both parent ({parent_node_id}) and child ({child_node_id}) must be registered.")
        _validate_identifier(transformation_name, "transformation_name")

        if parent_node_id == child_node_id:
            raise ValueError(f"Self-dependency on node {parent_node_id!r} is not a valid lineage edge.")

        for existing_edge in self.parent_to_children[parent_node_id]:
            if existing_edge.child_node_id == child_node_id:
                if existing_edge.transformation_name == transformation_name:
                    return existing_edge
                raise ValueError(
                    f"Dependency {parent_node_id!r} -> {child_node_id!r} already exists as "
                    f"{existing_edge.transformation_name!r}; cannot re-declare it as "
                    f"{transformation_name!r}."
                )

        # parent already reachable *from* child => the new edge closes a cycle.
        if self._is_reachable_forward(child_node_id, parent_node_id):
            raise ValueError(
                f"Dependency {parent_node_id!r} -> {child_node_id!r} would create a cycle "
                f"({parent_node_id!r} is already downstream of {child_node_id!r}). Lineage "
                "must remain a DAG for root-cause and impact traversals to terminate on a "
                "well-defined answer."
            )

        edge = LineageEdge(
            parent_node_id=parent_node_id,
            child_node_id=child_node_id,
            transformation_name=transformation_name
        )
        self.parent_to_children[parent_node_id].append(edge)
        self.child_to_parents[child_node_id].append(edge)
        return edge

    def _is_reachable_forward(self, start_node_id: str, target_node_id: str) -> bool:
        """Returns True if ``target_node_id`` is reachable from ``start_node_id`` via child edges."""
        if start_node_id == target_node_id:
            return True
        visited = {start_node_id}
        queue = deque([start_node_id])
        while queue:
            curr = queue.popleft()
            for edge in self.parent_to_children.get(curr, []):
                if edge.child_node_id == target_node_id:
                    return True
                if edge.child_node_id not in visited:
                    visited.add(edge.child_node_id)
                    queue.append(edge.child_node_id)
        return False

    def _traverse(self, start_node_id: str, forward: bool) -> List[str]:
        """Breadth-first traversal returning visited node ids in deterministic visit order."""
        visited = {start_node_id}
        order = [start_node_id]
        queue = deque([start_node_id])
        while queue:
            curr = queue.popleft()
            if forward:
                neighbours = [e.child_node_id for e in self.parent_to_children.get(curr, [])]
            else:
                neighbours = [e.parent_node_id for e in self.child_to_parents.get(curr, [])]
            for neighbour in neighbours:
                if neighbour not in self.nodes:
                    # Only reachable if the edge maps were populated outside
                    # add_dependency (e.g. rehydrated from a truncated store).
                    # Fail with a diagnosable message rather than a bare KeyError
                    # in the middle of an incident.
                    raise ValueError(
                        f"Lineage graph is corrupt: edge from {curr!r} references "
                        f"unregistered node {neighbour!r}."
                    )
                if neighbour not in visited:
                    visited.add(neighbour)
                    order.append(neighbour)
                    queue.append(neighbour)
        return order

    def _subgraph_is_acyclic(self, node_ids: List[str]) -> bool:
        """
        Kahn's algorithm over the subgraph induced by ``node_ids``.

        ``add_dependency`` already refuses cycle-closing edges, so this is a
        defence-in-depth check: it keeps ``is_dag_valid`` an actual measurement
        rather than a hard-coded claim if the internal edge maps are mutated
        directly or rehydrated from an external store.
        """
        members = set(node_ids)
        indegree = {n: 0 for n in members}
        for n in members:
            for edge in self.parent_to_children.get(n, []):
                if edge.child_node_id in members:
                    indegree[edge.child_node_id] += 1
        queue = deque(n for n in node_ids if indegree[n] == 0)
        settled = 0
        while queue:
            curr = queue.popleft()
            settled += 1
            for edge in self.parent_to_children.get(curr, []):
                child = edge.child_node_id
                if child in members:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        queue.append(child)
        return settled == len(members)

    def trace_upstream_root_cause(self, target_node_id: str) -> DataLineageAuditReport:
        """
        Traverses the graph backward from a target node (typically ``MODEL_INFERENCE``
        or ``ORDER_DECISION``) to every reachable ``DATA_SOURCE``.

        Nodes with no parents that are *not* declared ``DATA_SOURCE`` are reported
        separately in ``orphan_root_nodes``: they are broken lineage (an inference
        never linked to its feature snapshot), and an audit that reported only
        ``root_cause_sources`` would read as complete when it is not.
        ``total_nodes_traversed`` includes the target node itself.
        """
        if target_node_id not in self.nodes:
            raise ValueError(f"Target node {target_node_id} not found in lineage graph.")

        order = self._traverse(target_node_id, forward=False)
        root_sources: List[str] = []
        orphan_roots: List[str] = []
        for node_id in order:
            node = self.nodes[node_id]
            if node.node_type == "DATA_SOURCE":
                root_sources.append(node_id)
            elif not self.child_to_parents.get(node_id):
                orphan_roots.append(node_id)

        is_dag_valid = self._subgraph_is_acyclic(order)
        logger.info(
            "UPSTREAM LINEAGE AUDIT [%s]: traversed=%d root_sources=%s orphan_roots=%s dag_valid=%s",
            target_node_id, len(order), root_sources, orphan_roots, is_dag_valid,
        )
        if orphan_roots:
            logger.warning(
                "UPSTREAM LINEAGE AUDIT [%s]: %d dangling node(s) terminate lineage without a "
                "DATA_SOURCE: %s", target_node_id, len(orphan_roots), orphan_roots,
            )

        return DataLineageAuditReport(
            target_node_id=target_node_id,
            traversal_direction="UPSTREAM_ROOT_CAUSE",
            total_nodes_traversed=len(order),
            traversed_node_ids=order,
            root_cause_sources=root_sources,
            impacted_downstream_models=[],
            is_dag_valid=is_dag_valid,
            orphan_root_nodes=orphan_roots,
            node_fingerprints={n: self.nodes[n].data_hash_sha256 for n in order},
        )

    def trace_downstream_impact(self, source_node_id: str) -> DataLineageAuditReport:
        """
        Traverses the graph forward from a suspect artifact to every reachable
        ``MODEL_INFERENCE`` / ``ORDER_DECISION`` node (the blast radius of a
        corrupted payload). ``total_nodes_traversed`` includes the source node itself.
        """
        if source_node_id not in self.nodes:
            raise ValueError(f"Source node {source_node_id} not found in lineage graph.")

        order = self._traverse(source_node_id, forward=True)
        impacted_models = [n for n in order if self.nodes[n].node_type in IMPACT_NODE_TYPES]

        is_dag_valid = self._subgraph_is_acyclic(order)
        logger.info(
            "DOWNSTREAM LINEAGE AUDIT [%s]: traversed=%d impacted_models=%s dag_valid=%s",
            source_node_id, len(order), impacted_models, is_dag_valid,
        )

        return DataLineageAuditReport(
            target_node_id=source_node_id,
            traversal_direction="DOWNSTREAM_IMPACT",
            total_nodes_traversed=len(order),
            traversed_node_ids=order,
            root_cause_sources=[],
            impacted_downstream_models=impacted_models,
            is_dag_valid=is_dag_valid,
            node_fingerprints={n: self.nodes[n].data_hash_sha256 for n in order},
        )
