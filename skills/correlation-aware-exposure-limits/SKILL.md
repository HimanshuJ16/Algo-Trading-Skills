---
name: correlation-aware-exposure-limits
description: >-
  Use when a bot can hold multiple simultaneous positions, to prevent sector or factor concentration that per-instrument position limits alone don't catch
domain: algorithmic-trading
subdomain: risk-management
tags: ["risk-management"]
brokers_frameworks: []
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a strategy can select from a universe of instruments (e.g., multiple Nifty 50 constituents, multiple strikes/expiries) and may end up holding several positions at once. A per-instrument position size limit alone does not prevent concentration risk — a bot can be fully compliant with every individual position limit while still holding, say, five different bank-sector stocks that all move together, effectively taking one large directional bet on the banking sector while the position-limit checks each pass individually.

## Prerequisites

- A correlation matrix (or at minimum a sector/factor classification) for the instrument universe, updated on a reasonable cadence (correlations are not static — recompute periodically, e.g., rolling 60-90 day correlation, rather than using a single historical estimate indefinitely)
- Defined aggregate exposure limits at the sector/cluster level, separate from per-instrument limits

## Workflow

1. Before adding a new position, compute its correlation (or shared-sector/factor membership) with all currently held positions using a recently-updated correlation matrix, not a static one computed once at strategy design time — correlations between stocks/sectors shift meaningfully over months, and a stale matrix can approve a concentration that current market conditions would flag.
2. Define an aggregate exposure cap not just per-instrument but per correlation cluster (e.g., group instruments with pairwise correlation above a threshold into the same cluster, or use sector classification as a simpler proxy) — reject or size down a new position if it would push the cluster's aggregate notional exposure beyond the defined cap, even if each individual position within that cluster is within its own per-instrument limit.
3. For options-based strategies specifically, account for correlated exposure through shared underlying risk factors beyond just "same stock" — e.g., multiple strikes on the same underlying, or positions across highly correlated sector ETF constituents, should be evaluated for aggregate delta/vega exposure to the shared underlying factor, not treated as independent just because the specific contracts differ.
4. Recompute the correlation matrix on a defined schedule (e.g., weekly, or before each new trading session) using a rolling historical window, and log when cluster membership changes meaningfully — a stock moving from one correlation cluster to another is itself useful information about changing market structure.
5. When rejecting or sizing down a position due to a correlation-cluster breach, log the specific cluster and its current aggregate exposure so the decision is auditable rather than a silent no-op the strategy layer might not distinguish from "no signal today."
6. Treat correlation-based limits as a complement to, not a replacement for, the aggregate exposure and drawdown limits in `kill-switch-and-drawdown-circuit-breakers` — a strategy can breach aggregate risk limits even with perfect correlation-cluster management if overall position sizing is simply too large.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Relying only on per-instrument position limits and assuming diversification "because they're different stocks," without checking actual correlation or shared sector/factor exposure.
- Computing a correlation matrix once during strategy design and never updating it, so the risk check operates on stale relationships that no longer reflect current market structure.
- Treating different strikes/expiries on the same underlying as independent positions for exposure-limit purposes, undercounting true directional exposure to that underlying.
- Sizing down or rejecting a position silently without logging which cluster/correlation check caused it, making the behavior indistinguishable from a strategy simply not generating a signal.

## Verification

- Construct a test scenario where the strategy would naturally select several highly correlated instruments (e.g., multiple bank-sector stocks) in the same session and confirm the aggregate cluster exposure check reduces or rejects positions beyond the defined cap, even though each passes its individual per-instrument limit.
- Confirm the correlation matrix used in a given trading session is recent (check its computation timestamp against the defined refresh schedule) rather than a stale artifact from initial strategy design.
- Audit logs after a live/paper session to confirm every correlation-driven rejection or size-down is logged with the specific cluster and exposure figures involved.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `order-placement-idempotency`
