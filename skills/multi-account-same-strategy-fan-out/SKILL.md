---
name: multi-account-same-strategy-fan-out
description: Use when broadcasting a single quantitative strategy signal across multiple
  client accounts or sub-accounts (e.g. fund management / prop trading) to execute
  pro-rata order fan-out without cross-account order collision.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- multi-account
- fan-out
- pro-rata
- fund-management
- order-collision-prevention
brokers_frameworks:
- IBKR Allocations
- Multi-Account Manager
- Python Concurrent
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when managing funds, prop trading desks, or multi-account client portfolios where a single trading signal (e.g., "Buy 1,000 shares of AAPL") must be executed across $N$ independent sub-accounts simultaneously. Naive sequential order loops cause execution latency drift and fill price variance across accounts. This skill executes parallel pro-rata allocation fan-out, assigns unique per-account client order IDs (`CLORD_{account_id}_{timestamp}`), and prevents cross-account order collisions.

## Prerequisites

- Registry of sub-account IDs and individual Net Asset Values (NAV).
- Allocation method choice (Pro-Rata by NAV, Fixed Sizing, or Weight-Based).
- Multi-account broker API or sub-account credentials.

## Workflow

1. **Register Client Sub-Accounts**:
   - Store account IDs, NAVs, and target weight multipliers.

2. **Compute Pro-Rata Account Sizing**:
   - For a master signal target quantity $Q_{\text{master}}$, calculate sub-account quantity:
     $$Q_i = \text{round}\left(Q_{\text{master}} \times \frac{\text{NAV}_i}{\sum \text{NAV}}\right)$$

3. **Generate Collision-Free Client Order IDs**:
   - Assign unique `client_order_id` per sub-account: `CLORD_{account_id}_{seq_num}`.

4. **Parallel Order Dispatch**:
   - Dispatch sub-account orders concurrently via thread/async task pool.

5. **Aggregate Fill Execution Summary**:
   - Track fills across accounts and report execution variance.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Odd Lot Sizing Spikes**: Pro-rata fractional shares rounding down to 0 for small accounts. Must enforce minimum lot size (e.g. 1 share).
- **Cross-Account Order ID Collisions**: Reusing client order IDs across sub-accounts causing broker order rejection.
- **Unbalanced Partial Fills**: Some sub-accounts filling while others fail due to buying power constraints.

## Verification

- Submit master signal of 1,000 shares across 3 accounts ($500k, $300k, $200k NAV) and verify pro-rata quantities (500, 300, 200).
- Verify all generated client order IDs are unique across sub-accounts.
- Run `python scripts/test_fanout_engine.py` and confirm 100% pass rate.

## Related Skills

- `broker-failover-secondary-account-routing`
- `multi-strategy-capital-allocation-limits`
- `order-placement-idempotency`
---
