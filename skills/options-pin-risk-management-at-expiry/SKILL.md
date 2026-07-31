---
name: options-pin-risk-management-at-expiry
description: >-
  Options pin risk management engine identifying strike proximity risk on expiration day, evaluating contrary exercise instruction liabilities, and triggering automated position closes prior to market close.
domain: Derivatives Risk & Expiry Operations
subdomain: Pin Risk & Contrary Exercise Mitigation
tags: ["options-pin-risk", "expiry-operations", "contrary-exercise", "assignment-risk", "0dte-risk", "derivatives-risk"]
brokers_frameworks: ["OCC Expiration & Contrary Exercise Spec", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill on options expiration day (0DTE or monthly expiration Friday) when holding open options positions near the strike price ($|S - K| / S \le 1.0\%$). When an option expires near the money, options holders have until 5:30 PM EST to submit Contrary Exercise Advice to their broker. This creates extreme assignment ambiguity for short option sellers who cannot predict whether they will be assigned $100$ shares per contract over the weekend, risking catastrophic unhedged gap exposure and margin calls. This engine audits pin risk and enforces automated position closing or rolling prior to market close.

## Prerequisites

- Expiring option position (`symbol`, `underlying_symbol`, `strike`, `option_type`, `position_qty`, `spot_price`, `hours_to_expiry`).
- Pin policy configuration (`pin_distance_pct`: e.g. 1.0%, `pin_cutoff_hours`: e.g. 2.0 hours).

## Workflow

1. **Pin Distance & Expiry Cutoff Calculation**:
   - Compute percentage distance to strike:
     $$\text{PinDistance}_{\%} = \frac{|\text{SpotPrice} - \text{StrikePrice}|}{\text{SpotPrice}} \times 100\%$$
   - Audit if $\text{PinDistance}_{\%} \le \text{PinDistancePctThreshold}$ (1.0%) and $\text{HoursToExpiry} \le 2.0$ hours.
2. **Assignment Liability & Action Resolution**:
   - **Short Options ($-Q$)**: High Pin Risk $\implies$ Trigger `CLOSE_POSITION_BEFORE_EXPIRY` or `ROLL_POSITION` to eliminate post-market contrary exercise ambiguity.
   - **Long Options ($+Q$)**: High Pin Risk $\implies$ Evaluate whether to close or issue Contrary `DO_NOT_EXERCISE` (DNE) instructions.
3. **Assigned Share Notional Computation**:
   - Compute max potential assigned share notional exposure: $\text{Notional}_{\text{assigned}} = |Q| \times 100 \times \text{SpotPrice}$.
4. **Audit Report Generation**: Output structured `PinRiskReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Holding Short Options to Expiration Near Money**: Assuming short options expiring $\$0.05$ OTM will not be assigned, ignoring after-hours stock moves and post-market contrary exercise advice.
- **Unwinding One Leg of a Defined-Risk Spread**: Closing the long leg of a spread while leaving the short leg pinned at expiration, turning a defined-risk spread into an unhedged naked short.
- **Missing Broker Contrary Exercise Cutoff**: Attempting to submit Contrary Exercise / DNE instructions after the broker's internal cutoff (e.g. 4:30 PM EST vs OCC 5:30 PM EST).

## Verification

- Instantiate `OptionsPinRiskManagementEngine`. Input short Call ($-10$ contracts) at $\$100.50$ spot vs $\$100.00$ strike ($0.5\%$ distance) with $1.0$ hour to expiry $\implies$ verify `HIGH_PIN_RISK` and action `CLOSE_POSITION_BEFORE_EXPIRY`. Input deep OTM option ($10\%$ distance) $\implies$ verify `HOLD_TO_EXPIRY`.
- Run `python scripts/test_options_pin_risk_management_at_expiry.py`.

## Related Skills

- `options-chain-expiry-cycle-conventions-by-exchange`
- `options-greeks-real-time-portfolio-aggregation`
---
