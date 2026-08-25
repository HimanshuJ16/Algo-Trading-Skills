# Standards — factor-research-multiple-testing-correction

## What kind of standard this is

**None of the thresholds below are regulatory or exchange requirements.** No regulator,
exchange or standards body mandates a t-statistic hurdle or an FDR level for factor
research. Everything here is either (a) a definition from the peer-reviewed statistics
literature, or (b) a research recommendation from Harvey, Liu and Zhu (2016). Treat the
recommendations as defaults to justify, not as compliance gates to tick.

## Primary sources

| Source | Where used |
|---|---|
| Harvey, C. R., Liu, Y. and Zhu, H. (2016), "… and the Cross-Section of Expected Returns", *Review of Financial Studies* 29(1), 5–68. [Author copy](https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF) · [Publisher](https://academic.oup.com/rfs/article/29/1/5/1843824) | Sec. 3.4 procedure definitions; Table 4 worked example; Sec. 4.5 / Fig. 3 published cutoffs; the $t \ge 3.0$ recommendation |
| Benjamini, Y. and Hochberg, Y. (1995), "Controlling the False Discovery Rate", *JRSS-B* 57(1), 289–300 | BH step-up procedure; FDR control under independence |
| Benjamini, Y. and Yekutieli, D. (2001), "The Control of the False Discovery Rate in Multiple Testing under Dependency", *Annals of Statistics* 29(4), 1165–1188. [Project Euclid](https://projecteuclid.org/journals/annals-of-statistics/volume-29/issue-4/The-control-of-the-false-discovery-rate-in-multiple-testing/10.1214/aos/1013699998.full) | Thm 1.2 (BH valid under PRDS); Thm 1.3 and $c(M)$ (valid under arbitrary dependence) |
| Holm, S. (1979), "A Simple Sequentially Rejective Multiple Test Procedure", *Scandinavian Journal of Statistics* 6(2), 65–70 | Holm step-down procedure |

## Procedure definitions (verified against HLZ Sec. 3.4)

$M$ tests, ordered p-values $p_{(1)} \le \dots \le p_{(M)}$, rank $b$, family-wise level
$\alpha_w$, FDR level $\alpha_d$, and $c(M) = \sum_{j=1}^{M} 1/j$.

| Procedure | Controls | Rejection rule | Adjusted p-value | Dependence assumption |
|---|---|---|---|---|
| Bonferroni | FWER | $p_i \le \alpha_w / M$ | $\min[M p_i, 1]$ | Any |
| Holm (1979) | FWER | reject $H_{(1)} \dots H_{(k-1)}$, $k = \min\{b: p_{(b)} > \frac{\alpha_w}{M+1-b}\}$ | $\min[\max_{j \le i} (M-j+1) p_{(j)},\ 1]$ | Any |
| Benjamini-Hochberg | FDR | reject $H_{(1)} \dots H_{(k)}$, $k = \max\{b: p_{(b)} \le \frac{b}{M}\alpha_d\}$ | $\min_{j \ge i} \min[\frac{M}{j} p_{(j)},\ 1]$ | Independence (BH 1995) or PRDS (BY 2001 Thm 1.2) |
| BHY | FDR | reject $H_{(1)} \dots H_{(k)}$, $k = \max\{b: p_{(b)} \le \frac{b}{M \cdot c(M)}\alpha_d\}$ | $\min_{j \ge i} \min[\frac{M \cdot c(M)}{j} p_{(j)},\ 1]$ | Arbitrary (BY 2001 Thm 1.3) |

The monotonicity operators — the running minimum for the step-up procedures, the
running maximum for Holm — are not cosmetic. They are what makes
`adjusted_p <= level` reproduce the procedure's own rejection rule. Without them the
adjusted p-values are non-monotone in rank and can exceed the target level for factors
the rule accepts.

### Documented deviation from HLZ

HLZ write the BHY recursion with base case $p^{BHY}_{(M)} = p_{(M)}$. That base case
holds only at $c(M) = 1$, i.e. plain BH. Left as written it produces an adjusted
p-value for the largest p-value that contradicts BHY's own rejection rule whenever
$c(M) > 1$. This implementation uses the general form
$\min[\frac{M \cdot c(M)}{M} p_{(M)},\ 1]$, which reduces to HLZ's base case exactly
when $c(M) = 1$.

## HLZ published cutoffs (Sec. 4.5, Fig. 3; $M = 316$ tested factors)

| Procedure | Level | Implied $t$ | Implied $p$ | Year |
|---|---|---|---|---|
| Single test (no correction) | 5% | 1.96 | 5% | any |
| Bonferroni | $\alpha_w = 5\%$ | 3.78 | 0.02% | 2012 |
| Bonferroni | $\alpha_w = 5\%$ | 4.00 | 0.01% | 2032 (projected) |
| Holm | $\alpha_w = 5\%$ | 3.64 | 0.03% | 2012 |
| Holm | $\alpha_w = 5\%$ | 3.29 | 0.10% | 2012, on the 113 "common" factors |
| BHY | $\alpha_d = 1\%$ | 3.39 | 0.07% | stable post-2010 |
| BHY | $\alpha_d = 5\%$ | 2.78 | 0.54% | 2012 |
| BHY | $\alpha_d = 5\%$ | 2.81 | 0.50% | 2032 (projected) |

These are calibrated on HLZ's hand-collected census of published factors. They are
benchmarks for judging a factor against the published literature, **not** substitutes
for correcting your own batch — a firm running its own 500-specification sweep has its
own $M$.

## The $t \ge 3.0$ recommendation, stated accurately

HLZ's abstract: *"A new factor needs to clear a much higher hurdle, with a t-statistic
greater than 3.0."* Their conclusion adds three qualifications that are routinely
dropped when the number is quoted:

1. **3.0 corresponds to $p = 0.27\%$** (two-sided, normal approximation).
2. **3.0 is probably too low.** Their count of 316 factors covers prominent journals
   and a small fraction of working papers; factors that were tried and failed never
   entered the count at all. *"Given that our count of 316 tested factors is surely too
   low, this means the t-statistic cutoff is likely even higher."*
3. **It is not universal.** *"Should a t-statistic of 3.0 be used for every factor
   proposed in the future? Probably not. A case can be made that a factor developed
   from first principles should have a lower threshold … Nevertheless, a t-statistic of
   2.0 is no longer appropriate — even for factors that are derived from theory."*

Separately, HLZ state the **minimum** at 5% significance after multiplicity is about
$t = 2.8$ ($p \approx 0.5\%$), from the BHY line at $\alpha_d = 5\%$.

`HLZ_RECOMMENDED_T_HURDLE = 3.0` is the library default and is configurable via
`hlz_t_threshold`. Raise it, don't lower it, unless the factor has a genuine ex-ante
economic justification — and record that justification with the audit.

## Engineering defaults (calibrate; not standards)

| Parameter | Default | What it does |
|---|---|---|
| `alpha_target` | 0.05 | Family-wise level for Bonferroni and Holm, and the raw-significance cutoff. |
| `fdr_q_target` | 0.05 | FDR level for BH and BHY. HLZ use 5% for the FWER/FDR comparison and 1% for their headline BHY cutoff. |
| `hlz_t_threshold` | 3.0 | HLZ hurdle on $\lvert t \rvert$. Independent of this batch's $M$. |
| `total_tests_conducted` | `len(factors)` | $M$. **The default is the optimistic case.** See below. |
| `P_VALUE_T_CONSISTENCY_TOLERANCE` | 0.25 | Relative slack on the t/p consistency warning, sized to absorb two-significant-figure reporting, not distributional differences. |

## $M$ is the number of tests conducted, not the number reported

This is HLZ's central methodological point and the one most easily lost in
implementation. Every threshold above scales with $M$. Unreported specifications,
discarded parameterisations and the file drawer are all tests. Defaulting $M$ to the
size of the results file corrects for the survivors only, which is close to not
correcting at all. Pass `total_tests_conducted` with the true count, or a defensible
upper bound, and record how it was derived.

| $M$ | $c(M)$ | Bonferroni cutoff at $\alpha_w = 5\%$ | BHY rank-1 cutoff at $\alpha_d = 5\%$ |
|---|---|---|---|
| 10 | 2.929 | 0.500% | 0.171% |
| 100 | 5.187 | 0.050% | 0.0096% |
| 316 | 6.335 | 0.0158% | 0.0025% |
| 1,000 | 7.486 | 0.005% | 0.00067% |

## What these corrections cannot do

- They do not detect look-ahead bias, survivorship bias, or t-statistic inflation from
  overlapping returns. A corrected t-statistic on a leaked signal is still leaked.
- FDR is a batch expectation. A 5% FDR says nothing about whether any particular
  survivor is real.
- They say nothing about economic significance, capacity, or transaction costs.

## Category

`backtesting-methodology`
