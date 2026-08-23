# Workflows for Data Lineage Tracking for Audit and Debugging

1. **DAG Graph Construction**:
   - Register nodes with a SHA-256 content fingerprint, a `pipeline_version`, and a
     timezone-aware ISO-8601 UTC timestamp. Node classification must be one of
     `DATA_SOURCE`, `TRANSFORMATION`, `FEATURE_STORE`, `MODEL_INFERENCE`,
     `ORDER_DECISION`.
   - Add directed dependency edges `parent -> child`. Reject self-edges and any edge
     that would close a cycle (check reachability from the child to the parent before
     inserting). Hold exactly one edge per ordered `(parent, child)` pair.
   - Registration is **append-only**. A revised or backfilled artifact is a NEW node id
     linked to its predecessor, never a mutation of the existing node.

2. **Upstream Traversal (root cause)**:
   - Iterative breadth-first search backward along parent edges from the decision node.
     Iterative, not recursive, so traversal depth is bounded by graph size rather than
     by the Python recursion limit.
   - Collect `DATA_SOURCE` nodes into `root_cause_sources`.
   - Collect parentless nodes that are **not** `DATA_SOURCE` into `orphan_root_nodes` —
     these are broken lineage and must be repaired before the trace is treated as
     complete.

3. **Downstream Traversal (impact)**:
   - Iterative breadth-first search forward along child edges from the suspect artifact.
   - Collect `MODEL_INFERENCE` and `ORDER_DECISION` nodes as the blast radius.

4. **Audit Reporting**:
   - Emit traversed node ids in deterministic breadth-first visit order so repeated runs
     over the same graph produce identical reports.
   - Emit the SHA-256 fingerprint of every traversed node (`node_fingerprints`) alongside
     the traversal lists.
   - Compute `is_dag_valid` by running Kahn's algorithm over the induced subgraph of the
     traversed nodes. Never hard-code it: a report that asserts DAG validity without
     measuring it is a false audit claim.
