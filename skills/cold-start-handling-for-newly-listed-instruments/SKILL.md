---
name: cold-start-handling-for-newly-listed-instruments
description: Quantitative feature engineering and risk management module for handling
  newly listed instruments (IPOs, SPACs) using Bayesian volatility shrinkage, peer
  imputation, and position sizing caps during probation windows.
domain: Portfolio & Risk Management
subdomain: Feature Engineering & Risk
tags:
- cold-start
- ipo
- shrinkage
- imputation
- volatility
- position-sizing
brokers_frameworks:
- NumPy
- Pandas
- Generic Quantitative Pipeline
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating newly listed assets (e.g., recent IPOs, newly listed crypto tokens, or spin-offs) into systematic trading strategies. Newly listed assets lack sufficient historical track records ($N < N_{min\_warmup}$ days), causing standard rolling indicators (50-day moving average, 30-day volatility, Sharpe ratio) to produce `NaN` values or extreme noise. The Cold Start Handler applies Bayesian shrinkage toward peer-group priors and enforces strict capital allocation caps during a probationary period.

## Prerequisites

- Access to peer-group baseline metrics (e.g., sector ETF average volatility).
- Historical observation count for each instrument.

## Workflow

1. **Age Assessment**: Compute the number of available daily observations $N_{obs}$ for the target instrument.
2. **Probation Check**: If $N_{obs} < N_{min\_warmup}$ (e.g., 30 trading days), flag the instrument as `PROBATIONARY`.
3. **Bayesian Shrinkage Imputation**:
   - Compute observation weight: $w = \frac{N_{obs}}{N_{min\_warmup}}$.
   - Estimate volatility: $\sigma_{est} = w \cdot \sigma_{observed} + (1 - w) \cdot \sigma_{peer\_prior}$.
4. **Capital Allocation Cap**:
   - Limit max position size: $\text{Cap} = \text{Base\_Max\_Size} \times w$.
5. **Graduation**: Once $N_{obs} \ge N_{min\_warmup}$, transition instrument to `STANDARD` status with 100% weight on empirical data.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unshrinked Sample Volatility**: Calculating 5-day realized volatility for a 5-day old IPO and using it for Kelly position sizing. Extreme early noise will cause massive over-leveraging or complete under-allocation.
- **Dropping IPOs Entirely**: Rejecting all newly listed stocks for 60 days, missing early post-IPO drift or liquidity-driven alpha opportunities.
- **Ignoring Peer Sector Priors**: Using zero as a fallback for missing volatility instead of imputing the sector ETF prior.

## Verification

- Instantiate `ColdStartHandler` with $N_{min\_warmup}=30$. Feed an asset with 5 days of data ($\sigma_{obs} = 0.80$) and a sector prior ($\sigma_{peer} = 0.20$). Verify that the estimated volatility is smoothly shrunken toward the peer prior ($\sim 0.30$) and that position size is capped at $16.7\%$ ($5/30$).
- Run `python scripts/test_cold_start_handler.py`.

## Related Skills

- `categorical-feature-encoding-for-instrument-identity`
- `new-strategy-onboarding-checklist`
