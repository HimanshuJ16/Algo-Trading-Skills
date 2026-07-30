---
name: asic-market-integrity-rules-automated-trading
description: Compliance engine enforcing ASIC Market Integrity Rules (MIRs) for Automated
  Order Processing (AOP), including pre-trade filters and kill switches.
domain: regulatory-compliance-global
subdomain: regulatory
tags:
- compliance
- asic
- australia
- pre-trade-filter
- kill-switch
brokers_frameworks:
- generic
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill when deploying automated trading algorithms in Australia. The Australian Securities & Investments Commission (ASIC) Market Integrity Rules (specifically regarding Automated Order Processing or AOP) legally mandate that all electronic orders pass through direct, participant-controlled pre-trade filters and that an immediate "Kill Switch" is available.

## Prerequisites

- Python 3.9+
- A predefined risk configuration containing maximum order value, maximum volume, and allowable price deviations from the last traded price.

## Workflow

1. **Check Kill Switch**: The engine first verifies if the `AsicKillSwitchManager` has been triggered. If so, all orders are immediately rejected.
2. **Pre-Trade Filtering**: If the system is live, the `AsicAopPreTradeFilter` intercepts the order.
3. **Volume & Value Checks**: The order is rejected if its total value or volume exceeds the configured hard limits.
4. **Price Deviation Check**: The order's limit price is compared to the market's reference price. If it breaches the percentage deviation threshold, it is blocked to prevent market manipulation or "fat finger" errors.
5. **Approval**: Only if all checks pass is the order allowed to route to the broker.

## Common Pitfalls

- **Passive Pre-Trade Filters**: Implementing filters that merely "alert" on breaches rather than actively blocking the order. ASIC requires active gatekeeping.
- **Missing Global Kill Switch**: Implementing algorithm-specific pauses but failing to provide a global kill switch that can halt the entire AOP system instantly in the event of systemic failure.

## Verification

Run `python scripts/test_asic_market_integrity_rules_automated_trading.py` to confirm that the kill switch, value limits, and price deviation filters correctly intercept and block non-compliant orders.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `sec-rule-15c3-5-risk-controls-us`
