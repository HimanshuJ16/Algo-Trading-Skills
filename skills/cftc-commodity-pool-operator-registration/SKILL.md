---
name: cftc-commodity-pool-operator-registration
description: Regulatory compliance engine that continuously monitors portfolio exposure
  against the CFTC Rule 4.13(a)(3) de minimis exemption thresholds for CPO registration.
domain: Compliance & Regulation
subdomain: US Regulatory
tags:
- cftc
- cpo
- de-minimis
- margin
- futures
- compliance
brokers_frameworks:
- CFTC
- NFA
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill if you manage a multi-asset trading fund in the United States that trades "commodity interests" (futures, options on futures, swaps, and retail forex). If your fund trades these instruments, you must register as a Commodity Pool Operator (CPO) with the CFTC unless you qualify for an exemption. This engine programmatically monitors your portfolio to ensure it stays strictly within the Rule 4.13(a)(3) "de minimis" thresholds.

## Prerequisites

- Accurate tracking of the fund's total liquidation value (NAV).
- Accurate tracking of aggregate initial margin and premiums for all commodity interests.
- Accurate tracking of the net notional value of all commodity interests.

## Workflow

1. **Position Sizing**: Before entering a new futures or swap position, the OMS queries the `CftcCpoComplianceEngine`.
2. **Exemption Evaluation**: The engine evaluates the two statutory tests under Rule 4.13(a)(3):
   - **Test 1 (Margin Test)**: Does the aggregate initial margin and premiums exceed 5% of the fund's liquidation value?
   - **Test 2 (Notional Test)**: Does the aggregate net notional value exceed 100% of the fund's liquidation value?
3. **Approval/Rejection**: If the new trade would cause the portfolio to fail *both* tests, the trade is rejected (or flagged for immediate compliance review if manual override is permitted).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Unrealized PnL**: The liquidation value must be calculated *after* taking into account unrealized profits and losses. Using static initial capital will cause the engine to miscalculate the thresholds.
- **In-the-Money Options Rule**: Failing to exclude the in-the-money amount of an option premium when calculating the 5% margin test. The CFTC allows this exclusion, which provides more headroom.
- **Failing to File**: This engine only monitors the mathematical thresholds. The exemption is not automatic; you must still file a notice of exemption with the National Futures Association (NFA) annually.

## Verification

- Simulate a portfolio with a $1M liquidation value. Attempt to route a futures order requiring $60,000 in initial margin (6%) with a notional value of $1.5M (150%). The engine must block the trade because it fails both the 5% margin test and the 100% notional test.
- Run `python scripts/test_cftc_cpo_compliance_engine.py`.

## Related Skills

- `position-limit-reporting-cftc-large-trader`
- `regulatory-capital-requirement-tracking`
