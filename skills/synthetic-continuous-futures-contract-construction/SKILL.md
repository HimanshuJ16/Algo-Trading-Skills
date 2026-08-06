---
name: synthetic-continuous-futures-contract-construction
description: >-
  Production-grade Synthetic Continuous Futures Contract Construction Engine implementing volume/open-interest roll triggers and additive/proportional back-adjustment to eliminate roll date price discontinuities.
domain: Data Management & Quantitative Infrastructure
subdomain: Futures Market Data Engineering
tags: ["futures", "continuous-series", "back-adjustment", "roll-trigger", "market-data"]
brokers_frameworks: ["Futures Pipeline", "Pandas", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when constructing back-adjusted continuous futures time series for backtesting or quantitative technical analysis. Futures contracts expire at fixed intervals (quarterly/monthly). Appending raw individual contract prices creates artificial price jump discontinuities on roll dates, triggering false algorithmic signals or corrupting backtested P&L. This engine supports Volume Crossover (`VOLUME_CROSSOVER`), Open Interest Crossover (`OPEN_INTEREST_CROSSOVER`), and Calendar Roll rules paired with Additive Back-Adjustment (`ADDITIVE_BACK_ADJUSTMENT`) or Proportional Ratio Adjustments.

## Prerequisites

- Individual futures contract OHLCV DataFrames indexed by timestamp with volume and open interest fields.

## Workflow

1. **Contract Sequence Sorting**:
   - Order individual contracts chronologically by expiry code (e.g. `ESH24`, `ESM24`, `ESU24`, `ESZ24`).
2. **Roll Trigger Evaluation**:
   - Evaluate daily volume/open interest crossover: if $V_{\text{next}} > V_{\text{front}}$, trigger roll event.
3. **Cumulative Gap Calculation**:
   - On roll date $T$, compute price gap: $\text{Gap} = P_{\text{next}, T} - P_{\text{front}, T}$.
   - Update cumulative gap: $\text{CumGap} \leftarrow \text{CumGap} + \text{Gap}$.
4. **Price Adjustment & Output**:
   - Compute adjusted close: $P_{\text{adj}} = P_{\text{raw}} - \text{CumGap}$.
   - Output structured `ContinuousFuturesSeries`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naive Concatenation**: Appending raw contract prices without back-adjustment, creating massive artificial price spikes on roll dates.
- **Premature Rolling**: Rolling before liquidity shifts to the back contract, incurring wide bid-ask spread slippage.
- **Negative Historical Prices**: Using additive back-adjustment over multi-decade commodity series during deep backwardation, causing adjusted historical prices to turn negative (mitigate with ratio adjustment).

## Verification

- Construct continuous series for 2-contract mock dataset (`ESH24` and `ESM24`). Verify volume crossover triggers roll on expected date and cumulative gap equals exact price difference ($+25.0$).
- Run `python scripts/test_synthetic_continuous_futures.py`.

## Related Skills

- `survivorship-bias-free-universe-construction`
- `corporate-action-event-calendar-integration`
---
