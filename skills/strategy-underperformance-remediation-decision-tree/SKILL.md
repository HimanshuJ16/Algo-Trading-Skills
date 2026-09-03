---
name: strategy-underperformance-remediation-decision-tree
description: >-
  Use when a strategy has underperformed and someone is about to change something,
  routing the symptom through a fixed decision tree across dead alpha, execution or data
  dysfunction, regime shift and idiosyncratic loss.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: portfolio-multi-strategy
  tags: underperformance-remediation, triage-decision-tree, alpha-decay, parameter-recalibration, execution-optimization, strategy-governance, sharpe-standard-error
  brokers_frameworks: "Quantitative Triage Decision Tree; Remediation Governance Matrix; MiFID II RTS 6; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a live strategy has underperformed and someone is about to *do something about it*. The failure mode it exists to prevent is not analytical — it is the researcher who reaches for the parameter file first, every time, whatever the cause.

One symptom, a fallen Sharpe ratio, has four incompatible causes, and each remedy is wrong for the other three. Recalibrating parameters when the real problem is a stale data feed fits the model to the defect. Decommissioning during a market-wide regime shift permanently forfeits the recovery. Tuning the signal when 75% of the alpha is being eaten by slippage optimizes the wrong system entirely. The engine applies a **pre-declared triage order** to a metrics payload and returns an auditable routing decision plus the record of which nodes were tested on the way there:

| Node | Condition | Action |
|---|---|---|
| 1 | Alpha hypothesis declared invalid | `MANDATORY_STRATEGY_DECOMMISSION` |
| 2 | Data feed unhealthy, or slippage $>50\%$ of expected alpha | `OPTIMIZE_EXECUTION_AND_DATA` |
| 2a | Live window shorter than the configured minimum | `EXTEND_OBSERVATION_INSUFFICIENT_HISTORY` |
| 3 | Strategy **and** peer group both impaired | `TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL` |
| 4 | Strategy impaired, peer group healthy | `RECALIBRATE_MODEL_PARAMETERS` |
| — | Strategy at or above mandate | `MAINTAIN_TRADING` |

Its second job is to refuse. A payload with a `NaN` Sharpe, a sign-inverted slippage figure, a stringified boolean, or a zero expected alpha does not come back `MAINTAIN_TRADING` — it raises. An engine that certifies a strategy healthy on the *absence* of usable data is worse than no engine.

## When NOT to Use

- **As a significance test of strategy versus peers.** Nodes 3 and 4 are two independent threshold comparisons, not a hypothesis test, and the split between them has no null distribution. For a statistic that does — the Memmel-corrected Jobson-Korkie Sharpe-difference test — use `strategy-performance-decay-detection-vs-market-wide-decay`. This engine routes; that one tests.
- **On a short live track record, ungated.** Set `min_live_observations` or the tree will happily route a three-week-old strategy to decommissioning. At 60 daily observations the standard error of an annualized Sharpe near 1.0 is $\approx 2.05$ (Lo 2002) — larger than the threshold being tested. Check `sharpe_evidence_conclusive` before writing "underperforming" in a committee memo; it will usually be `False`.
- **As the executor of its own recommendation.** Every outcome is a *field on a dataclass*. Nothing here cancels an order, liquidates a position, halts a strategy, changes an allocation, or re-optimizes anything. Unwinding is `strategy-decommissioning-and-position-unwind-procedure`; halting is `kill-switch-and-drawdown-circuit-breakers`, which must remain structurally independent of strategy logic; the capital change is `capital-reallocation-based-on-live-performance`.
- **As a live risk control.** This runs at a governance cadence against aggregated statistics. It will not stop an intraday blow-up, and Node 3's "cut 50%" is a committee recommendation, not a circuit breaker.
- **Without a defensible peer benchmark.** The Node 3 / Node 4 split is only as good as the peer index. Benchmarking a market-neutral book against a long-only index manufactures a "parameter drift" verdict every time the index rallies. Choosing the benchmark is `benchmark-selection-for-strategy-evaluation`; this engine only compares against the number you supply.
- **To decide the retirement rule itself.** The four criteria that constitute "this strategy has failed" belong in `strategy-lifecycle-retirement-criteria`, declared before the drawdown. This engine assumes the underperformance is already established and asks only *why*.

## Prerequisites

- An `UnderperformanceTriageMetrics` payload: `strategy_id`, `live_sharpe`, `backtest_sharpe`, `peer_benchmark_sharpe`, `realized_slippage_bps`, `expected_alpha_bps`, `is_data_feed_healthy`, `is_alpha_hypothesis_valid`, and optionally `live_observation_count`.
- **Five caller conventions the engine cannot verify.** Getting any of them wrong silently changes the routing:
  - `is_alpha_hypothesis_valid` is a **human research judgment about the economic mechanism** — has the inefficiency been arbitraged away, has the market microstructure that created it changed — and **not** a re-reading of live P&L. Derive it from recent returns and Node 1 becomes a Sharpe threshold with an irreversible consequence, firing during exactly the regime shifts Node 3 exists to survive.
  - `realized_slippage_bps` is a **positive cost magnitude in basis points**. A signed convention (cost as a negative number) is rejected, not reinterpreted — see the pitfalls for why guessing is unsafe. Pass `0.0` for net price improvement.
  - `realized_slippage_bps` and `expected_alpha_bps` must share a **horizon and a unit of activity** — both per round trip, or both per unit time. Per-trade slippage against annualized alpha understates the ratio by roughly the number of trades per year and defeats Node 2 entirely.
  - All three Sharpe ratios share an **annualization convention and return frequency**, or the comparisons are meaningless.
  - `live_observation_count` counts the **live** return observations behind `live_sharpe`, not backtest observations.
- `backtest_sharpe` is carried as **context only** — no node reads it. The report returns `sharpe_gap_vs_backtest` for the committee's benefit.
- Thresholds you are prepared to defend. `min_healthy_sharpe = 1.0` and `min_peer_sharpe = 0.50` are **house defaults, not external standards** — see `references/standards.md`.
- A change-control process downstream. Acting on `RECALIBRATE_MODEL_PARAMETERS` is a material change requiring authorisation, recording, and retesting (`references/standards.md`).

## Workflow

1. **Validate the payload before routing anything.**
   - **Decision point — `NaN` is not a neutral value, it is the most dangerous one.** Every node is a threshold comparison, and every comparison against `NaN` is `False`. A `NaN` `live_sharpe` therefore fails `live_sharpe < 1.0` at Nodes 3 and 4 and falls straight through to the healthy branch, so a fully corrupt payload returns `MAINTAIN_TRADING` — "no remediation required". The engine raises `ValueError` instead.
   - **Decision point — reject a non-positive `expected_alpha_bps`, do not divide by it.** The slippage ratio is undefined at zero and sign-inverted below it, so Node 2 would clear however large the slippage was. A strategy with no expected alpha has no edge to protect: that is a Node 1 hypothesis question, not a Node 2 execution question.
   - **Decision point — reject a stringified boolean.** `is_alpha_hypothesis_valid="False"` is truthy. A JSON payload or an agent that passes the string clears Node 1 and keeps a dead strategy trading.

2. **Node 1 — Fundamental hypothesis audit** (resolves first, outranks everything):
   - Hypothesis invalid $\implies$ `MANDATORY_STRATEGY_DECOMMISSION`. It outranks a perfect Sharpe and flawless execution, because there is nothing to recalibrate and no execution fix that restores an edge that no longer exists.
   - **Decision point — two situations make this judgment suspect, and the report says so rather than blocking it.** If the data feed is flagged unhealthy, the metrics the judgment was formed against may be corrupt, and decommissioning is irreversible where a feed repair is not. If the peer group is *also* impaired, the "decay" may be the regime shift Node 3 exists to survive. Both surface in `warnings`.

3. **Node 2 — Execution and data quality audit** (must precede the Sharpe nodes):
   - Feed unhealthy, or $\frac{\text{slippage}}{\text{alpha}} > 50\%$ (strict: exactly $50\%$ clears) $\implies$ `OPTIMIZE_EXECUTION_AND_DATA`.
   - **Decision point — this node comes before Nodes 3 and 4 for a reason.** A broken feed or a cost that swallows the alpha invalidates the very Sharpe ratios Nodes 3 and 4 compare. Tuning signal parameters against fills destroyed by slippage fits the model to the execution defect and buries it.
   - When the feed is unhealthy, `warnings` records that every Sharpe figure in the report came from a feed the caller flagged as unreliable. Re-run the triage after the repair before acting on any comparison.

4. **Node 2a — Sample-size gate** (only when `min_live_observations` is configured):
   - Live window shorter than the minimum $\implies$ `EXTEND_OBSERVATION_INSUFFICIENT_HISTORY`, at reduced size.
   - **Decision point — a short track record is not evidence of decay, and it is not evidence of health either.** It sits below Nodes 1 and 2 deliberately: a dead hypothesis and a broken feed are not sample-size questions, and a young strategy should not be shielded from either.
   - Configuring the gate while omitting `live_observation_count` raises rather than silently ungating.

5. **Node 3 — Market-wide regime shift audit** (resolves before Node 4):
   - Strategy below mandate **and** peer benchmark below its health floor $\implies$ `TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL` (cut allocation 50%, retain the signal).
   - **Decision point — joint impairment resolves first because it is the conservative reading of identical evidence.** When peers are down too, the data does not support an idiosyncratic verdict, and a capital cut is reversible where a recalibration that overwrites the parameter set is not.

6. **Node 4 — Parameter drift audit**:
   - Strategy below mandate while peers are healthy $\implies$ `RECALIBRATE_MODEL_PARAMETERS`.
   - **Decision point — check `sharpe_gap_vs_peer` before re-optimizing.** The two thresholds are asymmetric (mandate $1.0$, peer floor $0.50$), so a strategy at $0.95$ against peers at $0.55$ lands here while *outperforming its cohort*. It is missing its own mandate without underperforming its peers, which is weak evidence of drift; the report warns, and the right response is usually to review the mandate or the benchmark, not the parameters.

7. **Read `warnings`, then route the report to governance.** An empty tuple means no caveats applied. Every Sharpe-driven outcome carries an evidence caveat when the sample size is unknown or the estimate sits within 1.96 standard errors of the mandate threshold, and every recalibration carries the material-change caveat. The report is a **recommendation for authorisation**, not an executed decision.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A `NaN` metric certifying a strategy healthy**: `float('nan') < 1.0` is `False`, so a corrupt payload clears Nodes 3 and 4 and returns `MAINTAIN_TRADING`. The most permissive verdict the engine can produce, issued on unusable data. Reject non-finite values; never let them reach a comparison.
- **A signed slippage convention disarming the execution node**: if your TCA system reports cost as $-15$ bps, the ratio is $-0.75$, which never exceeds $0.50$, so a strategy losing three quarters of its alpha to execution is routed to `RECALIBRATE_MODEL_PARAMETERS` instead. The convention is a positive magnitude, and violating it is silent.
- **Zero or negative expected alpha bypassing the ratio test**: guarding the division with `expected_alpha_bps > 0` and skipping the node when it fails means the strategies with no measurable edge — the ones most in need of the execution audit — are the ones that skip it.
- **Comparing per-trade slippage against annualized alpha**: a strategy paying 15 bps per round trip against 300 bps of annual alpha scores a 5% ratio and clears Node 2, when the horizon-matched figure at 100 trades a year is 500%.
- **HARKing (Hypothesizing After Results are Known)**: bolting an indicator onto a strategy to "fix" it, without establishing whether execution slippage, a regime shift, or alpha decay was the cause. The node order exists precisely to stop the parameter file being the first reflex.
- **Deriving the alpha hypothesis from live P&L**: it turns Node 1 into a Sharpe threshold whose consequence is irreversible, and it fires hardest during the drawdowns Node 3 is designed to survive. The judgment is about the economic mechanism, made by research, ideally recorded before the drawdown.
- **Decommissioning during a regime shift**: retiring a sound strategy while its whole peer group is equally impaired destroys capacity that cannot be rebuilt when the regime turns.
- **Recalibrating a strategy that is beating its peers**: with a $1.0$ mandate and a $0.50$ peer floor, a strategy at $0.95$ against peers at $0.55$ is routed to recalibration while leading its cohort. Read `sharpe_gap_vs_peer`, not just the action.
- **Reading a routing decision as a demonstrated finding**: at 60 daily observations the standard error of an annualized Sharpe near 1.0 is $\approx 2.05$. A strategy observed at 0.6 against a 1.0 mandate is not *shown* to be underperforming. The gates are policy floors, not statistical conclusions.
- **Letting recalibrations accumulate untested**: ESMA's February 2026 supervisory briefing (¶30) warns that "a series of minor or small changes due to recalibrations could accumulate over time, when uncontrolled or unchecked, into a material change in the model output without it being tested". A remediation engine emitting `RECALIBRATE_MODEL_PARAMETERS` on a monthly cadence is exactly that mechanism — record and retest each one.
- **Treating the report as the action**: `is_decommissioned=True` sets a boolean. Something else must cancel the orders and liquidate the book.

## Verification

- Instantiate `StrategyUnderperformanceRemediationEngine`. Invalid alpha hypothesis with an otherwise perfect payload (Sharpe 2.5, healthy peers, 7% slippage ratio) $\implies$ `MANDATORY_STRATEGY_DECOMMISSION`, `is_decommissioned=True`, `decisive_node="NODE_1_HYPOTHESIS_FAILURE"`. 15 bps slippage against 20 bps alpha (75%) $\implies$ `OPTIMIZE_EXECUTION_AND_DATA`. Unhealthy feed with 0.0 bps slippage $\implies$ `OPTIMIZE_EXECUTION_AND_DATA` and a caveat that every Sharpe in the report came from the bad feed. Strategy 0.6 against peers 1.5 $\implies$ `RECALIBRATE_MODEL_PARAMETERS`, `is_capital_reduced=False`. Strategy 0.3 against peers 0.2 $\implies$ `TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL`.
- Verify `annualized_sharpe_standard_error(1.50, 60, periods_per_year=1) == 0.188` and `(3.00, 60, periods_per_year=1) == 0.303`, reproducing Lo (2002) Table 1, and that `(1.0, 60) == 2.0514223` and `(0.0, 2520) == 0.3162278` at the 252-day default.
- Boundary checks: slippage exactly at $50\%$ clears Node 2 (strict `>`), $50.05\%$ fires it; `live_sharpe` exactly $1.0$ is healthy (inclusive), $0.9999$ is not; `peer_benchmark_sharpe` exactly $0.50$ selects Node 4, $0.4999$ selects Node 3.
- Negative checks — each must raise `ValueError`, not route: `NaN`/`±Inf` on any of the five numeric fields; a negative `realized_slippage_bps`; `expected_alpha_bps` of `0.0` or `-5.0`; `"False"`, `"true"`, `0`, `1` or `None` for either boolean flag; an empty or non-string `strategy_id`; a non-positive or non-integer `live_observation_count`; `min_peer_sharpe > min_healthy_sharpe`; a non-positive `max_slippage_alpha_ratio` or `periods_per_year`; and a configured `min_live_observations` with no `live_observation_count` in the payload.
- Confirm `warnings` fires on: a Node 1 judgment made against an unhealthy feed, a Node 1 judgment made while peers are impaired, a Node 4 routing where `sharpe_gap_vs_peer >= 0`, a healthy strategy against an impaired peer group, any Sharpe-driven routing with no `live_observation_count`, and every recalibration. Confirm a healthy strategy with 5,000 conclusive observations returns `warnings == ()`.
- Confirm `triage_path` records the cleared nodes in order with the decisive node last, and that unreached nodes are absent entirely — a cleared node in the record is evidence it was tested.
- Run `python -m unittest discover -s skills/strategy-underperformance-remediation-decision-tree/scripts` and confirm 100% pass rate (43 tests).

## Related Skills

- `strategy-performance-decay-detection-vs-market-wide-decay`
- `strategy-lifecycle-retirement-criteria`
- `strategy-decommissioning-and-position-unwind-procedure`
- `benchmark-selection-for-strategy-evaluation`
- `transaction-cost-analysis-tca-integration`
- `backtest-vs-live-performance-divergence-tracking`
- `capital-reallocation-based-on-live-performance`
- `kill-switch-and-drawdown-circuit-breakers`
