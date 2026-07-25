---
name: broker-account-margin-call-handling
description: >-
  Use when monitoring margin account health to calculate real-time maintenance margin ratios, enforce multi-tiered margin warning thresholds, evaluate predictive order impacts, and trigger automated liquidity-aware de-leveraging before broker forced liquidation.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "margin-call", "risk-management", "forced-liquidation-prevention", "margin-utilization", "liquidity-aware"]
brokers_frameworks: ["Interactive Brokers Reg T / Portfolio Margin", "Zerodha RMS", "Alpaca Margin API", "CME SPAN"]
version: "2.0"
author: quantitative-systems-engineer
license: Apache-2.0
---

## When to Use

Invoke this whenever an algorithmic trading bot operates on a margin account (Reg T, Portfolio Margin, or Futures margin). If adverse market movements push maintenance margin requirements above Net Liquidation Value (NLV), brokers issue margin calls and automatically liquidate positions at market prices, incurring severe slippage and realization losses. 

Implementing real-time margin ratio monitoring, multi-tiered risk gates (`WARNING` at 85%, `CANCEL_ORDERS` at 95%, `DE_LEVERAGE` at 100%), predictive pre-trade margin impact checks, and systematic, liquidity-aware order vetoing prevents broker-forced liquidation.

## Prerequisites

- Access to broker real-time account data: `net_liquidation_value`, `maintenance_margin`, `initial_margin`, `excess_liquidity`.
- Open order cancellation interface.
- Position de-leveraging prioritization logic (highest-margin, short options tail-risk, or liquidity-capped slicing).

## Workflow

1. **Calculate Margin Utilization Metrics**:
   - Compute Maintenance Margin Utilization Ratio: $M_{\text{ratio}} = \frac{\text{Maintenance Margin}}{\text{Net Liquidation Value}}$.
   - Compute Predictive Initial Margin Impact before order placement.

2. **Evaluate Multi-Tiered Margin Thresholds**:
   - **NORMAL** ($M_{\text{ratio}} < 0.85$): Normal trading operations.
   - **WARNING** ($0.85 \le M_{\text{ratio}} < 0.95$): Block new leverage-increasing orders; issue alert to risk operators.
   - **CRITICAL** ($0.95 \le M_{\text{ratio}} < 1.0$): Cancel all open pending orders immediately to release reserved margin buffers.
   - **BREACH** ($M_{\text{ratio}} \ge 1.0$): Execute automated de-leveraging to restore $M_{\text{ratio}} \le 0.75$ (target buffer) before broker forced liquidation.

3. **Execute De-Leveraging Order Slicing**:
   - Score and sort existing positions prioritizing unhedged short options (tail risk), high margin density, and highly liquid assets.
   - Apply a volume participation cap (e.g., max 10% of ADV) to prevent the de-leveraging algorithm from causing flash crashes in illiquid names.
   - Liquidate position slices systematically until the target margin buffer is restored.

4. **Predictive New Order Veto Guard**:
   - Intercept outbound order requests via `guard_new_order(margin_impact)`. Veto any order that *would* increase the projected margin ratio $\ge 0.85$.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Passive Waiting for Broker Liquidation**: Waiting for the broker's automated RMS engine to liquidate positions, leading to market-order slippage and bad fills.
- **Ignoring Predictive Margin Impact**: Approving an order when current margin is 80%, but the order itself pushes margin to 110%.
- **Illiquidity Spirals**: Dumping an illiquid asset all at once, crushing the bid price, lowering the NLV further, and triggering a secondary margin call.
- **Ignoring Tail Risk**: De-leveraging long equity positions first while leaving naked short options open during a volatility spike.

## Verification

- Ensure `evaluate_margin_health` correctly applies logic across initial and maintenance margins.
- Submit account metrics with $M_{\text{ratio}} = 0.88$ and confirm state transitions to `WARNING`.
- Simulate a high-impact order using `guard_new_order` and verify predictive rejection.
- Run unit test suite `python scripts/test_margin_call_engine.py` and confirm 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `options-margin-span-calculation-global`
- `correlation-aware-exposure-limits`
