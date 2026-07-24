---
name: benchmark-relative-performance-attribution
description: >-
  Use when analyzing backtest or live trading performance to decompose total returns into Alpha, Beta, Tracking Error, Information Ratio, and Brinson-Fachler allocation and selection effects
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "performance-attribution", "alpha-beta", "information-ratio", "brinson-attribution"]
brokers_frameworks: ["PyFolio", "Empyrial", "QuantStats", "Custom Performance Evaluators"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever evaluating a quantitative trading strategy against a benchmark index (e.g. S&P 500 `SPY`, NIFTY 50 `NIFTY`, or Bitcoin `BTC`). Reporting raw returns alone is insufficient because high strategy returns may simply be the result of taking uncompensated market beta. Decomposing total strategy returns into Annualized Alpha ($\alpha$), Beta ($\beta$), Tracking Error ($TE$), Information Ratio ($IR = \frac{\bar{R}_p - \bar{R}_b}{TE}$), and Brinson-Fachler Allocation/Selection effects is mandatory for institutional performance validation.

## Prerequisites

- Synchronized daily return series for strategy portfolio ($R_p$) and benchmark ($R_b$).
- Sector/asset weightings for Brinson-Fachler sector attribution.
- Risk-free rate $R_f$ (default 0.0).

## Workflow

1. **Synchronize Portfolio & Benchmark Return Series**:
   - Align daily return arrays $R_p$ and $R_b$ by date.

2. **Compute Beta ($\beta$) and Annualized Alpha ($\alpha$)**:
   - Compute Beta:
     $$\beta = \frac{\text{Cov}(R_p, R_b)}{\text{Var}(R_b)}$$
   - Compute Annualized Alpha:
     $$\alpha = \left(\bar{R}_p - R_f\right) - \beta \cdot \left(\bar{R}_b - R_f\right)$$

3. **Compute Tracking Error ($TE$) and Information Ratio ($IR$)**:
   - Active Return series: $D_t = R_{p,t} - R_{b,t}$.
   - Annualized Tracking Error: $TE = \text{Std}(D_t) \cdot \sqrt{252}$.
   - Information Ratio: $IR = \frac{\text{Mean}(D_t) \cdot 252}{TE}$.

4. **Execute Brinson-Fachler Sector Attribution**:
   - For each sector $i$:
     - Allocation Effect: $A_i = (w_{p,i} - w_{b,i}) \cdot (R_{b,i} - R_b)$
     - Selection Effect: $S_i = w_{b,i} \cdot (R_{p,i} - R_{b,i})$
     - Interaction Effect: $I_i = (w_{p,i} - w_{b,i}) \cdot (R_{p,i} - R_{b,i})$

5. **Generate Attribution Sign-off Report**:
   - A strategy passes sign-off if $\alpha > 0$, $IR \ge 0.50$, and Selection Effect $> 0$.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Beta Outperformance with Alpha**: Claiming a 20% return strategy is superior when a 1.5 beta leveraged index returned 25%.
- **Un-Synchronized Return Series**: Mismatching dates between strategy returns and benchmark returns.
- **Negative Information Ratio**: Accepting strategies with high tracking error but negative active returns ($IR < 0$).

## Verification

- Submit synthetic portfolio returns ($R_p = 1.2 \cdot R_b + 0.05 / 252$) and verify $\beta \approx 1.20$ and $\alpha \approx 5.0\%$.
- Verify `compute_brinson_attribution()` correctly separates Allocation and Selection effects.
- Run unit test suite `python scripts/test_attribution_engine.py` and confirm 100% pass rate.

## Related Skills

- `walk-forward-optimization-window-management`
- `survivorship-bias-free-universe-construction`
- `multi-asset-backtest-currency-normalization`
---
