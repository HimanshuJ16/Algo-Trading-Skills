---
name: concept-drift-vs-staleness-differentiation
description: >-
  Use when a live signal degrades and you must decide what to fix, separating concept
  drift in the conditional relationship, covariate shift in the feature distribution,
  and plain data staleness, since each calls for a different remedy.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: concept-drift, covariate-shift, data-staleness, psi, wasserstein, alpha-decay, monitoring
  brokers_frameworks: "NumPy; SciPy"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a live ML trading signal's predictive performance ($R^2$, directional accuracy, Sharpe) degrades and you must decide **what to fix** before you spend anything fixing it. The three plausible root causes call for three different, mutually exclusive remediations:

| Root cause | What changed | Remediation |
|---|---|---|
| `DATA_STALENESS` | Nothing about the market. The feature pipeline is replaying old values. | Fix the pipeline. **Do not retrain** — the model has not been shown to be at fault. |
| `COVARIATE_SHIFT` | $P(X)$ moved; $P(Y \mid X)$ intact. The model is extrapolating. | Refit on the updated input distribution. |
| `CONCEPT_DRIFT` | $P(Y \mid X)$ itself moved. The alpha decayed. | Re-specify features, shorten the lookback, or retire the strategy. Refitting the same feature set rarely recovers the edge. |

Terminology follows Moreno-Torres et al. (2012) — see `references/standards.md`.

## When NOT to Use

- **As a kill switch.** This returns a classification, not an action. Halting the strategy, cancelling working orders and flattening positions belong to `kill-switch-and-drawdown-circuit-breakers`.
- **As a continuous staleness monitor.** It compares two timestamps you supply. Tracking feature age continuously across a multi-source feed is `model-staleness-detection`.
- **On classification models where the target distribution moved.** Prior probability shift ($P(Y)$ changing with $P(X \mid Y)$ fixed) is a fourth regime this module does not separate; it will surface as an elevated error ratio and be labelled `CONCEPT_DRIFT`.
- **On windows smaller than ~100 observations.** PSI's distributional properties are not established below that (see `references/standards.md`); the module returns `INSUFFICIENT_DATA` rather than a number you should not trust.
- **As an automatic retraining trigger.** `recommended_action` is advisory text for a human or a governed pipeline, not an instruction to execute.

## Prerequisites

- Reference feature matrix $X_{ref}$ (training window) and current production matrix $X_{curr}$, `(observations, features)`, same feature set in the same order. Observation counts may differ.
- Reference residuals $e_{ref} = \hat{Y}_{ref} - Y_{ref}$ and current residuals $e_{curr}$, both computed **out-of-sample** — in-sample reference residuals understate `mse_ref` and inflate every subsequent ratio.
- Feature timestamp $T_{feat}$ and evaluation timestamp $T_{sys}$, in the **same clock domain and the same units (epoch seconds)**.
- `numpy`, `scipy`.

## Workflow

Evaluation order is fixed. Each step short-circuits.

1. **Clock skew.** If $T_{feat} > T_{sys} + \text{tolerance}$, return `CLOCK_SKEW`. A negative feature age is not a fresh feature; it is a timestamp of unknown provenance, and nothing downstream can be trusted. Fix the clock (NTP/PTP), not the model.
2. **Staleness.** If $\Delta T = T_{sys} - T_{feat} > \text{max\_staleness\_sec}$, return `DATA_STALENESS`. This **must** precede every distribution test: a frozen feed replays the reference distribution exactly, so a stale snapshot scores a near-zero PSI and a near-1.0 error ratio — it looks pristine.
3. **Data sufficiency.** If any input has fewer than `min_samples` observations, or contains any NaN/Inf, return `INSUFFICIENT_DATA`. Do **not** impute or drop: a partially broken feed that keeps producing confident verdicts is worse than one that stops.
4. **Residual error ratio** — the $P(Y \mid X)$ signal. $\text{ratio} = \text{MSE}(e_{curr}) / \text{MSE}(e_{ref})$. If $\text{MSE}(e_{ref}) = 0$ the ratio is undefined; return `INSUFFICIENT_DATA` rather than dividing by a fudge factor.
5. **Feature shift** — the $P(X)$ signal. Compute PSI **per feature** against reference-quantile bins with unbounded outer edges. Trigger on the per-feature **maximum**, never the mean: one broken feature in a hundred is a broken pipeline, and the mean dilutes it below any threshold.
6. **Classify**, in this precedence:
   - error ratio breached $\implies$ `CONCEPT_DRIFT`. If features also breached, the result still says `CONCEPT_DRIFT` but names the shifted features, because a large enough $P(X)$ move inflates residuals through extrapolation alone. A human adjudicates that case.
   - features breached, error ratio held $\implies$ `COVARIATE_SHIFT`.
   - neither $\implies$ `STABLE`.

Step 5 may use 1-D Wasserstein distance instead of PSI; the helper in `scripts/` implements PSI only.

> Full procedure: see `references/workflows.md`.
> Standards, thresholds and their provenance: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bounded PSI bins silently discard the evidence.** `numpy.histogram` *ignores* values outside the supplied edges. Building edges from reference percentiles and passing them straight to `histogram` therefore drops exactly the current observations that have left the historical support. Measured on this skill's own construction: a feature with 40% of its mass relocated far outside the reference range scores **0.205** with bounded edges — under the 0.25 rule of thumb, i.e. "no action" — versus **0.741** with $-\infty/+\infty$ outer edges. Always make the outer edges unbounded.
- **Averaging PSI across the feature universe.** One of 100 features fully relocated gives a mean PSI of 0.075 and a `STABLE` verdict. Report the mean, trigger on the max, and name the offending feature — "covariate shift detected" is unactionable if the operator cannot tell which feature moved.
- **Quantile bins collapsing on a sparse indicator.** Bin edges built from reference percentiles de-duplicate. A 95/5 binary flag over 10 bins de-duplicates to *two* edges — one bin spanning everything — and PSI is then identically **0.0** whatever the current sample does. Measured: a 95/5 indicator that flipped to 50/50 scored 0.0. Halt flags, regime flags and mostly-zero event counts are common trading features, so detect the collapse and fall back to bins between the distinct reference values.
- **Letting NaN decide.** A single NaN residual makes the MSE ratio NaN, and every `>=` comparison against NaN is `False`. A naive classifier therefore reports **`STABLE`** on poisoned data. Non-finite input must be its own status.
- **Absorbing a future-dated timestamp.** A clock-skewed feature timestamp produces a *negative* age, which passes an `age > limit` test unchallenged. The snapshot then gets scored on data of unknown vintage.
- **Reporting placeholder values as measurements.** A staleness verdict short-circuits before PSI is computed. Returning `psi = 0.0` for that snapshot puts "no drift observed" on a dashboard for a statistic that was never calculated. Return `None`.
- **Treating PSI > 0.25 as a test.** It is a credit-scoring rule of thumb with no controlled error rate, and its **power decreases as sample size grows** (Yurdakul and Naranjo 2020). Trading monitors run on large windows — precisely where the fixed band goes blind. Set `psi_significance_level` to use the chi-square benchmark instead.
- **Retraining on stale data.** The most expensive of these mistakes: an automated retrain fits the model to yesterday's prices being replayed as today's, and ships the result to production.
- **Reading a very large PSI as a distance.** PSI's magnitude for two distributions with disjoint support is an artefact of the zero-bin floor constant, not a measure of how far apart they are. Read it as "disjoint", never as "$n$ times worse".

## Verification

Run the unit suite:

```
python -m unittest discover -s skills/concept-drift-vs-staleness-differentiation/scripts
```

37 tests cover, among others:

- **PSI mass conservation** — a hand-constructed reference/current pair whose bin proportions are known exactly (reference 0.1 per decile; current 0.06 in bins 0–8 and 0.46 in bin 9). Expected PSI is derived from the closed form on those proportions, independently of the implementation's binning: 0.733278.
- **Chi-square calibration** — `psi_benchmark()` reproduces the published benchmark table of Yurdakul and Naranjo (2020), Table 2 ($B = 10$, $\alpha = 0.05$): 0.338 at $n = m = 100$, 0.085 at $n = m = 400$, 0.102 at $n = 200, m = 1000$.
- **Low-cardinality bin collapse** — a 95/5 indicator flipping to 50/50, expected PSI 1.324998 derived from the two-bin proportions (0.95/0.05 versus 0.50/0.50); the pre-fix code returned 0.0.
- **Regressions** for every pitfall above: out-of-support mass, mean-PSI dilution, NaN-to-`STABLE`, empty residuals, zero reference MSE, future-dated timestamps, placeholder result fields, `numpy` scalar timestamps.
- **Boundaries** — staleness at exactly `max_staleness_sec` (not stale), error ratio at exactly `error_ratio_threshold` (drift).
- **Structural validation** — mismatched feature counts, 3-D input, duplicate or miscounted `feature_names`, non-finite or non-numeric timestamps, invalid configuration.

Repository checks:

```
python tools/validate_skills.py
```

## Related Skills

- `model-staleness-detection`
- `feature-importance-drift-monitoring`
- `kill-switch-and-drawdown-circuit-breakers`
- `walk-forward-optimization-window-management`
- `class-imbalance-handling-for-rare-signal-events`
