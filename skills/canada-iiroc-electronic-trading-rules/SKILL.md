---
name: canada-iiroc-electronic-trading-rules
description: Pre-trade regulatory risk controls compliant with CIRO (formerly IIROC)
  UMIR Rule 7.1 and NI 23-103 for algorithmic trading in Canadian markets.
domain: Compliance
subdomain: Regulatory Controls
tags:
- canada
- iiroc
- ciro
- umir
- pre-trade-risk
- compliance
brokers_frameworks:
- Generic Execution
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying algorithms that route orders directly to Canadian marketplaces (TSX, NEO, CSE, etc.). Under National Instrument 23-103 and UMIR Rule 7.1, firms must implement automated pre-trade risk controls to prevent erroneous orders, enforce financial limits, and ensure proper regulatory marking (e.g., short sell flags).

## Prerequisites

- Trading system that generates normalized order objects.
- Historical or real-time reference data for Average Daily Volume (ADV) and Last Traded Price (LTP) to validate "fat-finger" checks.

## Workflow

1. **Rule Initialization**: Instantiate the `CiroPreTradeRiskEngine` with firm-specific capital limits and threshold multipliers.
2. **Order Interception**: Before any order is routed to the FIX gateway, pass it through the engine's `validate_order()` method.
3. **Fat-Finger Checks**: The engine checks if the order size exceeds a percentage of ADV or if the price deviates unacceptably from the LTP.
4. **Regulatory Checks**: The engine enforces UMIR-specific rules, such as requiring a `Short` or `Short-Mark-Exempt` flag if the account does not hold the security.
5. **Hard Rejection**: Any violation raises a `RegulatoryViolationException`, blocking the order from reaching the venue and alerting the Head of Trading.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Post-Trade Only**: Relying solely on T+1 drop-copy reconciliation. CIRO strictly mandates *pre-trade* blockage of erroneous orders.
- **Ignoring Short Flags**: Failing to mark short sales properly, leading to severe UMIR penalties.
- **Static Price Limits**: Hardcoding a "$50 limit" instead of dynamically linking price collars to the Last Traded Price (e.g., 5% away from LTP).

## Verification

- Simulate a "fat-finger" market order for 1,000,000 shares and confirm the engine blocks it before FIX transmission.
- Run `python scripts/test_canada_iiroc_electronic_trading_rules.py` to verify 100% pass rate.

## Related Skills

- `broker-account-margin-call-handling`
- `best-execution-record-keeping-global`
