---
name: model-staleness-detection
description: Use when an ML signal model is already live and needs continuous
  health monitoring - rolling realised accuracy with a confidence bound, binned
  PSI feature-distribution drift against the training baseline, and an explicit
  staleness threshold that reduces or halts the signal's position sizing -
  rather than being trusted indefinitely after its deployment-time validation
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- model-monitoring
- model-decay
- psi
- feature-drift
- position-sizing
brokers_frameworks:
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any ML-based signal model that has been promoted to live or paper trading. Validation at deployment time (`walk-forward-validation-setup`, `offline-train-online-infer-deployment`) is necessary but not sufficient: financial markets are non-stationary, so the statistical relationships a model learned can weaken or invert as regime, participant behaviour or microstructure changes. A model that was genuinely validated at deployment can be quietly wrong months later with no code change, no exception and no alert. Only active monitoring catches that.

Two signals are tracked, because they fail at different times:

| Signal | What it measures | Timing |
|---|---|---|
| Rolling realised accuracy | What actually happened, on outcomes that have realised | **Late.** Lags by the label horizon, and needs enough observations to distinguish decay from noise. |
| Feature distribution drift (PSI) | How far the live input distribution has moved from the one the model was fitted on | **Early.** Available immediately, but a shifted input is not proof the model is wrong. |

## When NOT to Use

- **As the kill switch itself.** This produces a status and a sizing multiplier. Cancelling working orders, flattening positions and locking out new ones belong to `kill-switch-and-drawdown-circuit-breakers`. Wire the halt status to that mechanism; do not reimplement it here.
- **As a root-cause diagnosis.** "The model is stale" does not say whether the feature pipeline stalled, `P(X)` moved, or `P(Y|X)` decayed — three problems with three different remediations. That separation is `concept-drift-vs-staleness-differentiation`.
- **On a model whose labels have not realised yet.** A 20-day-horizon model produces its first scoreable outcome 20 trading days after deployment. Until then only the drift signal exists, and the accuracy monitor must say so rather than imply health.
- **As a profitability monitor.** Directional accuracy is blind to the size of the moves it gets right: a model can hold 55% accuracy while its P&L inverts because the payoff asymmetry moved. Pair it with realised P&L attribution.
- **As a statistical test with a controlled error rate.** The 0.10/0.25 PSI bands are a credit-scoring rule of thumb (see `references/standards.md`), not a calibrated test.

## Prerequisites

- Logged predictions and their eventual realised outcomes for **every** live inference, not just the ones the strategy acted on. Restricting the sample to traded signals conditions the metric on the strategy's other filters and measures the filters as much as the model.
- Predictions recorded as **discrete labels**, with any continuous model output bucketed where the thresholds are visible. A monitor that coerces `1.2` and `1.7` to the same class is silently choosing your decision boundary.
- The training-time performance metrics, and — for real PSI rather than a location-only proxy — the **training feature sample itself**, not just its mean and standard deviation.
- A durable prediction log the rolling window can be reloaded from. The window lives in memory; a deploy or a crash otherwise resets the health gate to "no evidence".

## Workflow

1. **Log every live inference**: the prediction, the feature snapshot that produced it, and — once it realises — the actual outcome. Record the outcome only when it has *fully* realised. Scoring a prediction against a partially formed bar is look-ahead bias inside the monitor, and it reports health the strategy never had.
2. **Track a rolling metric over a moving window**, matching the metric used in offline validation. Never a cumulative all-time number: a cumulative average dilutes recent degradation with historical good performance and delays detection by exactly as long as the model has been running. Track precision on the traded side alongside accuracy where the strategy only acts on one direction — under class imbalance, accuracy scores the base rate.
3. **Refuse to judge on too few observations.** Below a minimum sample count the status is `INSUFFICIENT_DATA`, not `HEALTHY`. An empty window is absence of evidence; treating it as evidence of health is how a freshly restarted monitor certifies a model it has never observed. Reload the window from the prediction log on start-up so a deploy does not blind the gate.
4. **Read the accuracy point estimate next to its confidence bound.** Realised accuracy on a short window is noisy: 33 correct out of 60 is a point estimate of 0.550 and a 95% Wilson lower bound of 0.444 — statistically consistent with a coin flip. Require a breach to *persist* for a defined number of consecutive evaluations before halting; a single breaching window is not evidence of decay.
5. **Monitor feature distribution drift separately**, per feature, against the training baseline. Use binned PSI with unbounded outer bins, so live values that have left the historical support are counted rather than discarded. Trigger on the per-feature **maximum**, never the mean — one broken feature in a hundred is a broken pipeline, and the mean dilutes it below any threshold.
6. **Treat "cannot measure" as its own state.** Non-finite live values, an empty live batch, a feature with no registered baseline, and a registered feature absent from the batch are four different faults, and none of them is "no drift observed". Non-finite inputs mean the model is being scored on NaN — that halts. The others mean the monitoring is broken, not necessarily the model — those degrade and alert, naming the feature.
7. **Fix the staleness thresholds in advance**, before observing a suspicious pattern. Choosing "how bad is bad enough" after the fact is hindsight bias, and it reliably produces a threshold that justifies continuing to trade.
8. **On breach, change the model's authority, not the strategy's mind**: reduce the sizing multiplier, route to human review, or halt the signal's contribution. Let the halt **latch**. A model that halted should be retrained and shadow-validated, not waved back in because the next window looked better — and an unlatched gate flaps position size on noise.
9. **When retraining**, follow the walk-forward discipline in `walk-forward-validation-setup` on the newly available data, run the retrained model through the same shadow period as any new deployment, then clear the latched halt with an attributed operator and reason so the decision is auditable.

> Full step-by-step procedure and reference implementation notes: see `references/workflows.md`.
> Statistic definitions, threshold provenance and regulatory touchpoints: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Validating once at deployment and treating the result as permanent.** The single most common gap in production trading ML systems.
- **Monitoring cumulative accuracy**, which dilutes recent degradation with historical good performance.
- **Reporting an empty window as perfect.** A monitor that returns 1.0 accuracy when nothing has been recorded gives full size to a model it has no evidence about — and it looks exactly like a healthy model on every dashboard.
- **Halting on one breaching window.** A genuinely 55%-accurate model falls below a 52% threshold on **34.7%** of independent 60-observation windows by chance alone (exact binomial). A gate that halts on the first breach halts a healthy model about a third of the time, and the operators learn to override it.
- **Calling a location statistic PSI.** The standardised gap between two means is 0.0 for *any* change that leaves the mean where it was: a feature whose spread tripled scores zero drift. PSI is `sum((a - e) * ln(a / e))` over bins, and it sees scale and shape as well as location.
- **Building PSI bins with bounded outer edges**, which discards exactly the current observations that have left the historical support — the ones that matter most.
- **Quantile bins collapsing on a sparse indicator.** A 95/5 regime flag over 10 bins de-duplicates to a single bin spanning everything, and PSI is then identically 0.0 whatever the live sample does. Halt flags and mostly-zero event counts are ordinary trading features.
- **Letting an unmeasurable feature read as clean.** A NaN feature, a feature that stopped being produced, and a feature name that does not match any baseline all return 0.0 from a naive monitor — "no drift observed" for a statistic that was never computed.
- **Defaulting an unregistered feature to a standard-normal baseline.** A typo in a feature name then produces a confident number against a distribution nothing was trained on.
- **Continuing at full size after crossing a defined threshold "just to see if it recovers."** This is precisely the situation where the risk control overrides strategy discretion, not the reverse.
- **Re-alerting on every evaluation while halted.** A channel that repeats itself gets muted, and a muted channel misses the next incident. Alert on transitions.
- **Retraining on the data that triggered the alert without re-validating.** Fitting to a stalled feed replays yesterday's prices as today's and ships the result to production.

## Verification

Run the unit suite:

```
python -m unittest discover -s skills/model-staleness-detection/scripts
```

51 tests cover, among others:

- **PSI against hand-derived proportions** — a 100-value reference over 10 bins puts exactly 0.1 in each bin (asserted separately); relocating all current mass into the top bin gives a closed-form PSI of **8.283089**, independent of the module's binner.
- **Low-cardinality bin collapse** — a 95/5 indicator flipping to 50/50, expected **1.324998** from the two-bin proportions; a naive quantile binner returns 0.0.
- **Gaussian baseline** — with mean/std only, PSI has the closed form `z**2`; at `z = 2` the expected value is **4.0** (the previous implementation reported 2.0, the one-directional KL).
- **Wilson bounds** — 33/60 gives 0.444482, 140/250 gives 0.507992, and 10/10 gives 0.787058 rather than a zero-width interval.
- **Regressions for every pitfall above**: empty window reported as perfect accuracy, cold start reported as HEALTHY, a single breach halting, variance-only drift missed, NaN reported as clean, an empty live batch reported as clean, an unregistered feature scored against an implicit standard-normal baseline, a constant training baseline scored, `psi_halt_threshold` accepted but never read, `window=0` silently discarding every outcome, alerts re-firing every evaluation.
- **Boundaries** — accuracy exactly at the threshold (not a breach), PSI exactly at the halt threshold (halt), the breach streak resetting on recovery, and the hold-down before full size is restored.

Beyond the suite:

- Confirm the logged dataset can reproduce the rolling metric independently — spot-check that replaying the prediction log through the monitor gives the number the dashboard showed.
- Replay the threshold logic against a historical period where a comparable strategy is documented to have degraded, and confirm it would have flagged staleness at or before the point performance actually broke.
- Confirm the threshold, the automatic response and the halt-clearing procedure are documented and testable without live trading.

Repository checks:

```
python tools/validate_skills.py
```

## Related Skills

- `walk-forward-validation-setup`
- `offline-train-online-infer-deployment`
- `kill-switch-and-drawdown-circuit-breakers`
- `concept-drift-vs-staleness-differentiation`
- `model-versioning-and-rollback`
