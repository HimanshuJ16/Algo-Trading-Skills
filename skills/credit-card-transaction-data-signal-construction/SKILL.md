---
name: credit-card-transaction-data-signal-construction
description: Quantitative alternative data pipeline module for aggregating credit
  card transaction panel data, normalizing panel bias, computing YoY sales growth
  metrics, and predicting Wall Street earnings surprises.
domain: Quant Research & Alt Data
subdomain: Consumer Transaction Data
tags:
- alt-data
- credit-card-data
- earnings-prediction
- yoy-growth
- consensus-surprise
- panel-normalization
brokers_frameworks:
- Pandas
- NumPy
- Scikit-Learn
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when processing consumer credit/debit card transaction panel feeds (e.g. Yodlee, Second Measure, Earnest Analytics) to forecast public retail and consumer sector quarterly revenues ahead of official earnings releases. Because consumer card panel data provides high-frequency daily spending signals, this module normalizes sample panel coverage, calculates Year-over-Year (YoY) sales growth, and generates directional earnings surprise signals (`BEAT_BUY` / `MISS_SELL`) against Wall Street consensus estimates.

## Prerequisites

- Aggregated transaction panel feed (`ticker`, `date`, `total_spend_usd`, `transaction_count`).
- Wall Street consensus revenue estimate for the upcoming fiscal quarter.
- Historical panel-to-reported-revenue scaling factor ($\gamma_{\text{panel}}$).

## Workflow

1. **Panel Normalization**:
   - Scale raw panel spend by coverage multiplier: $\text{Implied Revenue} = \text{Panel Spend} \times \gamma_{\text{panel}}$.
2. **YoY & QoQ Growth Calculation**:
   - $\text{YoY Growth} = \frac{\text{Implied Revenue}_t - \text{Implied Revenue}_{t-1}}{\text{Implied Revenue}_{t-1}}$.
   - Decompose into Ticket Size Growth ($\Delta \text{Ticket}$) and Transaction Volume Growth ($\Delta \text{Volume}$).
3. **Consensus Surprise Model**:
   - Calculate $\text{Surprise Pct} = \frac{\text{Implied Revenue} - \text{Consensus Revenue}}{\text{Consensus Revenue}} \times 100\%$.
4. **Signal Generation**:
   - If $\text{Surprise Pct} > +2.5\% \implies$ `BEAT_BUY`.
   - If $\text{Surprise Pct} < -2.5\% \implies$ `MISS_SELL`.
   - Else $\implies$ `NEUTRAL`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unadjusted Panel Shift**: Ignoring panel composition shifts (e.g., card provider losing market share), leading to false revenue contraction signals.
- **Ignoring Availability Lag**: Backtesting using transaction data on the date it occurred rather than the date it became available from the vendor (typically 3-5 days lag).
- **Overlooking Seasonality Gaps**: Comparing Q4 holiday spending directly against Q1 without YoY calendar alignment.

## Verification

- Instantiate `CreditCardTransactionSignalEngine`. Input historical quarterly panel spend for ticker `CMG` (Chipotle). Set scaling factor $\gamma = 50.0$. For current quarter, input panel spend of $40M ($\text{Implied Revenue} = \$2.0\text{B}$). Input Wall Street consensus of $\$1.90\text{B}$ ($+5.26\%$ surprise). Verify engine emits signal `BEAT_BUY` with $+5.26\%$ predicted surprise.
- Run `python scripts/test_credit_card_transaction_data_signal_construction.py`.

## Related Skills

- `web-scraped-sentiment-data-pipeline`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
---
