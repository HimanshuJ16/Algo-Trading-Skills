---
name: physical-vs-cash-settlement-handling
description: >-
  Derivatives settlement handling engine distinguishing cash-settled contracts from physical delivery obligations, calculating expiration cashflows, and enforcing First Notice Date (FND) liquidations.
domain: Derivatives Settlement & Post-Trade Operations
subdomain: Expiration & Physical Delivery Risk Management
tags: ["settlement", "physical-delivery", "cash-settlement", "first-notice-date", "futures-expiration", "options-assignment", "derivatives"]
brokers_frameworks: ["CME Group / ICE / Eurex Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing expiring futures contracts and options positions across cash-settled vs physically settled markets. Cash-settled contracts (e.g. ES, SPX, Nifty 50, BTC perpetuals) settle automatically in cash at expiration without physical asset transfer. Physically settled contracts (e.g. WTI crude oil, Gold, Single Stock Futures, Equity Options) require physical asset delivery or full notional payment ($|Q| \cdot \text{Multiplier} \cdot P_{\text{settle}}$). Holding long physical futures past the First Notice Date (FND) without delivery infrastructure creates severe delivery default risk.

## Prerequisites

- Contract settlement specification (`symbol`, `settlement_type`: `'CASH'`/`'PHYSICAL'`, `multiplier`, `days_to_notice_date`).
- Account position state (`position_qty`, `entry_price`, `account_cash_balance`, `has_delivery_facility`).

## Workflow

1. **Settlement Type Classification**:
   - Classify contract as `CASH` or `PHYSICAL`.
2. **Cash Settlement Expiration Calculation**:
   - For `CASH` contracts: Compute final cash settlement PnL:
     $$\Delta \text{Cash} = Q \cdot \text{Multiplier} \cdot (P_{\text{settle}} - P_{\text{entry}})$$
3. **Physical Delivery & FND Risk Audit**:
   - For `PHYSICAL` contracts:
     - Check proximity to First Notice Date ($\text{DaysToNotice} \le 3$).
     - Calculate full notional delivery requirement: $V_{\text{delivery}} = |Q| \cdot \text{Multiplier} \cdot P_{\text{settle}}$.
     - If firm lacks delivery facilities or cash balance $< V_{\text{delivery}} \implies$ flag `PHYSICAL_DELIVERY_RISK_BREACH` and trigger mandatory roll/close order.
4. **Audit Report Generation**: Output structured `SettlementReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Holding Physical Futures Past FND**: Holding long commodity futures past First Notice Date, resulting in unwanted physical warehouse delivery assignments.
- **Underfunding Physical Option Exercise**: Exercising In-The-Money equity call options without sufficient margin equity to pay full 100% stock purchase price.
- **Confusing Index Cash Options with Equity Options**: Treating SPX (cash-settled) options like SPY (physically settled stock delivery) options.

## Verification

- Instantiate `PhysicalVsCashSettlementHandlingEngine`. Input 10 contracts of ES (Cash-settled, Multiplier 50) $\implies$ verify $0.00$ physical delivery risk. Input 5 contracts of CL (WTI Physical, Multiplier 1000 @ $\$70.00 \implies \$350,000$ delivery requirement) with 2 days to FND and no delivery facility $\implies$ verify `PHYSICAL_DELIVERY_RISK_BREACH` alert.
- Run `python scripts/test_physical_vs_cash_settlement_handling.py`.

## Related Skills

- `options-pin-risk-management-at-expiry`
- `margin-utilization-circuit-breaker`
---
