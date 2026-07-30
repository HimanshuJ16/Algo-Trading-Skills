import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass
class LineageNode:
    node_id: str                       # Unique node identifier (e.g. 'SRC_BLOOMBERG_AAPL')
    node_type: str                     # 'DATA_SOURCE', 'TRANSFORMATION', 'FEATURE_STORE', 'MODEL_INFERENCE', 'ORDER_DECISION'
    pipeline_version: str
    data_hash_sha256: str
    timestamp_utc: str
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
    total_nodes_traversed: int
    traversed_node_ids: List[str]
    root_cause_sources: List[str]
    impacted_downstream_models: List[str]
    is_dag_valid: bool

class DataLineageTrackerEngine:
    """
    Quantitative data lineage tracking engine for auditing market data pipelines,
    feature store lineage, and model decision graphs to perform root cause debugging and impact analysis.
    """
    def __init__(self):
        self.nodes: Dict[str, LineageNode] = {}
        self.parent_to_children: Dict[str, List[LineageEdge]] = {}
        self.child_to_parents: Dict[str, List[LineageEdge]] = {}

    def register_node(
        self,
        node_id: str,
        node_type: str,
        pipeline_version: str,
        raw_payload: str,
        timestamp_utc: str,
        description: str = ""
    ) -> LineageNode:
        data_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
        node = LineageNode(
            node_id=node_id,
            node_type=node_type.upper(),
            pipeline_version=pipeline_version,
            data_hash_sha256=data_hash,
            timestamp_utc=timestamp_utc,
            description=description
        )
        self.nodes[node_id] = node
        if node_id not in self.parent_to_children:
            self.parent_to_children[node_id] = []
        if node_id not in self.child_to_parents:
            self.child_to_parents[node_id] = []
        return node

    def add_dependency(self, parent_node_id: str, child_node_id: str, transformation_name: str):
        if parent_node_id not in self.nodes or child_node_id not in self.nodes:
            raise ValueError(f"Both parent ({parent_node_id}) and child ({child_node_id}) must be registered.")
        
        edge = LineageEdge(parent_node_id=parent_node_id, child_node_id=child_node_id, transformation_name=transformation_name)
        self.parent_to_children[parent_node_id].append(edge)
        self.child_to_parents[child_node_id].append(edge)

    def trace_upstream_root_cause(self, target_node_id: str) -> DataLineageAuditReport:
        """
        Traverses DAG backward from target node (e.g. MODEL_INFERENCE or ORDER_DECISION) to find all root data sources.
        """
        if target_node_id not in self.nodes:
            raise ValueError(f"Target node {target_node_id} not found in lineage graph.")

        visited: Set[str] = set()
        queue = [target_node_id]
        root_sources = []

        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)

            node = self.nodes[curr]
            if node.node_type == "DATA_SOURCE":
                root_sources.append(curr)

            # Traverse parents
            for edge in self.child_to_parents.get(curr, []):
                if edge.parent_node_id not in visited:
                    queue.append(edge.parent_node_id)

        logger.info(f"UPSTREAM LINEAGE AUDIT [{target_node_id}]: Traversed {len(visited)} nodes -> Root Sources: {root_sources}")

        return DataLineageAuditReport(
            target_node_id=target_node_id,
            traversal_direction="UPSTREAM_ROOT_CAUSE",
            total_nodes_traversed=len(visited),
            traversed_node_ids=list(visited),
            root_cause_sources=root_sources,
            impacted_downstream_models=[],
            is_dag_valid=True
        )

    def trace_downstream_impact(self, source_node_id: str) -> DataLineageAuditReport:
        """
        Traverses DAG forward from raw data source to find all impacted downstream features and model inferences.
        """
        if source_node_id not in self.nodes:
            raise ValueError(f"Source node {source_node_id} not found in lineage graph.")

        visited: Set[str] = set()
        queue = [source_node_id]
        impacted_models = []

        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)

            node = self.nodes[curr]
            if node.node_type in ["MODEL_INFERENCE", "ORDER_DECISION"]:
                impacted_models.append(curr)

            # Traverse children
            for edge in self.parent_to_children.get(curr, []):
                if edge.child_node_id not in visited:
                    queue.append(edge.child_node_id)

        logger.info(f"DOWNSTREAM LINEAGE AUDIT [{source_node_id}]: Traversed {len(visited)} nodes -> Impacted Models: {impacted_models}")

        return DataLineageAuditReport(
            target_node_id=source_node_id,
            traversal_direction="DOWNSTREAM_IMPACT",
            total_nodes_traversed=len(visited),
            traversed_node_ids=list(visited),
            root_cause_sources=[],
            impacted_downstream_models=impacted_models,
            is_dag_valid=True
        )
