---
name: tick-data-schema-versioning
description: >-
  Use when deploying distributed tick processing pipelines to attach explicit schema version headers (v1, v2, v3), execute backward/forward schema migration adapters, and prevent deserialization crashes across consumer microservices.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "schema-versioning", "migration-adapters", "backward-compatibility", "event-schema", "serialization"]
brokers_frameworks: ["Tick Schema Versioner", "Python Real-Time Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when upgrading internal tick data structures across distributed microservices (e.g., adding high-precision nanosecond timestamps, venue venue_id codes, or bid/ask size arrays). If consumer microservices (strategy workers, risk monitors, DB writers) are updated at different times, unversioned schema changes cause deserialization exceptions or misaligned data fields. This skill tags tick payloads with explicit version headers ($V$) and executes version migration adapters.

## Prerequisites

- Base tick schema definition and target version migration mappings.
- Backward compatibility rules for missing or added fields.

## Workflow

1. **Tag Payload with Schema Version Header**:
   - Prepend `schema_version` integer header (e.g. `version=2`) to serialized tick payloads.

2. **Intercept Incoming Payload Version**:
   - Inspect payload version $V_{\text{payload}}$ against reader's expected target version $V_{\text{target}}$.

3. **Execute Version Migration Adapter**:
   - $V_{\text{payload}} < V_{\text{target}}$: Apply upgrade migration adapter (e.g., populate missing $V_2$ fields with default values).
   - $V_{\text{payload}} > V_{\text{target}}$: Apply downgrade migration adapter (e.g., strip $V_3$ extensions for legacy $V_2$ reader).

4. **Deliver Normalized Version Payload**:
   - Pass clean, normalized tick object to strategy consumer.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Implicit Version Inferences**: Guessing schema version from payload key count instead of reading explicit version headers.
- **Breaking Field Type Mutations**: Changing a field from integer to string without registering a version upgrade adapter.
- **Dropping Unknown Fields in Migration**: Stripping unrecognized extension fields during intermediate hop migrations.

## Verification

- Submit $V_1$ legacy tick payload to $V_2$ target reader and verify automatic field upgrade.
- Submit $V_2$ tick payload to $V_1$ legacy reader and verify graceful backward compatibility.
- Run `python scripts/test_schema_versioner.py` and confirm 100% pass rate.

## Related Skills

- `broker-api-changelog-diffing-tool`
- `grpc-streaming-for-internal-service-communication`
- `kafka-based-tick-distribution-at-scale`
---
