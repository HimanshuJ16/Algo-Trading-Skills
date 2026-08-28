# Workflows for Strategy Lifecycle Retirement Criteria

The engine adjudicates; it does not measure. Every number in the payload is computed
upstream, and the quality of the decision is bounded by the quality of those inputs.

## 1. Performance Metrics Harvesting

- Collect, per strategy and per governance cycle: `backtest_sharpe`,
  `backtest_max_drawdown_pct`, `backtest_annual_return_pct` from the archived backtest of
  record, and `live_sharpe`, `live_max_drawdown_pct`, `live_information_ratio`,
  `live_ic_t_stat`, `live_realized_annual_return_pct` from live books and records.
- **Pin the backtest of record.** If the backtest figures are re-run each cycle against
  the current parameter set, the drawdown multiple and the drift ratio compare live
  performance against a moving target and the criteria mean nothing. Freeze the backtest
  that justified promotion to live, and version it.
- **Declare the drawdown convention at the boundary.** The engine requires positive
  magnitudes and raises on negatives. Convert once, at the warehouse query, not ad hoc at
  each call site.
- **Record how the IC t-statistic was computed** — one-sided or two-sided, sample size,
  overlapping windows or not, HAC-adjusted or not. The engine sees an opaque number, and
  1.96 means different things depending on the answers.
- Capture `live_observation_count` alongside the statistics. It is the only defence
  against retiring a strategy on a few weeks of noise.

## 2. Input Validation

- The engine rejects non-finite metrics, negative drawdowns, a zero backtested drawdown,
  an empty `strategy_id`, and a malformed `live_observation_count`.
- Treat a raise as a **data-pipeline incident**, not an inconvenience to be worked around
  with a `try/except` that defaults the metric to zero. Every one of these validations
  exists because the corresponding bad input previously produced a confident
  `ACTIVE_HEALTHY`.

## 3. Sample-Size Gate

- Configure `min_live_observations` to the shortest live window over which your IR and IC
  estimates carry signal — a function of holding period and trade frequency, not a
  universal constant.
- On `INSUFFICIENT_LIVE_HISTORY`: keep the strategy at incubation size, keep monitoring,
  and do **not** treat the absence of a retirement decision as a clean bill of health. The
  breach list in the report is informational and should still be read.

## 4. Quantitative Guardrail Audit

- Four criteria, strict comparisons, equal weight: IR $\ge 0.50$, live DD $\le 1.5\times$
  backtest DD, IC t-stat $\ge 1.96$, return drift $\ge -40\%$.
- Check `evaluated_criteria_count` on every report. If it is below 4, a guardrail did not
  run and `skipped_criteria` says which. `performance_drift_pct` of `None` means *not
  measurable* — render it that way; never coerce it to `0.0` for a dashboard.
- When drift is unevaluable, fall back to `return_gap_pct_points` and judge it manually
  against the strategy's mandate.

## 5. Decision Tree Classification

- 0 breaches with all four criteria evaluated → `ACTIVE_HEALTHY`.
- 0 breaches with any criterion unevaluated → `NEEDS_REVIEW`.
- 1 breach → `NEEDS_REVIEW`; 2 → `REDUCE_ALLOCATION`; $\ge 3$ → `MANDATORY_RETIREMENT`.
- Drawdown breach **and** live IR below `escalation_ir_floor` → `MANDATORY_RETIREMENT` at
  two breaches, with the reason recorded in `escalation_reason`.
- Read the decision together with `breached_criteria`, `skipped_criteria` and
  `escalation_reason`. The enum alone is a summary, not the finding.

## 6. Attribution Before Action

- Before acting on `REDUCE_ALLOCATION` or `MANDATORY_RETIREMENT`, run
  `strategy-performance-decay-detection-vs-market-wide-decay`. These four criteria cannot
  distinguish a decayed signal from a good strategy in a hostile regime, and retiring the
  latter at the bottom is a permanent loss.
- Check capacity, crowding and realised transaction costs too — all three produce live
  underperformance that looks identical to alpha decay here.

## 7. Governance Handoff

- Route `REDUCE_ALLOCATION` and `MANDATORY_RETIREMENT` to the capital-allocation
  committee, not to the strategy owner. Under RTS 6 Art. 9(2)–(5) the EU review obligation
  sits with the risk management function and requires senior-management approval; FINRA
  Regulatory Notice 15-09 recommends a cross-disciplinary committee including
  representation from outside trading.
- Archive the full `StrategyRetirementReport` — including `thresholds_applied` — with the
  cycle's records. It is the evidence that the rule pre-dated the drawdown.

## 8. Decommissioning Handoff

- `MANDATORY_RETIREMENT` is a decision, not an action. Hand off to
  `strategy-decommissioning-and-position-unwind-procedure` for the order-entry hard block
  and the orderly position unwind.

## 9. Threshold Change Control

- Any change to the engine's thresholds is a change to a risk control. ESMA's supervisory
  briefing (¶31) lists "Changing thresholds, kill switch logic, or alert triggers" as a
  material change to be timestamped, approved and recorded, and warns (¶30) against small
  recalibrations accumulating unchecked into a material change.
- Loosening a threshold because a specific strategy is failing it defeats the entire
  purpose of the skill. Change thresholds on a schedule, for all strategies, with a
  written rationale — never in response to a single pending decision.
