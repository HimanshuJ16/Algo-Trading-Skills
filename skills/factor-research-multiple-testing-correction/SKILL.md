---
name: factor-research-multiple-testing-correction
description: >-
  Quantitative research engine for applying multiple hypothesis testing corrections (Bonferroni, Holm-Bonferroni, Benjamini-Hochberg FDR, and Harvey-Liu-Zhu t>=3.0 haircut) to eliminate p-hacking and false alpha factor discoveries.
domain: Quantitative Research
subdomain: Factor Research & Overfitting Control
tags: ["factor-research", "multiple-testing-correction", "p-hacking", "fdr", "benjamini-hochberg", "bonferroni", "harvey-liu-zhu"]
brokers_frameworks: ["Harvey-Liu-Zhu 2016 Standard", "SciPy Stats", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative alpha research, factor discovery pipelines, and backtest overfitting control. When a quant team tests hundreds or thousands of candidate alpha factors ($M \ge 100$), traditional single-hypothesis significance testing ($t \ge 1.96$, $p \le 0.05$) suffers from severe p-hacking, declaring dozens of useless factors significant purely by chance. This module applies **Benjamini-Hochberg (BH) False Discovery Rate (FDR)**, **Bonferroni / Holm-Bonferroni FWER**, and **Harvey-Liu-Zhu ($t \ge 3.0$) haircut benchmarks**.

## Prerequisites

- List of candidate factor test results (factor ID, name, sample size $N$, raw t-statistic, raw p-value).
- Target significance level $\alpha = 0.05$ and FDR target $q^* = 0.05$.

## Workflow

1. **Raw Significance Audit**:
   - Evaluate raw unadjusted significance ($p_i \le 0.05$, $t_i \ge 1.96$).
2. **Bonferroni & Holm-Bonferroni FWER Correction**:
   - Compute Bonferroni threshold $\alpha_{\text{Bonferroni}} = \alpha / M$.
   - Execute Holm step-down sequence: $p_{(i)} \le \frac{\alpha}{M - i + 1}$.
3. **Benjamini-Hochberg (BH) False Discovery Rate (FDR) Control**:
   - Sort p-values $p_{(1)} \le p_{(2)} \le \dots \le p_{(M)}$.
   - Find max $k$ where $p_{(k)} \le \frac{k}{M} \times q^*$. Accept factors $1 \dots k$ as true discoveries.
4. **Harvey-Liu-Zhu (HLZ 2016) Haircut Benchmark**:
   - Enforce mandatory institutional t-statistic hurdle $t_i \ge 3.0$ ($p_i \le 0.0027$).
5. **Audit Report Generation**: Output structured `FactorMultipleTestingAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on Raw t-Stats ($t = 2.0$) for Large Factor Zoos**: Selecting top factors out of 1,000 candidates using $t \ge 2.0$ without multiple testing corrections, deploying random noise factors to production.
- **Over-Conservative Bonferroni Rejection**: Using rigid Bonferroni corrections on highly correlated factor families, mistakenly throwing away genuine alpha signals (Type II error).
- **Ignoring the Harvey-Liu-Zhu Haircut Benchmark**: Neglecting to apply $t \ge 3.0$ hurdles when testing historical anomaly factors documented in academic literature.

## Verification

- Instantiate `FactorMultipleTestingCorrectionEngine`. Ingest 100 candidate factor test results (where 10 are true alphas $t > 3.5$, 10 are marginal $t \approx 2.1$, and 80 are noise $t < 1.5$). Verify unadjusted testing falsely accepts 20 factors, Bonferroni accepts 10, and Benjamini-Hochberg FDR controls false discoveries to $\le 5\%$, correctly identifying true alpha factors.
- Run `python scripts/test_factor_research_multiple_testing_correction.py`.

## Related Skills

- `research-idea-pipeline-tracking-and-prioritization`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
---
