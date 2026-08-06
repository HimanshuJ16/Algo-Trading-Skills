---
name: synthetic-labels-from-triple-barrier-method
description: >-
  Production-grade Triple Barrier Method Synthetic Labeler (Marcos López de Prado framework) generating machine learning classification target labels (+1 Take-Profit, -1 Stop-Loss, 0 Vertical Time-Out) with dynamic volatility barrier scaling.
domain: Machine Learning & Quantitative Signal Generation
subdomain: Financial Labeling & Feature Engineering
tags: ["triple-barrier", "lopez-de-prado", "financial-ml", "take-profit", "stop-loss", "synthetic-labels"]
brokers_frameworks: ["Financial ML Pipeline", "Pandas", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when generating supervised target labels for financial machine learning classifiers (e.g. XGBoost, Random Forest, Neural Networks predicting directional signals or trade execution side). Traditional fixed-horizon return labeling ignores path dependency (e.g. price hitting stop-loss prior to target horizon). The Triple Barrier Method sets three dynamic limits: Upper Take-Profit Barrier ($+1$), Lower Stop-Loss Barrier ($-1$), and Vertical Time-Out Expiration Barrier ($0$).

## Prerequisites

- Price time series (pandas `Series`) indexed by timestamp.
- Volatility multiplier settings (`pt_mult`, `sl_mult`) and holding window length (`vertical_bars`).

## Workflow

1. **Dynamic Volatility Estimation**:
   - Compute exponentially weighted moving volatility $\sigma_t = \text{EWMStd}(\Delta \log P_t)$.
2. **Barrier Width Sizing**:
   - Upper Take-Profit Barrier: $U_t = P_t (1 + pt \cdot \sigma_t)$.
   - Lower Stop-Loss Barrier: $L_t = P_t (1 - sl \cdot \sigma_t)$.
3. **Forward Horizon Scanning**:
   - Scan future prices $P_{t+k}$ for $k \in [1, \text{vertical\_bars}]$.
   - Label $+1$ if $P_{t+k} \ge U_t$ first.
   - Label $-1$ if $P_{t+k} \le L_t$ first.
   - Label $0$ if vertical time barrier $k = \text{vertical\_bars}$ is reached without touching horizontal limits.
4. **DataFrame Output**: Return DataFrame with entry/exit timestamps, entry/exit prices, realized returns, and barrier labels.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Static Unscaled Barriers**: Using fixed percentage profit targets (e.g. $\pm 2\%$) regardless of market regime, causing excessive stop-outs during high-volatility regimes and missed targets during low-volatility regimes.
- **Lookahead Bias in Volatility**: Computing volatility using future prices; ensure volatility $\sigma_t$ uses only expanding or historical rolling windows up to time $t$.
- **Ignoring Meta-Labeling**: Training models directly on raw directional labels without using meta-labeling to decide trade sizing or trade filtering.

## Verification

- Generate labels for price series with sharp upward spike $\implies$ verify label equals $+1$ (`TAKE_PROFIT`). Generate labels for downward crash $\implies$ verify label equals $-1$ (`STOP_LOSS`). Generate labels for flat sideways consolidation $\implies$ verify label equals $0$ (`VERTICAL_TIMEOUT`).
- Run `python scripts/test_triple_barrier_labeler.py`.

## Related Skills

- `synthetic-data-generation-for-backtest-augmentation`
- `order-book-microstructure-signal-research`
---
