---
name: currency-pair-quoting-convention-normalization
description: Quantitative FX market data normalization module for enforcing ISO 4217
  currency priority, converting inverted quotes (USD/EUR -> EUR/USD), and recalibrating
  bid-ask spreads.
domain: Data Management Global
subdomain: FX Market Data Normalization
tags:
- fx-quoting
- currency-pair
- iso-4217
- base-quote-currency
- inverted-quote
- bid-ask-conversion
- pip-calculation
brokers_frameworks:
- ISO 4217
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when processing multi-vendor foreign exchange (FX) market data feeds (Bloomberg, Refinitiv, Interactive Brokers, Crypto FX pairs) to normalize non-standard or inverted currency pair quotes into market-standard ISO 4217 conventions. Market conventions specify a strict priority hierarchy (`EUR` > `GBP` > `AUD` > `NZD` > `USD` > `CAD` > `CHF` > `JPY`). Ingesting non-standard quotes (e.g. `USD/EUR` or `JPY/USD`) directly into trading models causes inverted order execution, wrong side fills, and incorrect PnL accounting.

## Prerequisites

- ISO 4217 Currency Hierarchy List: `['EUR', 'GBP', 'AUD', 'NZD', 'USD', 'CAD', 'CHF', 'JPY']`.
- Raw quote payload (`symbol_raw`, `bid`, `ask`).

## Workflow

1. **Base / Term Priority Audit**:
   - For pair `CUR1/CUR2`: Check indices of `CUR1` and `CUR2` in ISO 4217 priority list.
   - If Index(`CUR1`) < Index(`CUR2`) $\implies$ Pair is already in Market Standard Order.
   - If Index(`CUR1`) > Index(`CUR2`) $\implies$ Pair is INVERTED (e.g. `USD/EUR`).
2. **Inverted Quote Conversion**:
   - Swap currencies to standard pair: `EUR/USD`.
   - Invert prices:
     $$\text{Bid}_{\text{standard}} = \frac{1}{\text{Ask}_{\text{inverted}}}, \quad \text{Ask}_{\text{standard}} = \frac{1}{\text{Bid}_{\text{inverted}}}$$
3. **Pip Size & Spread Computation**:
   - Determine pip size ($0.01$ for JPY terms, $0.0001$ for standard terms).
   - $\text{Spread Pips} = \frac{\text{Ask}_{\text{standard}} - \text{Bid}_{\text{standard}}}{\text{Pip Size}}$.
4. **Audit Report Generation**: Output structured `NormalizedFxQuoteReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naively Inverting Bids and Asks**: Setting $\text{Bid}_{\text{std}} = \frac{1}{\text{Bid}_{\text{inv}}}$ instead of $\frac{1}{\text{Ask}_{\text{inv}}}$, creating negative spreads or crossed markets.
- **Incorrect Pip Size Scaling**: Applying $0.0001$ pip sizing to JPY pairs (`USD/JPY`, `EUR/JPY`), miscalculating transaction costs by a factor of 100.
- **Ignoring Non-Standard Crypto FX Pairs**: Treating `USDT/EUR` or `BTC/USD` without validating base vs terms currency conventions.

## Verification

- Instantiate `CurrencyPairQuotingNormalizer`. Input an inverted quote `USD/EUR` with Bid = $0.9090$, Ask = $0.9095$. Verify normalizer converts pair to `EUR/USD`, calculates standard Bid = $1 / 0.9095 = 1.099505$, Ask = $1 / 0.9090 = 1.100110$, and calculates spread in pips ($6.05$ pips). Input standard `USD/JPY` quote ($150.00 / 150.03$) and verify pip size $0.01$ ($3.0$ pips).
- Run `python scripts/test_currency_pair_quoting_convention_normalization.py`.

## Related Skills

- `vendor-specific-adjustment-methodology-reconciliation`
- `cross-vendor-timestamp-precision-reconciliation`
---
