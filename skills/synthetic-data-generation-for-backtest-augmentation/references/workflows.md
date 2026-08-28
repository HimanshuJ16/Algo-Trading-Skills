# Workflows — synthetic-data-generation-for-backtest-augmentation

Full procedure behind the summary in `SKILL.md`. Every return series below is a
**log** return, `r_t = ln(P_t / P_{t-1})`, and every volatility is **per bar**.

## 1. Establish and characterise the empirical baseline

1. Convert prices to log returns. If the source is simple returns, convert with
   `ln(1 + r)` before anything else — the module cannot detect the mixture and
   every reported moment will be biased if you skip this.
2. Reject non-finite observations at the boundary. A single `NaN` propagates
   through every mean, standard deviation and comparison; `NaN <= tolerance` is
   `False`, so a corrupt series produces a verdict that looks like a considered
   rejection. The reference implementation raises instead.
3. Record the bar frequency, the date range, and the instrument. A synthetic
   series is only interpretable against a stated baseline.
4. Compute the baseline moments once and keep them. They are the target the
   parity report measures against.

**Ask whether the baseline is long enough to be worth augmenting.** Resampling
60 bars into 10,000 does not create information; it produces a series that looks
statistically respectable and confidence intervals that are far too narrow. If
the sample is that short, the honest finding is "insufficient data", not a
smooth synthetic distribution.

## 2. Select the generator against the question being asked

| Question | Generator |
|---|---|
| What does this result look like with no volatility structure at all? | `generate_gbm` — an IID-normal null |
| Does the strategy survive realistic volatility clustering? | `generate_garch` |
| Does the strategy survive a differently-ordered version of *this* history? | `block_bootstrap_returns` |
| How much of the result depends on serial dependence? | `bootstrap_returns` (IID) as a deliberate contrast against the block bootstrap |

Do not use `bootstrap_returns` to augment a real series. It samples returns
independently, destroying volatility clustering and the loss *runs* that produce
the deepest drawdowns, so the resulting drawdown distribution is optimistically
biased — the augmentation makes the strategy look safer than the original
backtest did.

## 3. Parameterize

**GBM.** `mu` and `sigma` are per unit of `dt`; at the default `dt = 1/252` they
are annualized. `mu` is the drift of the *price* process
(`E[S_t] = S0 exp(mu t)`) and the generator applies the `-sigma^2/2` Ito
correction itself, so passing an estimated mean **log** return as `mu`
understates the drift by `sigma^2/2`.

**GARCH.** `omega`, `alpha`, `beta`, `mu` are all per bar. `omega` carries units
of squared per-bar returns; passing an annualized variance intercept inflates
simulated volatility by roughly `sqrt(252)`. Check the implied long-run
volatility `sqrt(omega / (1 - alpha - beta))` against the baseline before
simulating — it is the single fastest way to catch a units error.

`alpha + beta >= 1` raises. At or above 1 the process has no finite
unconditional variance, so there is neither a stationary point to start the
recursion at nor a quantity for the parity report to compare against. Clamping
the denominator, as the pre-2.0 implementation did, fabricates a plausible
number for something that does not exist.

**Block length.** `DEFAULT_BLOCK_SIZE = 5` is a placeholder so the signature has
a default. Select it from the series: too short and the resample behaves like an
IID bootstrap; too long and the number of distinct resamples collapses. See
`references/standards.md` for the selection literature. Run the analysis at two
or three block lengths — if the conclusion flips between them, the conclusion is
about the block length, not the strategy.

**Seed.** Always explicit. `seed=None` logs a warning and produces a run that
cannot be re-audited or distinguished from a favourable draw that was kept.

## 4. Simulate

```python
generator = SyntheticDataGenerator(seed=20260828)

gbm_prices = generator.generate_gbm(GBMConfig(mu=0.07, sigma=0.20, S0=100.0, steps=252))

garch = generator.generate_garch(GARCHConfig(omega=1e-5, alpha=0.10, beta=0.85, steps=252))
garch_returns = garch.returns          # also unpacks as (prices, sigmas)

resampled = generator.block_bootstrap_returns(historical_returns, steps=252, block_size=20)
```

The circular block bootstrap wraps modulo `n`, so every observation appears in
exactly `block_size` blocks and is equally likely to be drawn. A non-circular
moving-block implementation draws starts from `[0, n - B]` and under-samples both
ends of the series — at `n = 20, B = 5`, the first and last observations appear
roughly a quarter as often as interior ones, which systematically under-weights
whichever end of the window contains the crash.

For an ensemble, call the generator repeatedly on **one** seeded instance: the
stream advances, so the paths are independent and the whole ensemble is
reproducible from the single seed plus the call sequence. Do not re-seed per
path with `seed + i`; nearby seeds are not a guarantee of independent streams,
and it makes the ensemble harder to reproduce, not easier.

## 5. Validate before use

```python
report = generator.validate_synthetic_path(historical_returns, synthetic_returns,
                                           vol_tolerance=0.25)
```

Read the verdict for exactly what it is. `is_statistically_consistent` tests
volatility parity and nothing else:
`|sigma_synth - sigma_hist| / sigma_hist <= vol_tolerance`. It does not gate on
the mean — unestimable to useful precision over a backtest-length sample — nor
on skewness or kurtosis, whose sampling error at a few hundred observations
would reject correct generators. All four moments are reported for **both**
series so a human can judge them.

Kurtosis is Pearson (raw) kurtosis: 3.0 for a normal sample, excess kurtosis is
that minus 3. Expect a GARCH path to exceed a Gaussian baseline's kurtosis —
mixing normals of differing variance is leptokurtic — and expect a GBM path to
sit at 3.0, because its log returns are normal by construction.

Skewness and kurtosis of `None` mean **not measurable** (a constant series, or
fewer than four observations). Render them as such; never coerce them to 0.0. A
constant *baseline* raises: there is no scale against which to measure a
relative error.

Passing this gate is necessary, not sufficient. Matching four moments says
nothing about autocorrelation, tail index, or cross-asset dependence — a shuffled
series matches every moment of the original exactly and has none of its dynamics.

## 6. Integrate into the backtest and report honestly

1. Run the strategy across the ensemble and report the **distribution** of the
   outcome, not its mean. The point of augmentation is dispersion; collapsing it
   back to a single number discards the entire result.
2. Report the worst path alongside the median. If the strategy is unacceptable
   on the worst synthetic path, that is the finding.
3. Record with the result: seed, generator, every parameter, block length,
   tolerance, source series identity and date range, and the module version.
   Under the SEC Marketing Rule this recording discipline is a compliance
   artifact for a registered adviser, not only good hygiene — see
   `references/standards.md`.
4. State the method's ceiling explicitly wherever the result is presented: the
   bootstrap cannot produce a loss larger than the worst observed bar, and GBM
   understates tail risk by construction. A robustness claim that omits this is
   overstated.
5. Never present a synthetic-path result as performance. It is hypothetical
   performance, not a track record.
