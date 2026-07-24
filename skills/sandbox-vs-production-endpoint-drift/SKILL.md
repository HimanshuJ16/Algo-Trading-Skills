---
name: sandbox-vs-production-endpoint-drift
description: >-
  Use when testing broker API integrations to detect schema drift, response payload mismatches, header variations, and status code discrepancies between sandbox and production environments
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "sandbox-drift", "api-schema-audit", "environment-parity", "regression-testing"]
brokers_frameworks: ["All Broker REST APIs"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever promoting a trading bot or broker API integration from a paper/sandbox environment to live production. Broker sandboxes frequently deviate from live production environments in subtle ways: missing optional fields in order objects, returning 200 OK with inline error messages instead of HTTP 4xx, formatting timestamps differently, or omitting rate-limit headers. Auditing response schema parity between environments before live deployment prevents unexpected runtime crashes.

## Prerequisites

- Access credentials for both sandbox and production broker endpoints.
- Read-only probe endpoints (e.g., GET `/v2/account`, GET `/v2/orders`, GET `/v2/instruments`).
- Dict/Schema comparison utility.

## Workflow

1. **Capture Benchmark Responses**:
   - Issue identical GET requests to equivalent sandbox and production endpoints (e.g., sandbox `https://paper-api.alpaca.markets/v2/account` vs production `https://api.alpaca.markets/v2/account`).

2. **Schema & Field Type Comparison**:
   - Execute `EndpointDriftDetector.compare_schemas()`:
     - Check for missing fields present in production but absent in sandbox.
     - Check for data type mismatches (e.g. `price` returned as `float` in sandbox vs `string` in production).

3. **HTTP Header & Rate-Limit Audit**:
   - Inspect response headers across environments. Verify rate-limit headers (`X-RateLimit-Remaining`, `Retry-After`) exist in sandbox.

4. **Status Code Behavior Audit**:
   - Submit intentional invalid requests (e.g., bad symbol name) to both environments. Confirm both return identical HTTP status codes (e.g. 400 Bad Request).

5. **Generate Parity Audit Report**:
   - Export structured `EndpointDriftReport` categorizing drift findings by severity (`CRITICAL`, `WARNING`, `INFO`). Block live deployment if `CRITICAL` schema drift is detected.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Audited Live Promotion**: Assuming that code passing in sandbox will run identically in live production without schema verification.
- **Type Coercion Vulnerability**: Assuming float inputs when production returns string representations of price or quantity.
- **Missing Header Guards**: Relying on rate-limit headers that are present in sandbox but missing in production or vice versa.

## Verification

- Run `compare_schemas()` on identical sandbox and production payloads and verify zero drift is reported.
- Introduce a missing field and type mismatch into a mock response and verify `CRITICAL` drift is detected.
- Verify status code audit flags 200 OK vs 400 Bad Request discrepancies.
- Run unit test suite `python scripts/test_drift_detector.py` and confirm 100% pass rate.

## Related Skills

- `paper-to-live-promotion-checklist`
- `alpaca-paper-live-key-separation`
- `multi-broker-rate-limit-handling`
---
