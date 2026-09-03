# Tail Correlation Audit Checklist

## Data integrity

- [ ] Both series joined on a shared timestamp index; overlap counted, not assumed from equal lengths.
- [ ] Rows dropped by alignment/NaN removal reviewed — a large drop means a pipeline problem, not a tail finding.
- [ ] No `±inf`, non-numeric values, duplicate index labels, or flat/stale series.
- [ ] At least `min_observations` (default 20) aligned rows — and enough history that the **joint tail** can be populated ($\approx \alpha^2 n$ observations under independence).

## Estimation

- [ ] Unconditional Pearson correlation computed on the full overlap.
- [ ] Marginal $\alpha$-quantiles computed per series (default $\alpha = 0.10$).
- [ ] Exceedance correlation conditioned on the **intersection** of the two lower tails, never the union.
- [ ] `joint_tail_observations` at or above `min_tail_observations`; otherwise the pair is recorded as **indeterminate**.
- [ ] $\hat\chi(\alpha)$ read against its independence baseline of $\alpha$ — not against zero.

## Interpretation

- [ ] Breakdown judged on `tail_correlation_excess` and `benchmark_pvalue` against the Gaussian-copula null, **not** on `tail_correlation_delta`.
- [ ] Every `is_determinate=False` pair triaged as unmeasured, not as diversifying.
- [ ] Negative tail comovement recorded as a diversification benefit, not a breach.
- [ ] `lower_tail_matrix` not passed to an optimizer without a PSD repair step.
- [ ] Thresholds confirmed as calibrated internal policy, not treated as a standard or a regulatory requirement.

## Verification

- [ ] `python -m unittest discover -s skills/tail-correlation-between-strategies-under-stress/scripts` — all tests pass.
- [ ] `benchmark_seed` fixed and recorded alongside any result used in an allocation decision.
