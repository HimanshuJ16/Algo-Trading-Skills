---
name: data-lineage-tracking-for-audit-and-debugging
description: >-
  Use when an anomalous signal or unexplained backtest jump needs upstream and
  downstream tracing, maintaining an append-only lineage graph across market data,
  feature store and model decisions.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: data-lineage, dag-lineage, auditability, feature-store-lineage, root-cause-analysis, impact-analysis, openlineage
  brokers_frameworks: "OpenLineage Standard; Python Dataclasses"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in quantitative research, feature store engineering, and live trading systems to maintain end-to-end data lineage DAGs. When a live trading model generates an anomalous order signal or a backtest exhibits unexplained performance jumps, data lineage allows engineers to perform **Upstream Root Cause Analysis** (tracing a trade signal $S_t$ back to raw vendor ticks and transformation parameters) or **Downstream Impact Analysis** (identifying all downstream models impacted by a corrupted market data payload).

## When NOT to Use

- **As an OpenLineage-compliant emitter.** The reference engine is conceptually aligned with the OpenLineage dataset/job model but emits no `RunEvent` / `JobEvent` / `DatasetEvent` payloads, no run UUIDs, and no facets. If a downstream consumer (Marquez, a catalog, a data-contract gate) expects OpenLineage JSON, use an OpenLineage client — do not present this engine's report as OpenLineage lineage.
- **As the system of record for regulatory retention.** The engine holds lineage in memory. Durable retention, access control, and the operator-identity element of an audit trail are the caller's responsibility.
- **As a data-quality validator.** Lineage tells you *which* artifacts a decision depended on and *whether their content changed*; it does not tell you whether the content was correct. Pair it with `data-pipeline-schema-contract-testing`.
- **For cyclic or feedback pipelines.** A model whose output feeds back into its own input features cannot be represented: "the root cause" and "the blast radius" stop being well-defined. Break the loop by versioning each generation as a distinct node (`FEAT@v1` → `MODEL@v1` → `FEAT@v2`).

## Prerequisites

- Node classification schema: `DATA_SOURCE`, `TRANSFORMATION`, `FEATURE_STORE`, `MODEL_INFERENCE`, `ORDER_DECISION`. These five strings are enforced — an unrecognised type is rejected at registration rather than silently excluded from traversal results.
- Node metadata: `data_hash_sha256` (computed by the engine from the payload), `pipeline_version`, `timestamp_utc` (timezone-aware ISO-8601; naive timestamps are rejected), `schema_contract_version` where a contract governs the artifact.

## Workflow

1. **DAG Node & Edge Registration**:
   - Register data artifacts and transformations with SHA-256 content fingerprinting (`str` payloads are UTF-8 encoded; `bytes` payloads, e.g. Parquet blocks, are hashed as-is).
   - Establish parent-child dependency edges ($A \to B$). An edge that would close a cycle, or a self-edge, is rejected.
   - **Decision point — a payload changed under an existing node id.** Do NOT re-register the node with the new payload: registration is append-only and a conflicting re-registration raises. Register the revised artifact under a new node id (`FEAT_MOMENTUM@v2`) and link it to its predecessor, so the original decision remains reproducible.
2. **Upstream Root Cause Traversal**:
   - Given a target node (e.g. `ORDER_DECISION_99`), traverse parent edges breadth-first to isolate root raw data sources.
   - **Decision point — the report returns `orphan_root_nodes`.** Lineage terminated at a node that has no parents and is not a `DATA_SOURCE`. The trace is incomplete, not clean: repair the missing edge before concluding which source caused the anomaly.
3. **Downstream Impact Traversal**:
   - Given a corrupt data source (e.g. `BLOOMBERG_TICK_RAW`), traverse child edges breadth-first to flag all affected downstream features and active trading models (`MODEL_INFERENCE`, `ORDER_DECISION`).
4. **Audit Report Generation**: Output structured `DataLineageAuditReport`, including the SHA-256 fingerprint trace (`node_fingerprints`) for every traversed node and the measured `is_dag_valid` flag.
   - **Decision point — `is_dag_valid` is `False`.** The traversed subgraph contains a cycle (possible only if the edge maps were populated outside `add_dependency`, e.g. rehydrated from an external store). Treat the root-cause and impact lists as unreliable and repair the graph first.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Silently overwriting a lineage node on backfill**: re-registering `FEAT_MOMENTUM` with a corrected payload destroys the fingerprint that proves what the model actually consumed, making the original decision unreproducible. Where the lineage graph forms part of a US broker-dealer's electronic records, 17 CFR 240.17a-4(f)(2)(i) requires either a complete time-stamped audit trail permitting re-creation of the original record if it is modified or deleted (paragraph (A)) or WORM storage (paragraph (B)). Record revisions as new nodes.
- **Un-tracked Schema Drift**: Modifying feature transformation logic without updating lineage graph pipeline versions, making historical backtest reproduction impossible.
- **Dangling Nodes**: Registering model inferences without linking them back to the specific feature store snapshot version used during inference. Such a trace returns an empty `root_cause_sources` list — which reads like "no upstream source implicated" but actually means "lineage is broken"; check `orphan_root_nodes` before concluding anything.
- **Mistyped node classifications**: recording an inference as `"MODEL"` rather than `"MODEL_INFERENCE"` would exclude it from every impact traversal, producing a false all-clear during a data-corruption incident. The engine rejects unknown types for this reason.
- **Ignoring Data Fingerprinting**: Tracking dataset names without computing SHA-256 content hashes, failing to detect silent data mutation or backfills.
- **Treating traversal output as an ordered set**: audit artifacts must be reproducible byte-for-byte. Emit traversal results in deterministic visit order, never in set-iteration order.

## Verification

- Instantiate `DataLineageTrackerEngine`. Build a DAG: Raw Tick Feed (`SRC_1`) $\to$ VWAP Transformation (`TR_1`) $\to$ Momentum Feature (`FEAT_1`) $\to$ Signal Engine (`MODEL_1`). Trigger **Upstream Traversal** on `MODEL_1` and verify it traces back to `SRC_1` with `orphan_root_nodes == []`. Trigger **Downstream Traversal** on `SRC_1` and verify it identifies `MODEL_1` as an impacted node.
- Negative checks: adding `MODEL_1 -> SRC_1` must raise (cycle); re-registering `FEAT_1` with a different payload must raise (append-only); registering a node typed `"MODEL"` must raise (unknown type); a naive timestamp must raise.
- Run `python -m unittest discover -s skills/data-lineage-tracking-for-audit-and-debugging/scripts`.

## Related Skills

- `data-pipeline-schema-contract-testing`
- `historical-tick-data-storage-and-compaction`
- `backtest-determinism-and-reproducibility`
