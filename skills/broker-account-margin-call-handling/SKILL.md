---
name: broker-account-margin-call-handling
description: >-
  Use when monitoring margin account health to calculate real-time maintenance margin ratios, enforce multi-tiered margin warning thresholds, and trigger automated de-leveraging before broker forced liquidation
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "margin-call", "risk-management", "forced-liquidation-prevention", "margin-utilization"]
brokers_frameworks: ["Interactive Brokers Reg T / Portfolio Margin", "Zerodha RMS", "Alpaca Margin API"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever an algorithmic trading bot operates on a margin account (Reg T, Portfolio Margin, or Futures margin). If adverse market movements push maintenance margin requirements above Net Liquidation Value (NLV), brokers issue margin calls and automatically liquidate positions at market prices, incurring severe slippage and realization losses. Implementing real-time margin ratio monitoring, multi-tiered risk gates (`WARNING` at 85%, `CANCEL_ORDERS` at 95%, `DE_LEVERAGE` at 100%), and systematic order vetoing prevents broker-forced liquidation.

## Prerequisites

- Access to broker real-time account data: `net_liquidation_value`, `maintenance_margin_requirement`, `excess_liquidity`.
- Open order cancellation interface.
- Position de-leveraging prioritization logic (highest-margin or highest-beta positions first).

## Workflow

1. **Calculate Margin Utilization Metrics**:
   - Compute Margin Utilization Ratio: $M_{\text{ratio}} = \frac{\text{Maintenance Margin}}{\text{Net Liquidation Value}}$.
   - Compute Margin Deficit: $D = \max(0, \text{Maintenance Margin} - \text{Net Liquidation Value})$.

2. **Evaluate Multi-Tiered Margin Thresholds**:
   - **NORMAL** ($M_{\text{ratio}} < 0.85$): Normal trading operations.
   - **WARNING** ($0.85 \le M_{\text{ratio}} < 0.95$): Block new leverage-increasing orders; issue alert to risk operators.
   - **CRITICAL** ($0.95 \le M_{\text{ratio}} < 1.0$): Cancel all open pending orders immediately to release reserved margin buffers.
   - **BREACH** ($M_{\text{ratio}} \ge 1.0$): Execute automated de-leveraging to restore $M_{\text{ratio}} \le 0.85$ before broker forced liquidation.

3. **Execute De-Leveraging Order Slicing**:
   - Sort existing positions by initial margin requirement or volatility.
   - Liquidate position slices systematically until excess liquidity is restored above safety buffer.

4. **New Order Placement Veto Guard**:
   - Intercept outbound order requests via `guard_new_order()`. Veto any order that increases margin requirements when $M_{\text{ratio}} \ge 0.85$.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Passive Waiting for Broker Liquidation**: Waiting for the broker's automated RMS engine to liquidate positions, leading to market-order slippage and bad fills.
- **Ignoring Reserved Order Margin**: Leaving pending limit orders active during margin stress, consuming margin buffer.
- **Un-Tiered Liquidations**: Liquidating the entire portfolio at once instead of executing controlled position reductions to meet margin calls.

## Verification

- Submit account metrics with $M_{\text{ratio}} = 0.88$ and confirm state transitions to `WARNING` and blocks new position entries.
- Submit account metrics with $M_{\text{ratio}} = 0.96$ and confirm `cancel_open_orders()` is invoked.
- Submit account metrics with $M_{\text{ratio}} = 1.02$ and confirm automated de-leveraging liquidates highest-margin position slice.
- Run unit test suite `python scripts/test_margin_call_engine.py` and confirm 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `options-margin-span-calculation-global`
- `correlation-aware-exposure-limits`
---
