---
name: multi-horizon-forecasting-architecture
description: >-
  Use when models forecast the same instrument over several forward horizons at once and
  their outputs must become one tradeable alpha; rescales each forecast onto a common
  horizon before weighting them.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, multi-horizon, alpha-combination, information-coefficient, signal-decay, conflict-arbitration
  brokers_frameworks: "Information Coefficient (IC); Grinold Alpha = Volatility x IC x Score; Square-Root-of-Time Volatility Scaling; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a model set produces return forecasts for the *same instrument* over several forward horizons at once — for example $\tau \in \{5, 15, 60, 390\}$ minutes, where $390$ min is one US equity regular session (09:30–16:00 ET; use $1440$ for a 24-hour crypto day) — and those forecasts must collapse into one number a sizing or execution layer can act on. Short-horizon forecasts tend to carry higher Information Coefficients but decay fast and force turnover; long-horizon forecasts persist but say little about the next few minutes. The skill covers three things: putting forecasts of different horizons on a common scale, weighting them, and deciding what to do when the short and long ends disagree.

## When NOT to Use

- **All forecasts already share one horizon.** Then this is an ensemble problem, not a multi-horizon one — use `ensemble-signal-combination-without-overfitting`, which fits weights against a realized target instead of assuming a scaling law.
- **Cross-sectional ranking rather than a point return forecast.** Ranks have no horizon scale to normalize; combine them as ranks.
- **Selecting one horizon per regime rather than blending.** If the intent is to *switch* horizons rather than average them, use `regime-detection-for-strategy-switching`.
- **Deciding how much risk each horizon bucket may consume.** That is a budgeting question, not a forecasting one — see `risk-budget-allocation-across-time-horizons`.
- **As a risk control.** Conflict arbitration reduces signal conviction; it is not an exposure limit and must never be counted as one.

## Prerequisites

- One forecast per horizon (`horizon_steps`, `predicted_return`, `ic_score`, `confidence`). Competing models for the *same* horizon must be aggregated upstream — duplicate horizons are rejected.
- `horizon_steps` expressed in a single base unit (minutes, seconds, bars) shared by every prediction in a call. `predicted_return` is the return expected over that *whole* horizon, not a per-step rate.
- `ic_score` is an out-of-sample correlation between forecast and realized return **at that horizon**, so it must lie in $[-1, 1]$. An IC measured at one horizon does not describe another.
- For `EXPLICIT_VOL` scaling: a measured standard deviation of the realized $\tau_k$-step return for each horizon, including the target horizon.

## Workflow

1. **Ingest one forecast per horizon.** Reject non-finite values, out-of-range ICs or confidences, and non-positive horizons at the boundary. A single NaN forecast otherwise propagates silently into the composite alpha and then into a position size.

2. **Rescale every forecast onto one target horizon $\tau_\star$** — this step is what makes the forecasts addable, and skipping it is the skill's central failure mode:
   $$\tilde{y}_k = \hat{y}_{\tau_k} \cdot \frac{\sigma(\tau_\star)}{\sigma(\tau_k)}$$
   - `EXPLICIT_VOL`: use measured $\sigma(\tau_k)$. Preferred where the difference matters.
   - `SQRT_TIME`: assume $\sigma(\tau) \propto \sqrt{\tau}$, so the factor is $\sqrt{\tau_\star/\tau_k}$. Needs no extra inputs, but holds exactly only for zero-mean, homoscedastic, serially uncorrelated returns and understates risk under jumps, with the bias growing in the horizon.
   - `NONE`: no rescaling. Correct **only** when the caller has already normalized upstream.
   - Default $\tau_\star$ is the shortest horizon supplied — normally the one actually traded and re-evaluated. Choosing $\tau_\star$ sets the units of the composite, of `normalized_predictions`, and of `conflict_threshold`.

3. **Compute and normalize horizon weights** over the rescaled forecasts:
   - `IC_WEIGHTED`: $w_k = \max(0, IC_k) \cdot \text{confidence}_k$.
   - `INVERSE_HORIZON_SQRT`: $w_k = \text{confidence}_k / \sqrt{\tau_k}$. Note this tilts *toward* short horizons and therefore *raises* turnover — it is a decay heuristic, not a turnover control.
   - `EQUAL`: $w_k = 1$. The $1/N$ baseline; deliberately ignores `ic_score` and `confidence`.
   - Normalize $\bar{w}_k = w_k / \sum_j w_j$.
   - **If every weight is zero, do not fall back to equal weighting.** Under `IC_WEIGHTED` that state means no horizon has non-negative measured skill; emitting a full-strength average would trade a model set that has demonstrated none. Zero the composite and return `NO_VALID_HORIZON_WEIGHTS`.

4. **Synthesize the composite** $\alpha = \sum_k \bar{w}_k \tilde{y}_k$, an expected return **over $\tau_\star$**. `composite_score` restates it in target-horizon volatility units ($\alpha / \sigma(\tau_\star)$), the score side of Grinold's $\alpha = \sigma \cdot IC \cdot \text{score}$.

5. **Audit consensus and arbitrate short-vs-long conflict.** Report both the head-count consensus and the weight-weighted consensus — a 2-vs-2 split where the dissenters hold 5% of the weight is not a 50% disagreement. Flag a conflict when the shortest and longest horizons have opposite signs *and* both clear `conflict_threshold` **after rescaling**. Then apply the configured policy: `REPORT_ONLY`, `DAMPEN`, `SUPPRESS`, or `DEFER_LONG`. The status field records which fired, so a damped signal is never mistaken for a clean one.

6. **Emit the structured `MultiHorizonForecastReport`** for audit: weights, rescaled per-horizon forecasts, both consensus measures, conflict flag, target horizon, and status.

> Full procedure: see `references/workflows.md`.
> Method comparison and source citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naive horizon averaging.** Averaging a $+10$ bps 5-minute forecast with a $+200$ bps session forecast yields $105$ bps over *no defined horizon*. Rescale first: under $\sigma \propto \sqrt{\tau}$ the session forecast is worth $200/\sqrt{390/5} \approx 22.6$ bps at the 5-minute horizon, and the composite is $\approx 16$ bps — an order of magnitude apart from the naive number, and the difference goes straight into position size.
- **Applying one absolute threshold to unscaled forecasts.** A fixed $10$ bps conflict threshold is trivial for a session forecast and nearly unreachable for a 5-minute one, so conflicts driven by the short end go undetected. Compare on the rescaled series, in target-horizon units.
- **Treating an IC as horizon-free.** An IC measured on 5-minute forecasts says nothing about the 60-minute model. Weighting by a single IC reused across horizons silently assumes the horizons are equally skilled.
- **Reading IC weights as a minimum-variance combination.** All three schemes here are *marginal* — they judge each horizon alone. Overlapping-horizon forecasts are mechanically correlated (the 5-minute window sits inside the 60-minute window), so the blend is a heuristic, not an optimum. Estimating that covariance badly is usually worse than ignoring it, which is why no covariance path is offered.
- **Falling back to equal weighting when every IC is non-positive.** That converts "nothing here predicts" into a full-strength tradeable signal. Zero the composite and investigate the model set.
- **Duplicate horizons.** Keying weights by horizon and then applying them per prediction lets a repeated horizon consume its weight twice, so the applied weights exceed $1$ and the composite is overstated. Aggregate competing models per horizon before combining.
- **Silent scheme fallback.** A misspelled scheme that quietly degrades to equal weighting changes the traded signal with no error and no log line. Reject unknown schemes.
- **Reporting 100% consensus on a flat forecast set.** All-zero forecasts have no direction to agree on; a downstream sizer reading "100% consensus" may size up on nothing.
- **Ignoring short-vs-long conflicts.** Executing a 5-minute buy into a materially bearish session forecast trades against the macro trend. Detection alone is not arbitration — pick a policy and record it.
- **Overestimating short-horizon edge.** A high 5-minute IC does not survive turnover and slippage. Net the execution cost before comparing horizons; see `transaction-cost-analysis-tca-integration`.

## Verification

- Instantiate `MultiHorizonForecasterEngine` and confirm the composite is an expected return **over `target_horizon_steps`**: for $\tau = 5$ and $\tau = 45$ (an exact $\sigma$ ratio of $3$), a $90$ bps 45-step forecast must rescale to $30$ bps and the equal-weighted composite must be $20$ bps, not $50$ bps.
- Confirm `composite_alpha` equals $\sum_k \bar{w}_k \cdot$ `normalized_predictions[k]` before arbitration, and that $\sum_k \bar{w}_k = 1$ for every scheme.
- Confirm duplicate horizons, non-finite values, $|IC| > 1$, confidence outside $[0, 1]$, non-positive horizons, and unknown scheme/mode/policy strings all raise `MultiHorizonError` rather than degrading silently.
- Confirm an all-non-positive-IC set returns `composite_alpha == 0.0` with status `NO_VALID_HORIZON_WEIGHTS` and a WARNING log line.
- Confirm each `ConflictPolicy` produces the documented composite and status, and that arbitration does not fire when no conflict is flagged.
- Run `python -m unittest discover -s skills/multi-horizon-forecasting-architecture/scripts` and confirm a 100% pass rate.

## Related Skills

- `ensemble-signal-combination-without-overfitting`
- `risk-budget-allocation-across-time-horizons`
- `rebalancing-frequency-optimization-cost-vs-drift`
- `model-inference-latency-budget-for-live-trading`
- `label-noise-estimation-in-financial-targets`
