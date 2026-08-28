# Workflows for Strategy Underperformance Remediation Decision Tree

The engine resolves each evaluation in a fixed order and **every branch is terminal**.
The order is the policy: a dead hypothesis outranks an execution problem, an execution
problem outranks any Sharpe comparison, and joint impairment outranks an idiosyncratic
verdict. A strategy can never be routed to parameter recalibration in the same
evaluation in which its data feed was flagged broken.

## 0. Metrics harvesting

Collect, for the same trailing window and the same strategy:

| Field | Source | Convention |
|---|---|---|
| `live_sharpe` | Live P&L, net of fees and costs | Annualized, same frequency as the peer figure |
| `backtest_sharpe` | Research record | Context only; no node reads it |
| `peer_benchmark_sharpe` | Defensible peer index | Same annualization and frequency |
| `realized_slippage_bps` | TCA system | **Positive cost magnitude**, same horizon as alpha |
| `expected_alpha_bps` | Research record | **Strictly positive**, same horizon as slippage |
| `is_data_feed_healthy` | Data-quality monitoring | Genuine `bool` |
| `is_alpha_hypothesis_valid` | **Research review of the economic mechanism** | Genuine `bool` |
| `live_observation_count` | Live return series length | Live observations only, not backtest |

The two judgment fields are the ones that carry the most weight and the least
validation. `is_alpha_hypothesis_valid` in particular must answer "does the
inefficiency still exist" — has the trade been crowded out, has the market structure
or the fee schedule that created the edge changed, has the counterparty behaviour it
exploited stopped. It must **not** be a restatement of recent returns; deriving it from
P&L turns Node 1 into a Sharpe threshold whose consequence is irreversible, and it will
fire hardest during the drawdowns Node 3 exists to survive. Record the judgment, its
author, and its date alongside the report.

## 1. Input validation (before any node is evaluated)

Reject rather than compare. Every node is a threshold test, so an unusable input does
not produce a wrong answer loudly — it produces a *safe-looking* answer silently.

- **Non-finite values** (`NaN`, `±Inf`) on any of the five numeric fields. `float('nan')
  < 1.0` is `False`, so a `NaN` `live_sharpe` clears Nodes 3 and 4 and the engine
  returns `MAINTAIN_TRADING` — "no remediation required" — on a fully corrupt payload.
- **Negative `realized_slippage_bps`.** The convention is a positive cost magnitude. A
  caller reporting cost as $-15$ bps produces a ratio of $-0.75$, which never exceeds
  the 50% limit, so a strategy losing three quarters of its alpha to execution is
  routed to parameter recalibration instead. Pass `0.0` for net price improvement.
- **Non-positive `expected_alpha_bps`.** The ratio is undefined at zero and
  sign-inverted below it. Guarding the division and skipping the node — the obvious
  defensive reflex — means the strategies with no measurable edge skip the execution
  audit entirely.
- **Non-`bool` flags.** `is_alpha_hypothesis_valid="False"` is truthy and would clear
  Node 1. `0`, `1`, `None`, and `"true"` are rejected for the same reason.
- **Empty or non-string `strategy_id`.** An unattributable remediation record cannot be
  audited.
- **Non-positive or non-integer `live_observation_count`.**

Policy validation happens at construction: a non-positive `max_slippage_alpha_ratio` or
`periods_per_year`, a non-finite threshold, an invalid `min_live_observations`, or a
`min_peer_sharpe` above `min_healthy_sharpe` all raise.

## 2. Node 1 — Fundamental hypothesis audit

- `is_alpha_hypothesis_valid = False` $\implies$ `MANDATORY_STRATEGY_DECOMMISSION`,
  `is_decommissioned = True`, `is_capital_reduced = True`, logged at `ERROR`.
- Outranks a perfect Sharpe, healthy peers, and flawless execution. There is nothing to
  recalibrate and no execution fix that restores an edge that no longer exists.
- Two caveats surface in `warnings` rather than blocking the verdict:
  - **Feed unhealthy.** The metrics the judgment was formed against may be corrupt, and
    decommissioning is irreversible where a feed repair is not.
  - **Peer group also impaired.** The "decay" may be the regime shift Node 3 exists to
    survive. Verify the judgment is not a restatement of recent returns.
- Node 1 is deliberately **not** gated on sample size. A dead economic mechanism is a
  research finding, not a statistical one about the live window.

## 3. Node 2 — Execution and data quality audit

- Fires when the feed is unhealthy **or** $\text{slippage} / \text{alpha} >
  \text{max\_slippage\_alpha\_ratio}$. Comparison is **strict**: a ratio exactly at the
  limit clears; 50.05% fires.
- $\implies$ `OPTIMIZE_EXECUTION_AND_DATA`, `is_capital_reduced = True`, logged at
  `WARNING`. The remedy is a data-pipeline SLA fix, execution-algorithm order-slicing
  changes, or a venue/broker change — see `transaction-cost-analysis-tca-integration`
  and `execution-slippage-attribution-timing-vs-sizing`.
- **This node must precede Nodes 3 and 4.** A broken feed or an alpha-consuming cost
  invalidates the very Sharpe ratios those nodes compare, and recalibrating against
  fills destroyed by slippage fits the model to the execution defect and buries it.
- On an unhealthy feed, `warnings` records that every Sharpe figure in the report came
  from a source the caller flagged unreliable. Re-run the triage after the repair.
- The horizon-matching requirement is the quiet failure here: per-trade slippage against
  annualized alpha understates the ratio by roughly the number of trades per year.

## 4. Node 2a — Sample-size gate (opt-in)

- Active only when `min_live_observations` is configured. `None` (the default) disables
  it entirely and preserves an ungated tree.
- `live_observation_count < min_live_observations` $\implies$
  `EXTEND_OBSERVATION_INSUFFICIENT_HISTORY`: continue at reduced size and re-triage when
  the window is long enough. Neither capital reduction nor decommissioning.
- A short track record is not evidence of decay and not evidence of health. Returning a
  confident-looking verdict on 20 observations is the failure this gate prevents.
- It sits **below** Nodes 1 and 2 on purpose: a dead hypothesis and a broken feed are
  not sample-size questions, and a young strategy must not be shielded from either.
- Configuring the gate while omitting `live_observation_count` raises, rather than
  silently ungating the tree.

## 5. Node 3 — Market-wide regime shift audit

- Live Sharpe below the mandate **and** peer benchmark below the health floor $\implies$
  `TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL`: cut allocation 50%, retain signal
  execution until the regime turns. `is_capital_reduced = True`, logged at `WARNING`.
- **Resolves before Node 4** because joint impairment is the conservative reading of
  identical evidence. When peers are down too the data does not support an
  idiosyncratic verdict, and a capital cut is reversible where a recalibration that
  overwrites the parameter set is not.
- The 50% figure is house policy. The engine recommends it; something downstream must
  apply it — see `capital-reallocation-based-on-live-performance`.

## 6. Node 4 — Parameter drift audit

- Live Sharpe below the mandate while the peer benchmark is at or above its floor
  $\implies$ `RECALIBRATE_MODEL_PARAMETERS`. Capital is **not** reduced; the strategy
  keeps trading while the recalibration is researched, tested, and authorised.
- The remedy is walk-forward parameter re-optimization with out-of-sample validation —
  see `walk-forward-optimization-window-management` and
  `hyperparameter-tuning-without-target-leakage` — followed by retesting and
  re-authorisation before redeployment.
- **Read `sharpe_gap_vs_peer` before re-optimizing.** Because the mandate floor (1.0)
  and peer floor (0.50) are different numbers, a strategy at 0.95 against peers at 0.55
  lands here while leading its cohort. It is missing its own mandate without
  underperforming its peers — weak evidence of drift. The engine warns; the right
  response is usually to review the mandate or the benchmark, not the parameters.
- Every Node 4 outcome carries the material-change caveat. ESMA's February 2026
  supervisory briefing (¶30) warns that a series of small recalibrations can accumulate
  into an untested material change; each one must be timestamped, approved, recorded,
  and retested individually (¶31, ¶22).

## 7. Healthy branch

- Live Sharpe at or above the mandate (inclusive) $\implies$ `MAINTAIN_TRADING`, logged
  at `INFO`.
- Warns when the peer group is impaired while the strategy is not: confirm the
  benchmark is still a valid cohort before treating the outperformance as skill.

## 8. Evidence annotation (applies to every Sharpe-driven outcome)

- With `live_observation_count` supplied, the report carries
  `sharpe_standard_error` (Lo 2002) and `sharpe_evidence_conclusive`, true only when
  `live_sharpe` sits more than 1.96 standard errors from the mandate threshold.
- Without it, a caveat records that the sample size is unknown and the routing cannot be
  distinguished from estimation noise.
- At 60 daily observations the standard error near SR $= 1.0$ is $\approx 2.05$, larger
  than the threshold itself, so `sharpe_evidence_conclusive` is normally `False`. This
  is an honest report of what the data supports, not a defect.
- For a difference test with a defined null distribution, use
  `strategy-performance-decay-detection-vs-market-wide-decay`.

## 9. Governance handoff

- Route `StrategyRemediationReport` — action, `decisive_node`, the full `triage_path`
  including cleared nodes, the thresholds in force, and `warnings` — to the risk
  committee and strategy operations. The cleared nodes in the path are the evidence
  that the alternatives were tested, which is what makes the record auditable.
- **The report is a recommendation, not an action.** Nothing in this module cancels an
  order, liquidates a position, halts a strategy, or changes an allocation. Wire
  `MANDATORY_STRATEGY_DECOMMISSION` to
  `strategy-decommissioning-and-position-unwind-procedure`, and keep the independent
  halt path in `kill-switch-and-drawdown-circuit-breakers`.
- In an EU/UK regulated firm, the resulting change must be authorised by a person
  designated by senior management (RTS 6 Art. 5) and recorded with when, who, who
  approved, and the nature of the change (RTS 6 Art. 5(7)).
- Track the sequence of diagnoses per strategy, not just the latest one. Repeated Node 4
  verdicts across quarters are the accumulation pattern ESMA ¶30 describes, and are
  usually better evidence of alpha decay than any single evaluation.
