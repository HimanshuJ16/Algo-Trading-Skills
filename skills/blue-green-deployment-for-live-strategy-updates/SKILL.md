---
name: blue-green-deployment-for-live-strategy-updates
description: >-
  Use when deploying strategy code updates to a live trading system without a gap in
  market coverage or duplicate order risk, using a blue-green deployment pattern with
  traffic cutover and rollback capability.
domain: algorithmic-trading
subdomain: deployment-ops
tags: ["deployment", "blue-green", "zero-downtime", "strategy-updates", "rollback"]
brokers_frameworks: ["Docker", "Kubernetes", "Systemd", "Custom Deployment"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever deploying a strategy code update to a live trading system that
must maintain continuous market coverage. Naive restart-based deployments create gaps where
the bot is offline and misses fills, or where two instances briefly overlap causing duplicate
orders. Blue-green deployment maintains two parallel environments:
- **Blue** (current live) continues trading during deployment.
- **Green** (new version) is fully validated before traffic cutover.
- Atomic switchover with instant rollback capability.

## Prerequisites

- Two independent deployment slots (blue/green) with separate process IDs.
- Health check endpoint or readiness probe for each slot.
- Shared position state store that both slots can read.

## Workflow

1. **Deploy to Green**: Deploy new code to the inactive (green) slot.
2. **Validate Green**: Run health checks, verify connectivity, confirm no open orders.
3. **Drain Blue**: Stop blue from placing new orders (read-only mode).
4. **Cutover**: Atomically switch traffic to green.
5. **Monitor**: Watch green for errors for a configurable stabilization period.
6. **Rollback if Needed**: If green fails, instantly revert traffic to blue.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Duplicate Orders During Cutover**: Both slots briefly active and placing orders.
- **Stale Position State**: Green reading outdated position data during switchover.
- **No Rollback Plan**: Deploying without a tested rollback path.

## Verification

- Simulate a blue-green deployment and verify zero-gap cutover.
- Simulate green failure and verify rollback to blue succeeds.
- Run `python scripts/test_blue_green_deployer.py` and confirm 100% pass rate.

## Related Skills

- `systemd-supervision-for-trading-bots`
- `paper-to-live-promotion-checklist`
---
