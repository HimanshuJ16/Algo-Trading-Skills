# Pre-Flight Checklist — Synthetic Data Generation for Backtest Augmentation

Sign off before any synthetic-path result influences a promotion, sizing, or
risk-limit decision.

## Inputs

- [ ] Baseline series is in **log** returns (`ln(1 + r)` applied if the source was simple returns).
- [ ] Baseline is gap-free, finite, and at one consistent bar frequency; the frequency is recorded.
- [ ] Baseline is long enough to be worth resampling — resampling 60 bars to 10,000 creates no information and produces confidence intervals that are far too narrow.
- [ ] Instrument, date range, and vendor of the baseline are recorded with the result.

## Parameters

- [ ] GARCH parameters are covariance-stationary: $\alpha + \beta < 1$ (the engine raises otherwise; it does **not** clamp).
- [ ] GARCH $\omega, \alpha, \beta, \mu$ are **per bar**, not annualized. Implied long-run volatility $\sqrt{\omega / (1 - \alpha - \beta)}$ has been compared against the baseline as a units check.
- [ ] GBM $\mu$ and $\sigma$ are per unit of `dt` (annualized at the default `dt = 1/252`), and $\mu$ is the *price* drift — the engine applies the $-\sigma^2/2$ Ito correction itself.
- [ ] Block length was **chosen for this series**, not left at the `DEFAULT_BLOCK_SIZE = 5` placeholder, which is not a standard.
- [ ] Sensitivity to block length was checked at two or more values; if the conclusion flips, it is a conclusion about the block length, not the strategy.
- [ ] `vol_tolerance` was set from what the downstream backtest can tolerate; the 0.35 default is a house heuristic, not a standard.

## Method fit

- [ ] The IID `bootstrap_returns` was **not** used to augment a dependent series — it destroys volatility clustering and loss runs and biases drawdowns optimistically.
- [ ] The block bootstrap in use is **circular** (wraps modulo $n$). A non-circular moving-block resample under-samples both ends of the series — roughly a quarter weight at $n=20$, $B=5$ — and biases the resampled mean.
- [ ] No tail-risk or capital-at-risk claim rests on a GBM path; its log returns are IID normal by construction.
- [ ] No claim exceeds the worst observed bar — the bootstrap re-orders history, it does not extrapolate beyond it.
- [ ] Multi-asset work does not rely on per-symbol univariate paths, which are independent and destroy cross-sectional correlation.

## Validation

- [ ] Every synthetic series was passed through `validate_synthetic_path` before use.
- [ ] The verdict is read as a **volatility-parity gate only** — it does not test mean, skewness, or kurtosis parity.
- [ ] Reported skewness/kurtosis were compared **between** the two series by a human; kurtosis is Pearson (raw), 3.0 for a normal sample.
- [ ] `None` moments are rendered as "not measurable", never coerced to 0.0.
- [ ] `python test_synthetic_data_generator.py` passes from the `scripts/` directory (41 tests).

## Reproducibility and reporting

- [ ] An explicit integer seed was set; the run is not `seed=None`.
- [ ] Seed, generator, all parameters, block length, tolerance, and source series are recorded with the result.
- [ ] The reported outcome is the **distribution** across the ensemble plus the worst path — not the ensemble mean alone.
- [ ] The method's ceiling is stated wherever the result is presented.
- [ ] No synthetic-path figure is presented as performance. For an SEC-registered adviser, such figures are hypothetical performance under 17 CFR § 275.206(4)-1 and carry the conditions in § 275.206(4)-1(d)(6) — see `references/standards.md`.
