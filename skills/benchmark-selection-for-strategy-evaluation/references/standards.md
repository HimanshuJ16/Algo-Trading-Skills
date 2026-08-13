# Standards: Benchmark Selection

## What makes a benchmark valid

The CFA Institute performance-evaluation curriculum states that "valid benchmarks
should be unambiguous, investable, measurable, appropriate, reflective of current
investment opinions, specified in advance, and accountable."
*CFA Institute, "Portfolio Performance Evaluation" (refresher reading),
<https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/portfolio-performance-evaluation>.*

Only *appropriate* is partially measurable from a return series. **Investable,
unambiguous, measurable, specified in advance, and accountable are qualitative
properties that `BenchmarkSelector` cannot observe.** A statistical screen that
ranks a non-investable index first is not telling you it is a valid benchmark.

`specified in advance` is the property most easily destroyed by tooling: screening
20 candidates *after* the backtest and reporting against whichever produced the
best information ratio is benchmark selection bias, not benchmark selection.

## Why tracking error, not correlation, drives the ranking

Bailey's benchmark-quality tests include the criterion that a good benchmark shows
**lower variability in active returns** than a generic market-portfolio benchmark,
alongside coverage of the portfolio's holdings and similar style exposures under a
multi-factor risk model.
*Jeffery V. Bailey, CFA, "Evaluating Benchmark Quality," Financial Analysts Journal
48(3), 1992, <https://rpc.cfainstitute.org/research/financial-analysts-journal/1992/faj-v48-n3-33>.*

Correlation cannot express this. It is scale-invariant, so a 3x-levered clone of the
strategy correlates at exactly 1.00 while producing roughly 12x the tracking error of
a properly scaled benchmark. `BenchmarkSelector` therefore ranks by ascending
tracking error and reports `beta` and `r_squared` as diagnostics:

| Signal | Reading |
|---|---|
| High `r_squared`, `beta` near 1.0 | Benchmark matches both shape and scale. |
| High `r_squared`, `beta` far from 1.0 | Right exposure, wrong scale — leverage or volatility mismatch. |
| Low `r_squared` | Benchmark does not explain the strategy's variance regardless of beta. |
| `nan` correlation / beta | Benchmark has zero variance (e.g. a flat risk-free series); correlation is undefined, not zero. |

## Metric definitions as implemented

| Metric | Definition |
|---|---|
| Active return | `strategy - benchmark`, per period. No beta adjustment — this is **active return, not alpha**. |
| Tracking error | Standard deviation of active returns × √`periods_per_year`. |
| Information ratio | (mean active return × `periods_per_year`) ÷ tracking error. |
| Beta | `cov(strategy, benchmark) / var(benchmark)`. |

**Degrees of freedom.** Tracking error is universally defined as "the standard
deviation of active returns," but the sources consulted do not pin down whether the
sample (n−1) or population (n) denominator is intended. CFA-curriculum study material
describes active risk as the *sample* standard deviation of active returns, so this
module defaults to `ddof=1` and exposes `ddof=0` for the population convention. At
n=10 the two differ by ~5.4%; at n=252, ~0.2%. State which convention you used when
reporting — the difference flows straight into the information ratio.

## Threshold guidance — heuristics, not standards

Grinold and Kahn's *Active Portfolio Management* is the origin of the widely repeated
information-ratio scale of roughly 0.5 = good, 0.75 = very good, 1.0 = exceptional,
with 0.5 corresponding to a top-quartile manager. This is a **rule of thumb derived
from institutional active equity managers**, and it was not verified here against the
book's primary text — treat it as industry folklore with a known origin, not as a
standard, and do not transfer it uncritically to a high-turnover systematic strategy
or a different asset class.

A prior version of this file specified "correlation > 0.7 for directional
strategies." No authoritative source for that threshold was found, so it has been
removed rather than restated. Judge fit from `r_squared` and `beta` together, in the
context of the strategy's mandate, rather than against an invented cut-off.

## Regulatory touchpoint

Not a regulatory requirement for internal research. If you present performance under
the **GIPS standards**, the benchmark for a composite or pooled fund must be an
*appropriate total return* benchmark, where appropriate means it reflects the
composite's investment mandate, objective, or strategy — which is why a price-only
index is not an acceptable comparator for a strategy whose returns include income.
*GIPS "Guidance Statement on Benchmarks for Firms," effective 1 April 2021,
<https://www.gipsstandards.org/wp-content/uploads/2023/08/gs_benchmarks_firms.pdf>.
Applies to firms claiming GIPS compliance; verify current text and your own
applicability.*
