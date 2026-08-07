---
name: constructive-sale-rule-considerations-us
description: US tax compliance module for detecting Section 1259 Constructive Sale
  triggers (Short-Against-The-Box, Offsetting Swaps) and auditing Section 1259(c)(3)
  30-day/60-day Safe Harbor requirements.
domain: Tax Accounting & Compliance
subdomain: US Tax Rules
tags:
- tax-accounting
- section-1259
- constructive-sale
- short-against-the-box
- safe-harbor
- capital-gains
brokers_frameworks:
- IRS Section 1259
- Generic Tax Compliance
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing US taxable portfolios, tax-loss harvesting algorithms, or hedging strategies for appreciated long positions. Under **IRS Section 1259**, entering into an offsetting transaction (e.g. "shorting against the box", entering an equity swap, or buying deep ITM puts) against an appreciated long position triggers an immediate **Constructive Sale**, forcing instant recognition of taxable capital gains even if the underlying long stock is not sold.

## Prerequisites

- Long position cost basis and current fair market value (unrealized gain status).
- Dates of offsetting short/swap entry, short close, and tax year-end.

## Workflow

1. **Appreciated Position Check**: Verify if long position has unrealized capital gain ($\text{FMV} > \text{Cost Basis}$).
2. **Constructive Sale Audit**:
   - Check if an offsetting position (Short, Forward, Swap, ITM Put) has been established on the same or substantially identical asset.
   - If no offsetting transaction exists, result is `NO_CONSTRUCTIVE_SALE`.
3. **Section 1259(c)(3) Safe Harbor Exception Check**:
   - Requirement A: Is the offsetting position closed on or before Jan 30 following the tax year-end?
   - Requirement B: Is the long position held unhedged for at least 60 consecutive days following the closing date?
4. **Taxable Impact Calculation**:
   - If Safe Harbor passes $\implies$ `SAFE_HARBOR_QUALIFIED` (No immediate tax).
   - If Safe Harbor fails $\implies$ `CONSTRUCTIVE_SALE_TRIGGERED` (Realize gain $\text{FMV} - \text{Cost Basis}$ on entry date).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hedging During the 60-Day Unhedged Window**: Buying put options or re-establishing short exposure during the 60-day post-close safe harbor window, invalidating the exception and triggering retroactive tax penalties.
- **Missing the Jan 30 Cutoff**: Closing the short position on Feb 5 instead of Jan 30 following tax year-end, failing Requirement A.
- **Applying to Unappreciated Positions**: Flagging constructive sales on positions held at a loss. Section 1259 ONLY applies to appreciated financial positions ($\text{FMV} > \text{Cost Basis}$).

## Verification

- Instantiate `ConstructiveSaleRuleEngine`. Test an appreciated long stock ($100k cost basis, $250k FMV) hedged via a short sale. Simulate closing the short on Jan 15 of Year 2 and holding unhedged for 65 days. Verify that `SAFE_HARBOR_QUALIFIED` is returned. Test hedging again on Day 20 post-close and verify `CONSTRUCTIVE_SALE_TRIGGERED` with $150k realized taxable gain.
- Run `python scripts/test_constructive_sale_rule_considerations_us.py`.

## Related Skills

- `wash-sale-rule-tracking-us`
- `cross-strategy-tax-lot-optimization`
---
