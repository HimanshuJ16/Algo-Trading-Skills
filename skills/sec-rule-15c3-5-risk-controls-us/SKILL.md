---
name: sec-rule-15c3-5-risk-controls-us
description: >-
  Production-grade SEC Rule 15c3-5 Market Access Risk Controls Engine enforcing mandatory pre-trade credit/capital thresholds, single-order quantity and notional caps, fat-finger price collars, Reg SHO short sale locates, and restricted security list checks.
domain: Compliance & Market Governance
subdomain: SEC Rule 15c3-5 Market Access Controls
tags: ["sec-rule-15c3-5", "market-access-rule", "pre-trade-risk", "credit-thresholds", "fat-finger-collar", "reg-sho-locate"]
brokers_frameworks: ["SEC Rule 15c3-5 Market Access", "FINRA Pre-Trade Risk Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when implementing or auditing pre-trade risk controls for broker-dealers or sponsored market access clients under US SEC Rule 15c3-5 (the Market Access Rule). SEC Rule 15c3-5 mandates that broker-dealers with market access maintain direct and exclusive control over pre-trade risk systems to prevent the entry of erroneous orders, credit threshold breaches, unlocated short sales, or trades in restricted securities.

## Prerequisites

- Order payload (`MarketAccessOrder`: `order_id`, `account_id`, `symbol`, `side`, `quantity`, `price`, `nbbo_mid_price`, `accumulated_credit_used_usd`, `short_locate_id`).
- Regulatory pre-trade limits (`SecRule15c35Limits`: `account_credit_cap_usd`, `max_single_order_notional_usd`, `max_single_order_qty`, `max_price_collar_pct`, `restricted_symbols`).

## Workflow

1. **Credit & Capital Threshold Inspection**:
   - Check if projected account credit used ($\text{Accumulated Credit} + \text{Order Notional}$) exceeds pre-set limit.
2. **Single-Order Size & Price Collar Audit**:
   - Verify order quantity $\le \text{max\_single\_order\_qty}$ and notional $\le \text{max\_single\_order\_notional\_usd}$.
   - Audit price collar: reject orders deviating $> 5\%$ from NBBO mid price to prevent fat-finger orders.
3. **Reg SHO Short Sale Locate Verification**:
   - For `SELL_SHORT` orders, verify valid short locate ID is present.
4. **Restricted Security Check**:
   - Block orders for symbols on the firm's restricted trading list.
5. **Execution Output**: Output structured `MarketAccessCheckResult`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unfiltered / Naked Market Access**: Allowing client orders to bypass pre-trade risk checks directly to exchange matching engines.
- **Unchecked Short Sale Locates**: Accepting short sell orders without verifying a valid Reg SHO locate ID, violating FINRA Rule 4320 and SEC Rule 203(b)(1).
- **Static Unadjusted Credit Caps**: Failing to dynamically adjust credit thresholds during high-volatility market sessions.

## Verification

- Instantiate `SecRule15C35RiskControlsUsEngine`. Evaluate valid order $\implies$ verify `is_allowed=True`. Evaluate order exceeding notional cap ($400k > $100k cap) and qty cap $\implies$ verify `SINGLE_ORDER_NOTIONAL_CAP` and `SINGLE_ORDER_QTY_CAP` violations triggered. Evaluate price collar breach (33% from NBBO) $\implies$ verify `PRICE_COLLAR_FAT_FINGER` violation. Evaluate short sell without locate $\implies$ verify `SHORT_SALE_LOCATE_MISSING` violation.
- Run `python scripts/test_sec_rule_15c3_5_risk_controls_us.py`.

## Related Skills

- `risk-control-unit-testing-framework`
- `risk-limit-breach-escalation-matrix`
---
