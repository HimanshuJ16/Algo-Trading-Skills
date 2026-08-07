---
name: currency-gain-loss-tax-treatment-for-forex-trading
description: Quantitative tax model for evaluating US IRC Section 988 (Ordinary Income/Loss)
  vs Section 1256 (60/40 Capital Gains Opt-Out Election) tax treatment for spot forex,
  futures, and currency forwards.
domain: Tax Accounting & Global Reporting
subdomain: Forex Tax Accounting
tags:
- forex-tax
- section-988
- section-1256
- 60-40-rule
- ordinary-income
- currency-gains
- opt-out-election
brokers_frameworks:
- IRS Form 6781
- Form 1040
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in spot forex trading algorithms, global macro funds, and currency arbitrage strategies operating under US tax jurisdiction. By default, spot forex gains and losses fall under **IRC Section 988** (Ordinary Income / Loss, taxed at rates up to 37%). Active forex traders with net profitable strategies can elect out of Section 988 to qualify for **IRC Section 1256 (60/40 60% LTCG / 40% STCG)** treatment, lowering effective tax rates from $37.0\%$ to $\approx 26.8\%$. Conversely, strategies expecting net losses benefit from Section 988's uncapped ordinary loss deduction.

## Prerequisites

- Trade execution PnL log with instrument types (`SPOT_FOREX`, `CURRENCY_FUTURES`, `FORWARDS`).
- Taxpayer marginal rates: `ordinary_income_rate` (e.g. 37.0%), `ltcg_rate` (e.g. 20.0%).

## Workflow

1. **Trade PnL Aggregation**: Ingest total realized PnL for forex trades over tax year $T$.
2. **Section 988 Ordinary Tax Calculation**:
   - If Net PnL $> 0 \implies \text{Tax}_{988} = \text{PnL} \times \text{Ordinary Rate}$.
   - If Net PnL $< 0 \implies \text{Tax Benefit}_{988} = \text{Loss} \times \text{Ordinary Rate}$ (Uncapped ordinary loss deduction).
3. **Section 1256 (60/40) Opt-Out Calculation**:
   - Blended Rate $= 0.60 \times \text{LTCG Rate} + 0.40 \times \text{STCG Rate}$.
   - If Net PnL $> 0 \implies \text{Tax}_{1256} = \text{PnL} \times \text{Blended Rate}$.
   - If Net PnL $< 0 \implies$ Net capital loss subject to \$3,000 annual deduction cap.
4. **Election Recommendation**:
   - Recommend `ELECT_SECTION_1256` if Net PnL $> 0$ and $\text{Tax}_{1256} < \text{Tax}_{988}$.
   - Recommend `REMAIN_SECTION_988` if Net PnL $< 0$ to preserve full ordinary loss deduction.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing to File Contemporaneous Opt-Out**: Making a Section 1256 election at tax filing time rather than documenting the opt-out in internal books prior to placing the first trade.
- **Applying 60/40 Rules to Un-elected Spot Forex**: Assuming spot forex automatically receives 60/40 treatment without filing an explicit Section 988 opt-out election.
- **Forfeiting Ordinary Loss Deductions**: Opting into Section 1256 during a loss-making year, capping loss deductions at \$3,000 instead of claiming full ordinary loss write-offs.

## Verification

- Instantiate `ForexTaxTreatmentEngine`. Set Ordinary Rate = 37%, LTCG Rate = 20%. Input \$100,000 net profit from spot forex trades. Verify Section 988 tax = \$37,000, Section 1256 tax = \$26,800 (60% @ 20% + 40% @ 37%), and engine recommends `ELECT_SECTION_1256` (\$10,200 tax savings). Input \$50,000 net loss and verify engine recommends `REMAIN_SECTION_988`.
- Run `python scripts/test_currency_gain_loss_tax_treatment_for_forex_trading.py`.

## Related Skills

- `section-1256-contract-tax-treatment-us-futures`
- `capital-gains-vs-business-income-classification`
---
