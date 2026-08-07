---
name: canary-releases-for-strategy-code-changes
description: Quantitative execution engine for safely transitioning strategies through
  SHADOW, CANARY, and PRODUCTION stages with strict order size scaling.
domain: CI/CD
subdomain: Live Deployment
tags:
- canary
- shadow-mode
- deployment
- risk-management
- order-scaling
brokers_frameworks:
- Generic Execution
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying a new quantitative strategy or a significant model update to a live production environment. It provides a formal `StrategyCanaryRouter` that routes signals into three distinct phases:
1. **Shadow Mode**: Live data ingestion, generating signals, but intercepting and discarding execution requests.
2. **Canary Mode**: Sending live orders, but mathematically restricting the order size by a fractional multiplier (e.g., 5%) to cap maximum financial exposure.
3. **Production Mode**: Full uninhibited order sizing.

## Prerequisites

- Execution engine capable of intercepting and rewriting order quantities before FIX/REST routing.
- Established metrics (e.g., latency, PnL) to objectively promote the strategy from Shadow -> Canary -> Prod.

## Workflow

1. **Configuration**: Register the strategy ID with the `StrategyCanaryRouter` and assign it a `DeploymentPhase`.
2. **Signal Interception**: When the strategy generates an order (e.g., `Buy 10,000 AAPL`), it passes through the router.
3. **Action by Phase**:
   - `SHADOW`: The router sets quantity to 0 and returns a mock execution ID.
   - `CANARY`: The router scales the quantity (e.g., 5% = `500 AAPL`), rounds to the nearest valid lot size, and routes it.
   - `PRODUCTION`: Routes the full `10,000 AAPL`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Neglecting Lot Sizes**: Scaling a 100-share Canary order by 5% yields 5 shares, which might be rejected by exchanges with minimum 100-share board lots. The router must handle lot rounding.
- **Canarying Illiquid Assets**: A canary order for 1 share in a highly illiquid market provides zero valuable slip/fill data.

## Verification

- Submit a 1,000 share order under Canary Mode (10% scale) and verify exactly 100 shares are routed.
- Run `python scripts/test_canary_releases_for_strategy_code_changes.py`.

## Related Skills

- `blue-green-deployment-for-live-strategy-updates`
- `binance-futures-testnet-to-mainnet-promotion`
