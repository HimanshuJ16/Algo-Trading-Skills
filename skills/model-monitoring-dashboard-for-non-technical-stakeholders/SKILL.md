---
name: model-monitoring-dashboard-for-non-technical-stakeholders
description: >-
  Use when a live model's health must reach a risk officer or portfolio manager who
  cannot interpret a PSI figure; grades accuracy, drift and staleness into plain
  statuses. Not a substitute for MiFID II RTS 6 real-time monitoring.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, monitoring-dashboard, model-health, non-technical, traffic-light, risk-reporting, population-stability-index, model-governance
  brokers_frameworks: "Model Monitoring Dashboard Engine; Python standard library (dataclasses, math, logging)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this when a live ML trading model's health has to reach someone who will act
on it but cannot interrogate the statistics behind it. That reader is not
hypothetical. ESMA's supervisory briefing on algorithmic trading records that RTS 6
Article 16(2) requires real-time monitoring under **two lines of defence** — by the
trader in charge of the algorithm, *and* by the risk management function or an
independent risk control function, which "should not be hierarchically dependent
from the trader" and must be "endowed with appropriate powers, tools and procedures
to challenge the trader" (¶95–96). It adds that an IT function does not count as so
endowed. Article 2 separately requires compliance staff to have at least a general
understanding of how the firm's algorithms operate (¶50).

A second line that can only read the model's health through the first line's
interpretation of a Kolmogorov–Smirnov statistic is not independently challenging
anything. The dashboard's job is to make the health of the model legible without
handing over the statistics, and to be honest about what it does not know.

Four components are graded and aggregated to the **worst** of them:

| Component | What it detects |
|---|---|
| Prediction Accuracy | Realised out-of-sample decay against the configured floor. |
| Model Age | Days since retrain — the staleness axis. |
| Feature Drift PSI | Input distribution moving away from the training window. |
| Inference Latency | The model taking longer to answer than the trading path allows. |

## When NOT to Use

- **As a substitute for RTS 6 real-time monitoring.** Article 16(1) requires
  monitoring of *algorithmic trading activity* to detect signs of disorderly
  trading, and Article 16(2) fixes who must perform it. This grades **model
  quality**, which is a different object: a perfectly healthy model can place
  disorderly orders, and a drifted model can trade quietly within every limit. This
  dashboard is one input to that reader, not the control itself.

- **As a kill switch.** `recommended_action` is advisory text. The module cancels
  nothing, flattens nothing and halts nothing — see
  `kill-switch-and-drawdown-circuit-breakers` for enforcement and
  `risk-limit-breach-escalation-matrix` for routing and acknowledgement.

- **As an automatic retraining trigger.** ESMA lists "retraining or modifying
  machine learning components" among the change types warranting retesting (¶31),
  and warns that a series of small recalibrations can accumulate, unchecked, into a
  material change in model output that was never tested (¶30, ¶47). `SCHEDULE_RETRAIN_AND_REVIEW`
  opens a change request; it does not close one. See `model-versioning-and-rollback`.

- **As the diagnostic.** The dashboard says *which* component is unhealthy, never
  *why*. Distinguishing a frozen feature pipeline from a genuine $P(Y \mid X)$ shift
  is `concept-drift-vs-staleness-differentiation`; a reshuffled importance ranking is
  `feature-importance-drift-monitoring`. Handing a RED to a stakeholder without
  having run the diagnostic invites the most expensive mistake in this area —
  retraining on a stale feed.

- **When you cannot state what the bands mean.** The defaults for accuracy, model
  age and latency are conventions with no external authority (see
  `references/standards.md`). A dashboard whose colours nobody can justify is worse
  than no dashboard, because it launders an arbitrary threshold as a risk verdict.

- **As a source of computed statistics.** It grades numbers you supply. Computing
  PSI correctly (unbounded outer bin edges, per-feature maximum, collapse detection)
  is `concept-drift-vs-staleness-differentiation`; computing a latency percentile
  is `model-inference-latency-budget-for-live-trading`.

## Prerequisites

- **Out-of-sample accuracy in percentage points** (`58.5`, not `0.585`), in
  $[0, 100]$. In-sample accuracy on the dashboard turns the whole report into a
  statement about the training set.
- **Days since last retrain**, a non-negative whole number. Derive it from the model
  registry's recorded training timestamp, not from a file mtime that a redeploy
  resets.
- **Feature-drift PSI**: the per-feature **maximum** across the monitored feature
  set, never the mean. One relocated feature in a hundred gives a mean PSI of about
  0.075 and reads as stable.
- **An inference-latency budget** (`latency_green_max_ms`, `latency_amber_max_ms`)
  and the p99 latency to compare against it — or an explicit
  `monitor_latency=False` declaring latency out of scope. There is no defensible
  default budget; a sub-millisecond tick-to-trade path and a five-second
  end-of-day rebalance are both normal.
- **Calibrated thresholds.** `DashboardThresholds` defaults exist so the module
  runs, not because 55% and 14 days are right for your strategy.

## Workflow

1. **Fix the meaning of the three colours before choosing any number.** The Basel
   Framework's backtesting zones (MAR32.7–32.9) are the reference semantics, and
   they are worth copying: green "does not itself suggest a problem"; amber "does
   raise questions … for which such a conclusion is not definitive"; red "almost
   certainly indicates a problem". Amber is *unresolved*, not *slightly bad* — which
   is what makes it the right colour for a metric that was not measured.

2. **Grade each component against its own band.** Boundary conventions differ by
   component and are not arbitrary:
   - PSI edges belong to the **worse** band — exactly $0.10$ is AMBER, exactly
     $0.25$ is RED — because that is how the cited rule of thumb is stated.
   - Accuracy, model-age and latency edges belong to the **better** band, because no
     external source fixes them and inclusive-GREEN is the least surprising default.
   - **Decision point — do not read the accuracy floor as a break-even point.** 50%
     directional accuracy is break-even only for a symmetric, cost-free payoff. With
     average win $W$, average loss $L$ and round-turn cost $c$, the break-even hit
     rate is $p^{*} = (L + c)/(W + L)$: a 60%-accurate model with $W = 1$, $L = 2$
     loses money, and a 40%-accurate model with $W = 2$, $L = 1$ makes it. Calibrate
     the floor from the strategy's own payoff profile.

3. **Grade an unmeasured metric AMBER, and record its value as null.**
   - **Decision point — silence is not health.** A metric that was not reported this
     cycle, or a latency with no configured budget, must never contribute GREEN. The
     engine returns `measured=False` and `value=None` for it. Writing `0.0` into that
     field puts a number that was never computed in front of a risk reviewer, and
     `0.0` PSI reads as perfect stability.

4. **Reject impossible telemetry rather than grading it.** A negative model age is a
   clock skew or a broken `last_retrained_at`, not a fresh model; a negative PSI is a
   broken drift computation; an accuracy of 150% is a unit error; a `NaN` compares
   `False` against every threshold and would fall through to the healthy branch.
   Each raises `DashboardInputError`.
   - **Decision point — an exception here is a monitoring failure, not a pass.**
     Escalate it exactly as you would a RED. Code that wraps `evaluate_health` in
     `try/except` and renders an empty tile has silently deleted the control.

5. **Aggregate to the worst component, never an average.** Averaging is the specific
   failure this skill exists to prevent: a 30%-accuracy collapse averaged against
   three pristine components produces a comfortable-looking score.

6. **Name the components that drove the colour in the headline.** "STATUS IS RED,
   immediate intervention required" is not actionable by a reader who cannot then go
   and read the PSI themselves — it sends them straight back to the telemetry the
   dashboard was built to replace.

7. **Pick the action from *why* the status moved, not only how far.**
   - RED on any component $\implies$ `HALT_TRADING_IMMEDIATELY`, stated as advisory.
   - AMBER where every AMBER component is unmeasured $\implies$
     `RESTORE_MODEL_TELEMETRY`.
   - AMBER where a measured metric has degraded $\implies$ `SCHEDULE_RETRAIN_AND_REVIEW`.
   - **Decision point — never recommend a retrain because a metric is missing.** It
     sends the operator to rebuild the model when the fault is in the exporter, and
     a retrain fitted on a degraded feed is worse than no retrain.

8. **Retain the report, not just the colour.** `DashboardReport.to_dict()` is
   JSON-serialisable. Monitoring evidence is one input to the RTS 6 Article 9 annual
   self-assessment and validation report, and a rendered tile is not evidence.

> Full procedure: see `references/workflows.md`.
> Thresholds, their provenance, and what is *not* backed by any authority: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting GREEN for a metric that was never measured.** The single most damaging
  failure available to a stakeholder dashboard: it converts a monitoring outage into
  a positive assurance. Prior to v2.0.0 this engine accepted a `latency_ms` argument
  and never evaluated it — 250 ms against a 100 ms budget rendered GREEN.
- **Averaging sub-component scores.** A severe accuracy crash disappears behind three
  healthy components. Aggregate on the worst, and name it.
- **Grading impossible telemetry instead of rejecting it.** A negative model age
  passes any `age > limit` test unchallenged and renders as the freshest model on the
  board. So does a negative PSI, and an accuracy of 150%.
- **Letting `NaN` decide.** Every `>=` comparison against `NaN` is `False`, so a
  threshold ladder walks past each band and lands on whichever branch is last. That
  branch is a *verdict*, and it was reached by arithmetic on a missing number.
- **Reading GREEN as "the model is fine" when accuracy is implausibly high.** A
  broken label pipeline or a leaked target produces 98% out-of-sample accuracy, and
  every band here grades that GREEN. The dashboard can reject an *impossible* value
  (150%) but not an *implausible* one, because no defensible upper band exists. Treat
  a sudden jump in accuracy as an incident, not a win — see
  `feature-engineering-without-leakage` and `lookahead-bias-elimination`.
- **Passing accuracy as a fraction.** `0.58` is a legal percentage and a
  catastrophically wrong one; it grades RED and triggers a halt recommendation on a
  perfectly healthy model. The engine logs a unit warning, but the caller owns the
  units.
- **Averaging PSI across the feature universe before it reaches the dashboard.** The
  dashboard cannot detect this — it receives one number. Supply the per-feature max.
- **Treating PSI > 0.25 as a test.** It is a credit-scoring rule of thumb with no
  controlled error rate, and its *power decreases as sample size grows*
  (Yurdakul & Naranjo 2020). Trading monitors run on large windows, which is exactly
  where the fixed band goes blind.
- **Calibrating the staleness band so that RED fires routinely.** A model-age RED
  raises the same `HALT_TRADING_IMMEDIATELY` recommendation as an accuracy collapse.
  If the retrain cadence makes that a weekly event, the halt recommendation stops
  being read — and the control is gone by consent rather than by decision.
- **Wiring `SCHEDULE_RETRAIN_AND_REVIEW` into an automated retrain-and-deploy.**
  Retraining an ML component is a change type ESMA flags for retesting; an automated
  pipeline converts a monitoring control into an untested change to a live algorithm.
- **Presenting the recommended action as an executed one.** The dashboard halts
  nothing. If the runbook does not say who executes the halt and by what mechanism,
  RED is a colour and not a control.

## Verification

- **Healthy snapshot.** `NonTechnicalMonitoringDashboard(DashboardThresholds(latency_green_max_ms=50.0, latency_amber_max_ms=100.0))`
  with accuracy 58.5%, age 5 days, PSI 0.04, latency 12 ms $\implies$ `GREEN`,
  `NO_ACTION_REQUIRED`, empty `driving_components`, all four components `measured`.
- **Latency (regression).** The same snapshot with `latency_ms=250.0` $\implies$
  `RED`, `HALT_TRADING_IMMEDIATELY`, `driving_components == ["Inference Latency"]`.
  Against the pre-2.0 implementation this returns `GREEN`.
- **Ungoverned latency (regression).** `NonTechnicalMonitoringDashboard()` with no
  latency bounds $\implies$ `AMBER`, `RESTORE_MODEL_TELEMETRY`, and a latency
  component with `measured is False` and `value is None`. Pre-2.0: `GREEN`.
- **Band edges.** Accuracy $55.0 \to$ GREEN, $54.99 \to$ AMBER, $50.0 \to$ AMBER,
  $49.99 \to$ RED. Age $14 \to$ GREEN, $15 \to$ AMBER, $30 \to$ AMBER, $31 \to$ RED.
  Latency $50.0 \to$ GREEN, $50.1 \to$ AMBER, $100.0 \to$ AMBER, $100.1 \to$ RED.
- **PSI edges (regression).** $0.0999 \to$ GREEN, $0.10 \to$ **AMBER**,
  $0.2499 \to$ AMBER, $0.25 \to$ **RED**. Pre-2.0 graded exactly $0.10$ GREEN and
  exactly $0.25$ AMBER, one band better than the cited convention at each edge.
- **Worst-component aggregation.** Accuracy 30% with age 0, PSI 0.0 and latency
  0.1 ms $\implies$ `RED` naming `Prediction Accuracy`. A simultaneous AMBER and RED
  $\implies$ `RED`, with only the RED components named.
- **Action selection.** A missing metric $\implies$ `RESTORE_MODEL_TELEMETRY`, never
  `SCHEDULE_RETRAIN_AND_REVIEW`. A measured AMBER alongside a missing metric
  $\implies$ `SCHEDULE_RETRAIN_AND_REVIEW`. A RED alongside a missing metric
  $\implies$ `HALT_TRADING_IMMEDIATELY`.
- **Negative checks — each must raise `DashboardInputError` (regression).** Age
  $-5$; accuracy $150$ and $-1$; PSI $-3.0$; latency $-1.0$; `NaN`/$\pm\infty$ on any
  metric; a `str`, `list` or `bool` metric; a fractional model age; an empty or
  non-string `model_name`. A `numpy`/`pandas` integer or float scalar must be
  **accepted**, not rejected as "not a number". Pre-2.0, the first three each returned `GREEN`.
- **Configuration — each must raise `DashboardConfigError`.** Inverted accuracy,
  staleness, PSI or latency bands; an accuracy band above 100; a zero PSI band; a
  half-configured latency budget (one bound set, the other `None`); a non-finite
  band; a non-`DashboardThresholds` config; a non-`bool` `monitor_latency`.
- **Serialisation.** `to_dict()` survives a JSON round trip and keeps an unmeasured
  component's `value` as `null`, not `0.0`.
- Run `python -m unittest discover -s skills/model-monitoring-dashboard-for-non-technical-stakeholders/scripts`
  and confirm a 100% pass rate (45 tests).

## Related Skills

- `concept-drift-vs-staleness-differentiation`
- `model-staleness-detection`
- `feature-importance-drift-monitoring`
- `model-inference-latency-budget-for-live-trading`
- `kill-switch-and-drawdown-circuit-breakers`
- `risk-limit-breach-escalation-matrix`
- `model-versioning-and-rollback`
- `model-card-documentation-for-trading-models`
- `risk-reporting-for-external-stakeholders`
