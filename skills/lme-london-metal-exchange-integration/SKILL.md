---
name: lme-london-metal-exchange-integration
description: >-
  Quantitative market gateway engine for the London Metal Exchange (LMEselect API), enforcing metal contract lot tonnage (Copper/Aluminium 25 MT, Nickel 6 MT), USD/MT tick sizes, and prompt date mechanics.
domain: Global Market Integration & FX
subdomain: Commodity Futures & LME Connectivity
tags: ["lme", "london-metal-exchange", "copper", "aluminium", "nickel", "prompt-dates", "3m-benchmark", "lmeselect"]
brokers_frameworks: ["LMEselect FIX Protocol", "LME Smart Routing", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing trading bots and order gateway interfaces for the London Metal Exchange (LME). Unlike standard futures exchanges that trade fixed monthly expiration contracts, LME operates on a unique **Prompt Date** structure (Daily prompts out to 3 months, Weekly out to 6 months, and Monthly out to 123 months) anchored by the benchmark **3-Month Prompt (3M)**. Orders are quoted in **USD per Metric Ton ($/MT)** with specific contract lot sizes (Copper `CA` / Aluminium `AH` $= 25\text{ MT}$, Nickel `NI` $= 6\text{ MT}$, Tin `SN` $= 5\text{ MT}$).

## Prerequisites

- LME order payload (`metal_code`: `CA`/`AH`/`NI`/`ZS`/`PB`/`SN`, `prompt_date`: `'3M'`/`'CASH'`/YYYY-MM-DD, `side`: `BUY`/`SELL`, `price_usd_per_mt`, `lots`).
- Official LMEselect contract specifications and tick size schedules ($0.50\text{ USD/MT}$).

## Workflow

1. **Metal Contract Specs & Tonnage Audit**:
   - Resolve metal contract specifications:
     - Copper (`CA`), Primary Aluminium (`AH`), Zinc (`ZS`), Lead (`PB`): $25\text{ MT}$ per lot.
     - Primary Nickel (`NI`): $6\text{ MT}$ per lot.
     - Tin (`SN`): $5\text{ MT}$ per lot.
   - Calculate total metric tonnage $M_{\text{tonnage}} = \text{lots} \times \text{lot\_size\_mt}$.
2. **Prompt Date Validation**:
   - Verify prompt date format (`'3M'`, `'CASH'`, or valid ISO YYYY-MM-DD date).
3. **USD/MT Price Tick Size Audit**:
   - Verify order price is an exact multiple of $\$0.50$/MT (LMEselect standard electronic tick).
4. **Total Notional Calculation & Report Generation**:
   - Calculate total notional $V_{\text{usd}} = M_{\text{tonnage}} \times P_{\text{usd\_per\_mt}}$. Output structured `LmeOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Lot Tonnage Across Metals**: Assuming 25 MT per lot for Nickel (`NI`), which actually trades in 6 MT lots, causing severe $4.16\times$ over-positioning errors.
- **Treating LME Like CME Monthly Futures**: Hardcoding single-date monthly expirations, failing to account for daily prompt date rolls.
- **Violating $0.50/MT Tick Size**: Submitting sub-penny prices like $\$9,250.23$/MT on LMEselect instead of $\$0.50$/MT increments ($\$9,250.50$/MT).

## Verification

- Instantiate `LmeExchangeApiEngine`. Route 10 lots of 3M Copper (`CA` @ $\$9,250.00$/MT, $25\text{ MT}$ lot) $\implies$ verify total tonnage $= 250\text{ MT}$, total notional $= \$2,312,500.00$, and approves `LME_ORDER_VALIDATED`. Route 10 lots of Nickel (`NI` @ $\$16,500.00$/MT, $6\text{ MT}$ lot) $\implies$ verify total tonnage $= 60\text{ MT}$. Audit invalid price tick ($\$9,250.23$/MT) $\implies$ verify `INVALID_TICK_SIZE`.
- Run `python scripts/test_lme_london_metal_exchange_integration.py`.

## Related Skills

- `jse-south-africa-api-integration`
- `exchange-tick-size-regime-tracking`
---
