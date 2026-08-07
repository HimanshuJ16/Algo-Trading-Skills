---
name: dynamic-position-sizing-based-on-realized-volatility
description: Use when calculating portfolio position sizes to scale allocations inversely
  to recent realized volatility (volatility targeting), maintaining constant risk
  exposure across calm and volatile market regimes.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- volatility-targeting
- realized-volatility
- position-sizing
- riskmetrics-ema
- vol-scaling
brokers_frameworks:
- Realized Volatility Position Sizer
- Python NumPy
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing trend-following, mean-reversion, or multi-asset strategies where static capital allocation ($X\%$ per trade) leads to unintentional risk concentration during high-volatility market regimes. Volatility targeting dynamically scales position size inversely to annualized realized volatility $\sigma_{\text{realized}}$, ensuring that a strategy consumes a constant risk budget (e.g. $15\%$ target annualized volatility) whether the VIX is at $12$ or $45$.

## Prerequisites

- Daily or intraday return series $r_t$.
- Target annualized volatility $\sigma_{\text{target}}$ (e.g. $0.15 = 15\%$).
- Volatility estimator preference: Simple Rolling StdDev (e.g. 20-day) or RiskMetrics Exponentially Weighted Moving Average (EWMA with $\lambda=0.94$).

## Workflow

1. **Estimate Realized Volatility**:
   - Compute EWMA volatility:
     $$\sigma_t^2 = \lambda \sigma_{t-1}^2 + (1 - \lambda) r_t^2$$
   - Annualize volatility: $\sigma_{\text{ann}} = \sqrt{252} \times \sigma_t$.

2. **Compute Volatility Scalar**:
   $$\text{Scalar}_i = \frac{\sigma_{\text{target}}}{\max(\sigma_{\text{floor}}, \sigma_{\text{ann}})}$$

3. **Apply Min/Max Multiplier Bounds**:
   $$\text{FinalScalar}_i = \text{clip}(\text{Scalar}_i, \text{MinScalar}, \text{MaxScalar})$$

4. **Calculate Volatility-Adjusted Allocation**:
   $$\text{CapitalAllocation}_i = \text{BaseCapital} \times \text{FinalScalar}_i$$

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Lagged Volatility Response During Crashing Markets**: Using a 60-day simple moving average volatility estimator that reacts too slowly during a sudden market crash, leaving positions oversized.
- **Hyper-Leveraging in Ultra-Quiet Regimes**: Allowing the volatility scalar to reach $10\times$ during abnormally low vol periods without enforcing `MaxScalar` caps ($2.0\times$).

## Verification

- Test volatility sizer across quiet regime ($\sigma_{\text{realized}} = 7.5\%$, target $15\%$) and crash regime ($\sigma_{\text{realized}} = 60\%$, target $15\%$), verifying scalar scales from $2.0\times$ max cap down to $0.25\times$.
- Run `python scripts/test_realized_vol_sizer.py` and confirm 100% pass rate.

## Related Skills

- `correlation-aware-exposure-limits`
- `liquidity-adjusted-position-sizing`
- `value-at-risk-var-live-monitoring`
---
