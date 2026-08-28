# Standards — quantile-regression-for-uncertainty-aware-signals

## Quantile levels and their role

| Quantile Tau | Loss Function | Purpose |
|---|---|---|
| $\tau = 0.10$ | Pinball Loss ($\tau=0.10$) | Lower bound / Tail risk estimate |
| $\tau = 0.50$ | Pinball Loss ($\tau=0.50$) | Median forecast (Signal direction) |
| $\tau = 0.90$ | Pinball Loss ($\tau=0.90$) | Upper bound / Upside potential |

Any three strictly increasing levels in $(0,1)$ are accepted; three is structural, because
the sizer needs a lower tail, a direction, and an upper tail. Nothing about the choice of
$0.10/0.50/0.90$ is mandated by any regulator or standards body — see the disclaimer below.

## Method facts (verified against primary sources)

| Fact | Source |
|---|---|
| The $\tau$-th regression quantile is the minimiser of an asymmetrically weighted sum of absolute errors — the "check" or Pinball loss — generalising the sample quantile to the linear model | Koenker, R. & Bassett, G. (1978), "Regression Quantiles", *Econometrica* **46**(1), 33–50 ([record](https://econpapers.repec.org/RePEc:ecm:emetrp:v:46:y:1978:i:1:p:33-50)) |
| A scoring function is consistent for the quantile functional iff it has the form $S(x,y) = \lvert \mathbf{1}\{x \ge y\} - \alpha \rvert \,\lvert g(x) - g(y)\rvert$ for non-decreasing $g$; the standard asymmetric piecewise-linear (Pinball) score is the case $g(t) = t$. Squared error is consistent for the **mean**, not for any quantile. | Gneiting, T. (2011), "Making and Evaluating Point Forecasts", *JASA* **106**(494), 746–762 ([preprint](https://arxiv.org/abs/0912.0902)) |
| $L_\alpha(y,\hat y) = \alpha(y-\hat y)$ if $y \ge \hat y$, else $(1-\alpha)(\hat y - y)$; equivalently $(y-\hat y)(\alpha - \mathbf{1}\{\hat y > y\})$. At $\alpha = 0.5$ the loss is proportional to absolute error (MAE/2 per observation). | scikit-learn, [`mean_pinball_loss` reference](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.mean_pinball_loss.html) |
| Independently estimated quantile curves may cross; sorting (monotone rearrangement) yields a curve that is **closer to the true quantile curve in finite samples** than the original non-monotone estimate | Chernozhukov, V., Fernández-Val, I. & Galichon, A. (2010), "Quantile and Probability Curves Without Crossing", *Econometrica* **78**(3), 1093–1125, [doi:10.3982/ECTA7880](https://doi.org/10.3982/ECTA7880) ([preprint](https://arxiv.org/abs/0704.3649)) |
| Stochastic approximation converges when the step sizes satisfy $\sum_t \eta_t = \infty$ and $\sum_t \eta_t^2 < \infty$ | Robbins, H. & Monro, S. (1951), "A Stochastic Approximation Method", *Ann. Math. Statist.* **22**(3), 400–407, [doi:10.1214/aoms/1177729586](https://doi.org/10.1214/aoms/1177729586) |
| Averaging the iterates of a stochastic approximation accelerates it and is asymptotically optimal — the basis for deploying a tail average rather than the final, noisier iterate on a non-smooth loss | Polyak, B. T. & Juditsky, A. B. (1992), "Acceleration of Stochastic Approximation by Averaging", *SIAM J. Control Optim.* **30**(4), 838–855, [doi:10.1137/0330046](https://doi.org/10.1137/0330046) |
| Probabilistic forecasts should be evaluated by **maximising sharpness subject to calibration** — a narrow interval is only informative once its coverage is correct | Gneiting, T., Balabdaoui, F. & Raftery, A. E. (2007), "Probabilistic Forecasts, Calibration and Sharpness", *JRSS-B* **69**(2), 243–268, [doi:10.1111/j.1467-9868.2007.00587.x](https://doi.org/10.1111/j.1467-9868.2007.00587.x) |

`pinball_loss` / `mean_pinball_loss` are unit-tested against scikit-learn's two published
worked examples and against hand-computed asymmetric cases. Band recovery is unit-tested
against closed-form Gaussian quantiles, and coverage against the nominal levels
out-of-sample.

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry standards. No regulator or standards
body publishes a mandatory quantile level, band-width floor, or confidence-scaling rule.

| Parameter | Default | What it actually does |
|---|---|---|
| `quantiles` | $(0.10, 0.50, 0.90)$ | An 80% central band. Wider levels give a more conservative sizer and need more data to estimate; the tails are always the least well-determined part of the fit. |
| `learning_rate` | $0.2$ | Initial step size **as a multiple of the training targets' interquartile range**, so it is unit-free and applies to return-scale and price-scale targets alike. |
| `decay_power` | $0.6$ | Robbins–Monro exponent. Any value in $(0.5, 1.0]$ satisfies both convergence conditions; $0.6$ decays slowly enough to leave a usable travel budget. $0.0$ disables decay and does **not** converge. |
| `averaging_tail` | $0.5$ | Fraction of final iterates entering the Polyak–Ruppert average. $0.0$ deploys the measurably noisier last iterate. |
| `min_uncertainty_width` | $10^{-4}$ | Narrowest band accepted as a *measurement*, in the target's units (1 bp of return). At or below it the sizer returns $0$ and flags `uncertainty_floor_binding`. **Meaningless against price-scale targets — recalibrate.** |
| `extrapolation_z_limit` | $5.0$ | Standardised-feature magnitude beyond which a prediction is **flagged** (not blocked) as out-of-domain. Financial features are fat-tailed, so 5 sigma is a diagnostic threshold; calibrate it to the feature set. |
| `epochs` (a `fit` argument) | $1$ | One pass suffices for large samples; small samples need several. Confirm the choice with `calibration_report` on held-out data rather than assuming. |

## Known limitations

- **Linear in the supplied features.** No basis expansion; non-linear quantile structure must be engineered into $X$.
- **Marginal coverage only.** `calibration_report` measures unconditional coverage over the sample given. Coverage can be right on average and badly wrong inside a specific regime — which is the case that costs money.
- **Look-ahead is the caller's responsibility.** No mechanism can detect a target overlapping its own feature window. A leaky target narrows the band and therefore *increases* the position.
- **Fitted once per call.** `fit` is a complete refit, not a continuation. Re-fit walk-forward; `train_sample` continues a decaying step schedule and so refines rather than adapts to a regime change.
- **Out-of-domain inputs saturate rather than fail.** A linear model's median forecast and its band both scale with the features, so their ratio -- and therefore the position size -- stays at the cap however far outside the fitting range the input goes. Extrapolation is reported, not blocked; acting on it is a risk-policy decision.
- **The size multiplier is a heuristic.** $\lvert q_{0.50}\rvert / \text{width}$ is a signal-to-uncertainty ratio, not a Kelly fraction and not a risk budget. It is unbounded above before capping, and must be constrained again by independent pre-trade exposure limits.

## Regulatory note

This skill encodes quantitative modelling practice, not a compliance control, and no
jurisdiction-specific requirement is asserted here. Where a firm's model-governance
regime applies to signal models, the artefacts this engine produces that are usually
relevant to it are the out-of-sample coverage report, the recorded seed and
hyperparameters of each fit, and the refusal conditions above. Consult qualified
compliance counsel for the requirements actually binding on your entity.

## Category

`financial-ml`
