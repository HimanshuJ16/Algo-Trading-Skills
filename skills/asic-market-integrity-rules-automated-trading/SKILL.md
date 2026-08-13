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
version: "1.2.0"
author: System
license: MIT
---

## When to Use

Use this skill when deploying automated trading algorithms in Australia. The Australian Securities & Investments Commission (ASIC) Market Integrity Rules (specifically regarding Automated Order Processing or AOP) legally mandate that all electronic orders pass through direct, participant-controlled pre-trade filters and that an immediate "Kill Switch" is available.

## Prerequisites

- Python 3.9+
- A predefined risk configuration (`AsicMarketIntegrityConfig`) containing maximum order value, maximum volume, and allowable price deviations from the last traded price. All limits must be positive and finite; non-positive or non-finite values raise `ValueError` at construction (a disabled control is a misconfiguration, not a valid state).
- A real-time, valid reference price (last traded or mid) per instrument. A zero or stale reference price is rejected rather than used to compute deviation.

## Workflow

1. **Check Kill Switch**: The engine first verifies if the `AsicKillSwitchManager` has been triggered. If so, all orders are immediately rejected (ASIC Rule 5.6.3(1)(d)). The kill switch is thread-safe and records every trigger/reset with timestamp, reason and actor in an auditable log.
2. **Non-Finite Input Guard**: Orders with `NaN` or `+/- Inf` in price, quantity, or reference price are rejected outright. NaN comparisons silently evaluate to `False`, so without an explicit guard they would bypass every numeric limit.
3. **Field Sanity & Reference Price**: Non-positive quantity/price is rejected. A zero or non-positive reference price is rejected (rather than causing a `ZeroDivisionError`) since deviation cannot be safely computed.
4. **Pre-Trade Filtering**: If the system is live, the `AsicAopPreTradeFilter` intercepts the order.
5. **Volume & Value Checks**: The order is rejected if its total value or volume exceeds the configured hard limits.
6. **Price Deviation Check**: The order's limit price is compared to the market's reference price. If it strictly breaches the percentage deviation threshold, it is blocked to prevent "fat finger" errors. Deviation exactly equal to the limit is allowed.
7. **Approval**: Only if all checks pass is the order allowed to route to the broker. Every decision (approved or rejected) returns a `ComplianceResult` carrying a machine-readable `rejection_code`, `order_id`, and `checked_at_unix` timestamp for the ASIC audit trail.

## Common Pitfalls

- **Passive Pre-Trade Filters**: Implementing filters that merely "alert" on breaches rather than actively blocking the order. ASIC requires active gatekeeping (RG 241.35 — reject is a mandatory filter outcome).
- **Missing Global Kill Switch**: Implementing algorithm-specific pauses but failing to provide a global kill switch that can halt the entire AOP system instantly in the event of systemic failure (Rule 5.6.3(1)(d)-(e)).
- **NaN/Inf Inputs Bypassing Limits**: `NaN > limit` evaluates to `False` in Python, so a NaN price or quantity silently passes every numeric check. Non-finite inputs must be rejected explicitly before any comparison.
- **Zero Reference Price Crash**: A zero or stale reference price causes `ZeroDivisionError` in the deviation check, taking the pre-trade control offline at the exact moment it is needed. Reject such orders instead.
- **Unauditable Kill Switch**: Triggering or resetting the kill switch without recording timestamp, reason and actor breaks the ASIC recordkeeping obligation (Part 5.6). All transitions must be logged.
- **Disabled Limits via Misconfiguration**: A negative or zero `max_order_value`/`max_order_volume`/`max_price_deviation_pct` silently disables a mandatory control. Validate config at construction.
- **Filter Parameter Drift**: Changing filter parameters without administrator-level direct control and audit violates Rule 5.6.3(2). Parameter changes must be controlled and reviewable.

## Verification

Run `python scripts/test_asic_market_integrity_rules_automated_trading.py` to confirm that the kill switch, value limits, and price deviation filters correctly intercept and block non-compliant orders.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `sec-rule-15c3-5-risk-controls-us`
