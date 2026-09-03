---
name: tail-correlation-between-strategies-under-stress
description: Measure lower-tail dependence and joint-tail exceedance correlation between strategies, benchmarked against a Gaussian-copula null so conditioning bias is not mistaken for diversification breakdown.
domain: portfolio-multi-strategy
subdomain: tail-risk
tags: [tail-correlation, lower-tail-dependence, copula, diversification-breakdown, stress-testing]
brokers_frameworks: [numpy, pandas]
version: "2.0.0"
author: Quant Team
license: MIT
---

# Tail Correlation Between Strategies Under Stress

The `tail-correlation-between-strategies-under-stress` skill measures whether strategy pairs that look diversifying on the full sample stay diversifying in the **joint left tail**. It computes the lower-tail quantile exceedance correlation and an empirical tail-dependence estimate, and — critically — compares both against a **Gaussian-copula null simulated at the pair's own correlation, sample size and tail level**, so that the mechanical bias of conditioning on extreme observations is not read as evidence of diversification breakdown.

## When to Use

- When allocating capital across multi-strategy portfolios and the allocation assumes diversification survives a crash.
- When auditing a newly onboarded sub-strategy whose full-sample correlation to the book looks benign.
- During stress testing, when you need the joint-crash probability of a pair rather than its average comovement.
- When calibrating portfolio-level risk limits that are only binding in extreme regimes.

## When NOT to Use

- **As a capital control on its own.** The output is evidence for an allocation committee, not a limit engine. Enforce with `correlation-aware-exposure-limits` and `multi-strategy-capital-allocation-limits`.
- **On short histories.** At $\alpha = 0.10$ an independent pair puts only about $\alpha^2 n$ observations in the joint tail — roughly 5 for $n = 500$. Below `min_tail_observations` the engine returns `is_determinate=False`; that is a statement of ignorance, **not** a clean bill of health.
- **For upper-tail or asymmetry questions.** This module measures the lower tail only. Correlation asymmetry between the two tails is a separate estimation problem.
- **As a substitute for full-sample correlation monitoring.** Use `cross-strategy-correlation-monitoring` for rolling $\rho$ and diversification ratios; this skill answers a narrower, harder question.
- **To read `lower_tail_matrix` into an optimizer.** Every pair is estimated on its own overlap and its own tail subsample, so the matrix is not guaranteed positive semi-definite.

## Prerequisites

- Overlapping daily return series per pair, sharing one timestamp index, with at least `min_observations` (default 20) aligned non-null rows — and realistically far more, since the *joint tail* is what must be populated, not the sample.
- No non-finite values and no zero-variance (flat, stale or idle) series; the engine rejects both rather than imputing them.
- Python 3.9+ with `numpy` and `pandas`.

## Workflow

1. **Align and validate.** Join the pair on its index and drop non-overlapping rows. Equal series lengths do not imply a shared index — alignment happens before any sufficiency check, and the count of dropped rows is logged. Reject `±inf`, non-numeric values, duplicate index labels and zero-variance series outright.
2. **Compute the unconditional correlation.** This is context, not the comparison baseline (see step 5).
3. **Compute the joint-tail exceedance correlation.** Take the marginal $\alpha$-quantiles $q_A, q_B$ and correlate the observations where **both** $R_A \le q_A$ **and** $R_B \le q_B$ (the intersection, per Longin–Solnik and Ang–Chen). If fewer than `min_tail_observations` survive, or the tail slice is flat, stop: report `is_determinate=False` and NaN. Do not substitute a number.
4. **Compute empirical tail dependence.** $\hat\chi(\alpha) = \mathbb{P}(R_B \le q_B \mid R_A \le q_A)$. Read it against the independence baseline of $\alpha$ — under independence this statistic equals $\alpha$, not zero.
5. **Benchmark against a Gaussian copula.** Simulate the same estimator on bivariate normals drawn at the pair's own $\rho$, $n$ and $\alpha$. Report the **excess** over that benchmark and a one-sided p-value. This is the decision variable; the raw $\rho_{\text{tail}} - \rho_{\text{uncond}}$ delta is reported for continuity only and is dominated by selection bias.
6. **Flag breakdown.** Warn when the exceedance correlation reaches `breakdown_threshold` in absolute level, **or** when its excess over the Gaussian benchmark reaches `breakdown_excess_threshold` at a p-value no greater than `breakdown_max_pvalue`. Detection is one-sided: negative tail comovement is a diversification benefit, not a breach.

## Common Pitfalls

- **Comparing tail correlation to full-sample correlation.** Conditioning a sample on the size of its own variables changes the correlation of the retained subsample even when the true correlation is constant (Boyer, Gibson & Loretan 1997; Forbes & Rigobon 2002). A negative "delta" is the expected result for a perfectly well-behaved Gaussian pair, not a finding. Compare against a simulated null instead.
- **Conditioning on the union of the two tails.** Selecting rows where $R_A \le q_A$ **or** $R_B \le q_B$ retains an L-shaped region in which low-$A$ days pair with typical $B$ and vice versa. This manufactures strong *negative* correlation: a bivariate normal with true $\rho = 0.6$ scores about $-0.19$. Version 1.0.0 of this skill did exactly that and could therefore almost never fire.
- **Reading a thin joint tail as diversification.** The dangerous failure is a reassuring number computed from four observations. Treat `is_determinate=False` as "unmeasured", and require the joint tail to be populated before signing off on an allocation.
- **Mistaking $\hat\chi(\alpha)$ for the copula coefficient $\lambda_L$.** $\lambda_L$ is the limit as $u \to 0^+$; $\hat\chi(0.10)$ is a finite-level estimate whose independence baseline is $0.10$. A Gaussian pair with $\rho = 0.6$ scores $\hat\chi(0.10) \approx 0.39$ while being *asymptotically tail independent* ($\lambda_L = 0$). Judging "severe coupling" without that baseline flags ordinary correlation as tail risk.
- **Assuming Gaussian joint distributions in the risk model itself.** A bivariate normal with $|\rho| < 1$ has $\lambda_L = 0$ at any correlation (Embrechts, McNeil & Straumann 2002), so a Gaussian portfolio model structurally cannot produce joint crashes. That is precisely why this skill measures the excess over it.
- **Injecting identical crash values into test fixtures.** A block of constant crash returns gives the tail slice zero variance and an undefined correlation. The engine returns NaN rather than a spurious $\pm 1$.

## Verification

Run the test suite:
```bash
python -m unittest test_tail_correlation_between_strategies_under_stress.py
```

Expected statistical behavior (reproduced by the suite): a Clayton pair with $\lambda_L \approx 0.71$ is flagged; independent and Gaussian pairs at $\rho$ up to $0.8$ are not; a 30-observation sample returns `is_determinate=False`.

## Related Skills

- `cross-strategy-correlation-monitoring`
- `correlation-aware-exposure-limits`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
- `stress-testing-against-historical-crash-scenarios`
