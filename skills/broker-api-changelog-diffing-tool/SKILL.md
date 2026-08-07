---
name: broker-api-changelog-diffing-tool
description: Use when updating broker SDKs or API integrations to automatically diff
  release-over-release OpenAPI/JSON schemas, detecting breaking endpoint removals,
  renamed parameters, mutated enums, request body changes, and response model alterations
  before production deployment.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- api-changelog
- schema-diffing
- openapi
- breaking-changes
- ci-cd-security
- quantitative-engineering
brokers_frameworks:
- Schema Diffing Engine
- Python OpenAPI Parser
- Quantitative Standards
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill prior to upgrading broker SDK versions or pulling new API specifications (e.g., Binance OpenAPI specs, Coinbase REST schemas, IBKR Client Portal specs). Broker API version updates frequently introduce silent breaking changes — removing required fields in nested response objects, altering parameter types, or deleting enum values. This skill rigorously diffs API schemas release-over-release to flag these changes in CI/CD build pipelines, adhering to strict institutional quant standards.

## Prerequisites

- Baseline (older) OpenAPI/JSON schema snapshot.
- Target (newer) OpenAPI/JSON schema snapshot.

## Workflow

1. **Ingest Old and New API Schemas**:
   - Load OpenAPI/Swagger v2 or v3 JSON/YAML specifications for $V_{\text{old}}$ and $V_{\text{new}}$.

2. **Diff Endpoint & Path Hierarchies**:
   - Identify deleted paths, added endpoints, and modified HTTP methods.

3. **Diff Request Parameters & Bodies**:
   - recursively scan for removed fields, modified field data types, deleted enums, and new mandatory parameters.
   - Specifically handles `requestBody` objects across different `content-type` specifications.

4. **Diff Response Schemas**:
   - Ensure the broker does not remove payload fields that trading algorithms might depend on to parse market state, executions, or balances.
   - Check for response type mutations.

5. **Classify Breaking Change Severity**:
   - `REMOVED_ENDPOINT` / `CRITICAL_BREAKING`
   - `REMOVED_FIELD` / `HIGH_BREAKING`
   - `TYPE_MUTATION` / `HIGH_BREAKING`
   - `ENUM_MUTATION` / `HIGH_BREAKING`
   - `REMOVED_RESPONSE_FIELD` / `HIGH_BREAKING`
   - `RESPONSE_TYPE_MUTATION` / `HIGH_BREAKING`
   - `NEW_REQUIRED_PARAMETER` / `MEDIUM_BREAKING`
   - `ADDED_OPTIONAL_FIELD` / `NON_BREAKING_INFO`

6. **Generate CI/CD Compatibility Report**:
   - Fail CI build if critical, high, or medium breaking changes are detected without adapter updates.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Silent Enum Value Modifications**: Broker removing order states or cancellation reason strings, breaking quantitative state machines.
- **Ignoring Nested Payload Objects**: Diffing top-level JSON keys while missing breaking changes inside nested arrays or request bodies.
- **Treating Optional Field Additions as Breaking**: Raising false alarm build failures for non-breaking backward-compatible additions.

## Verification

- Input two OpenAPI schemas where an endpoint is removed, a response field is removed, an enum is removed, and a field type is mutated.
- Verify non-breaking optional field additions are correctly classified as `INFO`.
- Run `python scripts/test_changelog_differ.py` and confirm 100% pass rate.

## Related Skills

- `broker-api-versioning-migration-playbook`
- `broker-api-deprecation-notice-monitoring`
- `sandbox-vs-production-endpoint-drift`
