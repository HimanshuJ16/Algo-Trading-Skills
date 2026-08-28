---
name: synthetic-data-generation-for-backtest-augmentation
description: >-
  Use when a backtest rests on too little history and needs additional price/return
  paths: Geometric Brownian Motion diffusion baselines, GARCH(1,1) volatility
  clustering, circular block bootstrap resampling that preserves serial dependence,
  and a moment-parity report auditing synthetic samples against the empirical
  baseline before they are allowed to influence a promotion decision.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- synthetic-data
- backtest-augmentation
- geometric-brownian-motion
- garch
- circular-block-bootstrap
- monte-carlo
brokers_frameworks:
- Synthetic Data Generation Engine
- NumPy
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a strategy's evidence base is one short price history and you need to know how much of the result is the strategy and how much is that particular path. It supplies three generators — a Geometric Brownian Motion diffusion baseline, a GARCH(1,1) path with volatility clustering, and a circular block bootstrap that resamples the empirical series while preserving short-range serial dependence — plus `validate_synthetic_path`, which compares the first four moments of a synthetic sample against its empirical baseline before anything downstream trusts it.

Use it to answer "would this strategy have survived a differently-ordered version of the same history, or a path with the same volatility dynamics but different draws?" The synthetic paths are inputs to a robustness argument, never evidence of performance.

## When NOT to Use

- **As a source of tail risk beyond the sample.** The bootstrap re-orders observed returns; it cannot produce a bar worse than the worst bar in the input. GBM's log returns are IID normal by construction — no fat tails, no clustering — so a GBM path materially *understates* tail risk. For shocks outside the sample use `scenario-based-stress-testing-custom-shocks` and `stress-testing-against-historical-crash-scenarios`.
- **To manufacture history you do not have.** A 60-bar sample block-bootstrapped to 10,000 bars still contains 60 bars of information. The synthetic series will look statistically respectable and the confidence intervals derived from it will be far too narrow.
- **As a substitute for out-of-sample testing.** Every path here is generated from, or parameterized by, the same data the strategy was fitted on. It says nothing about overfitting — that is `walk-forward-validation-setup` and `factor-research-multiple-testing-correction`.
- **For multi-asset backtests, as-is.** Each generator produces one univariate series. Running it per symbol yields independent paths and destroys the cross-sectional correlation that dominates portfolio risk. Correlated multi-asset simulation is out of scope; see `cross-asset-correlation-regime-shifts`.
- **To fit GARCH parameters.** This module simulates from parameters you supply; it performs no QMLE estimation. Estimate them with a dedicated package.
- **To advertise performance.** See the regulatory note in `references/standards.md`: performance computed on synthetic paths is *hypothetical performance* under the SEC Marketing Rule and carries specific conditions for a registered adviser.

## Prerequisites

- An empirical **log**-return series for bootstrapping or for the validation baseline — gap-free, finite, at one consistent bar frequency. Every series this module produces or consumes is a log return, `r_t = ln(P_t / P_{t-1})`; mixing simple and log returns silently biases every reported moment.
- For GBM: `mu` and `sigma` per unit of `dt`. At the default `dt = 1/252` they are annualized — pass `sigma=0.20` for 20% annualized volatility, not the per-day 0.0126.
- For GARCH: `omega`, `alpha`, `beta`, `mu` **per bar**, satisfying `alpha + beta < 1`.
- An explicit integer seed. `SyntheticDataGenerator(seed=None)` runs from OS entropy and logs a warning: an augmentation run that cannot be reproduced cannot be audited.
- A defensible volatility tolerance for the parity gate. The 0.35 default is a house heuristic, not a standard.

## Workflow

1. **Choose the generator against what you are actually testing**:
   - `generate_gbm(GBMConfig(mu, sigma, S0, dt, steps))` — an IID-normal null. Use it to establish what a result looks like when there is *no* volatility structure, not as a realistic market.
   - `generate_garch(GARCHConfig(omega, alpha, beta, mu, S0, steps))` — volatility clustering with Gaussian innovations.
   - `block_bootstrap_returns(historical_returns, steps, block_size)` — empirical, non-parametric, keeps short-range dependence.
   - **Decision point — do not reach for `bootstrap_returns` (IID) to augment a real series.** It samples individual returns independently, destroying volatility clustering and the loss *runs* that produce the deepest drawdowns. A drawdown measured on an IID resample of a dependent series is optimistically biased. It is provided only as a deliberate null for measuring how much dependence matters.

2. **Set the block length from the series, not from the default**:
   - **Decision point — `DEFAULT_BLOCK_SIZE = 5` is a placeholder, not a standard.** Too short and the resample behaves like an IID bootstrap, breaking up the dependence it exists to preserve; too long and the number of distinct blocks collapses and every path looks like the original. The bias/variance-optimal length depends on sample size and autocorrelation structure (Politis & White 2004, corrected 2009) — see `references/standards.md`.
   - `block_size=1` degenerates to the IID bootstrap and logs a warning; `block_size > len(series)` raises.

3. **Keep GARCH parameters inside the stationary region**:
   - **Decision point — `alpha + beta >= 1` raises, it is not clamped.** At or above 1 the process has no finite unconditional variance, so there is nothing for moment validation to compare against. The previous implementation floored the denominator at 0.001 and fabricated an unconditional volatility for a process that has none. Nelson (1990) shows such a process can still be *strictly* stationary (IGARCH); that case is deliberately out of scope here rather than silently approximated.
   - The recursion starts at the stationary point (`sigma_0^2 = eps_0^2 = omega / (1 - alpha - beta)`), so no burn-in period needs to be discarded.

4. **Validate before use, and read the verdict for exactly what it says**:
   - `validate_synthetic_path(historical_returns, synthetic_returns, vol_tolerance)` returns a `SyntheticValidationReport` carrying mean, per-bar volatility, skewness and Pearson kurtosis for **both** series.
   - **Decision point — `is_statistically_consistent` is a volatility-parity gate only.** It tests `|sigma_synth - sigma_hist| / sigma_hist <= vol_tolerance` and nothing else. It deliberately does not gate on the mean (unestimable to useful precision over a backtest-length sample) or on skewness/kurtosis (sampling error at a few hundred observations would reject correct generators). Judge those from the reported numbers yourself. Passing is necessary, not sufficient.
   - **Decision point — `None` moments mean "not measurable", never 0.0.** Skewness and kurtosis are `None` for a constant series or fewer than four observations. A constant *baseline* raises: there is no scale against which to measure relative error.
   - Kurtosis is **Pearson (raw)** kurtosis — 3.0 for a normal sample. Excess kurtosis is that value minus 3.

5. **Carry the parameters with the result**: seed, generator, all parameters, block length, tolerance, and the source series identity. A synthetic-path result without them is not reproducible and not auditable.

> Full procedure: see `references/workflows.md`.
> Standards, sources, and the regulatory note: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **IID resampling of a dependent series**: sampling returns independently destroys volatility clustering and serial correlation. The resulting drawdown distribution is optimistically biased, so the augmentation makes the strategy look *safer* than the original backtest.
- **A non-circular block bootstrap sold as circular**: if block starts are drawn from `[0, n - B]` rather than wrapped modulo `n`, observations near the start and end of the series appear far less often than interior ones — at `n = 20, B = 5`, roughly a quarter as often. Whichever end of the window holds the crash is then systematically under-sampled, and the resampled mean is biased. The wrap is what makes every observation equally likely.
- **Explosive GARCH parameters accepted silently**: `alpha + beta >= 1` gives a process with no finite unconditional variance. Clamping the denominator produces a plausible-looking number for a quantity that does not exist.
- **Additive epsilons in standardized moments**: dividing by `sigma^4 + 1e-9` is scale-dependent. At a daily return scale (`sigma = 0.01`, `sigma^4 = 1e-8`) the epsilon is 10% of the denominator and kurtosis reads 2.74 instead of 3.0; at an intraday scale it reads 0.003. Guard degenerate dispersion with a *relative* test, not an additive one.
- **Confusing simple and log returns**: feeding simple returns to a validator comparing against log-return synthetics biases every moment, and nothing in the report can detect it.
- **Confusing per-bar and annualized parameters**: `GARCHConfig.omega` is per-bar variance; `GBMConfig.sigma` is per unit of `dt`. Passing an annualized `omega` inflates simulated volatility by roughly `sqrt(252)`.
- **Treating moment parity as sufficient**: matching four moments says nothing about autocorrelation, tail index, or cross-asset dependence. A shuffled series matches every moment of the original exactly and has none of its dynamics.
- **Bootstrapping a short sample to a long one**: resampling 60 bars into 10,000 does not create information. Confidence intervals from such a series are far too narrow.
- **Losing the seed**: an unreproducible augmentation run cannot be re-audited, and no reviewer can distinguish it from a favourable draw that was kept.
- **Presenting synthetic-path results as performance**: they are hypothetical performance, not a track record — see `references/standards.md`.

## Verification

- **Circularity**: resample `np.arange(20)` with `block_size=5`, `steps=20`, over 3,000 draws and count occurrences per index. Every observation's relative weight must be ~1.0; the non-circular predecessor gave ~0.26 at both ends. With `block_size == n` a single draw must reproduce the whole series as a rotation, adjacent modulo `n`.
- **Moment correctness at every scale**: validate a 200,000-sample Gaussian series against itself at `sigma` = 0.20, 0.01, 0.001 and 0.0001. Kurtosis must be 3.0 ± 0.10 and skewness 0.0 ± 0.05 in all four cases. Confirm skewness and kurtosis are unchanged when the series is scaled by 1e-4.
- **GBM discretization**: reconstruct the path from the raw normals of an identically seeded stream and confirm each step equals `S_{t-1} * exp((mu - sigma^2/2) dt + sigma sqrt(dt) Z_t)`. With `sigma = 0`, `paths[-1]` must equal `S0 * exp(mu * steps * dt)`.
- **GARCH recursion**: unroll `sigma_t^2 = omega + alpha eps_{t-1}^2 + beta sigma_{t-1}^2` by hand for six steps and match. Confirm `sigmas[0] == sigmas[1] == sqrt(omega / (1 - alpha - beta))` exactly — the stationary start — and that `returns` equals `np.diff(np.log(prices))`.
- **Clustering is real**: lag-1 autocorrelation of squared GARCH returns must exceed 0.10 over 100,000 bars, while the same statistic on a GBM path stays under 0.02.
- **Rejections**: `alpha + beta >= 1`, `omega <= 0`, negative `alpha`/`beta`, `S0 <= 0`, `steps < 1`, `block_size` of 0 or greater than the series length, NaN/Inf in either series, a single-observation series, a constant historical baseline, and a negative or non-finite `vol_tolerance` must each raise `ValueError`. `block_size=0` previously spun in an unbounded loop.
- **Boundary**: with a baseline of population sd exactly 1.0, a synthetic series scaled by exactly 1.25 must pass `vol_tolerance=0.25` (the gate is `<=`) and fail at `1.25 + 1e-6`.
- Run `python test_synthetic_data_generator.py` from the `scripts/` directory and confirm 100% pass rate (41 tests).

## Related Skills

- `monte-carlo-strategy-robustness-testing`
- `walk-forward-validation-setup`
- `scenario-based-stress-testing-custom-shocks`
- `stress-testing-against-historical-crash-scenarios`
- `market-data-simulator-for-offline-development`
- `backtest-determinism-and-reproducibility`
- `survivorship-bias-free-universe-construction`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
