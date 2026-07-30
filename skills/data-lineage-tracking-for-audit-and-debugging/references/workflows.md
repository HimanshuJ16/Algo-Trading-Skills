# Workflows for Data Lineage Tracking for Audit and Debugging

1. **DAG Graph Construction**:
   - Register nodes and directed dependency edges.
2. **Upstream Traversal**:
   - Depth-first search (DFS) backward from decision to root data source.
3. **Downstream Traversal**:
   - Depth-first search (DFS) forward from data source to active trading models.
4. **Audit Reporting**:
   - Output lineage graph DAG structure and SHA-256 fingerprint trace.
