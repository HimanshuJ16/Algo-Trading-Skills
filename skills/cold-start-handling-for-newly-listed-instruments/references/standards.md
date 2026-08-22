# Standards for Cold Start Handling of Newly Listed Instruments

## 0. How to read this document

Estimating volatility for a young listing is a **modelling choice, not a regulated
activity**. Nothing in sections 1-3 is a legal requirement. Section 4 records
market-structure facts that are external and verifiable, and that should inform the
length of a probation window instead of a round number; where a rule is cited, its
jurisdiction and source are named. Section 5 is this repository's engineering standard.

Every numeric default in the implementation (`warmup_period_days=30`,
`prior_strength_days=10`) is an engineering default to be calibrated, not a threshold
anyone has established.

## 1. Provenance of the shrinkage estimator

**Primary source.** A. Gelman, J. Carlin, H. Stern, D. Dunson, A. Vehtari, D. Rubin,
*Bayesian Data Analysis*, 3rd ed., chapters 2-3 (conjugate analysis of the normal
variance). <http://www.stat.columbia.edu/~gelman/book/>

With a scaled-inverse-chi-squared prior on the variance,
`sigma**2 ~ Inv-chi2(nu_0, sigma_peer**2)`, and a sample variance `s**2` carrying
`nu = n - 1` degrees of freedom, the posterior is
`Inv-chi2(nu_0 + nu, tau_1**2)` with

```
tau_1**2 = (nu_0 * sigma_peer**2 + nu * s**2) / (nu_0 + nu)
```

which is exactly the weighted average this module computes, with
`w = nu / (nu + nu_0)`. The parameterisation and update are standard; a public
statement of the same posterior parameters is at
<https://handwiki.org/wiki/Scaled_inverse_chi-squared_distribution>.

**What the module returns.** `tau_1**2`, the posterior *scale*. The posterior **mean** of
`sigma**2` is `nu_1 / (nu_1 - 2) * tau_1**2` with `nu_1 = nu_0 + nu` — larger, and
therefore more conservative for sizing. The module returns the scale; a caller wanting
`E[sigma**2]` applies the factor itself. Both are defensible; silently mixing them is not.

**Why `nu = n - 1` and not `n`.** A sample variance computed around an estimated mean has
`n - 1` degrees of freedom, so a single observation supports no variance estimate at all.
The implementation therefore assigns weight `0.0` below two observations rather than
producing a weighted zero.

**Sampling error of the sample estimator.** For i.i.d. normal returns,
`Var(s**2) / sigma**4 = 2 / nu`. This is the calibration handle for `nu_0`: a prior whose
own estimate of `sigma**2` has relative variance `v` is worth `nu_0 = 2 / v` degrees of
freedom. It is also why the sample estimator does not become trustworthy at any
particular window length — 30 observations still carry roughly 26% relative standard
error on the variance.

## 2. Variance space, not standard-deviation space

The blend is applied to variances. The naive alternative,
`w * sigma_obs + (1 - w) * sigma_peer`, is not the posterior above and is biased low:
by Jensen's inequality on the concave square root,

```
w * sigma_obs + (1 - w) * sigma_peer  <=  sqrt(w * sigma_obs**2 + (1 - w) * sigma_peer**2)
```

with equality only when the two agree. For a risk input, a systematic bias toward lower
volatility is a systematic bias toward larger positions.

## 3. Assumptions this estimator makes, and when they fail

| Assumption | Failure mode for a young listing |
|---|---|
| Returns are i.i.d. within the sample | Post-listing volatility decays; early observations over-represent price discovery. |
| The sample and the prior measure the same quantity | A sector-**ETF** prior is a diversified volatility and is structurally below single-name volatility. |
| Observations are complete | Halted sessions and the listing auction are not returns; counting them inflates `n`. |
| Volatility is the binding risk | It is not, while the name cannot be borrowed, is inside a lock-up-driven float squeeze, or trades in a handful of lots. |

## 4. Market-structure anchors for the probation window

These are facts about the instruments, not requirements on your system. They are listed
because they give a probation window a reason to be the length it is.

| Anchor | What it is | Source |
|---|---|---|
| Lock-up expiry | Insider lock-ups are contractual (underwriting agreement), **not** legally mandated, and are typically 180 days. Expiry adds float and commonly changes the volatility regime — often *after* a 30-day warmup has ended. | SEC, "Investor Bulletin: Investing in an IPO"; SEC, "Initial Public Offerings: Lockup Agreements" <https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/updated-investor-bulletin-investing-ipo> |
| Index seasoning (US) | S&P U.S. indices require a security to have traded on an eligible exchange for at least 12 months before it is eligible, with no fast-entry exception; Nasdaq-100 requires three full calendar months but has fast-entry criteria; FTSE Russell has no explicit seasoning outside its quarterly IPO additions. Verified as of 2026 against a secondary summary — check the current methodology documents before relying on a number. | Invesco, "Major US equity indexes IPO treatments" <https://www.invesco.com/apac/en/institutional/insights/etf/major-us-equity-indexes-ipo-treatments.html>; primary: S&P Dow Jones Indices *S&P U.S. Indices Methodology* |
| LULD price bands | The Limit Up-Limit Down Plan applies to NMS stocks (rights and warrants excluded). A newly listed stock is not in the S&P 500 or Russell 1000 at listing, so it is not a Tier 1 security and trades under Tier 2 parameters: 10% bands above $3.00, doubled in the last 25 minutes of the session. Trading pauses in early sessions are therefore routine and produce gaps in the return series. | LULD Plan <https://www.luldplan.com/>; SEC DERA working paper on Amendment 10 <https://www.sec.gov/files/marketstructure/research/dera_wp_the_effect_of_amendment_10_of_the_luld_plan.pdf> |
| Short-sale availability | A new listing frequently has no borrow. Volatility shrinkage says nothing about whether a short leg is executable. | See `us-reg-sho-short-sale-locate-requirements` |

The Tier 1/Tier 2 classification above is an inference from the LULD Plan's tier
definitions plus the index-seasoning rules, not a quotation from either; verify per
security if the band width matters to your logic.

## 5. Engineering standard

| Requirement | Standard |
|---|---|
| Shrinkage | Short-sample volatility MUST be blended toward a peer prior in **variance** space, with a weight derived from degrees of freedom rather than from the probation window's length. |
| Zero NaN/Inf policy | The estimator MUST NOT emit `NaN`, `Inf`, or a non-positive volatility. Inputs that would produce one MUST raise, not propagate. A missing sample MUST be handled as a branch, never as a zero weight applied to `NaN`. |
| Prior integrity | A missing, zero, or negative peer prior MUST be rejected. Zero is not a neutral fallback: it asserts a riskless instrument. |
| Observation counting | `n_obs` MUST be a count of usable observations, not a calendar difference. |
| Position scaling | The size cap MUST be monotonically non-decreasing in `n_obs` and MUST NOT exceed the configured base allocation. |
| Separation of concerns | The statistical weight and the risk-appetite ramp MUST be separate quantities. Graduation from probation MUST NOT be taken to mean the estimate is unshrunk. |
| Auditability | The output MUST record whether the instrument's own sample was used at all. |
