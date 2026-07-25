---
name: broker-api-versioning-migration-playbook
description: >-
  Use when migrating a live trading system between broker API versions (e.g. V1 to V2 REST/WebSocket APIs) to execute zero-downtime version migration using shadow traffic validation, payload translation layers, and canary traffic cutovers.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "api-versioning", "migration-playbook", "zero-downtime", "canary-deployment", "shadow-traffic"]
brokers_frameworks: ["Broker API Adapters", "Python Custom Migrator"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever a brokerage updates its API protocol or deprecates an older API version (e.g. Coinbase Pro to Advanced Trade, IBKR Client Portal API v1 to v2, Zerodha Kite v3 to v4). Upgrading live trading bots risks order outages or silent payload breaks. This skill executes a structured 4-phase migration playbook: Schema Mapping, Shadow Traffic Auditing, Canary Rollout (25% -> 50% -> 100%), and Emergency Version Rollback.

## Prerequisites

- API documentation for both legacy (V1) and target (V2) broker API versions.
- Abstracted broker adapter interface separating strategy logic from API payloads.

## Workflow

1. **Phase 1 — Dual-Version Adapter Construction**:
   - Implement `V1Adapter` and `V2Adapter` implementing a unified `IBrokerAdapter` contract.

2. **Phase 2 — Shadow Traffic Auditing**:
   - Route live read/market-data requests to both V1 and V2 in parallel.
   - Audit V1 vs V2 response schema diffs and verify data equivalence without dispatching real orders to V2.

3. **Phase 3 — Canary Traffic Cutover**:
   - Configure canary traffic percentage (e.g., 25% of live orders to V2, 75% to V1).
   - Dynamically scale V2 traffic percentage as fill reliability is validated.

4. **Phase 4 — Decommission & Rollback Guard**:
   - If V2 encounters unexpected schema errors or HTTP 5xx codes, instantly set canary percentage to 0% (full fallback to V1).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Silent Order Type Semantic Drift**: V1 and V2 handling limit/stop price parameters under slightly different field names (e.g., `price` vs `limit_price`).
- **Timestamp Precision Discrepancies**: V1 returning epoch seconds while V2 returns ISO-8601 strings or nanosecond timestamps.
- **Unvalidated Shadow Orders**: Attempting shadow mode on order placement calls (causing duplicate live orders on the exchange).

## Verification

- Simulate shadow mode on market data calls and verify schema diff auditor.
- Simulate canary traffic cutover from 0% -> 50% -> 100% and confirm traffic split.
- Run `python scripts/test_api_migrator.py` and confirm 100% pass rate.

## Related Skills

- `blue-green-deployment-for-live-strategy-updates`
- `broker-agnostic-adapter-interface`
- `sandbox-vs-production-endpoint-drift`
---
