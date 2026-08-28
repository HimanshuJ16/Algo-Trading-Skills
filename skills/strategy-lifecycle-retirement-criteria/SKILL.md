---
name: strategy-lifecycle-retirement-criteria
description: >-
  Deterministic strategy retirement adjudicator that applies four pre-declared guardrails — live Information Ratio, live-vs-backtest drawdown multiple, IC t-statistic, and return drift — to a performance payload and returns an auditable lifecycle decision, refusing to certify a strategy on corrupt, sign-inverted, or unevaluable metrics.
domain: Investment Governance & Capital Allocation
subdomain: Strategy Lifecycle Governance
tags: ["strategy-lifecycle", "strategy-retirement", "alpha-decay", "information-ratio", "ic-t-stat", "performance-drift", "governance-audit-trail"]
brokers_frameworks: ["Quantitative Strategy Governance", "Alpha Decay Monitoring", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a multi-strategy book needs the retirement rule written down *before* the drawdown, and applied identically to every strategy. The failure mode it exists to prevent is not analytical — it is the researcher who built the strategy arguing that this quarter is unrepresentative, every quarter.

The engine measures nothing. It **adjudicates**: you supply already-computed live and backtest statistics, and it applies four pre-declared guardrails, a fixed escalation ladder, and produces a record of which criteria fired, which could not be evaluated, and which thresholds were in force. Feed it at your governance cadence (monthly or quarterly is typical), and route its output to the capital-allocation committee.

Its second job is to refuse. A payload with a `NaN` metric, a sign-inverted drawdown, or an unevaluable criterion does not come back `ACTIVE_HEALTHY` — it raises or downgrades. An engine that certifies a strategy on the *absence* of data is worse than no engine.

## When NOT to Use

- **On a short live track record.** The engine cannot tell 10 live days from 10 years. An IR or an IC t-stat over a few weeks is noise, and retiring a good strategy on noise destroys capital as surely as never retiring a bad one. Supply `live_observation_count` and set `min_live_observations`; without both, **no sample-size gate exists** and the ladder will happily retire a three-week-old strategy.
- **To diagnose *why* performance decayed.** A market-wide regime shift and a strategy-specific alpha decay produce identical output here. Attribution belongs to `strategy-performance-decay-detection-vs-market-wide-decay`; without it, you will retire a perfectly good strategy in the middle of an unfavourable regime.
- **As a live risk control.** This runs on a governance cadence against aggregated statistics; it will not stop an intraday blow-up. The out-of-band control is `kill-switch-and-drawdown-circuit-breakers` and `portfolio-level-stop-loss-independent-of-strategy-stops`.
- **As the unwind mechanism.** `MANDATORY_RETIREMENT` is a decision, not an action. Blocking order entry and liquidating the book is `strategy-decommissioning-and-position-unwind-procedure`.
- **With the default thresholds unexamined.** All four are house heuristics. `min_live_information_ratio=0.50` sits at the 75th percentile of Grinold & Kahn's active-manager table — the default retires anything below top-quartile. That may be exactly right for your book, or absurd; it is not a standard. See `references/standards.md`.

## Prerequisites

- A `StrategyPerformanceMetrics` payload: `strategy_id`, `backtest_sharpe`, `backtest_max_drawdown_pct`, `live_sharpe`, `live_max_drawdown_pct`, `live_information_ratio`, `live_ic_t_stat`, `live_realized_annual_return_pct`, `backtest_annual_return_pct`, and optionally `live_observation_count`.
- **Drawdowns as positive magnitudes.** A 20% peak-to-trough decline is `20.0`, never `-20.0`. Enforced, not inferred — see the pitfall below for why guessing is unsafe. `backtest_max_drawdown_pct` must be strictly positive; `0.0` is rejected as an unpopulated field.
- **All metrics finite.** Non-finite values raise; they do not degrade gracefully.
- **An IR and an IC t-stat you can explain.** The engine consumes both as opaque numbers. Whether the t-stat came from a one-sided or two-sided test, whether it was computed on overlapping windows without an autocorrelation adjustment, and how many observations sit behind it are all invisible here and all change what the 1.96 threshold means.
- `backtest_sharpe` and `live_sharpe` are carried as **context only** — no criterion reads them.
- Thresholds you are prepared to defend to a committee, and a change-control process around them: changing a retirement threshold is itself a material change under ESMA's supervisory expectations (`references/standards.md`).

## Workflow

1. **Validate the payload before adjudicating anything.**
   - Reject non-finite metrics. **Decision point — `NaN` is not a neutral value.** `nan < 0.50` is `False`, as is every other comparison, so a fully corrupt payload scores zero breaches and reads `ACTIVE_HEALTHY`. The engine raises instead.
   - Reject negative drawdowns and a zero backtested drawdown. **Decision point — do not "helpfully" take the absolute value.** Both sign conventions are common; guessing wrong is silent. Fail loudly and make the caller declare the convention.

2. **Gate on sample size before running the ladder.**
   - If `min_live_observations` is configured and the payload's `live_observation_count` falls short, return `INSUFFICIENT_LIVE_HISTORY`. Breaches are still computed and reported, for information only.
   - **Decision point — a short track record is not evidence of decay, and it is not evidence of health either.** The correct action is `EXTEND_OBSERVATION` at reduced size, not retirement and not full allocation.

3. **Evaluate the four criteria.** Comparisons are strict — a value sitting exactly on a threshold does not breach.
   - $\text{IR}_{\text{live}} < 0.50$ → `ALPHA_DECAY_IR`.
   - $\text{DD}_{\text{live}} > 1.5 \times \text{DD}_{\text{backtest}}$ → `DRAWDOWN_BREACH`.
   - IC t-stat $< 1.96$ → `IC_STATISTICAL_DECAY`.
   - $\text{Drift} = (R_{\text{live}} - R_{\text{backtest}}) / R_{\text{backtest}} \times 100 < -40\%$ → `PERFORMANCE_DRIFT`.
   - **Decision point — the drift ratio is not always computable.** It is undefined when the backtested return is non-positive and numerically meaningless when it is near zero (0.1% backtest vs 0.05% live is a 5 bp miss that reads as $-50\%$ drift). In both cases `performance_drift_pct` is `None`, the criterion is named in `skipped_criteria`, and `return_gap_pct_points` — the plain percentage-point difference, always defined — is reported instead. **It is never substituted with `0.0`.**

4. **Apply the escalation ladder.**
   - 0 breaches, all four criteria evaluated → `ACTIVE_HEALTHY`.
   - 0 breaches, **any criterion not evaluated** → `NEEDS_REVIEW`. **Decision point — `ACTIVE_HEALTHY` asserts that all four guardrails passed.** A consumer reading only `decision` must never be told that on the strength of three.
   - 1 breach → `NEEDS_REVIEW` (watchlist).
   - 2 breaches → `REDUCE_ALLOCATION` (cut capital 50%, pending committee review).
   - $\ge 3$ breaches → `MANDATORY_RETIREMENT`.
   - **Override — a drawdown breach with a live IR below zero retires at *two* breaches.** A strategy simultaneously losing against its benchmark and exceeding its backtested worst case has failed on both axes that matter, and waiting for a third confirmation costs capital. The override is reported in `escalation_reason`, so a two-breach retirement is never unexplained. Disable it with `escalate_on_negative_ir_with_drawdown_breach=False`.

5. **Archive the report, then hand off.** `thresholds_applied` echoes every parameter in force — a threshold-dependent decision is not reproducible without it. `MANDATORY_RETIREMENT` is a decision; the actual order-entry block and position unwind are `strategy-decommissioning-and-position-unwind-procedure`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Passing drawdowns as negative numbers.** Under the negative convention, criterion 2 inverts: `-30.0 > -15.0` is `False`, so a live drawdown *three times* the backtested worst case compares as within limits and the strategy is certified healthy. This is the highest-severity failure the skill has, and it is invisible — the report looks perfect. The engine now raises; do not "fix" it by taking `abs()` at the call site without first confirming which convention your data warehouse actually uses.
- **Treating a missing measurement as a passing one.** A drift of `0.0` returned because the backtested return was negative reads exactly like a strategy tracking its backtest perfectly. Any report where `evaluated_criteria_count < 4` has an un-run guardrail, whatever `breached_criteria` says.
- **Reading `1.96` as "95% confidence" in the decay question you are actually asking.** 1.96 is the *two-tailed* 5% critical value of the standard normal; the one-sided 5% value is 1.645. "Has the IC decayed to non-positive?" is a one-sided question. Separately, an IC t-stat computed on overlapping forecast windows without a Newey-West correction is inflated, so the same 1.96 is a materially weaker test than it appears.
- **Counting four breaches as four independent findings.** IR, IC t-stat and return drift all degrade together when a signal stops working. "3 of 4 breached" is closer to one finding confirmed three ways. The ladder is a severity heuristic, not a statistical test, and the criteria are equally weighted by fiat.
- **Retiring on a track record too short to mean anything.** The most expensive misuse of this engine: a strategy three weeks into live trading breaches all four criteria on noise and gets decommissioned. If `live_observation_count` is `None` the gate silently does not run.
- **Re-tuning the thresholds until the strategy passes.** The engine's only real value is that the rule pre-dates the drawdown. Changing `min_live_information_ratio` from 0.50 to 0.30 because a favourite strategy is at 0.35 is the exact behaviour the skill exists to prevent — and under ESMA's supervisory briefing, changing risk-control thresholds is a material change requiring approval and a record.
- **Emotional parameter tweaking on the strategy instead of the process.** Repeatedly recalibrating a decaying strategy resets its live track record to zero each time, which conveniently makes every criterion unevaluable.
- **Citing a regulator in support of these numbers.** No regulator prescribes an IR floor, a drawdown multiple, or a t-stat cut-off for withdrawing a strategy. Retirement for economic underperformance is a business decision. What *is* regulated is different — see `references/standards.md`.

## Verification

- **Healthy baseline.** `StrategyLifecycleRetirementEngine()` with IR $1.2$, IC t-stat $2.5$, live DD $11.0$ against backtest DD $10.0$, live return $18.0$ vs backtest $20.0$ → `ACTIVE_HEALTHY`, `is_retired False`, `breached_criteria == []`, `evaluated_criteria_count == 4`, `performance_drift_pct == -10.0` (from $(18-20)/20 \times 100$), `return_gap_pct_points == -2.0`.
- **Full decay.** Backtest DD $8.0$ (allowed $12.0$), live DD $20.0$, IR $-0.2$, t-stat $0.4$, live return $-5.0$ vs backtest $25.0$ → `MANDATORY_RETIREMENT`, 4 breaches, drift $-120.0$.
- **Boundaries — exactly on a threshold must pass.** IR exactly $0.50$, t-stat exactly $1.96$, live DD exactly $15.0$ (= $1.5 \times 10.0$), and drift exactly $-40.0$ (live $12.0$ vs backtest $20.0$) all produce `ACTIVE_HEALTHY`. One tick past each must breach.
- **Sign-convention regression.** A live DD of $-30.0$ against a backtest DD of $-10.0$ must raise `ValueError`, not report `ACTIVE_HEALTHY`. A `backtest_max_drawdown_pct` of $0.0$ must raise.
- **Corrupt-data regression.** A `NaN` or `Inf` in any of the eight numeric fields must raise `ValueError`; a non-numeric value must raise `TypeError`.
- **Unevaluable drift regression.** Backtest return $-3.0$ with live return $-40.0$ → `performance_drift_pct is None`, `evaluated_criteria_count == 3`, `NEEDS_REVIEW` (not `ACTIVE_HEALTHY`), `return_gap_pct_points == -37.0`. Backtest $0.1$ vs live $0.05$ → drift `None` and no manufactured breach. Backtest exactly $1.0$ vs live $0.5$ → drift $-50.0$, evaluated normally.
- **Escalation override.** Live DD $20.0$ with IR $-0.2$ → `MANDATORY_RETIREMENT` at 2 breaches, `escalation_reason` containing `OVERRIDE_DD_AND_NEGATIVE_IR`. With `escalate_on_negative_ir_with_drawdown_breach=False` the same payload → `REDUCE_ALLOCATION`.
- **Sample-size gate.** With `min_live_observations=126`, a payload breaching all four criteria at `live_observation_count=15` → `INSUFFICIENT_LIVE_HISTORY`, `is_retired False`, four breaches still listed. At $126$ observations the same payload → `MANDATORY_RETIREMENT`.
- **Constructor validation.** A non-positive `max_drawdown_multiplier`, a non-finite threshold, a `mandatory_retirement_breach_count` outside $1..4$, and a non-positive `min_live_observations` must each raise `ValueError`.
- Run `python -m unittest discover -s . -p "test_*.py"` from `scripts/` — 45 tests, 100% pass rate.

## Related Skills

- `strategy-performance-decay-detection-vs-market-wide-decay`
- `strategy-decommissioning-and-position-unwind-procedure`
- `strategy-underperformance-remediation-decision-tree`
- `strategy-committee-governance-for-capital-allocation-decisions`
- `backtest-vs-live-performance-divergence-tracking`
- `capital-reallocation-based-on-live-performance`
