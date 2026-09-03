# Pre-Flight / Sign-off Checklist — strategy-lifecycle-retirement-criteria

Use this before signing off a lifecycle decision for a live strategy.

## Inputs

- [ ] **Backtest of record is pinned and versioned** — not re-run against the current
      parameter set. A moving backtest makes the drawdown multiple and the drift ratio
      meaningless.
- [ ] **Drawdowns supplied as positive magnitudes** (a 20% decline is `20.0`). The
      conversion happens once at the warehouse boundary, not ad hoc per call site.
- [ ] **`backtest_max_drawdown_pct` is a real backtested figure**, not an unpopulated
      `0.0`. (The engine raises on `0.0` — a clean run is the evidence.)
- [ ] **All eight numeric fields finite.** No `NaN`, no `Inf`. (The engine raises; a
      raise is a data-pipeline incident, not something to catch and default to zero.)
- [ ] **IR provenance recorded:** benchmark-relative, or an absolute-return Sharpe at a
      zero risk-free rate? The $0.50$ default was calibrated against the former.
- [ ] **IC t-stat provenance recorded:** one-sided or two-sided, sample size, overlapping
      windows, HAC-adjusted or not. Without this, $1.96$ is an unexamined number.
- [ ] **`live_observation_count` supplied.**

## Sample size

- [ ] **`min_live_observations` is configured.** It defaults to `None`, which is *no gate*
      — the ladder will retire a three-week-old strategy on noise.
- [ ] **`INSUFFICIENT_LIVE_HISTORY` was not read as a pass.** It means the evidence does
      not exist yet: hold at incubation size, keep monitoring, review the informational
      breach list.

## Criteria audit

- [ ] **`evaluated_criteria_count` checked on every report.** Below 4 means a guardrail did
      not run; `skipped_criteria` names it.
- [ ] **`performance_drift_pct is None` rendered as "not measurable"** everywhere it
      appears — never coerced to `0.0`, never dropped from the report.
- [ ] **Where drift was unevaluable, `return_gap_pct_points` was judged manually** against
      the strategy's mandate.
- [ ] **Boundary understood:** comparisons are strict. IR exactly $0.50$, t-stat exactly
      $1.96$, live DD exactly $1.5\times$ backtest, and drift exactly $-40\%$ all pass.

## Decision

- [ ] **`decision` read together with `breached_criteria`, `skipped_criteria` and
      `escalation_reason`.** The enum alone is a summary, not the finding.
- [ ] **`ACTIVE_HEALTHY` treated as a positive assertion** that all four guardrails passed
      — and confirmed it is not a `NEEDS_REVIEW` downgrade from an unevaluated criterion.
- [ ] **A two-breach `MANDATORY_RETIREMENT` was explained** by `escalation_reason`
      (`OVERRIDE_DD_AND_NEGATIVE_IR`), not accepted as an inconsistency.
- [ ] **Breach count not read as independent evidence.** IR, IC t-stat and drift degrade
      together; "3 of 4" is closer to one finding confirmed three ways.

## Attribution before action

- [ ] **Decay attribution run** (`strategy-performance-decay-detection-vs-market-wide-decay`)
      before acting on `REDUCE_ALLOCATION` or `MANDATORY_RETIREMENT`. These criteria cannot
      tell a dead signal from a good strategy in a hostile regime.
- [ ] **Capacity, crowding and realised transaction costs checked** — each produces
      underperformance that looks identical to alpha decay here.

## Governance

- [ ] **Decision routed to the capital-allocation committee, not the strategy owner.**
      (RTS 6 Art. 9(2)–(5): risk management owns the review, senior management approves.
      FINRA RN 15-09: cross-disciplinary committee with representation outside trading.)
- [ ] **Full report archived, including `thresholds_applied`.** A threshold-dependent
      decision is not reproducible without the thresholds in force.
- [ ] **No threshold was changed in response to this decision.** Changing a retirement
      threshold is a material change to a risk control (ESMA supervisory briefing ¶31):
      scheduled, applied to all strategies, approved, timestamped, and recorded.
- [ ] **No regulator cited in support of the numeric thresholds.** They are house
      parameters; retirement for underperformance is a business decision.

## Handoff

- [ ] **`MANDATORY_RETIREMENT` handed to
      `strategy-decommissioning-and-position-unwind-procedure`** for the order-entry hard
      block and orderly unwind. The decision is not the action.

## Automated Testing

- [ ] Run `python -m unittest discover -s skills/strategy-lifecycle-retirement-criteria/scripts` — 45 tests,
      100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Engine parameters used: ___________________________
- Backtest of record (version/hash): ___________________________
