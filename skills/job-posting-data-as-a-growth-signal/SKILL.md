---
name: job-posting-data-as-a-growth-signal
description: >-
  Use when building a fundamental growth feature from web-scraped job postings,
  measuring quarter-on-quarter hiring velocity and engineering role mix while penalising
  stale ghost listings.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: quant-research-alt-data
  tags: job-postings, alt-data, hiring-velocity, growth-signal, ghost-jobs-filter, alpha-factors, corporate-expansion
  brokers_frameworks: "LinkUp / Coresignal Scraped Datasets; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building **fundamental growth** features for equity factor models from web-scraped job posting datasets (LinkUp, Coresignal, Greenhouse-sourced panels). Corporate hiring is a costly, revealed-preference capital allocation decision, and the peer-reviewed evidence supports it as a forward-looking disclosure: Gutiérrez, Lourie, Nekrasov and Shevlin (*Management Science*, 2020) find changes in online job postings are positively associated with future firm performance — one-year-ahead growth in headcount, sales and earnings — with a **stronger relation when the postings represent growth rather than replacement hiring**. That growth-vs-replacement distinction is what the role-mix weighting in this module approximates.

This module computes, for one company and one observation period: QoQ active-posting growth, a role-mix multiplier that up-weights Engineering/R&D and Sales openings, a configurable stale-listing haircut that shrinks the score toward neutral when postings look like ghost listings, a small-base gate, and a normalized **Corporate Expansion Score** in $[-1.0, +1.0]$.

## When NOT to Use

- **Not an expected-return forecast.** `EXPANSION_BULLISH` describes the *firm's* trajectory, not the *stock's*. The accounting-based hiring-rate factor points the other way in the cross section: Belo, Lin and Bazdresch (*JPE*, 2014) find high hiring rates predict **lower** subsequent returns (roughly -1.5pp annual risk premium per +10pp hiring rate). Combine this score with valuation and risk factors; never wire it straight to an order router.
- **Not point-in-time safe on its own.** The engine takes two counts and has no knowledge-time axis. Feeding it vendor data stamped with the *observation* date rather than the *delivery* date leaks look-ahead into any backtest. Wrap the input with `backtesting-alt-data-strategies-with-realistic-availability-lag` first.
- **Not a licensing or compliance control.** Scraped posting data raises vendor contract, web-scraping and MNPI questions this module does not touch. See `alternative-data-vendor-due-diligence-checklist` and `insider-trading-controls-for-alternative-data-usage`.
- **Not a cross-vendor comparator.** Aggregator-sourced feeds carry duplicate listings that direct-from-career-site feeds do not. Two vendors report different counts for the same company, so a series spliced across vendors — or across a panel-coverage change — measures the vendor, not the company.
- **Not valid on thin coverage.** Sub-`min_previous_postings` bases produce arithmetic, not signal: 2 to 10 postings is +400%. Those snapshots return `INSUFFICIENT_DATA`.
- **Not seasonally adjusted.** See the pitfall below; QoQ on a seasonal hirer measures the calendar.

## Prerequisites

- Company job posting snapshot (`ticker`, `company_name`, `current_active_postings_count`, `previous_active_postings_count`, `engineering_postings_pct`, `sales_postings_pct`, `avg_posting_duration_days`).
- Both counts drawn from the **same vendor panel** under the **same point-in-time convention**, with the vendor's publication lag already applied upstream.
- Role shares expressed as fractions in $[0.0, 1.0]$ (0.5 means 50%), summing to at most 1.0 — they are shares of the same posting count and cannot overlap.
- Calibration decisions for the four engine parameters. The defaults (120-day stale threshold, 0.5 haircut, 10-posting floor, 0.25 classification band) are this module's conventions, **not** an industry or regulatory standard; no external source prescribes them.

## Workflow

1. **Validate the Snapshot Before Scoring**: `calculate_growth_score` calls `snapshot.validate()` first and raises `JobPostingSignalError` on negative counts, NaN/infinite values, shares outside $[0,1]$, role shares summing above 100%, or a blank ticker. This is deliberate: an unchecked NaN count clamps to -1.0 and is reported as a confident `CONTRACTION_BEARISH`, and `nan > 120` is `False`, so a NaN duration would silently escape the ghost penalty.
2. **Gate the Small Base**: if `previous_active_postings_count < min_previous_postings` (default 10), classification is `INSUFFICIENT_DATA` and the score is 0.0. A zero previous count has no defined growth rate at all — the engine reports 0.0 rather than substituting a denominator.
3. **QoQ Active Hiring Velocity**: compute $\text{Growth}_{\text{pct}} = \frac{\text{Current} - \text{Previous}}{\text{Previous}} \times 100$. This value is always reported for audit, even when the base gate suppresses the score.
4. **Strategic Role Mix Weighting**: apply $W_{\text{role}} = 1.0 + (\text{Engineering share} \times 0.5) + (\text{Sales share} \times 0.3)$, so a 100%-engineering book carries $W = 1.5$.
5. **Stale Listing Haircut**: if `avg_posting_duration_days` is **strictly greater** than the threshold, multiply by $(1 - \text{haircut factor})$. Note the direction: this shrinks the score toward **neutral in both directions**, so a contraction of $-0.54$ becomes $-0.27$ and a $-0.40$ reading is demoted from bearish to neutral. Stale postings make the count less informative — they do not make the firm more bearish.
6. **Score, Clamp and Classify**: $S_{\text{growth}}$ is the clamp of the raw product into $[-1.0, +1.0]$.
   - $S_{\text{growth}} \ge +0.25 \implies$ `EXPANSION_BULLISH`.
   - $S_{\text{growth}} \le -0.25 \implies$ `CONTRACTION_BEARISH`.
   - Otherwise $\implies$ `STABLE_NEUTRAL`.
7. **Rank on the Unclamped Score**: the clamp saturates above roughly 75% QoQ growth, collapsing every fast grower onto exactly $+1.0$. For cross-sectional ranking use `raw_growth_score` (unclamped) or `qoq_postings_growth_pct`; $|\text{raw}| > 1$ marks a saturated reading and the audit note carries `[SATURATED at clamp bound]`.
8. **Audit Report Generation**: output structured `JobPostingSignalReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading `EXPANSION_BULLISH` as a Buy**: the label describes hiring, not expected return, and the hiring-rate literature finds the opposite sign for returns. A backtest that longs the top expansion decile without a valuation control is testing a documented *negative* return predictor.
- **A Mean Duration That Hides the Ghost Tail**: 90% fresh postings plus 10% two-year-old ghosts averages about 100 days and never trips a 120-day threshold. The average is a blunt detector; where the vendor exposes a stale *share* or per-posting creation/deletion dates, prefer those and set the threshold accordingly.
- **Haircutting a Hard-to-Fill Senior Req as a Ghost Job**: Chen and Li (*Review of Accounting Studies*, 2023) find longer vacancy duration for **high-skill** roles is associated with **higher** future profitability, while fast fills signal strength for low-skill roles. A flat haircut on an engineering-heavy firm penalises exactly the case the evidence says is benign — the same firm this module's role weighting just up-weighted.
- **The Haircut Quietly Rescuing a Shrinking Firm**: because it shrinks toward neutral symmetrically, a firm cutting postings 40% with a stale book scores $-0.20$ and reads `STABLE_NEUTRAL`, not bearish. Check `has_ghost_postings_penalty` before trusting a neutral reading.
- **Ranking on a Saturated Score**: two firms at $+1.34$ and $+3.00$ raw both report $+1.0$. Any cross-sectional sort on `corporate_growth_score` silently ties the entire top of the distribution.
- **Small-Base Arithmetic**: a company going from 2 to 10 postings is +400% growth and would dominate any long book. Hence the base gate — do not lower it below the vendor's coverage floor for micro-caps.
- **Unadjusted Seasonal Hiring Noise**: QoQ comparison treats a retailer's Q4 holiday requisition spike as corporate growth. The engine is period-agnostic: pass the **same quarter one year prior** as `previous_active_postings_count` for seasonal hirers, or de-seasonalize upstream. Do not mix YoY and QoQ bases within one cross-section.
- **Splicing Vendors or Panels**: a vendor adding a company's regional career sites mid-history creates a posting-count jump indistinguishable from real hiring.
- **Trading the Observation Date**: posting counts are collected, deduplicated and delivered with a lag. Scoring on the observation date rather than the delivery date is look-ahead bias, not alpha.

## Verification

- Audit Tech Expansion (`current=300`, `previous=150` $\implies +100\%$ growth, Engineering $= 50\%$, Sales $= 30\%$, Duration $= 30$ days): role factor $= 1 + 0.25 + 0.09 = 1.34$, raw score $= 1.34$, so the reported $S_{\text{growth}}$ is the **clamp bound $+1.0$** (saturated, not $1.34$) and the classification is `EXPANSION_BULLISH`.
- Audit Stale Layoff Company (`current=50`, `previous=100`, Engineering $= 10\%$, Sales $= 10\%$, Duration $= 150$ days): role factor $= 1.08$, raw $= -0.50 \times 1.08 \times 0.5 = -0.27 \implies$ `CONTRACTION_BEARISH` with `has_ghost_postings_penalty=True`. Re-run at Duration $= 90$ days and confirm the score doubles to $-0.54$ — the haircut is symmetric.
- Assert the threshold is strict: Duration $= 120.0$ carries no penalty, $120.01$ does.
- Assert `previous=0` and `previous=2` both return `INSUFFICIENT_DATA` with a zero score, and that `previous=10` is scored.
- Assert NaN counts, NaN durations, negative counts, a share passed as `50`, and role shares summing above 1.0 all raise `JobPostingSignalError` rather than producing a score.
- Assert two saturating firms with raw scores $1.34$ and $3.00$ are separable by `raw_growth_score` while both report $+1.0$.
- Run `python -m unittest discover -s skills/job-posting-data-as-a-growth-signal/scripts` and confirm a 100% pass rate.

## Related Skills

- `app-download-and-usage-data-for-consumer-companies`
- `patent-filing-data-for-innovation-signal-research`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
- `alternative-data-vendor-due-diligence-checklist`
- `insider-trading-controls-for-alternative-data-usage`
