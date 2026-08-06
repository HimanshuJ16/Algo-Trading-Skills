---
name: wash-trade-and-spoofing-self-detection
description: "Institutional market integrity skill for real-time self-detection of Wash Trades (self-cross executions without change in beneficial ownership) and Spoofing / Layering patterns (non-bona fide order cancellations following opposite-side fills) under CFTC, SEC, FINRA, and MiFID II RTS 6 rules."
domain: Global Regulatory Compliance & Market Integrity
subdomain: Algorithmic Market Abuse Surveillance (MiFID II RTS 6 / CFTC Rule 1.38)
tags:
- wash-trade
- spoofing
- layering
- market-manipulation
- self-match-prevention
- mifid-ii-rts-6
- cftc-rule-1-38
- finra-rule-5210
brokers_frameworks:
- cftc
- sec
- finra
- mifid-ii-rts-6
- ice
- cme
version: 1.1.0
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when developing algorithmic execution systems, market making engines, or pre-trade / post-trade surveillance gateways to prevent market manipulation violations under **CFTC Rule 1.38 (7 U.S.C. § 6c(a))**, **SEC Rule 10b-5**, **FINRA Rule 5210**, and **MiFID II RTS 6 Article 13**.

This skill provides institutional mechanisms to:
- Monitor real-time order streams (`PLACE`, `CANCEL`, `FILL`) per trader and sub-account.
- Detect **Wash Trades / Self-Cross Executions** (opposite orders at the same price within a time window with common beneficial ownership).
- Identify **Spoofing & Layering** (rapid order cancellations on one side of the book following a fill on the opposite side).
- Calculate **Order Cancellation Ratios ($R_{\text{cancel}} \ge 90\%$)** and track average order lifespans ($< 500\ \text{ms}$).
- Issue real-time `CRITICAL` compliance alerts for trade halt or Self-Match Prevention (SMP) triggers.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `typing`).
- High-frequency order event log feed (`PLACE`, `CANCEL`, `FILL` with timestamps, order IDs, trader IDs, prices, and quantities).

## Workflow

1. **Ingest Order Event**: Construct `OrderEvent` specifying event ID, order ID, trader ID, account ID, symbol, side (`BUY` or `SELL`), price, quantity, action, and microsecond timestamp.
2. **Execute Wash Trade Check**: On `PLACE` actions, call `check_wash_trade_self_cross(event)` to scan active resting orders for self-cross matches at identical prices within the time window.
3. **Execute Spoofing / Layering Check**: On `FILL` actions, call `check_spoofing_pattern_on_fill(event)` to detect rapid cancellations of large non-bona fide orders on the opposite side.
4. **Compute Trader Metrics**: Call `get_trader_metrics(trader_id)` to evaluate overall order cancellation ratios and average order lifespans.
5. **Trigger Compliance Action**: If a violation is flagged (`CRITICAL` severity), trigger Self-Match Prevention (SMP) order cancellation or halt algorithmic execution.

## Common Pitfalls

- **Neglecting Cross-Sub-Account Control**: Wash trade rules apply to orders executed across DIFFERENT accounts under COMMON beneficial ownership. SMRT engines must map sub-accounts to master entity IDs.
- **Ignoring Microsecond Order Lifespans**: Spoofing often occurs in sub-second intervals ($< 500\ \text{ms}$). Surveillance engines using coarse second-resolution timestamps fail to detect high-frequency spoofing bursts.
- **Confusing Market Making Liquidity with Layering**: Genuine market makers update quotes frequently. Surveillance models must differentiate bona fide quotes (filled orders on both sides) from non-bona fide layering (cancellations only on one side upon opposite fill).
- **Failing to Store Event Audit Logs**: Regulators (FINRA / CFTC) require firms to produce complete order lifecycle audit trails (`OATS` / `CAT`). Omitting event logs during surveillance invalidates regulatory defense.

## Verification

Run the unit test suite to validate compliant order flows, wash trade self-cross detection, spoofing/layering pattern recognition, cancellation ratio tracking, and exception handling:

```bash
python -m unittest discover -s skills/wash-trade-and-spoofing-self-detection/scripts
```

## Related Skills

- `uk-fca-algorithmic-trading-systems-controls`
- `uk-senior-managers-regime-algo-accountability`
- `us-reg-nms-order-protection-rule-compliance`
- `us-reg-sho-short-sale-locate-requirements`
