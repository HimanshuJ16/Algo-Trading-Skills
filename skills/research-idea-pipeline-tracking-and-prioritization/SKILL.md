---
name: research-idea-pipeline-tracking-and-prioritization
description: >-
  Use when a quantitative research team has more candidate alpha ideas than
  researcher-months, to register each hypothesis once, rank the active backlog
  with a transparent multi-factor triage score (expected Sharpe, capacity,
  implementation complexity, data cost), enforce lifecycle transitions with an
  append-only audit trail, and surface ideas stalled in a stage.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- research-pipeline
- idea-tracking
- alpha-prioritization
- research-governance
- multiple-testing
brokers_frameworks:
- Research Pipeline Prioritization Engine
- Python standard library (math, dataclasses, enum, datetime)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a research team is choosing what to work on next and the backlog is longer than the capacity to research it. Researcher time, data budgets, and compute are scarce; without a written register, backlogs get ordered by whoever argued most recently, the same rejected idea gets re-proposed a year later by someone who was not in the room, and ideas sit in `BACKTESTING` for months with nobody accountable for a pass/fail call.

The engine gives you four things: a register that refuses duplicate ideas, a transparent triage score you can argue with, a lifecycle state machine with an append-only transition log (including *why* an idea was rejected), and a stalled-idea report that makes the bottleneck visible.

Score, in full — it is deliberately simple enough to audit by hand:

$$\text{priority} = \frac{S \times \log_{10}(C_{\text{USD}})}{k \times d}$$

where $S$ is expected Sharpe, $C$ is estimated capacity in **whole US dollars**, $k$ is implementation complexity (1–5) and $d$ is data cost tier (1–5).

## When NOT to Use

- **As an approval or sign-off control.** This is a triage aid. It records no testing evidence, no reviewer, no segregation of duties. Pre-deployment algorithm approval belongs to `strategy-research-to-production-pipeline-governance` and the jurisdiction-specific compliance skills.
- **With an unadjusted best-of-N backtest Sharpe.** `expected_sharpe` is the input that dominates the ranking, and a Sharpe selected as the best of many trials is biased upward by more than this score can ever resolve. Deflate it first (`factor-research-multiple-testing-correction`), or at minimum record that you did not.
- **To compare scores across two registers.** The score is ordinal and its scale depends on the capacity unit and on the tier conventions in use. A score of 8 is not "twice as good" as 4, and the 8 from last quarter's register is not comparable to this quarter's unless both used identical conventions.
- **With capacity in anything but US dollars.** The capacity term is $\log_{10}$ of a dimensional quantity. Priced in thousands, every score changes by $3S/(kd)$ — a *different* amount per idea — and the ranking reorders. See the Verification section for a worked example.
- **To detect duplicate or correlated ideas.** Two descriptions of the same trade both rank highly and the score cannot see it. Overlap is `cross-strategy-correlation-monitoring`.
- **As a capacity estimator.** `estimated_capacity_usd` is an input you must produce elsewhere — see `strategy-capacity-estimation-before-scaling-capital`.

## Prerequisites

- One idea record per hypothesis: `idea_id`, `title`, `author`, `expected_sharpe`, `estimated_capacity_usd`, `implementation_complexity` (1–5), `data_cost_tier` (1–5).
- **A single Sharpe convention across the whole register.** Same annualisation (annualised is usual), same adjustment for multiple testing, or the ranking compares numbers that do not mean the same thing.
- Capacity in **whole US dollars**, $\ge 1$. Below $\$1$ the logarithm turns negative and inverts the sign of the score; the engine raises.
- Non-negative expected Sharpe. A losing idea belongs in `REJECTED`, not in the ranking — with $S<0$ the denominator inverts and a *harder* idea would rank *better*.
- Thresholds you are willing to defend: `min_priority_score`, `top_n`, `max_stage_age_days`. The defaults are house heuristics with no external basis — see `references/standards.md`.
- A timezone-aware UTC clock. The engine takes an injectable `clock` so reports are reproducible; a naive datetime raises rather than silently mis-measuring time-in-stage by the local UTC offset.

## Workflow

1. **Register the idea once**:
   - `add_idea(ResearchIdea(...))` validates every field at construction: non-finite, negative-Sharpe, sub-$1 capacity, out-of-range tier, blank identity field, and unknown stage all raise `ResearchPipelineError`.
   - **Decision point — a duplicate `idea_id` is an error, not an update.** Overwriting would discard the existing idea's stage and its whole transition history, which is the record the register exists to keep. Re-registering a revised idea means either `update_stage` on the original or a new id.

2. **Score and rank the active backlog**:
   - `generate_pipeline_report()` scores every idea not in `REJECTED` and sorts descending.
   - **Decision point — rank on the exact score, break ties on `idea_id`.** Rounding before sorting manufactures ties that then resolve by registration order, so the same backlog entered in a different order produces a different "top idea". Format for display only at the point of display.
   - **Decision point — nothing is ever silently dropped.** Ideas below `min_priority_score` are ranked and returned with `below_priority_threshold=True` and counted in `below_threshold_count`. An idea that vanishes from the report is an idea nobody can decide about.
   - Read `ranked_ideas` for the whole active backlog; `top_priority_ideas` is the first `top_n` of it — the shortlist, not the backlog.

3. **Move the idea through its lifecycle**:
   - `update_stage(idea_id, new_stage, reason=...)` enforces `ALLOWED_TRANSITIONS`: `PROPOSED → BACKTESTING`, `BACKTESTING → PAPER_TRADING` (or back to `PROPOSED`), `PAPER_TRADING → PRODUCTION_READY` (or back to `BACKTESTING`), and any stage → `REJECTED`.
   - **Decision point — an unknown stage string raises rather than being accepted.** The old failure mode is specific and silent: `update_stage(id, "rejcted")` upper-cases to `REJCTED`, which is not `REJECTED`, so the idea stays in the active ranking while the stage breakdown shows a phantom bucket. Same for an unknown `idea_id` — it raises instead of returning `False`, because a caller who ignores a `False` believes an idea was rejected when it is still being ranked.
   - **Decision point — rejection requires a written reason.** An unexplained rejection cannot stop the same idea being proposed again next quarter, which is the entire point of keeping a register.
   - **Decision point — `REJECTED` is terminal; reviving is `reopen_idea(idea_id, reason)`.** A separate, logged call, so bringing a dead idea back is a deliberate act with the rejection still visible in `get_history(idea_id)`.

4. **Review the stalled backlog and prune**:
   - The report lists every non-terminal idea whose last stage change is older than `max_stage_age_days`, worst first, and logs a warning.
   - **Decision point — staleness measures time since the last stage change, not time since the last work.** An idea genuinely under active research for 60 days is reported as stalled. That is a prompt to record a stage change or a decision, not an accusation.
   - **Decision point — `PRODUCTION_READY` and `REJECTED` are terminal and never stall.** The pipeline's job on them is done; a promoted strategy's fate belongs to `strategy-lifecycle-retirement-criteria`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ranking on an unadjusted best-of-N Sharpe**: the proposer tried nine specifications and reported the tenth. Harvey, Liu & Zhu (2016) put the multiple-testing hurdle for a new factor near a t-ratio of 3.0 rather than 2.0; the selection bias this engine inherits from its input is larger than any ordering difference it produces.
- **Mixing Sharpe conventions in one register**: a daily Sharpe of 0.15 and an annualised Sharpe of 2.4 ranked side by side is a $\sqrt{252}$ error, not a research judgement.
- **Passing capacity in thousands or in a non-USD currency**: the score is $\log_{10}$ of a dimensional quantity, so the unit is part of the formula. Every score shifts by $3S/(kd)$ and the ranking reorders — silently, because all the numbers still look plausible.
- **Chasing low-capacity alphas**: a Sharpe-3.0 idea that holds $\$200\text{k}$ and a Sharpe-1.5 idea that holds $\$80\text{M}$ are not competing for the same capital. That is what the capacity term is for; deleting it turns the register back into a Sharpe beauty contest.
- **Ignoring data cost**: nine months of research on a signal that needs a $\$200\text{k}$/year alternative-data subscription nobody has budgeted.
- **Treating the score as cardinal**: writing "idea A is 2.4× better than idea B" in a memo. It orders a backlog; it is not an expected-value estimate, and its scale is an artefact of the log anchor and the tier conventions.
- **Trusting a stage typo**: `"rejcted"`, `"Rejected "`, `"REJECT"` — anything that is not a legal stage leaves the idea in the ranking. The engine raises; a hand-rolled register that upper-cases a string will not.
- **Silent duplicate registration**: re-adding an existing `idea_id` to "update" it wipes the stage and the audit trail, which converts a governed pipeline back into a spreadsheet.
- **Unbounded stage dwell**: no `stage_entered_at` means no way to answer "what is stuck", so the bi-weekly review has nothing to work from and ideas age out of relevance in `BACKTESTING`.
- **Re-researching a rejected idea**: without a recorded rejection reason, the register cannot tell a reviewer *why* the answer was no, and the same work gets commissioned again.

## Verification

- Instantiate `ResearchIdeaPipelineTrackingAndPrioritizationEngine(clock=FrozenClock())`. Register a $S{=}2.0$, $\$100\text{M}$, complexity 1, data cost 1 idea and a $S{=}1.2$, $\$10\text{M}$, complexity 4, data cost 4 idea. Because both capacities are exact powers of ten the expected scores are hand-computable: $2.0\times 8/1 = 16.0$ and $1.2\times 7/16 = 0.525$. Confirm ranks 1 and 2, and that the second is flagged `below_priority_threshold` at the default threshold of 1.0.
- Unit-dependence check: idea A ($S{=}1.0$, $\$10^9$, $k{=}1$, $d{=}1$) scores 9.0 and idea B ($S{=}6.0$, $\$10^4$, $k{=}2$, $d{=}1$) scores 12.0, so B ranks first. Divide both capacities by 1000 and A scores 6.0 while B scores 3.0 — **the ranking flips**. This is why capacity must be in whole US dollars.
- Negative checks, each must raise `ResearchPipelineError`: `NaN`/`±Inf` Sharpe or capacity, negative Sharpe, capacity below $\$1$, tier 0 / 6 / 50 / `2.0` / `True`, blank `idea_id`/`title`/`author`, stage `"REJCTED"`, duplicate `idea_id`, unknown `idea_id`, `PROPOSED → PRODUCTION_READY`, `REJECTED → BACKTESTING`, rejection with no reason, `reopen_idea` on a non-rejected idea, and a naive (timezone-less) clock.
- Determinism: register two ideas with identical scores as `Z_LAST` then `A_FIRST` and confirm `A_FIRST` ranks first — ties break on `idea_id`, not on registration order.
- Status: an empty register reports `NO_IDEAS`; a register whose every idea is `REJECTED` reports `NO_ACTIVE_IDEAS`, **not** `PIPELINE_ACTIVE`.
- Stall boundary: exactly `max_stage_age_days` in stage is not stalled; one second past it is. A stage change resets the clock; `PRODUCTION_READY` and `REJECTED` never stall.
- Run `python -m unittest discover -s . -p "test_*.py"` from the `scripts/` directory and confirm a 100% pass rate.

## Related Skills

- `factor-research-multiple-testing-correction`
- `strategy-research-to-production-pipeline-governance`
- `strategy-capacity-estimation-before-scaling-capital`
- `new-strategy-onboarding-checklist`
- `strategy-lifecycle-retirement-criteria`
- `reproducible-ml-training-pipelines`
