---
name: broker-api-changelog-diffing-tool
description: >-
  Use when updating broker SDKs or API integrations to automatically diff release-over-release OpenAPI/JSON schemas, detecting breaking endpoint removals, renamed parameters, and type mutations before production deployment.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "api-changelog", "schema-diffing", "openapi", "breaking-changes", "ci-cd-security"]
brokers_frameworks: ["Schema Diffing Engine", "Python OpenAPI Parser"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill prior to upgrading broker SDK versions or pulling new API specifications (e.g. Binance OpenAPI specs, Coinbase REST schemas, IBKR Client Portal specs). Broker API version updates frequently introduce silent breaking changes — removing required fields, altering parameter types, or changing status enum values. This skill automatically diffs API schemas release-over-release to flag breaking changes in CI/CD build pipelines.

## Prerequisites

- Baseline (older) OpenAPI/JSON schema snapshot.
- Target (newer) OpenAPI/JSON schema snapshot.

## Workflow

1. **Ingest Old and New API Schemas**:
   - Load OpenAPI/Swagger v2 or v3 JSON/YAML specifications for $V_{\text{old}}$ and $V_{\text{new}}$.

2. **Diff Endpoint & Path Hierarchies**:
   - Identify deleted paths, added endpoints, and modified HTTP methods.

3. **Diff Request & Response Schemas**:
   - Scan for removed request/response fields, modified field data types, and new mandatory parameters.

4. **Classify Breaking Change Severity**:
   - `REMOVED_ENDPOINT` (Critical)
   - `REMOVED_FIELD` (High)
   - `TYPE_MUTATION` (High)
   - `NEW_REQUIRED_PARAMETER` (Medium)
   - `ADDED_OPTIONAL_FIELD` (Non-breaking Info)

5. **Generate CI/CD Compatibility Report**:
   - Fail CI build if critical or high breaking changes are detected without adapter updates.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Silent Enum Value Modifications**: Broker adding new order states or cancellation reason strings without updating top-level schema types.
- **Ignoring Nested Payload Objects**: Diffing top-level JSON keys while missing breaking changes inside nested arrays or objects.
- **Treating Optional Field Additions as Breaking**: Raising false alarm build failures for non-breaking backward-compatible additions.

## Verification

- Input two OpenAPI schemas where an endpoint is removed and a field type is changed, verifying breaking change detection.
- Verify non-breaking optional field additions are correctly classified as `INFO`.
- Run `python scripts/test_changelog_differ.py` and confirm 100% pass rate.

## Related Skills

- `broker-api-versioning-migration-playbook`
- `broker-api-deprecation-notice-monitoring`
- `sandbox-vs-production-endpoint-drift`
---
