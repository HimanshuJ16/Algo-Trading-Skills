# Standards — strategy-performance-attribution-vs-market-beta

## Definitions as implemented (verified against the cited source)

| Quantity | Definition as implemented | Source |
|---|---|---|
| Jensen's alpha | Intercept of $R_{j,t}-R_{F,t}=\alpha_j+\beta_j(R_{M,t}-R_{F,t})+u_{j,t}$ | Jensen (1968), eq. 8 — below |
| Fama-French 3-factor alpha | Intercept of $R_{s,t}-R_{f,t}=\alpha+\beta_M(R_{M,t}-R_{f,t})+\beta_S\mathrm{SMB}_t+\beta_H\mathrm{HML}_t+\varepsilon_t$ | Fama & French (1993) — below |
| SMB / HML treatment | Entered **raw**; the risk-free rate is never subtracted from them | Ken French data library construction — below |
| Periodic risk-free rate | $(1+R_{f,\text{annual}})^{1/P}-1$, geometric | Matches French's RF construction — below |
| Annualized alpha | $\alpha \times P \times 100$, arithmetic | Convention; see limitations |
| Factor contribution | $\beta_i \times \bar{x_i} \times P \times 100$, where $\bar{x_i}$ is the mean of the regressor **as it entered the regression** | Identity below |
| Return decomposition | $\overline{R_s} = R_{f,\text{period}} + \alpha + \sum_i \beta_i \bar{x_i}$, exact for OLS with an intercept | Identity below |
| $R^2$ | $1 - SS_{res}/SS_{tot}$; adjusted $R^2 = 1-(1-R^2)(n-1)/(n-k)$ | Standard OLS |
| OLS coefficient covariance | $\hat{\sigma}^2 (X'X)^{-1}$, $\hat{\sigma}^2 = SS_{res}/(n-k)$ | Standard OLS |
| HAC coefficient covariance | $(X'X)^{-1}\hat{S}(X'X)^{-1}\cdot n/(n-k)$ with Bartlett weights $1-l/(L+1)$ | Newey & West — below |
| Default HAC lag | $L=\lfloor 4(n/100)^{2/9}\rfloor$ | Newey & West (1994) — below |
| Significance test | $\lvert t\rvert \ge$ two-sided Student-$t$ critical value at `significance_level` with $n-k$ df | Jensen (1968) — below |

$P$ is `periods_per_year`; $n$ aligned observations; $k$ regressors including the intercept.

## Primary sources

**Jensen's alpha** — Michael C. Jensen, "The Performance of Mutual Funds in the Period
1945–1964", *Journal of Finance* 23(2), 1968, pp. 389–416
([author's manuscript](https://www.efalken.com/LowVolClassics/Jensen1967.pdf)).

- Eq. (8) is the estimating equation: the excess return of the portfolio regressed on
  the excess return of the market, with an unconstrained intercept. Jensen: "we allow
  for the possible existence of a non-zero constant in eq. (7) by using (8) as the
  estimating equation."
- On the intercept: "if the portfolio manager has an ability to forecast security
  prices, the intercept, $\alpha_j$, in eq. (8) will be positive. Indeed, it represents
  the average incremental rate of return on the portfolio per unit time which is due
  solely to the manager's ability to forecast future security prices." A naive
  buy-and-hold policy "can be expected to yield a zero intercept."
- **On inference** — the basis for using Student-$t$ rather than the normal 1.96:
  "the sampling distribution of the estimate, $\hat{\alpha}_j$, is a student $t$
  distribution with $n-2$ degrees of freedom." This engine generalizes to $n-k$ df for
  the multi-factor case.
- **On the residual assumption** — the basis for offering HAC standard errors: Jensen's
  footnote 12 states the error term "should be serially independent", reasoning that
  a manager who saw serial dependence would trade it away. Realized strategy residuals
  routinely violate this.
- **On return units** — the basis for the documented approximation: "the linear
  relationships of eqs. (1a) and (2) hold for any length time interval as long as the
  returns are measured as continuously compounded rates of return." Jensen's own fund
  and market returns are log returns. This engine regresses **simple** periodic
  returns, the near-universal practice, which is an approximation that is accurate for
  small per-period returns.
- Jensen also cautions that if the residuals are not normally distributed "the
  estimates of the parameters will not be distributed according to the student $t$
  distribution" — the $p$-values reported here inherit that caveat.

**Fama-French three-factor model** — Eugene F. Fama & Kenneth R. French, "Common Risk
Factors in the Returns on Stocks and Bonds", *Journal of Financial Economics* 33(1),
1993, pp. 3–56. The time-series regression adds the size and value spreads to the
market excess return; the intercept is the three-factor alpha, the abnormal return left
after controlling for market, size and book-to-market exposure.

**Factor construction and units** — Kenneth R. French, Data Library
([factor definitions](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/f-f_factors.html)).

- `Mkt-RF` is the "value-weight return of all CRSP firms incorporated in the US and
  listed on the NYSE, AMEX, or NASDAQ … **minus the one-month Treasury bill rate**". It
  is therefore **already an excess return** — pass it with
  `market_returns_are_excess=True`.
- `SMB` = ⅓(Small Value + Small Neutral + Small Growth) − ⅓(Big Value + Big Neutral +
  Big Growth); `HML` = ½(Small Value + Big Value) − ½(Small Growth + Big Growth). Both
  are differences of portfolio returns, i.e. **zero-investment, self-financing
  spreads**. Nothing is subtracted from them.
- `RF` is "the simple daily rate that, over the number of trading days, compounds to
  1-month TBill rate" — a geometric de-annualization, which is why this engine
  de-annualizes geometrically rather than dividing by 252.
- **Units are percent, not decimals.** Verified directly against
  `F-F_Research_Data_Factors_daily.csv` (retrieved from the data library, file header
  `,Mkt-RF,SMB,HML,RF`), whose final rows read
  `20260630,    0.73,    0.10,   -0.62,    0.01`. A daily `Mkt-RF` of `0.73` is 0.73%,
  not 73%. Divide French's columns by 100 before passing them to this engine.

**Newey-West HAC standard errors** — Whitney K. Newey & Kenneth D. West, "Automatic Lag
Selection in Covariance Matrix Estimation", *Review of Economic Studies* 61(4), 1994,
pp. 631–653; the estimator itself is Newey & West, *Econometrica* 54(3), 1986.

- The heteroskedasticity- and autocorrelation-consistent "meat" is
  $\hat{S}=\hat{\Gamma}_0+\sum_{l=1}^{L}\left(1-\frac{l}{L+1}\right)\left(\hat{\Gamma}_l+\hat{\Gamma}_l'\right)$
  with $\hat{\Gamma}_l=\sum_t u_t u_{t-l} x_t x_{t-l}'$; the Bartlett weights guarantee
  a positive semi-definite estimate.
- The rule-of-thumb lag $L=\lfloor 4(n/100)^{2/9}\rfloor$ is the Newey & West (1994)
  automatic bandwidth as implemented in Stata's `newey` and R's `sandwich::NeweyWest`
  ([sandwich documentation](https://sandwich.r-forge.r-project.org/reference/NeweyWest.html)).
  It is a default, not an optimum — set `hac_lags` explicitly when the residual's
  dependence horizon is known (e.g. the overlap length of the holding period).
- The $n/(n-k)$ multiplier is the small-sample correction applied by Stata's `newey`.
- **HAC is order-dependent.** The kernel weights observations by lag distance, so the
  engine requires a monotonically increasing index before it will compute HAC standard
  errors. OLS coefficients are order-invariant and are unaffected.

## The additive decomposition

OLS with an intercept forces $\sum_t \hat{\varepsilon}_t = 0$, so the sample means
satisfy exactly

$$\overline{R_s} = R_{f,\text{period}} + \hat{\alpha} + \sum_i \hat{\beta_i}\,\bar{x_i}$$

Multiplying every term by $P \times 100$ gives the annualized attribution reported in
`factor_breakdown`, and `unexplained_residual_pct` is its closure error. That residual
is zero up to floating-point noise **by construction**; a non-zero value indicates an
implementation fault, not an economic finding.

The identity holds only when each $\bar{x_i}$ is the mean of the series that actually
entered the regression, over the same aligned rows — the market leg net of the
risk-free rate when `market_returns_are_excess=False`, SMB and HML raw.

## Known limitations

- **In-sample, backward-looking, single-window.** Alpha, betas and $R^2$ describe the
  supplied sample. They are not forecasts, and a regression on data that also selected
  the strategy carries a selection bias this engine cannot detect.
- **Simple returns, arithmetic annualization.** Both are approximations relative to
  Jensen's continuously-compounded derivation; they degrade as per-period returns grow.
- **Static betas.** One regression assumes constant exposure across the window.
- **No multiple-testing correction.** Each report is a single 5% test.
- **Normality of residuals is assumed for the $p$-values**, per Jensen's own caveat.
  Financial returns are fat-tailed; treat borderline $p$-values as borderline.
- **$R^2$ is not evidence about alpha.** It measures variance explained; alpha is a
  statement about the mean. A high $R^2$ is fully compatible with a large, significant
  alpha. No threshold on $R^2$ implies anything about the magnitude or significance of
  the intercept.
- **Equity factors for equity strategies.** SMB and HML are US equity spreads. Applying
  them to non-equity strategies produces loadings without economic content.

## Regulatory note

No regulator mandates a particular attribution model for internal strategy review, and
this engine implements none. The GIPS standards govern how firms present *composite*
performance to prospective clients and are out of scope here. Where performance figures
are shown to external investors, the presentation requirements of the relevant
jurisdiction apply independently of anything computed by this skill.

## Category

`portfolio-performance-attribution`
