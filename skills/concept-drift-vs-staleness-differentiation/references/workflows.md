# Workflows for Drift vs. Staleness Classification

## 1. Assemble the monitoring snapshot

| Input | Requirement |
|---|---|
| $X_{ref}$, $X_{curr}$ | `(observations, features)`. Same feature set, same order. Observation counts may differ. |
| $e_{ref} = \hat{Y}_{ref} - Y_{ref}$, $e_{curr}$ | Both **out-of-sample**. In-sample reference residuals understate $\text{MSE}_{ref}$ and inflate every ratio computed from it. |
| $T_{feat}$, $T_{sys}$ | Epoch seconds, same clock domain. If they come from different hosts, the skew between those hosts is part of the measurement. |

## 2. Decision tree

Each step short-circuits; nothing below it is computed.

```
                    T_feat > T_sys + tolerance ?
                              |
                  yes --------+-------- no
                   |                     |
              CLOCK_SKEW      T_sys - T_feat > max_staleness_sec ?
           (fix the clock)               |
                             yes --------+-------- no
                              |                     |
                     DATA_STALENESS       any input < min_samples,
                   (fix the pipeline,     or containing NaN/Inf,
                    do NOT retrain)       or MSE_ref == 0 ?
                                                    |
                                        yes --------+-------- no
                                         |                     |
                                 INSUFFICIENT_DATA    MSE ratio >= error_ratio_threshold ?
                              (repair inputs; the                |
                               model is unverified,  yes --------+-------- no
                               not healthy)           |                     |
                                             CONCEPT_DRIFT     any feature PSI > its trigger ?
                                          (names any features             |
                                           that also breached) yes -------+------- no
                                                                |                   |
                                                       COVARIATE_SHIFT           STABLE
                                                    (names the features)
```

Ordering rationale, in full, is in `references/standards.md`. The two that are
easiest to get wrong:

- **Clock skew before staleness.** $T_{sys} - T_{feat} < 0$ passes an
  `age > limit` test unchallenged.
- **Staleness before any distribution test.** A frozen feed replays the
  reference distribution, so PSI $\approx 0$ and MSE ratio $\approx 1$ — a stale
  snapshot is indistinguishable from a healthy one by drift statistics alone.

## 3. Feature shift calculation

Per feature $j$:

1. If $X_{ref}^{(j)}$ has fewer than two distinct values, PSI is undefined.
   Report it as degenerate — **do not score it 0.0**, which reads as stable.
2. Bin edges = reference quantiles of $X_{ref}^{(j)}$ at
   $0, 100/B, \dots, 100$ percent, de-duplicated.
3. If step 2 leaves fewer than three distinct edges — which happens whenever the
   reference is concentrated on a handful of values, e.g. a 95/5 halt flag —
   rebuild the edges as midpoints between the distinct reference values.
   Without this the feature gets a single bin and PSI is identically 0.0 for
   every sparse indicator in the set.
4. **Replace the outer two edges with $-\infty$ and $+\infty$.** Without this,
   `numpy.histogram` discards every current observation outside the reference
   range — the exact observations that indicate the feature has left its
   historical support.
5. $p_i$, $q_i$ = reference and current proportions per bin; empty bins floored
   to `1e-4`.
6. $\text{PSI}_j = \sum_i (q_i - p_i)\ln(q_i/p_i)$.

Trigger on $\max_j \text{PSI}_j$ against either the fixed `psi_threshold` or the
sample-size-aware $\left(\tfrac{1}{n}+\tfrac{1}{m}\right)\chi^{2}_{\alpha,B-1}$
benchmark. Report the mean, but never trigger on it: one broken feature in a
hundred is a broken pipeline, and the mean hides it.

## 4. Acting on the verdict

| Verdict | First action | Do not |
|---|---|---|
| `CLOCK_SKEW` | Reconcile NTP/PTP and the feed's timestamp source. Treat all recent signals from this feed as suspect. | Draw any drift conclusion from this snapshot. |
| `DATA_STALENESS` | Inspect the ingestion pipeline; confirm the feed is advancing. | Retrain. The model has not been shown to be at fault. |
| `INSUFFICIENT_DATA` | Repair the monitoring inputs. | Read it as healthy. The model is *unverified*, which is a different state. |
| `COVARIATE_SHIFT` | Check the named features for a pipeline, corporate-action or reference-data cause first; if the move is genuine, refit on the updated $P(X)$. | Re-specify the model. $P(Y \mid X)$ has not been shown to have moved. |
| `CONCEPT_DRIFT` | If features also breached, adjudicate: a large $P(X)$ move inflates residuals through extrapolation alone. Otherwise re-specify features, shorten the lookback, or begin the retirement process. | Assume a refit on the same feature set recovers the edge. |
| `STABLE` | Nothing. Record the statistics for threshold calibration. | Treat a single stable snapshot as confirmation; drift is detected in sequences, not points. |

## 5. Calibration loop

Both thresholds are defaults, not findings:

- `psi_threshold` / `psi_significance_level` — prefer the calibrated benchmark
  once the monitoring window exceeds a few hundred observations, since the
  fixed 0.25 band's power *falls* as samples grow.
- `error_ratio_threshold` — collect the strategy's own distribution of rolling
  MSE ratios during a period known to be healthy, and set the trigger from that
  distribution rather than from the 1.50 default.
- `max_staleness_sec` — set from the strategy's decision cadence. The 300s
  default is a placeholder and is far too loose for anything intraday.
