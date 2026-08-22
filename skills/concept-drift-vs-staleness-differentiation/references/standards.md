# Standards for Drift vs. Staleness Classification

These are engineering standards and cited statistical results. **No regulator
mandates any threshold on this page**, and every number below is a defensible
default to be calibrated, not a compliance floor.

US model-risk guidance covers model monitoring in general terms but names no
drift metric: Federal Reserve SR 26-2, *Revised Guidance on Model Risk
Management* (17 April 2026), superseded SR 11-7 (2011) and SR 21-8, and applies
to Fed-regulated banking organizations with over $30 billion in total assets;
OCC Bulletin 2026-13 is the companion issuance. Most trading firms fall outside
its scope entirely. Confirm applicability to your own entity and jurisdiction
before treating any of it as binding, and note that the authors of this skill
verified only the scope and supersession above from primary sources - not the
detailed content of SR 26-2.

## Engineering requirements

| Requirement | Rationale |
|---|---|
| Clock skew MUST be resolved before staleness. | A feature timestamp ahead of the evaluation clock yields a *negative* age that passes an `age > limit` test unchallenged. The snapshot is then scored on data of unknown vintage. |
| Staleness MUST be resolved before any distribution test. | A frozen feed replays the reference distribution exactly: PSI near zero, error ratio near one. A stale snapshot looks pristine to every drift statistic. |
| Non-finite or undersized input MUST return its own status, never `STABLE`. | Comparisons against `NaN` are `False`, so a NaN-poisoned snapshot falls through to the healthy branch of any naive threshold ladder. |
| Unmeasured result fields MUST be null, not zero. | A short-circuited diagnosis that reports `psi = 0.0` puts "no drift observed" on a dashboard for a statistic that was never computed. |
| Model retraining MUST NOT be executed when the root cause is `DATA_STALENESS`. | Retraining fits the model to old prices being replayed as current ones and ships the result to production. |
| The feature-shift trigger MUST be per-feature, not an average. | Averaging PSI across the universe dilutes a single broken feature below any threshold. Measured here: 1 of 100 features fully relocated gives a mean PSI of 0.075. |
| Quantile bin edges MUST be checked for collapse. | Reference percentiles de-duplicate: a 95/5 binary indicator over 10 bins yields two distinct edges, i.e. a single bin, and PSI is then identically 0.0 whatever the current sample does. Measured here: a 95/5 indicator flipping to 50/50 scored PSI 0.0. Fall back to bins between the distinct reference values. |
| PSI bin edges MUST be unbounded at the extremes. | `numpy.histogram` **ignores values outside the supplied bin edges** ([NumPy documentation](https://numpy.org/doc/stable/reference/generated/numpy.histogram.html)). Reference-percentile edges therefore discard precisely the current observations that have left the historical support. Measured here: a feature with 40% of its mass moved outside the reference range scores PSI 0.205 with bounded edges versus 0.741 with `-inf`/`+inf` edges — the bounded value falls *below* the 0.25 rule of thumb. |

## Population Stability Index

$$\text{PSI} = \sum_{i=1}^{B} (q_i - p_i)\,\ln\!\left(\frac{q_i}{p_i}\right)$$

where $p_i$, $q_i$ are the reference and current proportions in bin $i$. Bins are
reference quantiles. Empty bins are floored to a small constant (`1e-4` here) so
the logarithm stays finite; **this makes PSI's magnitude for disjoint
distributions an artefact of that constant, not a distance.**

### Thresholds

| Trigger | Status | Source |
|---|---|---|
| PSI < 0.10 "little change", 0.10 ≤ PSI < 0.25 "moderate change", PSI ≥ 0.25 "significant change, action required" | Industry rule of thumb. **Not a statistical test** — quoted as having no reference to type I or type II error rates. | Yurdakul & Naranjo (2020), abstract; attributed there to Lewis (1994). |
| $\text{PSI} > \left(\tfrac{1}{n} + \tfrac{1}{m}\right)\chi^{2}_{\alpha,\,B-1}$ | Calibrated test at level $\alpha$. Follows from their Theorem 3.3: $\left(\tfrac{1}{n}+\tfrac{1}{m}\right)^{-1}\text{PSI}$ is approximately $\chi^{2}_{B-1}$ under the null of no shift. | Yurdakul & Naranjo (2020), eq. 4.1 and Theorem 3.3. |

**Why the rule of thumb is the wrong default for trading monitors.** Yurdakul &
Naranjo report that for $B = 10$ the 0.25 benchmark "seems reasonable for sample
sizes $n$ and $m$ between 100 and 200, but it is too conservative for larger
sample sizes", and that the fixed 0.10 and 0.25 tests have *powers that decrease
with sample size*. Monitoring windows in trading are typically far larger than
200 observations, which is exactly the regime where the fixed band goes blind.
Their published Table 2 ($B = 10$, $\alpha = 0.05$) gives 0.338 at
$n = m = 100$, 0.169 at $n = m = 200$, 0.085 at $n = m = 400$ and 0.034 at
$n = m = 1000$; `psi_benchmark()` in `scripts/` reproduces these values.

The `min_samples = 100` default comes from the same paper: their simulations
confirm the chi-square approximation for sample sizes as low as 100.

The implementation triggers on strict `PSI > threshold`, matching eq. 4.1; the quoted rule of thumb uses `0.25 ≤ PSI`. The difference is measure-zero for continuous
features.

### Residual error ratio

$$\text{MSE Ratio} = \frac{\overline{e_{curr}^{2}}}{\overline{e_{ref}^{2}}}$$

The default trigger of 1.50 is an **operator-chosen heuristic with no published
provenance**. No parametric test is offered for it here, deliberately: the
F-ratio of residual variances requires independent, homoscedastic, normally
distributed residuals, and financial model residuals are fat-tailed,
heteroscedastic and autocorrelated, so its nominal error rate does not hold.
Calibrate this threshold against the strategy's own historical distribution of
rolling MSE ratios rather than adopting 1.50 because it appears here.

## Terminology

Following Moreno-Torres, Raeder, Alaiz-Rodríguez, Chawla & Herrera (2012), "A
unifying view on dataset shift in classification", *Pattern Recognition* 45(1),
521–530:

- **Covariate shift** — $P(X)$ differs between training and deployment while
  $P(Y \mid X)$ is unchanged.
- **Concept shift** (used here as *concept drift*) — $P(Y \mid X)$ differs while
  $P(X)$ is unchanged.
- **Prior probability shift** — $P(Y)$ differs while $P(X \mid Y)$ is unchanged.
  This module does **not** separate this fourth regime; it will present as an
  elevated error ratio and be reported as `CONCEPT_DRIFT`.

## References

- Yurdakul, B. & Naranjo, J. (2020). "Statistical properties of the population
  stability index." *Journal of Risk Model Validation* 14(4), 89–100.
  DOI: 10.21314/JRMV.2020.227.
  <https://www.risk.net/journal-of-risk-model-validation/7725371/statistical-properties-of-the-population-stability-index>
- Moreno-Torres, J.G., Raeder, T., Alaiz-Rodríguez, R., Chawla, N.V. & Herrera,
  F. (2012). "A unifying view on dataset shift in classification." *Pattern
  Recognition* 45(1), 521–530.
  <https://doi.org/10.1016/j.patcog.2011.06.019>
- NumPy `numpy.histogram` reference — out-of-range values are ignored; the last
  bin is right-inclusive.
  <https://numpy.org/doc/stable/reference/generated/numpy.histogram.html>
- Board of Governors of the Federal Reserve System (2026). *Revised Guidance on
  Model Risk Management*, SR 26-2, 17 April 2026 - supersedes SR 11-7 (2011)
  and SR 21-8 (2021). Applies to Fed-regulated banking organizations with over
  $30 billion in total assets.
  <https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm>
- Office of the Comptroller of the Currency (2026). *Model Risk Management:
  Revised Guidance*, OCC Bulletin 2026-13.
  <https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html>
