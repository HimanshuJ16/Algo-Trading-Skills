# Deep Workflow Reference — quantile-regression-for-uncertainty-aware-signals

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Assemble a lag-correct dataset.** Every target $y_t$ must be a return realised
   strictly after all of row $t$'s features were observable. Split into a fitting sample
   and a **disjoint** evaluation sample before anything else — the scaler and the
   intercept warm start are both derived from the fitting sample, and deriving either from
   the union leaks the evaluation distribution into the fit.

2. **Evaluate Pinball Loss.**
   $$L_{\tau}(y, \hat{y}) = \max\bigl(\tau(y - \hat{y}),\ (\tau - 1)(y - \hat{y})\bigr)$$
   Both branches are non-negative and the loss is zero only at $\hat y = y$. It is
   non-differentiable at $\hat y = y$; the subgradient used is
   $\partial L/\partial \hat y = -\tau$ for $y \ge \hat y$ and $1-\tau$ otherwise, taking
   $-\tau$ at equality (a valid element of $[-\tau, 1-\tau]$).

3. **Train one model per quantile level.** Independent coefficient vectors and intercepts
   for $\tau = 0.10, 0.50, 0.90$. Three implementation details are load-bearing:

   - **Standardise features from training-fold statistics only.** A single global step size
     cannot serve columns of different magnitudes; one column two orders larger dominates
     every update. A constant column is collinear with the intercept, carries no
     conditional information, and is centred to zero rather than dividing by a zero scale.
   - **Warm-start each intercept at the marginal quantile of the training targets.** A
     decaying schedule has a finite travel budget ($\sum \eta_t$ diverges, but slowly), so
     an estimator starting from zero may never reach a distant optimum. The marginal
     quantile is the exact answer when no feature is informative and a good starting point
     when they are, leaving SGD only the conditional structure to learn.
   - **Decay the step size and average the tail.** The pinball subgradient has magnitude
     $\tau$ or $1-\tau$ and never shrinks as the fit improves, so a constant step produces
     a permanent $O(\eta)$ limit cycle — and that leftover oscillation is reported as
     uncertainty. Use $\eta_t = \eta_0\,\sigma_y / (1+t)^p$ with $p \in (0.5, 1]$
     (Robbins–Monro), and deploy the Polyak–Ruppert average of the final
     `averaging_tail` fraction of iterates rather than the last, noisier one.

   The step scale $\sigma_y$ is the training targets' interquartile range, falling back to
   the configured outer-quantile spread and then the full range. A target with **no**
   dispersion is fatal, not a fallback case: every quantile of a constant is that constant,
   and scaling the step by an arbitrary number instead would let SGD push the three
   warm-started intercepts apart into a band that is purely a step-size artefact — which
   the sizer would then convert into a position.

4. **Enforce Monotonicity.** Sort the three predictions. This is the monotone
   rearrangement of Chernozhukov, Fernández-Val & Galichon (2010), which is weakly closer
   to the true quantile curve than the crossed estimate, so the repair never costs
   accuracy. Record whether it fired: a high crossing rate means the three fits disagree
   about ordering, i.e. under-convergence or too little data, and repair is not a reason to
   stop investigating.

5. **Measure coverage out-of-sample before trusting the band.** For each level, the
   fraction of realised targets at or below the prediction should be $\approx \tau$.
   Score after rearrangement, since the rearranged values are what the sizer consumes.
   Sharpness only counts once coverage is right (Gneiting, Balabdaoui & Raftery 2007): a
   narrow band with 60% coverage at $\tau = 0.90$ is an over-confident sizer, and it will
   scale positions up on exactly the forecasts the model understands least. If coverage is
   off, raise `epochs`, add data, or revisit the features — do not size on it.

6. **Compute the band and scale the position.**
   $$w = \hat q_{\tau_{\text{upper}}} - \hat q_{\tau_{\text{lower}}}$$
   $$\text{Size} = \operatorname{sign}(\hat q_{\text{central}}) \cdot \min\!\left(\text{MaxSize},\ \frac{|\hat q_{\text{central}}|}{w}\right), \qquad \text{Size} = 0 \ \text{ if } w \le w_{\min}$$

   - Report the **measured** $w$, never $w_{\min}$: conflating them overstates the
     dispersion of a collapsed model in every downstream risk report.
   - $w \le w_{\min}$ is the absence of a measurement, not maximum confidence. Dividing by
     a floored width instead — the classic formulation `width = max(eps, q90 - q10)` —
     saturates any cap and turns a degenerate model into the largest permitted position.
   - Check the input is **in-domain**. Both $\hat q_{\text{central}}$ and $w$ scale with
     the features, so their ratio stays roughly constant far outside the fitting range: a
     nonsensical feature value saturates the cap and reports maximum conviction rather
     than an obviously wrong number. `is_extrapolating` and `max_feature_zscore` are the
     only outward sign; the size is not. Flagged, not blocked — mild extrapolation is
     routine as features drift, so hard-zeroing would silently kill a live strategy.
   - $\hat q_{\text{lower}} < 0 < \hat q_{\text{upper}}$ means the band does not support
     the sign of the trade at that confidence level, even though the ratio still yields a
     signed multiplier. This is surfaced, not acted on: whether it disqualifies the trade
     is a risk-policy decision for the caller.

7. **Refit walk-forward, and keep the multiplier subordinate to hard limits.** `fit` is a
   complete refit — the semantic each walk-forward window wants. `train_sample` continues
   the decaying schedule from where `fit` stopped, so online steps refine rather than adapt
   to a regime change. The multiplier is unitless and unbounded above before capping; cap
   it, then constrain it again with independent, non-bypassable pre-trade exposure limits.

## Failure modes this procedure prevents

| Failure | Mechanism | Guard |
|---|---|---|
| Degenerate band sized at maximum | $\lvert q_{0.50}\rvert / \epsilon$ saturates any cap | Refuse to size when $w \le w_{\min}$; flag `uncertainty_floor_binding` |
| Corrupt data producing a signed signal | Every comparison against NaN is False, so the update silently takes the "below prediction" branch and drags all quantiles down | Reject non-finite features and targets at the boundary |
| Silently ignored features | `zip`-based dot products truncate surplus features without error | Exact feature-count check on every call |
| Half-updated model after a bad row | Coefficients mutated before the failing index is reached | Validate the whole dataset up front; a rejected observation leaves state untouched |
| Confident prediction from an untrained model | All-zero coefficients yield a well-formed zero prediction with a zero-width band | `predict` and `train_sample` require a prior `fit` |
| Uncertainty that never improves with data | Constant step size on a non-decaying subgradient | Robbins–Monro decay plus Polyak–Ruppert tail averaging |
| Band measuring the model's constraints, not the data | No intercept forces all quantile lines through the origin | Intercept always fitted, warm-started at the marginal quantile |
| Spurious band from a dispersion-free target | Step scale falls back to an arbitrary constant and pushes intercepts apart | Constant targets are refused outright |
| Over-confident sizing | Sharpness reported without coverage | `calibration_report` on a disjoint sample as a gate before sizing |
| Maximum position from an out-of-domain feature | Median forecast and band both scale with the features, so the ratio saturates the cap instead of blowing up | `is_extrapolating` / `max_feature_zscore` against `extrapolation_z_limit` |
| Non-finite coefficient reaching `predict` | Extreme magnitudes overflow the dot product during training, and every later comparison against the result is False | Post-fit finiteness check raises instead |

## Production Implementation Reference

- Reference code: `scripts/quantile_regression_model.py`
  (`QuantileRegressionSignalModel`, `QuantilePrediction`, `QuantileCalibration`,
  `pinball_loss`, `mean_pinball_loss`, `empirical_quantile`).
- Automated unit tests: `scripts/test_quantile_regression_model.py`.
- For non-linear quantile structure or production scale, prefer a gradient-boosted
  quantile objective or a linear-programming quantile solver; the discipline in steps
  3–6 transfers unchanged.
