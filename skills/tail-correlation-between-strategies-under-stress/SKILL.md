---
name: tail-correlation-between-strategies-under-stress
description: Quantify lower-tail dependence and non-linear correlation spikes between strategies during market crash regimes.
domain: portfolio-multi-strategy
subdomain: tail-risk
tags: [tail-correlation, lower-tail-dependence, copula, diversification-breakdown, stress-testing]
brokers_frameworks: [numpy, pandas, scipy]
version: 1.0.0
author: Quant Team
license: MIT
---

# Tail Correlation Between Strategies Under Stress

The `tail-correlation-between-strategies-under-stress` skill measures lower-tail dependence ($\lambda_L$) and conditional exceedance correlation between multi-strategy portfolios during extreme downside market stress. It detects diversification breakdown where sub-strategies that appear uncorrelated during normal regimes become highly correlated during crashes.

## When to Use

- When allocating capital across multi-strategy hedge fund portfolios.
- When auditing diversification benefits prior to deploying capital to newly onboarded sub-strategies.
- During stress testing and Tail Risk Value-at-Risk (tVaR) modeling.
- When configuring portfolio-level risk limits for extreme market regimes.

## Prerequisites

- Overlapping daily return series for all evaluated strategy pairs ($\ge 20$ observations minimum).
- Python 3.9+ with `numpy` and `pandas`.

## Workflow

1. **Calculate Quantiles**: Determine the 10th percentile ($\alpha = 0.10$) downside return threshold for each strategy.
2. **Compute Unconditional Correlation**: Calculate standard Pearson correlation over the full evaluation period.
3. **Compute Lower Tail Dependence**: Evaluate empirical conditional probability $\lambda_L = \mathbb{P}(R_B \le q_B \mid R_A \le q_A)$.
4. **Compute Conditional Exceedance Correlation**: Calculate correlation conditioned on downside stress events ($R_A \le q_A$ or $R_B \le q_B$).
5. **Detect Diversification Breakdown**: Flag pair if conditional tail correlation $\ge 0.70$ or if $\Delta \rho = \rho_{\text{tail}} - \rho_{\text{uncond}} \ge 0.40$.

## Common Pitfalls

- **Assuming Gaussian Joint Distributions**: Assuming normal distribution understates extreme downside joint crash probabilities.
- **Short Sample Windows**: Using small sample sizes yields noisy quantile estimates; ensure sufficient historical crash data or synthetic stress scenarios.
- **Ignoring Non-linear Regime Shifts**: Relying solely on full-sample linear correlation masks hidden tail dependencies.

## Verification

Run the test suite:
```bash
python -m unittest test_tail_correlation_between_strategies_under_stress.py
```

## Related Skills

- `cross-strategy-correlation-monitoring`
- `tail-correlation-between-strategies-under-stress`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
