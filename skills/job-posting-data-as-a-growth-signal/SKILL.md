---
name: job-posting-data-as-a-growth-signal
description: >-
  Quantitative alternative data signal engine analyzing web-scraped corporate job postings, measuring QoQ hiring velocity, engineering/R&D role mix, ghost job penalties, and corporate expansion scores.
domain: Quant Research & Alt Data
subdomain: Employment Alt Data & Corporate Expansion Factors
tags: ["job-postings", "alt-data", "hiring-velocity", "growth-signal", "ghost-jobs-filter", "alpha-factors", "corporate-expansion"]
brokers_frameworks: ["LinkUp / Coresignal Scraped Datasets", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing fundamental growth signals and equity factor models using alternative web-scraped job posting datasets (LinkUp, Coresignal, Glassdoor). Corporate hiring represents a costly, revealed-preference capital allocation decision that leads quarterly earnings and revenue growth by 1 to 3 months. This module calculates QoQ active job opening growth rates, weights high-value Engineering/R&D and Sales roles, applies a 50% haircut penalty for stale "ghost listings" ($> 120\text{ days}$ duration), and generates a normalized **Corporate Expansion Score**.

## Prerequisites

- Company job posting snapshot (`ticker`, `company_name`, `current_active_postings_count`, `previous_active_postings_count`, `engineering_postings_pct`, `sales_postings_pct`, `avg_posting_duration_days`).
- Stale listing threshold ($> 120\text{ days}$).

## Workflow

1. **Job Posting Data Ingestion & Stale Listing Filtering**:
   - Filter out stale "ghost listings" (positions open $> 120$ days without updates) by applying a $50\%$ haircut penalty.
2. **QoQ Active Hiring Velocity Calculation**:
   - Compute $\text{Growth}_{\text{pct}} = \frac{\text{Current} - \text{Previous}}{\max(1, \text{Previous})} \times 100$.
3. **Strategic Role Mix Weighting**:
   - Apply role multiplier $W_{\text{role}} = 1.0 + (\text{Engineering \%} \times 0.5) + (\text{Sales \%} \times 0.3)$.
4. **Normalized Growth Score & Signal Classification**:
   - Compute Score $S_{\text{growth}} \in [-1.0, +1.0]$.
   - $S_{\text{growth}} \ge +0.25 \implies$ `EXPANSION_BULLISH`.
   - $S_{\text{growth}} \le -0.25 \implies$ `CONTRACTION_BEARISH`.
   - Otherwise $\implies$ `STABLE_NEUTRAL`.
5. **Audit Report Generation**: Output structured `JobPostingSignalReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing to Filter Ghost Listings**: Including stale job postings that companies leave open for 6+ months without actively hiring, producing false expansion signals.
- **Ignoring Role Mix**: Equal-weighting administrative/replacement openings with strategic R&D / AI engineering hires that signal major new product investments.
- **Unadjusted Seasonal Hiring Noise**: Treating seasonal retail holiday hiring spikes as permanent corporate growth.

## Verification

- Instantiate `JobPostingSignalEngine`. Audit Tech Expansion (`current=300`, `previous=150` $\implies +100\%$ growth, Engineering $= 50\%$, Duration $= 30$ days) $\implies$ verify engine calculates $S_{\text{growth}} = +0.85$ and classifies `EXPANSION_BULLISH`. Audit Stale Layoff Company (`current=50`, `previous=100`, Duration $= 150$ days) $\implies$ verify ghost penalty and `CONTRACTION_BEARISH`.
- Run `python scripts/test_job_posting_data_as_a_growth_signal.py`.

## Related Skills

- `app-download-and-usage-data-for-consumer-companies`
- `patent-filing-data-for-innovation-signal-research`
---
