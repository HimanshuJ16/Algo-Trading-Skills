# Standards for Data Lineage Tracking for Audit and Debugging

| Metric | Engineering Standard |
|---|---|
| Lineage Integrity | ALL feature store computations and trade signals MUST maintain complete DAG lineage back to raw market data. |
| Content Fingerprinting | ALL data lineage nodes MUST record SHA-256 content hashes and pipeline version numbers. |
| Traversal Depth | Upstream and downstream lineage graph traversals MUST support unlimited depth. |
