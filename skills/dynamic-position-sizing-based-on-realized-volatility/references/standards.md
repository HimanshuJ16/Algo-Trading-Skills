# Standards — dynamic-position-sizing-based-on-realized-volatility

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry standards. No regulator or standards
body publishes a mandatory volatility target or leverage cap; the right values depend on
the strategy's mandate, the instrument's liquidity, and the firm's risk appetite.
Calibrate each against your own drawdown tolerance and record the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| Target Annualized Volatility ($\sigma_{\text{target}}$) | $15.0\%$ | Numerator of the scalar. Sets the risk budget the sizer aims to hold constant. |
| Max Leverage Scalar (`MaxScalar`) | $2.0\times$ | Hard cap on scale-up in quiet regimes. With the defaults below this is the binding constraint, not the floor. |
| Min Downside Scalar (`MinScalar`) | $0.20\times$ | Hard floor on scale-down, preventing a volatility spike from zeroing the allocation. |
| Volatility Floor ($\sigma_{\text{floor}}$) | $5.0\%$ | Substituted for realized volatility **in the scalar denominator only**. Bounds the raw scalar at $\sigma_{\text{target}}/\sigma_{\text{floor}} = 3.0$; because `MaxScalar` is $2.0$, the cap binds first. It is a leverage brake, not a volatility measurement, and is never reported as realized volatility. |
| Rolling minimum observations | $20$ | Minimum sample for the rolling estimator. |
| EWMA tolerance | $1\%$ | Feeds the effective-window requirement below. |

## Estimator facts (verified against the primary source)

Source: J.P. Morgan/Reuters, **RiskMetrics™ — Technical Document, Fourth Edition (1996)**
([MSCI archive copy](https://www.msci.com/documents/10199/5915b101-4206-4ba0-aee2-3449d5c7e95a)).

| Fact | Location |
|---|---|
| EWMA variance recursion $\sigma_t^2 = \lambda\sigma_{t-1}^2 + (1-\lambda) r_{t-1}^2$ | Eq. 5.3 and Eq. 5.37 |
| Optimal decay factors: $\lambda = 0.94$ for the daily data set, $\lambda = 0.97$ for the monthly data set | Sec. 5.3.2, p. 100 |
| Returns are centred on zero rather than on the sample mean when computing variance and covariance | Sec. 5.3.1.2, p. 93 |
| Effective number of observations $K = \ln(\text{tolerance}) / \ln(\lambda)$ | Eq. 5.26 |
| Effective observations at a 1% tolerance: $\lambda=0.94 \Rightarrow 74$ days; $\lambda=0.97 \Rightarrow 151$ days. At $\lambda=0.94$: 112 days at 0.1%, 149 at 0.01%, 186 at 0.001% | Table 5.7, Chart 5.8 |
| Horizon scaling uses the square-root-of-time rule | Sec. 1.2, p. 13 |

`required_ewma_observations()` implements Eq. 5.26 and is unit-tested to reproduce every
row of Table 5.7 at the 1% tolerance level, and the full $\lambda = 0.94$ row across all
four published tolerances.

## Known limitations of volatility targeting

- The scalar is **backward-looking**. It stabilises forecast volatility, not realized
  outcomes, and provides no protection against gaps or jumps.
- The two estimators shipped here embed **different mean assumptions** (EWMA: zero mean,
  per RiskMetrics; rolling: sample mean with the $(n-1)$ correction) and will not agree
  on identical data.
- Sizing is **per-asset**. Correlation is not modelled, so summing independently
  vol-targeted positions does not yield a vol-targeted portfolio.

## Category

`risk-management`
