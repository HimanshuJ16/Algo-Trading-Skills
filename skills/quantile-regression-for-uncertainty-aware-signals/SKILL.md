---
name: quantile-regression-for-uncertainty-aware-signals
description: >-
  Use when position sizing needs a signal that says how sure it is; predicts conditional
  return quantiles rather than a point forecast, so a confident and a guessing
  prediction are not sized identically.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, quantile-regression, uncertainty-estimation, pinball-loss, confidence-scaling, position-sizing
  brokers_frameworks: "Quantile Regression Signal Engine; Python NumPy"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a signal feeding position sizing needs to say *how sure it is*, not just which way to lean. A point-forecast model ($E[Y|X]$) returns one number and no dispersion, so every forecast is sized identically whether the model is confident or guessing. Quantile regression instead fits a lower ($\tau=0.10$), central ($\tau=0.50$), and upper ($\tau=0.90$) conditional quantile by minimising Pinball Loss, and the outer spread $\hat q_{0.90} - \hat q_{0.10}$ becomes the model's own uncertainty statement: wide band, smaller position.

Use it also when the *shape* of the return distribution matters and not only its centre — asymmetric downside, heteroscedasticity that varies with a feature (volatility, spread, time of day), or a tail estimate you intend to size against.

## When NOT to Use

- **As a risk control.** The multiplier is a signal-to-uncertainty ratio, not a Kelly fraction, not a risk budget, and not a stop. It must sit *behind* independent, non-bypassable pre-trade limits — see `kill-switch-and-drawdown-circuit-breakers`.
- **When the band has not been coverage-checked out-of-sample.** An uncalibrated band is worse than no band: if $\hat q_{0.90}$ actually covers 60% of outcomes, the model is confidently wrong and the sizer *scales up* on exactly the forecasts it understands least. Run `calibration_report` on held-out data before sizing on anything.
- **On a constant or near-constant target.** Every quantile of a constant is that constant; there is no band to estimate. The engine raises rather than manufacturing one.
- **As a substitute for a lag-correct dataset.** Nothing here detects a target that overlaps its own feature window; a leaky target produces a spuriously narrow band and therefore a *larger* position. See `feature-engineering-without-leakage` and `lookahead-bias-elimination`.
- **Where non-linear quantile structure dominates.** This engine is linear in the features supplied. For production-scale non-linear fits, use a gradient-boosted quantile objective or a linear-programming quantile solver; what this skill contributes is the surrounding discipline — crossing repair, coverage measurement, and refusing to size a degenerate band.
- **With a single fit held across regimes.** Refit walk-forward (`walk-forward-validation-setup`); a band fitted in a calm regime understates risk in a volatile one, silently.

## Prerequisites

- Feature matrix $X$ and target $y$, where every $y_t$ is realised **strictly after** all of row $t$'s features were observable.
- Three strictly increasing quantile levels in $(0,1)$ — default $\tau \in \{0.10, 0.50, 0.90\}$. The central level supplies direction; the outer pair defines the band.
- A **held-out** sample for coverage measurement, disjoint from the fitting sample.
- `min_uncertainty_width` calibrated to the target's units (the default `1e-4` is 1 bp of return; it is meaningless against price-scale targets).
- Independent pre-trade risk limits already in place downstream.

## Workflow

1. **Evaluate Pinball (Quantile) Loss** — the asymmetric piecewise-linear loss whose minimiser is the conditional $\tau$-quantile:
   $$L_{\tau}(y, \hat{y}) = \begin{cases} \tau (y - \hat{y}) & \text{if } y \ge \hat{y} \\ (1 - \tau) (\hat{y} - y) & \text{if } y < \hat{y} \end{cases}$$
   - **Decision point — do not substitute MSE.** Squared error is consistent for the *mean* and for no quantile (Gneiting 2011). An MSE-trained model cannot be turned into a tail estimator by any amount of thresholding.
   - At $\tau = 0.5$ the loss is *half* the absolute error, so a mean pinball loss at the median is MAE/2. Do not compare it against a raw MAE figure.

2. **Train Multi-Quantile Model** — fit one parameter vector $W_{\tau}$ (and intercept) per level.
   - **Decision point — an intercept is mandatory, not optional.** Without one, all three quantile lines are forced through the origin, so the model cannot represent a location-shift family $y = f(x) + \varepsilon$ at all, and the band it reports is an artefact of that constraint. In this engine the intercept is always fitted and warm-started at the *marginal* quantile of the training targets.
   - **Decision point — a constant step size does not converge here.** The pinball subgradient has magnitude $\tau$ or $1-\tau$ and never shrinks as the fit improves, so a fixed step leaves a permanent $O(\eta)$ oscillation that lands directly in the band width. Use the Robbins–Monro schedule $\eta_t = \eta_0 \sigma_y / (1+t)^p$ with $p \in (0.5, 1]$, and deploy the Polyak–Ruppert average of the final iterates rather than the last one.
   - Features are standardised from **training-fold statistics only**; a scaler fitted on the full sample leaks the evaluation distribution into the model.

3. **Repair Quantile Crossing** — sort the three predictions (monotone rearrangement, Chernozhukov et al. 2010), which is provably weakly closer to the true quantile curve than the crossed estimate.
   - **Decision point — a repaired crossing is a diagnostic, not just a fix.** Frequent crossings mean the three independent fits disagree about ordering, i.e. the fit is under-converged or the sample is too small. Check `crossing_repaired`; do not treat repair as a licence to stop investigating.

4. **Verify Calibration Before Sizing** — measure empirical coverage per level on held-out data. A correctly fitted $\tau$-quantile puts a fraction $\tau$ of realised targets at or below it.
   - **Decision point — sharpness only counts once coverage is right** (Gneiting, Balabdaoui & Raftery 2007). If coverage is off, increase `epochs` or data, or re-examine the features; do not size on it.

5. **Compute the Uncertainty Band**:
   $$\text{UncertaintyWidth} = \hat{q}_{0.90} - \hat{q}_{0.10}$$
   - **Decision point — a band at or below `min_uncertainty_width` is the absence of a measurement, not maximum confidence.** The sizer returns $0$ and sets `uncertainty_floor_binding`. Report the *measured* width, never the floor.

6. **Scale Position Size by Confidence**:
   $$\text{SizeMultiplier} = \operatorname{sign}(\hat{q}_{0.50}) \cdot \min\!\left(\text{MaxSize},\ \frac{|\hat{q}_{0.50}|}{\text{UncertaintyWidth}}\right)$$
   $$\text{SizeMultiplier} = 0 \quad \text{if UncertaintyWidth} \le \text{MinWidth}$$
   - **Decision point — check whether the input is in-domain.** Because a linear model's median forecast *and* its band both scale with the features, their ratio stays roughly constant however far outside the fitting range the input goes — so a nonsensical feature value produces a *capped-out* position that looks like maximum conviction rather than an obviously broken number. `is_extrapolating` and `max_feature_zscore` surface it; the size itself never will.
   - **Decision point — check whether the band straddles zero.** When $\hat q_{0.10} < 0 < \hat q_{0.90}$ the band does not support the sign of the trade at that confidence level, even though the ratio still yields a signed multiplier. `interval_straddles_zero` and the `sized_direction_unsupported_by_band` status surface it; whether it disqualifies the trade is a risk-policy decision for the caller, not the model.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Floored band width becoming maximum leverage**: computing `width = max(epsilon, q90 - q10)` and then dividing by it converts a degenerate model — untrained, collapsed, or fed the wrong feature count — into the *largest* permitted position, because $|q_{50}|/\epsilon$ saturates any cap. A collapsed band must zero the size, not maximise it.
- **NaN silently biasing every quantile**: the quantile update branches on $y - \hat y \ge 0$, and every comparison against NaN is False, so a NaN target quietly takes the "below the prediction" branch and drags all three quantiles down. The model then emits a well-formed, confidently *signed* signal built entirely from corrupt data. Reject non-finite inputs at the boundary; there is no NaN to notice downstream.
- **Quantile Crossing**: predicting $\hat{q}_{0.10} > \hat{q}_{0.90}$ from unconstrained separate fits. Enforce non-crossing by rearrangement — and treat a high crossing rate as evidence of under-convergence.
- **Using MSE Loss for Extreme Quantiles**: MSE fits the conditional mean and is consistent for no quantile, so it cannot estimate a tail at any threshold.
- **Mistaking a narrow band for skill**: sharpness without calibration is over-confidence. A model whose 80% band covers 55% of outcomes will size hardest precisely where it is most wrong.
- **Omitting the intercept**: quantile lines through the origin cannot represent a location shift, so the reported dispersion measures the constraint rather than the data.
- **Constant step size on a non-smooth loss**: the subgradient magnitude never decays, so more data does not improve the estimate — it just re-randomises the oscillation, and the leftover step-size noise is reported as uncertainty.
- **Standardising or warm-starting on the full sample**: scaler statistics and marginal quantiles taken across train *and* test leak the evaluation distribution into the fit, flattering both coverage and sharpness.
- **Silent feature-count truncation**: `zip`-based dot products drop surplus features without error, so a caller who adds a feature and forgets to bump `num_features` gets confident predictions that ignore it.
- **Extrapolating without noticing**: feed a feature 100 sigma outside the fitting range and the confidence ratio does not blow up — it saturates the cap, so the sizer reports maximum conviction on an input the model has never seen anything like. The magnitudes give no warning; only an explicit domain check does.
- **Treating the multiplier as a bet size**: $|q_{50}| / \text{width}$ is unitless and unbounded above; it is a relative confidence weight, and it must be capped and then constrained again by independent exposure limits.

## Verification

- Recover known quantiles: fit an intercept-only model (`num_features=0`) on the integers $0 \ldots 100$ and confirm $\hat q_{0.10}, \hat q_{0.50}, \hat q_{0.90} \approx 10, 50, 90$ — the exact order statistics.
- Location-shift band: on $y = 5 + x + N(0,1)$ confirm the fitted width $\approx z_{0.90} - z_{0.10} = 2.5631$ at every $x$, and the central forecast $\approx 7.0$ at $x=2$. An intercept-free model inflates this width by roughly $130\%$ on the same data.
- Heteroscedastic band: on $y = 2x + N(0, 0.5x)$ confirm the width scales linearly with $x$ — $\approx 0.5\,x\,(z_{0.90} - z_{0.10})$ — and that the width at $x=3$ exceeds twice the width at $x=1$.
- Scale invariance: repeat on return-scale targets ($\sigma \approx 0.012$) and confirm the band is recovered with the default `learning_rate` unchanged, since the step is a multiple of the target's interquartile range.
- Out-of-sample coverage: `calibration_report` on a disjoint sample must return empirical coverage within a couple of points of $0.10 / 0.50 / 0.90$, and the conditional model's pinball loss must beat an intercept-only model's at every level.
- Pinball loss against published values: `mean_pinball_loss([1,2,3],[0,2,3], 0.1)` and `mean_pinball_loss([1,2,3],[1,2,4], 0.9)` both equal $1/30$ (scikit-learn's documented examples); at $\tau=0.5$ the loss equals half the absolute error.
- Negative checks: a non-finite feature or target, the wrong feature count, `predict` or `train_sample` before `fit`, a non-positive `max_position_size`, a non-increasing or non-triple quantile set, an empty dataset, and a constant target must each raise — and a rejected observation must leave the coefficients untouched.
- Safety checks: a degenerate band must yield `confidence_scaled_size == 0.0` with `uncertainty_floor_binding` true, and the reported `uncertainty_width` must be the measured value, never the floor.
- Domain checks: a feature far outside the fitting range must set `is_extrapolating` with `max_feature_zscore` above the limit and status `sized_outside_training_feature_range` — while `confidence_scaled_size` sits at the cap, which is precisely why the flag is needed. A degenerate band takes precedence over extrapolation in `status_message`.
- Determinism: two fits on identical data with the same `seed` must produce identical coefficients, without disturbing global RNG state.
- Run `python -m unittest discover -s skills/quantile-regression-for-uncertainty-aware-signals/scripts` and confirm 100% pass rate.

## Related Skills

- `feature-engineering-without-leakage`
- `walk-forward-validation-setup`
- `correlation-aware-exposure-limits`
- `dynamic-position-sizing-based-on-realized-volatility`
- `explainable-boosting-machines-for-regulated-signals`
- `model-staleness-detection`
- `kill-switch-and-drawdown-circuit-breakers`
