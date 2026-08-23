---
name: credit-card-transaction-data-signal-construction
description: Quantitative alternative data pipeline module for aggregating credit card
  transaction panel data, normalizing panel bias, computing YoY sales growth metrics,
  and predicting Wall Street earnings surprises.
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
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when processing consumer credit/debit card transaction panel feeds (e.g. Yodlee, Bloomberg Second Measure, Earnest Analytics, Facteus) to forecast public retail and consumer sector quarterly revenues ahead of official earnings releases. Because consumer card panel data provides high-frequency daily spending signals, this module normalizes sample panel coverage, calculates Year-over-Year (YoY) sales growth decomposed into ticket-size and transaction-volume components, and generates directional earnings surprise signals (`BEAT_BUY` / `MISS_SELL`) against point-in-time Wall Street consensus estimates.

## When NOT to Use

- **Intraday or execution decisions.** This engine produces quarterly pre-earnings research signals with a vendor delivery lag of hours-to-days; it is not a timing or execution tool.
- **Position sizing off `confidence_score`.** That field is a naive linear heuristic rank in [0, 1], not a calibrated probability — size positions from your own backtested statistics only.
- **Companies without meaningful card-panel coverage** (B2B, wholesale-driven, or heavily cash markets) — panel noise dominates any signal.
- **Backtesting without point-in-time discipline.** This engine has no availability-lag or revision handling; use `backtesting-alt-data-strategies-with-realistic-availability-lag` to enforce the vendor's actual delivery lag and as-delivered snapshots.

## Prerequisites

- Aggregated transaction panel feed (`ticker`, `date`, `total_spend_usd`, `transaction_count`).
- Point-in-time Wall Street consensus revenue estimate for the upcoming fiscal quarter (restated consensus leaks information).
- Historical panel-to-reported-revenue scaling factor ($\gamma_{\text{panel}}$), calibrated against reported 10-Q revenue.

## Workflow

1. **Panel Normalization**:
   - Scale raw panel spend by coverage multiplier: $\text{Implied Revenue} = \text{Panel Spend} \times \gamma_{\text{panel}}$.
2. **YoY Growth Calculation & Decomposition** (seasonality-aligned quarters, $t$ vs $t-4$):
   - $\text{YoY Growth} = \frac{\text{Implied Revenue}_t - \text{Implied Revenue}_{t-4}}{\text{Implied Revenue}_{t-4}}$.
   - Decompose multiplicatively: $(1 + g_{\text{revenue}}) = (1 + g_{\text{ticket}}) \times (1 + g_{\text{volume}})$ via `decompose_growth` — average ticket = panel spend / transaction count.
   - Decision point: volume growth collapsing while ticket growth stays stable suggests a panel composition shift, not true demand contraction — recalibrate $\gamma$ before trading the signal.
3. **Consensus Surprise Model**:
   - Calculate $\text{Surprise Pct} = \frac{\text{Implied Revenue} - \text{Consensus Revenue}}{\text{Consensus Revenue}} \times 100\%$.
4. **Signal Generation** (boundaries inclusive; threshold is a calibratable default, not a regulatory constant — tune it to measured panel noise):
   - If $\text{Surprise Pct} \ge +2.5\% \implies$ `BEAT_BUY`.
   - If $\text{Surprise Pct} \le -2.5\% \implies$ `MISS_SELL`.
   - Else $\implies$ `NEUTRAL`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unadjusted Panel Shift**: Ignoring panel composition shifts (e.g. card provider losing market share), leading to false revenue contraction signals — credit-card panels demonstrably moved retail stocks on such artifacts (WSJ, 2017). The ticket/volume decomposition is the first-line diagnostic.
- **Assuming a universal vendor lag**: delivery lags range from ~12 hours to 5-7 days or weekly depending on vendor and product tier; a blanket 3-day assumption both under- and over-states availability. Always use the vendor's documented availability timestamps.
- **Ignoring Panel Revisions**: card panels restate as late-posting transactions settle — backtesting on final restated values rather than as-delivered snapshots introduces look-ahead bias.
- **Ignoring Availability Lag**: Backtesting using transaction data on the date it occurred rather than the date it became available from the vendor.
- **Overlooking Seasonality Gaps**: Comparing Q4 holiday spending directly against non-aligned periods; align on fiscal (e.g. NRF 4-5-4) calendars and handle 53-week-year restatements.
- **Silent data errors**: a zero or negative prior-year panel base is a data problem, not 0% growth — the engine raises `ValueError` rather than emitting a false flat-growth number; missing prior-year data yields `NaN`, distinguishable from a true 0.0.

## Verification

- Instantiate `CreditCardTransactionSignalEngine(panel_scaling_multiplier=50.0)`. For ticker `CMG` (Chipotle), current quarter panel spend $40M ($\text{Implied Revenue} = \$2.0\text{B}$), consensus $\$1.90\text{B}$: verify signal `BEAT_BUY`, predicted surprise $+5.26\%$, heuristic confidence $0.76$, and `yoy_growth_pct = NaN` (no prior year supplied).
- With prior year `2024-Q1` panel spend $30M / 1.5M transactions and current $33M / 1.6M: verify YoY $= +10.0\%$, ticket-size growth $+3.125\%$, transaction-volume growth $+6.6667\%$, and $1.03125 \times 1.066667 - 1 = 10\%$ (multiplicative identity).
- Run `python -m unittest discover -s skills/credit-card-transaction-data-signal-construction/scripts`.

## Related Skills

- `web-scraped-sentiment-data-pipeline`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
