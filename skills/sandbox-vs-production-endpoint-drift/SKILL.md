---
name: sandbox-vs-production-endpoint-drift
description: >-
  Production-grade API schema drift detector, response payload comparator, header audit tool, and environment parity reporter auditing discrepancies between sandbox test environments and live production broker endpoints.
domain: DevSecOps & Quality Assurance
subdomain: API Schema Drift & Environment Parity
tags: ["schema-drift", "sandbox-parity", "api-contract", "broker-integration", "devsecops", "openapi-diff"]
brokers_frameworks: ["Endpoint Drift Detector", "Python Dataclasses", "DevSecOps Standards"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when auditing or maintaining parity between broker sandbox/paper environments and live production endpoints. Broker developers frequently update sandbox endpoints to test upcoming features or alter production payload schemas without updating paper trading documentation. Schema drift (missing fields, data type shifts like string vs float, missing rate-limit headers, or status code discrepancies) causes production algorithms to crash when promoted from sandbox to live trading.

## Prerequisites

- Sample JSON responses from sandbox and production endpoints (`sandbox_json`, `prod_json`).
- Sample HTTP headers and status codes (`sandbox_headers`, `prod_headers`, `sandbox_status`, `prod_status`).

## Workflow

1. **JSON Payload Schema Comparison**:
   - Compare field presence: flag fields present in production but missing in sandbox as `CRITICAL`.
   - Flag fields present in sandbox but absent in production as `WARNING`.
2. **Data Type Mismatch Audit**:
   - Audit data types for common fields (e.g. `price`: float vs string $\implies$ `CRITICAL`).
3. **Header & Rate Limit Audit**:
   - Check for missing rate-limit headers (`x-ratelimit-limit`, `retry-after`) in sandbox.
4. **Status Code Discrepancy Check**:
   - Flag status code mismatches (e.g. sandbox 200 vs prod 400 for identical invalid payload). Output `EndpointDriftReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **False Sense of Security in Sandbox**: Assuming identical code behavior in production because unit tests passed in sandbox.
- **Ignoring String vs Numeric Type Shifts**: Accepting string numbers `"150.5"` in production when sandbox returned numeric `150.5`, causing type error crashes.
- **Unmonitored Rate Limit Header Drift**: Failing to handle rate-limit headers because the sandbox environment did not return `X-RateLimit-Remaining`.

## Verification

- Instantiate `EndpointDriftDetector`. Compare identical JSON payloads $\implies$ verify `passed=True`, zero critical/warning findings. Compare payload with missing production field $\implies$ verify `passed=False` with `CRITICAL` finding. Compare float vs string type mismatch $\implies$ verify `CRITICAL` finding. Compare status code mismatch (200 vs 400) $\implies$ verify status code finding.
- Run `python scripts/test_drift_detector.py`.

## Related Skills

- `sandbox-credential-leakage-prevention`
- `broker-api-changelog-diffing-tool`
---
