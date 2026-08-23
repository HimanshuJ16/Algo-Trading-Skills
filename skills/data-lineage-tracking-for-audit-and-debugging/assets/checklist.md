# Pre-Flight Checklist

## Graph construction
- [ ] Are data lineage nodes registered with SHA-256 content hashes and pipeline versions?
- [ ] Is every node classified as one of `DATA_SOURCE`, `TRANSFORMATION`, `FEATURE_STORE`, `MODEL_INFERENCE`, `ORDER_DECISION` (no ad-hoc types that traversals would silently skip)?
- [ ] Are node timestamps timezone-aware ISO-8601 UTC?
- [ ] Are parent-child data dependency edges ($A \to B$) maintained in a DAG, with self-edges and cycle-closing edges rejected?

## Immutability
- [ ] Is node registration append-only — does a conflicting re-registration raise rather than overwrite?
- [ ] Are backfills and corrections recorded as NEW versioned node ids linked to their predecessors?
- [ ] If the graph is inside the firm's regulated recordkeeping perimeter, is it retained under a control that satisfies 17 CFR 240.17a-4(f)(2)(i)(A) (audit trail) or (B) (WORM)?

## Traversal & reporting
- [ ] Is Upstream Root Cause Analysis functional for debugging anomalous model signals?
- [ ] Is `orphan_root_nodes` checked before a trace is declared complete (empty `root_cause_sources` may mean broken lineage, not "no source implicated")?
- [ ] Is Downstream Impact Analysis functional for evaluating market data corruption blast radius?
- [ ] Is `is_dag_valid` measured over the traversed subgraph rather than hard-coded?
- [ ] Is traversal output deterministic (visit order) so two runs produce identical audit artifacts?
- [ ] Does the report carry the SHA-256 fingerprint trace for every traversed node?

## Scope
- [ ] Is it clear to consumers that this output is not OpenLineage-compliant lineage?
- [ ] Is durable retention / access control handled outside this in-memory engine?
