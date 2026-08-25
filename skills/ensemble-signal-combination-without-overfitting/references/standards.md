# Financial ML Standards — ensemble-signal-combination-without-overfitting

| Weighting Method | Constraint / Regularization | Target Required | Primary Strength | Primary Weakness |
|---|---|---|---|---|
| Equal Weighting (1/N) | Zero free parameters | No | Robust baseline, zero estimation error | Ignores that some sub-models are better than others |
| Inverse Forecast-Error Variance | $w_i \propto 1/\hat{\sigma}_i^2$ where $\hat{\sigma}_i^2$ is model $i$'s MSE | Yes | Downweights inaccurate sub-models | Marginal criterion: ignores correlation between sub-models |
| Shrunk NNLS | $w_i \ge 0$, Tikhonov ridge, $1/N$ shrinkage ($\lambda = 0.5$), cap $\le 0.40$ | Yes | Joint fit; prevents negative bets; blends toward robustness | Needs enough history; unstable under multicollinearity without damping |

Note: "inverse variance" here means inverse *forecast-error* variance. Weighting by
the variance of the standardized signal itself is degenerate — a $Z$-scored series has
unit variance by construction, so such a scheme silently returns $1/N$.

## Sources

These are the published results the method choices above rest on. All were checked
against the primary bibliographic record; none is a regulatory requirement — this
skill's subject matter is quantitative methodology, not compliance.

| Claim | Source | Status |
|---|---|---|
| Combining forecasts by inverse mean-squared-prediction-error weights, $w_i = \hat{\sigma}_i^{-2} / \sum_j \hat{\sigma}_j^{-2}$, ignoring cross-model correlation | Bates, J. M. & Granger, C. W. J. (1969), "The Combination of Forecasts", *Journal of the Operational Research Society* 20(4), 451–468 | Verified; the canonical definition of the inverse-variance combination used here |
| Imposing non-negativity on estimated weights reduces estimation risk and acts as implicit shrinkage, even when the constraint is "wrong" | Jagannathan, R. & Ma, T. (2003), "Risk Reduction in Large Portfolios: Why Imposing the Wrong Constraints Helps", *Journal of Finance* 58(4), 1651–1684. https://doi.org/10.1111/1540-6261.00580 | Verified; stated for portfolio weights, applied here by analogy to signal-combination weights |
| The naive $1/N$ rule is hard to beat out of sample because estimation error in optimized weights outweighs the theoretical optimality gain | DeMiguel, V., Garlappi, L. & Uppal, R. (2009), "Optimal Versus Naive Diversification: How Inefficient is the 1/N Portfolio Strategy?", *Review of Financial Studies* 22(5), 1915–1953. https://doi.org/10.1093/rfs/hhm075 | Verified; the rationale for shrinking toward $1/N$ rather than using the raw fit |
| Non-negative least squares solved exactly by a finitely-terminating active-set algorithm | Lawson, C. L. & Hanson, R. J. (1974), *Solving Least Squares Problems*, Prentice-Hall, Chapter 23, p. 161 (reissued SIAM Classics in Applied Mathematics, 1995) | Verified as the standard algorithm; it is the same method `scipy.optimize.nnls` implements, which the reference implementation was cross-checked against |

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

No jurisdiction-specific rule governs how alpha signals are blended. Where this skill
touches regulated surface, it does so indirectly: model documentation and change
control (see `model-card-documentation-for-trading-models`), and the general
requirement under regimes such as MiFID II RTS 6 and SEC Rule 15c3-5 that trading
logic be tested and its risk controls independent of the strategy. The weight cap here
is a *diversification* control, not a risk limit — position-level exposure limits must
be enforced separately (`correlation-aware-exposure-limits`).
