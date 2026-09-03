# Pre-Flight / Sign-off Checklist — quantile-regression-for-uncertainty-aware-signals

## Input data
- [ ] Every target is a return realised **strictly after** all of its row's features were observable (no look-ahead).
- [ ] Fitting and evaluation samples are **disjoint**; the feature scaler and the intercept warm start are derived from the fitting sample only.
- [ ] Non-finite features and targets (`NaN`/`inf`) are rejected before training, not silently absorbed into the "below prediction" branch of the update.
- [ ] Feature count matches `num_features` exactly — surplus features are an error, not something to truncate away.
- [ ] Targets have genuine dispersion; a constant target is refused rather than fitted.

## Estimation
- [ ] Pinball loss (`pinball_loss` / `mean_pinball_loss`) implemented for all three quantiles, and verified against independently published values.
- [ ] Quantile levels are exactly three, strictly increasing, in $(0,1)$.
- [ ] An intercept is fitted for every quantile and warm-started at the marginal quantile of the training targets.
- [ ] Step sizes decay on a Robbins–Monro schedule, $p \in (0.5, 1]$ — a constant step does not converge on this loss.
- [ ] Deployed coefficients are the Polyak–Ruppert tail average, not the final iterate.
- [ ] `epochs` chosen against measured held-out coverage, not assumed (small samples need several passes).
- [ ] Fit is reproducible: `seed` recorded alongside the hyperparameters, and global RNG state untouched.

## Calibration (gate before sizing)
- [ ] `calibration_report` run on a **disjoint** sample; empirical coverage is close to $0.10 / 0.50 / 0.90$.
- [ ] The conditional model beats an intercept-only baseline on mean pinball loss at every level.
- [ ] Sharpness is only claimed once coverage is right — a narrow band with wrong coverage is over-confidence, not precision.
- [ ] `crossing_repaired` monitored; a high crossing rate is treated as evidence of under-convergence, not as a solved problem.
- [ ] Coverage is understood to be **marginal**; regime-specific coverage checked separately where regimes matter.

## Sizing
- [ ] Quantile monotonicity enforced by rearrangement before the band is computed.
- [ ] Position size scaled by the measured band: $\operatorname{sign}(q_{0.50}) \cdot \min(\text{MaxSize}, |q_{0.50}| / w)$.
- [ ] A band at or below `min_uncertainty_width` yields size $0$ — **never** the maximum position via a floored divisor.
- [ ] `min_uncertainty_width` calibrated to the target's units (the $10^{-4}$ default is 1 bp of return, meaningless for price-scale targets).
- [ ] Reported `uncertainty_width` is the measurement, never the floor.
- [ ] `is_extrapolating` / `max_feature_zscore` checked on every prediction, and `extrapolation_z_limit` calibrated to the feature set — an out-of-domain input saturates the cap rather than looking broken.
- [ ] `interval_straddles_zero` surfaced, and the policy for a band that does not support the trade's sign is decided and documented.
- [ ] `max_position_size` is positive and finite, and the cap is known to bind or not bind deliberately.
- [ ] `predict` is never reachable on an unfitted model.

## Scope
- [ ] The multiplier is treated as a relative confidence weight, not a Kelly fraction or a risk budget.
- [ ] Independent, non-bypassable pre-trade exposure limits and a drawdown/kill-switch control exist downstream.
- [ ] Walk-forward refitting scheduled; a single fit is not carried across regimes.
- [ ] Defaults (quantile levels, learning rate, decay, averaging tail, width floor) calibrated and the rationale recorded — they are library defaults, not industry standards.

## Testing
- [ ] Negative checks covered: non-finite input, wrong feature count, unfitted `predict`/`train_sample`, non-positive `max_position_size`, non-positive `extrapolation_z_limit`, invalid quantile triples, empty dataset, constant target, diverged (non-finite) fit.
- [ ] Automated Testing: Run `python -m unittest discover -s skills/quantile-regression-for-uncertainty-aware-signals/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
