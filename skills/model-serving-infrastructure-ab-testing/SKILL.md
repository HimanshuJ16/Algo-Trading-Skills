---
name: model-serving-infrastructure-ab-testing
description: >-
  Champion-Challenger model serving A/B testing engine, managing deterministic traffic routing, shadow execution, and Welch's t-test statistical significance testing for model promotion.
domain: Quant Research Alt Data
subdomain: Model Serving Infrastructure & Live A/B Testing
tags: ["model-serving", "ab-testing", "champion-challenger", "welchs-t-test", "shadow-mode", "traffic-routing", "model-promotion"]
brokers_frameworks: ["Welch's Two-Sample t-Test", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying candidate machine learning alphas or execution algorithms into live production to replace an existing production model. Institutional trading infrastructure requires **Champion-Challenger A/B Testing** to validate performance improvements without exposing live capital to unproven model risk. This module manages deterministic traffic routing (e.g. 80% Champion vs 20% Challenger) or non-trading `SHADOW_MODE`, collects execution PnL returns, runs Welch's Two-Sample $t$-Test for statistical significance ($p < 0.05$), and outputs automated promotion or demotion recommendations.

## Prerequisites

- Experiment configuration (`experiment_id`, `champion_model_id`, `challenger_model_id`, `traffic_split_ratio`: e.g. 0.80, `test_mode`: `'LIVE_SPLIT'` or `'SHADOW'`, `min_sample_size`: e.g. 30).
- Execution return samples in basis points (`champion_returns_bps`, `challenger_returns_bps`).

## Workflow

1. **Deterministic Traffic Routing**:
   - Hash request key (`symbol`, `account_id`) to allocate traffic between Champion ($80\%$) and Challenger ($20\%$).
   - In `SHADOW_MODE`, log Challenger signals without executing orders.
2. **Execution Return Aggregation**:
   - Collect trade return samples in basis points ($\text{bps}$) for both models.
3. **Welch's Two-Sample $t$-Test**:
   - Compute mean returns ($\bar{X}_A, \bar{X}_B$) and sample variances ($s_A^2, s_B^2$).
   - Compute Welch's $t$-statistic:
     $$t = \frac{\bar{X}_B - \bar{X}_A}{\sqrt{\frac{s_A^2}{n_A} + \frac{s_B^2}{n_B}}}$$
   - Estimate degrees of freedom $\nu$ and calculate two-tailed $p$-value.
4. **Promotion / Demotion Decision**:
   - If $N < N_{\text{min}} \implies$ Output `CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES`.
   - If $\bar{X}_B > \bar{X}_A$ and $p < 0.05 \implies$ Recommend `PROMOTE_CHALLENGER_TO_CHAMPION`.
   - If $\bar{X}_B < \bar{X}_A$ and $p < 0.05 \implies$ Recommend `REJECT_CHALLENGER`.
5. **Audit Report Generation**: Output structured `ABTestReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Peeking & Early Stopping**: Halting A/B tests prematurely upon seeing a temporary positive $p$-value before reaching minimum sample sizes ($N < N_{\text{min}}$).
- **Ignoring Heteroscedasticity**: Using standard Student's $t$-test assuming equal variances instead of Welch's $t$-test when comparing volatile Challenger models against stable Champions.
- **Trading Live on Untested Challengers**: Deploying 50% live capital to a new Challenger without prior `SHADOW_MODE` validation.

## Verification

- Instantiate `ModelABTesterEngine`. Test 50 Champion returns ($\mu = 2.0\text{ bps}$) vs 50 Challenger returns ($\mu = 8.0\text{ bps}$) $\implies$ verify Welch's $t$-statistic calculation, $p < 0.01$, and status `PROMOTE_CHALLENGER_TO_CHAMPION`. Test underperforming Challenger ($\mu = -3.0\text{ bps}$) $\implies$ verify status `REJECT_CHALLENGER`.
- Run `python scripts/test_model_ab_tester.py`.

## Related Skills

- `model-card-documentation-for-trading-models`
- `factor-research-multiple-testing-correction`
---
