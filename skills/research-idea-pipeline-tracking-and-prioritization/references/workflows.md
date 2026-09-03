# Deep Workflow Reference — research-idea-pipeline-tracking-and-prioritization

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Prepare the register

The engine is instantiated with the three thresholds and a clock:

```python
engine = ResearchIdeaPipelineTrackingAndPrioritizationEngine(
    min_priority_score=1.0,     # flag, never drop, below this
    top_n=5,                    # shortlist length
    max_stage_age_days=30.0,    # stall threshold, non-terminal stages only
    clock=lambda: datetime.now(timezone.utc),
)
```

All four are validated at construction. A non-finite or negative `min_priority_score`, a
`top_n` below 1 (or a `bool`), a non-positive or non-finite `max_stage_age_days`, and a
non-callable `clock` all raise `ResearchPipelineError`.

The clock must return a **timezone-aware** datetime. A naive datetime raises: comparing it
against UTC mis-measures every time-in-stage by the local UTC offset, silently, and the
error only shows up as ideas that never stall.

## 1. Idea registration

`ResearchIdea` validates in `__post_init__`, so an invalid idea cannot exist — not even
outside the engine. Every one of these raises `ResearchPipelineError`:

| Input problem | Why it is fatal |
|---|---|
| Non-finite `expected_sharpe` or `estimated_capacity_usd` | `NaN` propagates into the score, and `NaN` compares `False` against everything, so the idea sorts to an arbitrary position — plausibly rank 1 — while looking like a computed result. |
| `expected_sharpe < 0` | The score divides by $k \times d$, so a negative Sharpe makes a *harder* idea rank *better*: $-2.0 \times 7/1 = -14.0$ versus $-2.0 \times 7/5 = -2.8$. A losing idea belongs in `REJECTED`. |
| `estimated_capacity_usd < 1` | $\log_{10}$ is negative below $\$1$, flipping the sign of the whole expression. |
| `implementation_complexity` or `data_cost_tier` outside $[1,5]$, non-`int`, or `bool` | Clamping an out-of-range tier to 1 would award the maximum possible score to the worst-specified idea. |
| Blank `idea_id`, `title`, or `author` | An unattributable idea cannot be reviewed or chased. |
| Unknown `stage` string | See §3 — a misspelt stage keeps a rejected idea in the ranking. |

`ResearchIdea` is a **frozen** dataclass and `add_idea` stores its own copy, so a caller
holding a reference cannot mutate `expected_sharpe` or `stage` after registration and
bypass validation. `engine.ideas` likewise returns a fresh dict.

**A duplicate `idea_id` raises.** Silently overwriting — the previous behaviour — discards
the existing idea's stage and its entire transition history. To revise an idea, either move
it with `update_stage` or register the revision under its own id and reject the original
with a reason that points at the replacement.

## 2. Scoring and ranking

$$\text{priority} = \frac{S \times \log_{10}(C_{\text{USD}})}{k \times d}$$

`calculate_priority_score` returns the **exact** float. Nothing rounds before the sort:
rounding to 4 dp manufactures ties between ideas that differ at the 5th decimal, and those
ties then resolve by registration order, so the same backlog entered in a different order
produces a different "top idea".

`generate_pipeline_report` then:

1. Counts every idea into `stage_breakdown`, which always carries **all five stage keys**,
   zero-filled. A fixed key set means a consumer never has to guess whether a missing key
   means zero or means a typo created a phantom bucket.
2. Scores every idea whose stage is not in `INACTIVE_STAGES` (i.e. everything but
   `REJECTED`).
3. Sorts on `(-score, idea_id)`. The `idea_id` tie-break makes the ranking a function of
   the register's contents alone, independent of insertion order.
4. Assigns `rank` from 1, flags `below_priority_threshold` where `score < min_priority_score`,
   and counts those into `below_threshold_count`. **Nothing is filtered out.** An idea that
   disappears from the report is an idea a reviewer cannot decide about; the threshold's job
   is to nominate candidates for pruning, not to hide them.
5. Returns `ranked_ideas` (the whole active backlog, in order) and `top_priority_ideas`
   (`ranked_ideas[:top_n]`, the shortlist).

`status` has three values, and the distinction matters:

| Status | Meaning |
|---|---|
| `NO_IDEAS` | The register is empty. |
| `NO_ACTIVE_IDEAS` | Ideas exist but every one is `REJECTED`. Reporting this as `PIPELINE_ACTIVE` would hide a stalled pipeline. |
| `PIPELINE_ACTIVE` | At least one idea is rankable. |

### What the score is not

The capacity term is $\log_{10}$ of a dimensional quantity — implicitly $\log_{10}(C/\$1)$.
Re-expressing capacity in thousands subtracts 3 from every $\log_{10}$, which changes each
score by $3S/(kd)$ — a different amount per idea — and **reorders the ranking**:

| Idea | $S$ | $k \times d$ | $C$ (USD) | score | $C$ (thousands) | score |
|---|---|---|---|---|---|---|
| A | 1.0 | 1 | $10^9$ | 9.0 | $10^6$ | 6.0 |
| B | 6.0 | 2 | $10^4$ | **12.0** | $10^1$ | 3.0 |

B ranks first in dollars, A ranks first in thousands. Neither is more correct; the unit is
part of the formula. Pass whole US dollars, always.

## 3. Lifecycle state machine

```
PROPOSED  ──▶ BACKTESTING ──▶ PAPER_TRADING ──▶ PRODUCTION_READY
    ▲             │  ▲              │                    │
    └─────────────┘  └──────────────┘                    │
                                                          ▼
  any stage ──────────────────────────────────────▶  REJECTED
                                                          │
                              reopen_idea(reason) ────────┘
                                     ▼
                                 PROPOSED
```

`ALLOWED_TRANSITIONS` is a public module constant, so a caller can render the legal moves
rather than guess them. One-step demotions are legal because research iterates: a
paper-trading result can send an idea back to backtesting.

`update_stage(idea_id, new_stage, reason="")` — every failure mode is loud:

| Situation | Behaviour |
|---|---|
| Unknown `idea_id` | Raises. Returning `False` instead lets a caller who ignores the return believe an idea has been rejected while it is still being ranked. |
| Unknown stage string (`"rejcted"`, `"REJECT"`, `"done"`) | Raises, listing the legal stages. Upper-casing any string instead produces a phantom `REJCTED` bucket in the breakdown while the idea stays active. |
| Legal stage, illegal transition | Raises, naming the legal targets from the current stage. |
| Target is the current stage | No-op, returns `False`, writes no history entry. |
| Target is `REJECTED` with a blank `reason` | Raises. An unexplained rejection cannot stop the same idea being re-proposed next quarter. |
| Target is `REJECTED` from any stage | Allowed, always. |

Case-insensitive stage names and `PipelineStage` members are both accepted, so
`"backtesting"`, `"BACKTESTING"` and `PipelineStage.BACKTESTING` are equivalent.

`reopen_idea(idea_id, reason)` is the only route out of `REJECTED`. It moves the idea to
`PROPOSED`, requires a reason, and leaves the original rejection in the history. Reviving a
dead idea is therefore a deliberate, attributable act rather than a stage string.

Each transition appends an immutable `StageTransition(idea_id, from_stage, to_stage, at,
reason)` retrievable with `get_history(idea_id)`, oldest first. The log is append-only in
the sense that the engine offers no way to edit or delete an entry — it is **not** durable
storage; serialise it if you need it to survive the process.

## 4. Stalled-idea review

For every idea in a non-terminal stage:

$$\text{days\_in\_stage} = \frac{(\text{now} - \text{stage\_entered\_at})}{86400\text{ s}}$$

Strictly greater than `max_stage_age_days` is stalled; exactly at the threshold is not.
`stage_entered_at` is set at registration and reset on every applied transition, so a
demotion back to `BACKTESTING` restarts the clock.

`stalled_ideas` is sorted worst-first by `(-days_in_stage, idea_id)` and a `WARNING` is
logged naming each stalled idea and its stage. `PRODUCTION_READY` and `REJECTED` never
appear: the pipeline's work on them is finished, and what happens to a promoted strategy
belongs to `strategy-lifecycle-retirement-criteria`.

If the injected clock ever moves backwards, a `WARNING` names the affected idea and states
that stall detection is unreliable for that report — a backwards clock would otherwise make
every idea look freshly touched and disable the check silently.

## 5. Feeding the register honestly

The arithmetic here is trivial and the ranking is only as good as `expected_sharpe`:

- Use **one** Sharpe convention across the whole register — same horizon, same
  annualisation, same treatment of the risk-free rate.
- Deflate a best-of-N backtest Sharpe before entering it (Bailey & López de Prado, 2014);
  see `factor-research-multiple-testing-correction`. Harvey, Liu & Zhu (2016) put the
  multiple-testing hurdle for a new factor near a t-ratio of 3.0.
- Produce `estimated_capacity_usd` with an actual capacity analysis
  (`strategy-capacity-estimation-before-scaling-capital`), not a guess anchored on the
  strategy's ambition.
- Record the thresholds used alongside any report you circulate. The ranking is
  threshold-dependent and not reproducible without them.
