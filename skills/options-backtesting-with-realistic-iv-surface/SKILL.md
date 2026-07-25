---
name: options-backtesting-with-realistic-iv-surface
description: >-
  Use when backtesting options strategies (straddles, vertical spreads, iron condors) to interpolate dynamic 3D implied volatility (IV) surfaces across strike moneyness and term structure, avoiding flat IV mispricing errors.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "options-backtesting", "iv-surface", "volatility-smile", "greeks-calculation", "black-scholes", "derivatives"]
brokers_frameworks: ["Options IV Surface Engine", "Python SciPy", "NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when backtesting options trading strategies (e.g., delta-neutral straddles, vertical spreads, calendar spreads, iron condors). Naive option backtests assuming a flat constant IV or single ATM volatility introduce severe pricing errors (overpricing OTM calls, underpricing OTM puts due to volatility skew). This skill interpolates realistic 3D Implied Volatility surfaces $\sigma(K/S, T)$ across strike moneyness $K/S$ and time-to-expiration $T$, evaluating exact Black-Scholes option prices and Greeks.

## Prerequisites

- Underlying asset price series $S$, strike prices $K$, risk-free rate $r$, and historical ATM volatility $\sigma_{\text{atm}}$.
- Skew parameters $\alpha$ (skew slope) and $\beta$ (smile curvature).

## Workflow

1. **Construct Dynamic IV Surface $\sigma(K, T)$**:
   - Compute strike moneyness $m = \frac{K}{S}$.
   - Evaluate smile/skew model:
     $$\sigma(m, T) = \sigma_{\text{atm}}(T) + \alpha (m - 1.0) + \beta (m - 1.0)^2$$

2. **Evaluate Black-Scholes Option Price & Greeks**:
   - Compute $d_1 = \frac{\ln(S/K) + (r + 0.5 \sigma^2) T}{\sigma \sqrt{T}}$ and $d_2 = d_1 - \sigma \sqrt{T}$.
   - Calculate Call/Put price and Greeks ($\Delta, \Gamma, \Theta, \nu$).

3. **Simulate Portfolio Delta Hedging & Margin Requirements**:
   - Rebalance underlying hedge inventory based on net portfolio Delta $\sum \Delta_i$.

4. **Audit Volatility Skew Arbitrage Drag**:
   - Compare backtest returns computed with dynamic IV surface vs flat IV.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Flat ATM Volatility for OTM Options**: Underpricing OTM put hedges due to ignoring the equity volatility skew penalty.
- **Ignoring Dividend Adjustment in $d_1/d_2$**: Failing to adjust underlying price $S' = S - D e^{-r t_D}$ for discrete dividends prior to expiration.
- **Ignoring Early Assignment Risk**: Assuming options can never be assigned early prior to expiration.

## Verification

- Evaluate OTM put price ($K/S = 0.90$) under flat IV vs skewed IV, verifying expected volatility skew price premium.
- Compute Option Delta and Gamma across strike range $m \in [0.8, 1.2]$.
- Run `python scripts/test_options_iv_backtester.py` and confirm 100% pass rate.

## Related Skills

- `vectorized-vs-event-driven-backtest-tradeoffs`
- `transaction-cost-analysis-tca-integration`
- `options-greeks-risk-management`
---
