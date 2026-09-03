# Institutional Standards — synthetic-data-generation-for-backtest-augmentation

| Method | Definition | What it reproduces | What it cannot reproduce |
|---|---|---|---|
| Geometric Brownian Motion | $S_t = S_{t-1} \exp\!\big((\mu - \tfrac{1}{2}\sigma^2)\,dt + \sigma\sqrt{dt}\,Z_t\big)$, $Z_t \sim \mathcal{N}(0,1)$ i.i.d. | A drifting diffusion with constant volatility; an analytically tractable null | Volatility clustering, fat tails, skew, jumps. Log returns are IID normal **by construction** |
| GARCH(1,1) | $\sigma_t^2 = \omega + \alpha\varepsilon_{t-1}^2 + \beta\sigma_{t-1}^2$, $r_t = \mu + \sigma_t Z_t$ | Volatility clustering and unconditional excess kurtosis | Conditional fat tails (Gaussian innovations here), leverage/asymmetry, jumps |
| Circular block bootstrap | Resample contiguous blocks of fixed length $B$, wrapping modulo $n$ | The empirical marginal distribution and short-range serial dependence up to lag $B$ | Dependence beyond lag $B$; any loss larger than the worst observed bar |
| IID bootstrap | Resample individual observations with replacement | The empirical marginal distribution | **All** serial dependence — biases resampled drawdowns optimistically |

All volatilities produced and reported by the reference implementation are
**per bar**. Nothing in the module annualizes; multiply by $\sqrt{F}$ at the
reporting layer if you need an annualized figure.

## Moment definitions

The validation report uses the standard sample moment ratios with the
**population** divisor ($ddof = 0$), applied identically to both series:

$$\hat\sigma = \sqrt{\tfrac{1}{n}\textstyle\sum (x_i - \bar x)^2},\qquad
b_1 = \tfrac{1}{n}\textstyle\sum (x_i - \bar x)^3 / \hat\sigma^3,\qquad
b_2 = \tfrac{1}{n}\textstyle\sum (x_i - \bar x)^4 / \hat\sigma^4$$

$b_2$ is **Pearson (raw) kurtosis**: 3.0 for a normal distribution. Excess
kurtosis is $b_2 - 3$. These are the biased (method-of-moments) estimators, not
the bias-corrected $G_1$/$G_2$ that `scipy.stats.skew(bias=False)` and Excel's
`SKEW`/`KURT` return — do not compare the numbers across conventions without
converting.

Degenerate dispersion is detected **relatively**, against the series' own scale,
and the moments are then reported as `None`. An additive guard is not a valid
alternative: adding $10^{-9}$ to $\hat\sigma^4$ is 10% of the denominator at a
daily return scale ($\hat\sigma = 0.01$) and reports a Gaussian sample's kurtosis
as 2.74 instead of 3.0.

## Block-length selection — there is no standard value

`DEFAULT_BLOCK_SIZE = 5` in the reference implementation is a **placeholder so
the signature has a default**, not a recommendation and not a standard. Nothing
in the literature endorses a fixed block length for daily returns.

The trade-off is explicit: a block shorter than the dependence horizon breaks up
the very autocorrelation the method exists to preserve (bias); a block long
relative to $n$ collapses the number of distinct resamples and inflates the
variance of the estimate. The bias/variance-optimal length grows with the sample
size at a rate depending on the statistic — Hall, Horowitz & Jing (1995) give
$n^{1/3}$, $n^{1/4}$ and $n^{1/5}$ rates for bias, variance and one-sided
distribution-function estimation respectively — and on the autocorrelation
structure of the series. Politis & White (2004), corrected by Patton, Politis &
White (2009), give a data-driven selection procedure; implementations exist in
R (`blocklength`) and in the authors' MATLAB code.

**Choose the block length from the series and record the choice with the
result.** Sensitivity of the conclusion to the block length is itself a finding:
if a robustness verdict flips between $B = 5$ and $B = 20$, the verdict is about
the block length, not the strategy.

## Volatility tolerance — a house parameter

The $35\%$ relative volatility band on `is_statistically_consistent` is a
**house risk-appetite parameter**, chosen so a few-hundred-bar Gaussian resample
passes routinely. It is not published, not regulatory, and is exposed as
`vol_tolerance` precisely so it is not mistaken for a fixed rule. Calibrate it
from what the downstream backtest can actually tolerate.

Note what the gate does *not* cover. It is a volatility-parity check only. The
mean of a return series is not estimable to useful precision over a
backtest-length sample, and the sampling error of skewness and kurtosis at a few
hundred observations is large enough that gating on them would reject correct
generators. Both are reported for both series so a human can judge them.

## Stationarity of the GARCH recursion

$\alpha + \beta < 1$ is required and enforced. It is the condition for
**covariance stationarity** — the existence of a finite unconditional variance
$\omega / (1 - \alpha - \beta)$, which is both what the recursion is initialized
at and the only thing moment validation could compare against.

Nelson (1990) establishes that the condition for **strict** stationarity and
ergodicity is weaker, $E[\ln(\beta + \alpha Z^2)] < 0$, and that IGARCH(1,1)
with a positive drift is strictly stationary while having no finite
unconditional variance. Such a process is a legitimate object of study and is
deliberately **out of scope** here: with no unconditional variance there is
nothing to initialize the recursion at and nothing for the parity report to
measure. The implementation raises rather than approximating.

## Sources

Bibliographic details were verified against the Crossref record or the
publisher's own page; DOIs resolve.

| Claim | Source | Status |
|---|---|---|
| GARCH(1,1) specification; $\alpha_1 + \beta_1 < 1$ is the condition for wide-sense stationarity | Bollerslev, T. (1986), "Generalized autoregressive conditional heteroskedasticity", *Journal of Econometrics* 31(3), 307–327. https://doi.org/10.1016/0304-4076(86)90063-1 | Verified; basis for `generate_garch` and its stationarity check |
| Strict stationarity of GARCH(1,1) holds under a weaker log-moment condition; IGARCH(1,1) with positive drift is strictly stationary | Nelson, D. B. (1990), "Stationarity and Persistence in the GARCH(1,1) Model", *Econometric Theory* 6(3), 318–334. https://doi.org/10.1017/S0266466600005296 | Verified (abstract read); basis for scoping IGARCH out rather than calling it invalid |
| Resampling with replacement estimates the sampling distribution of a statistic | Efron, B. (1979), "Bootstrap Methods: Another Look at the Jackknife", *The Annals of Statistics* 7(1), 1–26. https://doi.org/10.1214/aos/1176344552 | Verified; basis for `bootstrap_returns` |
| The IID bootstrap is invalid under serial dependence; resampling contiguous blocks preserves short-range dependence (moving-block bootstrap) | Künsch, H. R. (1989), "The Jackknife and the Bootstrap for General Stationary Observations", *The Annals of Statistics* 17(3), 1217–1241. https://doi.org/10.1214/aos/1176347265 | Verified; the method the circular variant corrects the edge effect of |
| Wrapping the series into a circle before blocking equalizes each observation's drawing probability and removes the moving-block edge effect | Politis, D. N. & Romano, J. P. (1992), "A circular block-resampling procedure for stationary data", in LePage, R. & Billard, L. (eds.), *Exploring the Limits of Bootstrap*, Wiley, 263–270. | Verified as a book chapter (no DOI); basis for `block_bootstrap_returns`. The equal-weighting property is additionally demonstrated empirically in the test suite |
| Geometrically distributed block lengths yield a stationary resampled series | Politis, D. N. & Romano, J. P. (1994), "The Stationary Bootstrap", *Journal of the American Statistical Association* 89(428), 1303–1313. https://doi.org/10.1080/01621459.1994.10476870 | Verified; named as the alternative that is **not** implemented here |
| Optimal block length depends on sample size and dependence structure; rates $n^{1/3}$, $n^{1/4}$, $n^{1/5}$ by target | Hall, P., Horowitz, J. L. & Jing, B.-Y. (1995), "On blocking rules for the bootstrap with dependent data", *Biometrika* 82(3), 561–574. https://doi.org/10.1093/biomet/82.3.561 | Verified; basis for refusing to call $B = 5$ a standard |
| Data-driven automatic block-length selection | Politis, D. N. & White, H. (2004), "Automatic Block-Length Selection for the Dependent Bootstrap", *Econometric Reviews* 23(1), 53–70. https://doi.org/10.1081/ETC-120028836 — corrected by Patton, A., Politis, D. N. & White, H. (2009), *Econometric Reviews* 28(4), 372–375. https://doi.org/10.1080/07474930802459016 | Verified, including that the 2004 algorithm requires the 2009 correction; cited as the recommended procedure, not implemented here |

## Regulatory & Operational Notes

**No regulator mandates synthetic-data augmentation of a backtest, and nothing in
this skill is a compliance control.** One surface is directly relevant and worth
stating precisely, with its jurisdiction attached:

- **US — SEC Marketing Rule, 17 CFR § 275.206(4)-1.** Any performance figure
  computed on synthetic paths is **hypothetical performance**: § 275.206(4)-1(e)(8)
  defines it as "performance results that were not actually achieved by any
  portfolio of the investment adviser," expressly including "performance that is
  backtested by the application of a strategy to data from prior time periods"
  and "targeted or projected performance returns." Before an SEC-registered
  investment adviser may include such performance in an advertisement,
  § 275.206(4)-1(d)(6) requires the adviser to adopt and implement policies and
  procedures reasonably designed to ensure the performance is relevant to the
  likely financial situation and investment objectives of the intended audience,
  and to provide sufficient information for that audience to understand "the
  criteria used and assumptions made in calculating such hypothetical
  performance" and "the risks and limitations of using such hypothetical
  performance in making investment decisions."

  Two practical consequences. First, the criteria-and-assumptions condition is
  what makes the recording discipline in the workflow — seed, generator,
  parameters, block length, tolerance, source series — a compliance artifact and
  not just good hygiene. Second, this obligation attaches to **advertising by a
  registered adviser**; it does not apply to a proprietary trader, an individual,
  or an unregistered firm running the same simulation for internal research. Do
  not universalize it, and do not treat internal use of this skill as triggering
  it.
  https://www.law.cornell.edu/cfr/text/17/275.206(4)-1

- **EU/EEA — MiFID II RTS 6** (Commission Delegated Regulation (EU) 2017/589,
  19 July 2016), Article 5 ("Testing methodologies") requires an investment firm
  to establish clearly delineated methodologies to develop and test an
  algorithmic trading system, algorithm or strategy prior to its deployment or
  substantial update. Robustness testing across simulated paths is one way to
  evidence such a methodology; the article does **not** prescribe synthetic data
  or any particular simulation technique. Cite it, if at all, as context for the
  documentation requirement, not as a mandate for this method.
  https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng

Where a synthetic-path result feeds an actual risk limit, enforcement of that
limit is a separate control that must be independent of strategy and research
code (`kill-switch-and-drawdown-circuit-breakers`).
