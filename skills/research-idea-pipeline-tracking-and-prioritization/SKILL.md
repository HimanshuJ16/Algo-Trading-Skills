---
name: research-idea-pipeline-tracking-and-prioritization
description: >-
  Quantitative research idea pipeline tracking and prioritization engine scoring alpha hypotheses by expected Sharpe ratio, capacity scaling, implementation complexity, and data acquisition cost.
domain: Quantitative Research & Alpha Management
subdomain: Research Governance & Pipeline Prioritization
tags: ["research-pipeline", "idea-tracking", "alpha-prioritization", "sharpe-ratio", "capacity-estimation", "quant-governance"]
brokers_frameworks: ["Alpha Prioritization Model", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing a quantitative research team or pipeline evaluating multiple candidate alpha strategies, execution algorithms, or alternative data signals. Quantitative research resources (researcher time, data budgets, compute power) are scarce. Without a formal prioritization framework, teams risk spending months researching low-capacity or overly complex ideas with weak Sharpe potential. This engine tracks research ideas across lifecycle stages (`PROPOSED`, `BACKTESTING`, `PAPER_TRADING`, `PRODUCTION_READY`), scores them using a multi-factor formula, and ranks top-priority projects.

## Prerequisites

- Research idea metadata (`idea_id`, `title`, `author`, `expected_sharpe`, `estimated_capacity_usd`, `implementation_complexity`, `data_cost_tier`).
- Stage lifecycle tracking (`PROPOSED`, `BACKTESTING`, `PAPER_TRADING`, `PRODUCTION_READY`, `REJECTED`).

## Workflow

1. **Idea Registration**:
   - Register new research idea with Sharpe, capacity, complexity, and data cost metrics.
2. **Multi-Factor Priority Scoring**:
   - Calculate Priority Score:
     $$\text{Priority Score} = \frac{\text{Expected Sharpe} \times \log_{10}(\text{Estimated Capacity})}{\text{Implementation Complexity} \times \text{Data Cost Tier}}.$$
3. **Lifecycle Stage Tracking**:
   - Update idea stage as research progresses from backtesting through paper trading.
4. **Pipeline Ranking & Report Generation**: Output structured `ResearchPipelineReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Chasing Low-Capacity Alphas**: Prioritizing high-Sharpe strategies that can only handle $100k capacity over scalable $50M strategies.
- **Ignoring Data Cost Penalties**: Spending months researching an alpha that requires $200k/year proprietary alternative data feeds without factoring in ROI.
- **Unstructured Pipeline Bottlenecks**: Allowing ideas to stall indefinitely in `BACKTESTING` stage without clear pass/fail criteria.

## Verification

- Instantiate `ResearchIdeaPipelineTrackingAndPrioritizationEngine`. Register high-Sharpe ($2.0$), $50M capacity, low complexity idea vs low-Sharpe ($1.2$), $10M capacity, high complexity idea $\implies$ verify high-Sharpe idea ranked #1. Update stage to `REJECTED` $\implies$ verify excluded from active top priority list.
- Run `python scripts/test_research_idea_pipeline_tracking_and_prioritization.py`.

## Related Skills

- `factor-research-multiple-testing-correction`
- `reproducible-ml-training-pipelines`
---
