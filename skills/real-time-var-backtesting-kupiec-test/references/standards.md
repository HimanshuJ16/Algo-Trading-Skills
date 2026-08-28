# Standards for Real-Time VaR Backtesting (Kupiec POF + Basel Traffic Light)

Every threshold below is traced to a primary source. Where a source does not specify a
value, that is stated rather than filled in.

## 1. Statistical test — Kupiec Proportion of Failures

| Item | Standard | Source |
|---|---|---|
| Test statistic | $LR_{\text{POF}} = -2\ln\left[\frac{(1-p)^{T-x}p^x}{(1-\hat\pi)^{T-x}\hat\pi^x}\right]$, $\hat\pi = x/T$ | Kupiec, P. (1995), "Techniques for Verifying the Accuracy of Risk Measurement Models", *Journal of Derivatives* 3(2), 73–84 |
| Null hypothesis | Unconditional coverage: true exception probability $= p = 1 - \alpha_{\text{VaR}}$ | Kupiec (1995); Campbell, FEDS 2005-21, Sec. 2 |
| Reference distribution | Asymptotically $\chi^2$ with **1** degree of freedom (one restriction) | Standard LR theory; Campbell, FEDS 2005-21, Sec. 3.1 |
| Tail | **Two-sided** — rejects both excess and deficient breach counts | Follows from the $\chi^2$ form of the LR statistic |
| p-value transform | $P(\chi^2_1 > s) = \operatorname{erfc}(\sqrt{s/2})$ — an exact identity, since $\chi^2_1$ is the square of a standard normal | — |
| Significance level | Rejection at $p\text{-value} < 0.05$ is convention, **not** a regulatory mandate. BCBS specifies zones, not a Kupiec threshold | bcbs22; MAR32.8–32.15 |
| Published check values | $T=250$: $LR_{\text{POF}} = 0.76$ at $x=4$; $12.95$ at $x=10$ | Campbell, S. (2005), "A Review of Backtesting and Backtesting Procedures", Federal Reserve FEDS 2005-21, Sec. 3.1 |
| Known power limitation | At the one-year, 8-violation threshold, a model reporting $3\%$ VaR while claiming $1\%$ is detected only ~$65\%$ of the time | Kupiec (1995), as reported in Campbell, FEDS 2005-21, Sec. 3.1 |
| Known blind spot | POF tests unconditional coverage only; it cannot detect breach clustering (the independence property) | Christoffersen, P. (1998), "Evaluating Interval Forecasts", *International Economic Review* 39, 841–862 |

## 2. Supervisory framework — Basel traffic light

Primary source: BCBS, *Supervisory framework for the use of "backtesting" in conjunction
with the internal models approach to market risk capital requirements*, January 1996
(bcbs22), <https://www.bis.org/publ/bcbs22.pdf>. Carried into the consolidated Basel
Framework at **MAR32.8–MAR32.15** (zones) and **MAR99.9–MAR99.21** (statistical
derivation), <https://www.bis.org/basel_framework/chapter/MAR/32.htm>.

| Item | Standard | Source |
|---|---|---|
| Observation window | The most recent twelve months, "approximately 250 daily observations"; MAR32.18 requires "at least one year" | bcbs22 Sec. 2; MAR32.18 |
| Coverage level | 99th percentile VaR (bank-wide zones); desk-level backtesting additionally uses the 97.5th percentile | bcbs22 Sec. 1; MAR32.18 |
| Exception definition | Actual or hypothetical loss exceeding the VaR measure; the count used is the **greater** of the two | MAR32.18(1) |
| Missing P&L or risk measure | Counts as an outlier | MAR32.18(2) |
| Zone boundaries (250 obs, 99%) | Green $0$–$4$; amber $5$–$9$; red $10$ or more | bcbs22 Table 2; MAR32.9 Table 1 |
| Boundary rule for other sample sizes | Amber begins where cumulative binomial probability $\ge 95\%$; red where it $\ge 99.99\%$ | bcbs22 Table 2 notes |
| Tail | **One-sided** — only excess breaches are penalised | Implied by the cumulative-probability rule above |
| Naming | MAR32.8 calls the middle zone **amber**; bcbs22 (1996) called it **yellow**. Same zone | MAR32.8 vs bcbs22 Sec. 3(c) |

### Cumulative probabilities and multipliers, 250 observations at 99% coverage

The cumulative probability column is bcbs22 Table 2. The multiplier column is the
**in-force** MAR32.9 Table 1 total multiplier. The 1996 "increase in scaling factor"
column is retained for historical traceability — it is an *increment* on a base scaling
factor of 3, not a total, and must not be added to the MAR32.9 figure.

| Exceptions | Zone | $P(X \le x)$ | MAR32.9 multiplier (in force) | bcbs22 (1996) increment |
|---|---|---|---|---|
| 0 | Green | 8.11% | 1.50 | 0.00 |
| 1 | Green | 28.58% | 1.50 | 0.00 |
| 2 | Green | 54.32% | 1.50 | 0.00 |
| 3 | Green | 75.81% | 1.50 | 0.00 |
| 4 | Green | 89.22% | 1.50 | 0.00 |
| 5 | Amber | 95.88% | 1.70 | 0.40 |
| 6 | Amber | 98.63% | 1.76 | 0.50 |
| 7 | Amber | 99.60% | 1.83 | 0.65 |
| 8 | Amber | 99.89% | 1.88 | 0.75 |
| 9 | Amber | 99.97% | 1.92 | 0.85 |
| 10 or more | Red | 99.99% | 2.00 | 1.00 |

**Applicability limit.** BCBS generalises the *boundaries* to other sample sizes via the
cumulative-probability rule, but publishes **no** multiplier steps for windows other
than 250 observations at 99% coverage. The implementation therefore returns `None` for
`basel_backtesting_multiplier` off that basis rather than extrapolating an unsupported
number.

**Amber zone is not automatic.** bcbs22 Sec. 3(e) and MAR32.11 place the burden of proof
on the bank: the add-on "should generally be presumed" but the bank may demonstrate it
is not warranted. MAR32.15: a red-zone result means the supervisor will automatically
increase the multiplier or may disallow the model.

## 3. Trading-desk-level backtesting (FRTB)

| Item | Standard | Source |
|---|---|---|
| Window | Most recent 12 months of the desk's one-day P&L | MAR32.18 |
| Levels tested | 97.5th **and** 99th percentile one-day VaR, calibrated to the most recent 12 months, equally weighted | MAR32.18 |
| Disqualification threshold | More than **12** exceptions at the 99th percentile **or** **30** at the 97.5th percentile in the most recent 12-month period → all desk positions move to the standardised approach | MAR32.19 |

These desk-level thresholds are flat counts, not traffic-light zones, and are **not**
implemented by this skill — it implements the bank-wide zone classification of MAR32.9.

## 4. Jurisdictional scope

The Basel framework is issued by the BCBS and has force only as transposed by each
national regulator (EU CRR/CRR3, UK PRA rulebook, US federal banking agencies' market
risk rule, etc.). Transposition dates, and in some jurisdictions the parameters, differ.
Confirm the applicable local text before relying on any threshold here for a regulatory
filing. Nothing in this skill is legal or regulatory advice.
