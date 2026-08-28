# Pre-Flight / Review Checklist — research-idea-pipeline-tracking-and-prioritization

Use this before circulating a research priority ranking or running a backlog review.

## Inputs

- [ ] **One Sharpe convention across the whole register:** same horizon, same annualisation, same risk-free treatment. A daily $0.15$ ranked against an annualised $2.4$ is a $\sqrt{252}$ error, not a judgement.
- [ ] **Selection bias addressed:** each `expected_sharpe` is deflated for the number of trials behind it, or the register explicitly records that it is not. (Bailey & López de Prado, 2014; Harvey, Liu & Zhu, 2016 — see `references/standards.md`.)
- [ ] **Capacity in whole US dollars,** produced by an actual capacity analysis rather than the strategy's ambition. Not thousands, not another currency — the unit changes the ranking.
- [ ] **Tiers within $[1,5]$** and applied consistently by everyone filing ideas; the tier scale is written down somewhere.
- [ ] **Clean registration run:** no `ResearchPipelineError` raised. The engine rejects non-finite, negative-Sharpe, sub-$\$1$-capacity, out-of-range-tier, blank-field and unknown-stage inputs — a clean run is the evidence.

## Register integrity

- [ ] **No duplicate ids:** a re-registered `idea_id` raises. If one did, the revision was filed under its own id and the original was rejected with a reason pointing at it.
- [ ] **Every rejection carries a reason** in `get_history(idea_id)`. Without one, the register cannot stop the same idea being re-proposed next quarter.
- [ ] **Revived ideas went through `reopen_idea`,** so the original rejection is still visible in the history.
- [ ] **Stage breakdown inspected:** all five keys present, counts add up to `total_ideas`, no surprises.

## Ranking

- [ ] **Thresholds recorded with the report:** `min_priority_score`, `top_n`, `max_stage_age_days`. The ranking is threshold-dependent and not reproducible without them.
- [ ] **`ranked_ideas` reviewed, not just `top_priority_ideas`** — the shortlist is the first `top_n`, not the backlog.
- [ ] **Below-threshold ideas triaged, not ignored:** `below_threshold_count` ideas are flagged for a prune/keep decision. Nothing was filtered out of the report, so every one of them is a decision someone owes.
- [ ] **Score read as ordinal:** no memo says "2.4× better", no score is averaged, and no score is compared against a previous quarter's register.
- [ ] **Status understood:** `NO_ACTIVE_IDEAS` means every idea has been rejected — it is not the same as `PIPELINE_ACTIVE`, and it is not the same as `NO_IDEAS`.

## Stalled backlog

- [ ] **`stalled_ideas` worked through**, worst first. Each one gets a stage change, a rejection with a reason, or an explicit note that it is still under active research.
- [ ] **Staleness understood as time since the last *stage change*,** not since the last work done — an actively researched idea can appear here and that is the intended prompt.
- [ ] **Clock is timezone-aware UTC** and monotonic. Any "stall detection is unreliable" warning in the log has been investigated before trusting the report.

## Scope

- [ ] **Not used as an approval control.** Promotion sign-off, testing evidence and segregation of duties live in `strategy-research-to-production-pipeline-governance` and the jurisdiction-specific compliance skills — nothing here is presented to a regulator as satisfying an obligation.
- [ ] **Overlap between top-ranked ideas checked separately** (`cross-strategy-correlation-monitoring`) — the score cannot see that two ideas are the same trade.
- [ ] **History serialised if it must survive**, the engine holds it in memory only.

## Automated testing

- [ ] Run `python -m unittest discover -s . -p "test_*.py"` from the `scripts/` directory — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Engine parameters used: ___________________________
