---
name: real-time-var-backtesting-kupiec-test
description: >-
  Kupiec Proportion-of-Failures (POF) Likelihood Ratio statistical test for real-time Value-at-Risk (VaR) model backtesting and Basel regulatory zone classification.
domain: Risk Governance & Regulatory Compliance
subdomain: Statistical Risk Model Validation & Backtesting
tags: ["kupiec-test", "var-backtesting", "proportion-of-failures", "basel-traffic-light", "likelihood-ratio", "risk-governance"]
brokers_frameworks: ["Basel Committee on Banking Supervision (BCBS) VaR Guidelines", "Scipy Stats", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when validating intraday or daily Value-at-Risk (VaR) models under regulatory frameworks (Basel II/III/IV). The Kupiec Proportion-of-Failures (POF) test evaluates whether the observed frequency of VaR exception breaches ($N_{\text{exceptions}}$) over $T$ observations statistically conforms to the expected failure probability $p = 1 - \alpha$ (e.g. $p=0.01$ for $99\%$ VaR). If breach frequencies significantly exceed expected levels, the VaR model is rejected, requiring model recalibration or capital buffer surcharges (Basel Red/Yellow Zones).

## Prerequisites

- Number of observations ($T$, e.g. 250 or 1,000 trading days).
- Number of VaR exception breaches ($x$ or $N_{\text{exceptions}}$).
- Confidence level ($\alpha$, default 0.99 for $99\%$ VaR).

## Workflow

1. **Binomial Likelihood Ratio & Exact Test Setup**:
   - Expected exception rate $p = 1 - \alpha$.
   - Calculate binomial probability $P(X = x) = \binom{T}{x} p^x (1-p)^{T-x}$.
2. **Kupiec Likelihood Ratio Statistic ($LR_{\text{POF}}$)**:
   - Compute Likelihood Ratio:
     $$LR_{\text{POF}} = -2 \ln \left[ \frac{(1-p)^{T-x} p^x}{\left(1 - \frac{x}{T}\right)^{T-x} \left(\frac{x}{T}\right)^x} \right]$$
   - $LR_{\text{POF}}$ asymptotically follows a Chi-Square distribution with 1 degree of freedom ($\chi_1^2$).
3. **Statistical Hypothesis Decision**:
   - If $p\text{-value} < 0.05 \implies$ Reject Null Hypothesis (VaR model invalid/underestimating risk).
   - If $p\text{-value} \ge 0.05 \implies$ Accept Null Hypothesis (VaR model validated).
4. **Audit Report Output**: Return structured `KupiecResult`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Clustering (Independence Assumption)**: Kupiec POF tests proportion of failures but does not detect consecutive exception clustering (use Christoffersen Independence Test for clustering).
- **Small Sample Size Distortion**: Running Kupiec tests over short observation horizons ($T < 250$), resulting in low statistical power.
- **Wrong Confidence Parameter**: Using $\alpha = 0.99$ while evaluating $95\%$ VaR data ($p = 0.05$).

## Verification

- Instantiate `KupiecVaRBacktester(confidence_level=0.99)`. Input $T=1000, x=10$ exceptions $\implies$ expected $10$, $p\text{-value} \approx 1.0 \implies$ model accepted (`is_rejected=False`). Input $T=1000, x=25$ exceptions $\implies p\text{-value} < 0.05 \implies$ model rejected (`is_rejected=True`).
- Run `python scripts/test_kupiec_var_backtester.py`.

## Related Skills

- `portfolio-stress-test-including-liquidity-crunch-scenarios`
- `margin-utilization-circuit-breaker`
---
