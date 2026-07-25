---
name: broker-failover-secondary-account-routing
description: >-
  Use when building high-availability trading systems to route order flow automatically to a secondary broker account if the primary broker suffers connection degradation, rate limiting, or session outages mid-session.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "failover", "high-availability", "secondary-broker", "order-routing", "resilience"]
brokers_frameworks: ["Multi-Broker Adapter", "Python Custom Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing mission-critical trading strategies where primary broker downtime creates unacceptable execution risk. Mid-session broker API outages, maintenance windows, or rate-limiting suspensions can halt strategy execution. Secondary account routing dynamically redirects order dispatch to a pre-configured backup broker account while preserving position limits and risk constraints.

## Prerequisites

- Primary and secondary broker API client adapters configured.
- Unified symbol mapping between primary and secondary broker symbols.
- Configurable failure threshold (e.g., 3 consecutive order submission failures).

## Workflow

1. **Register Primary and Secondary Brokers**:
   - Initialize primary broker adapter (e.g., IBKR) and secondary backup adapter (e.g., Alpaca).

2. **Monitor Order Execution & Connection Health**:
   - Intercept order submission outcomes. Track consecutive failures, network timeouts, or 5xx server errors on the primary broker.

3. **Trigger Automatic Rerouting**:
   - When consecutive failures reach threshold `max_consecutive_failures`, trip primary breaker and mark primary broker status as `DEGRADED` or `DOWN`.
   - Automatically route subsequent orders to the secondary broker adapter.

4. **Unified Exposure & Position Accounting**:
   - Aggregates position exposures across both accounts in a unified ledger to prevent breaching total risk caps.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Split-Brain Portfolio Exposure**: Placing orders on both brokers simultaneously without unified position accounting.
- **Symbol Specification Mismatches**: Assuming primary and secondary brokers use identical symbol formats (e.g., IBKR `AAPL STK SMART` vs Alpaca `AAPL`).
- **Premature Failback**: Switching back to primary broker immediately upon a single success before proving connection stability.

## Verification

- Simulate primary broker failure (3 consecutive 500 errors) and verify automatic switch to secondary broker.
- Confirm unified position tracking aggregates positions across both primary and secondary accounts.
- Run `python scripts/test_failover_router.py` and confirm 100% pass rate.

## Related Skills

- `multi-region-failover-for-broker-connectivity`
- `broker-agnostic-adapter-interface`
- `multi-broker-rate-limit-handling`
---
