# Standards for Data Lineage Tracking for Audit and Debugging

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Lineage Integrity | ALL feature store computations and trade signals MUST maintain complete DAG lineage back to raw market data. A traversal terminating at a parentless non-`DATA_SOURCE` node MUST be reported as broken lineage, not as a clean audit. |
| Content Fingerprinting | ALL data lineage nodes MUST record SHA-256 content hashes and pipeline version numbers. |
| Append-Only Records | Lineage nodes MUST NOT be overwritten in place. A revised or backfilled artifact MUST be recorded as a new node id linked to its predecessor. |
| Acyclicity | Dependency edges MUST NOT close a cycle. `is_dag_valid` MUST be measured over the traversed subgraph, never asserted. |
| Traversal Depth | Upstream and downstream lineage graph traversals MUST support unlimited depth, which requires iterative (queue-based) traversal rather than recursion. |
| Determinism | Audit reports MUST be reproducible: traversal output ordered by visit order, never by set iteration order. |
| Timestamp Discipline | Node timestamps MUST be timezone-aware ISO-8601 and canonicalised to UTC; naive timestamps MUST be rejected at registration. |

## Regulatory touchpoints

| Jurisdiction | Instrument | Relevance | Status |
|---|---|---|---|
| US (broker-dealers, SBS entities) | 17 CFR 240.17a-4(f)(2)(i), as amended Oct 12 2022 (Rel. 34-96034; effective Jan 3 2023, compliance date May 3 2023) | Where lineage records form part of records required under Rules 17a-3/17a-4, they must be preserved either (A) with a complete time-stamped audit trail covering all modifications and deletions, the date/time of each action, the identity of the actor where applicable, and whatever else is needed to "permit re-creation of the original record if it is modified or deleted", or (B) exclusively in non-rewriteable, non-erasable (WORM) format. Directly motivates the append-only node rule. | Mandatory where applicable; scope is the broker-dealer's required records, not every internal engineering artifact. |

**Applicability caveat**: an internal feature-store lineage graph is not automatically a
required record. Confirm with compliance which artifacts fall inside the 17a-3/17a-4
perimeter before relying on this engine as a retention control; the engine itself provides
no durable storage, access control, or actor identity.

## Framework alignment

| Framework | Alignment | Limitation |
|---|---|---|
| OpenLineage | Shares the conceptual model of jobs consuming and producing datasets, with lineage expressed through inputs and outputs. | This engine is NOT an OpenLineage implementation: it emits no `RunEvent` / `JobEvent` / `DatasetEvent`, no run UUIDs, and no facets (`schema`, `dataSource`, `columnLineage`, ...). Use an OpenLineage client if a spec-compliant consumer is downstream. Source: <https://openlineage.io/docs/spec/object-model>. |
