---
name: cross-sectional-vs-time-series-model-design
description: >-
  Quantitative model architecture selector for distinguishing between Cross-Sectional (relative ranking, market-neutral) and Time-Series (absolute trend, volatility-scaled) alpha strategies.
domain: Quant Research & Modeling
subdomain: Model Architecture Design
tags: ["quant-modeling", "cross-sectional", "time-series", "z-score", "market-neutral", "volatility-scaling", "alpha-factors"]
brokers_frameworks: ["Pandas", "NumPy", "Scikit-Learn"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing quantitative trading models to choose between **Cross-Sectional (XSMOM / Relative Value)** and **Time-Series (TSMOM / Absolute Trend)** architectures. Cross-Sectional models evaluate assets relative to peers at a single point in time ($Z_{i,t} = \frac{X_{i,t} - \mu_{cs,t}}{\sigma_{cs,t}}$) to construct dollar-neutral, market-neutral portfolios. Time-Series models evaluate an asset relative to its own historical trajectory ($Z_{i,t} = \frac{X_{i,t} - \mu_{ts,i}}{\sigma_{ts,i}}$) to generate directional volatility-scaled positions.

## Prerequisites

- Multi-asset feature matrix $X_{N, K}$ ($N$ timestamps, $K$ assets).
- Strategy mandates: Market Neutrality requirement, Target Portfolio Volatility $\sigma_{\text{target}}$.

## Workflow

1. **Architecture Selection Audit**:
   - If strategy requires dollar/market neutrality across $K \ge 10$ assets $\implies$ Choose `CROSS_SECTIONAL`.
   - If strategy trades directional trends on single assets or futures $\implies$ Choose `TIME_SERIES`.
2. **Cross-Sectional Factor Normalization**:
   - For each timestamp $t$, standardize across asset axis:
     $$Z_{i,t} = \frac{X_{i,t} - \mu_{cs,t}}{\sigma_{cs,t}}$$
   - Demean weights to ensure market neutrality: $w_{i,t} = Z_{i,t} - \bar{Z}_t$.
3. **Time-Series Volatility Scaling**:
   - Standardize over time lookback $W$ per asset $i$:
     $$Z_{i,t} = \frac{X_{i,t} - \mu_{ts,i}}{\sigma_{ts,i}}$$
   - Scale weights by target volatility: $w_{i,t} = \text{sign}(Z_{i,t}) \times \frac{\sigma_{\text{target}}}{\sigma_{\text{realized, i}}}$.
4. **Signal Validation**: Confirm zero net exposure for cross-sectional signals and target risk alignment for time-series signals.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying Time-Series Scaling to Cross-Sectional Long-Short Portfolios**: Mixing up time-series lookbacks with cross-sectional peer ranks, breaking dollar neutrality.
- **Ignoring Cross-Sectional Market Beta**: Ranking raw returns without removing common market/sector factors ($\mu_{cs}$), causing the "cross-sectional" strategy to take implicit market directional exposure.
- **Un-scaled Time-Series Positions**: Sizing time-series trend positions on raw momentum without scaling by realized volatility, causing high-volatility assets to dominate portfolio risk.

## Verification

- Instantiate `ModelArchitectureSelectorEngine`. Input a $5 \times 10$ feature matrix. Run `transform_cross_sectional` and verify output weights sum to $0.0$ (dollar neutral) and $\sum |w| = 1.0$. Run `transform_time_series` with target volatility $15\%$ and verify weights are scaled inversely by realized volatility.
- Run `python scripts/test_model_architecture_selector.py`.

## Related Skills

- `ensemble-signal-combination-without-overfitting`
- `factor-research-multiple-testing-correction`
---
