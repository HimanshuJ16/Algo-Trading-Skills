---
name: benchmark-selection-for-strategy-evaluation
description: Choosing an appropriate benchmark against which to evaluate a strategy's
  risk-adjusted performance.
domain: Backtesting
subdomain: Evaluation
tags:
- backtesting
- benchmark
- evaluation
- metrics
brokers_frameworks:
- NumPy
version: "1.1.0"
author: System
license: MIT
---

# Benchmark Selection for Strategy Evaluation

## When to Use
Use this skill when evaluating a new strategy. Selecting the right benchmark is crucial; if you run a tech-heavy long-only strategy, benchmarking it against a risk-free rate or a generic broad index will make it look artificially good during tech bull markets.

`BenchmarkSelector` screens candidate benchmarks by how closely each one replicates the strategy's return stream, reporting tracking error, information ratio, beta, and R².

## When NOT to Use
- **To compute alpha.** This screen measures *active return* (`strategy − benchmark`) with no beta adjustment. A strategy with beta 1.5 to its benchmark will show active return that is mostly leveraged beta, not skill. For a beta-adjusted attribution use `strategy-performance-attribution-vs-market-beta`.
- **To pick a benchmark after seeing results.** Ranking candidates post hoc and keeping the flattering one is benchmark selection bias. The screen is for confirming a pre-specified shortlist.
- **As a substitute for benchmark validity.** Investability, unambiguity, measurability, and "specified in advance" are properties of the benchmark's construction that no return-series statistic can observe.
- **On a handful of observations.** With two data points correlation is ±1 by construction. Short samples produce confident-looking numbers with no content.

## Prerequisites
- Time series of strategy returns (at least 2 observations; realistically far more).
- Time series of candidate benchmark returns (e.g., SPY, QQQ, sector ETFs, risk-free rate), **total return** and date-aligned to the strategy.
- The return frequency, so `periods_per_year` can be set correctly.

## Workflow
1. **Fix the candidate list first.** Write down the candidates and the selection rule before running anything — otherwise the screen becomes a search for the most flattering comparator.
2. **Confirm total-return series.** A price-only index understates the benchmark by roughly its dividend yield per year, which the strategy then "outperforms" for free.
3. **Align the series.** Equal length is enforced; a one-day shift is not detectable by the tool and silently contaminates every statistic. Align on an explicit date index upstream.
4. **Set the conventions.** `periods_per_year` must match the data frequency — leaving a monthly series at the 252 default overstates tracking error by √21 ≈ 4.6x. `ddof=1` (sample) is the default; use `ddof=0` if your reporting standard requires the population convention.
5. **Run `evaluate_benchmarks(strategy_returns)`.** Results are sorted by ascending tracking error — the closest-replicating benchmark first.
6. **Read beta before accepting the top row.** If the best-fitting candidate has beta far from 1.0, it has the right exposure at the wrong scale; if `r_squared` is low, no candidate explains the strategy and you may need a multi-factor or absolute-return comparator.
7. **Check for `nan` and `±inf` before quoting anything.** `nan` correlation means the benchmark has zero variance (a flat risk-free series) — correlation is undefined there, not zero. `±inf` information ratio means zero active risk with non-zero active return.
8. **Confirm the qualitative properties, then freeze.** Verify the winner is investable and unambiguous, record the benchmark with its `periods_per_year`/`ddof` conventions and date range, and do not change it mid-evaluation.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls
- **Defaulting to SPY:** Using SPY as a benchmark for everything (e.g., a bond strategy, a short-only strategy, a crypto strategy).
- **Ignoring Risk-Free Rate:** For market-neutral absolute return strategies, the risk-free rate or a very low-volatility benchmark is appropriate. If the risk-free series wins the screen for a market-neutral strategy, that is the correct answer, not a defect.
- **Ranking by correlation:** Correlation is scale-invariant, so a 3x-levered clone of the strategy scores a perfect 1.00 while producing roughly 12x the tracking error of a properly scaled benchmark. Rank by tracking error and use beta to detect scale mismatch.
- **Calling active return alpha:** `strategy − benchmark` is active return. Alpha requires adjusting for beta; a high-beta strategy in a rising market shows large active return and zero skill.
- **Price-only benchmark series:** Comparing a dividend-receiving strategy against a price index hands it the dividend yield as free outperformance every year.
- **Wrong annualisation factor:** Tracking error and the information ratio both scale with √`periods_per_year`. A monthly series scored at 252 produces numbers that are wrong by a factor of 4.6 in opposite directions.
- **Treating a `nan` correlation as zero:** A flat risk-free benchmark has no variance, so correlation is undefined. Reporting 0.0 there makes the benchmark look deliberately uncorrelated when nothing was measured at all.
- **Benchmark shopping:** Re-running the screen after a bad quarter and switching to whichever benchmark now looks better violates "specified in advance" and invalidates the entire comparison.

## Verification
- Verify that Information Ratio correctly rewards excess return relative to tracking error.
- Check that a benchmark matching the strategy in **both shape and scale** yields lower tracking error than one matching only in shape. High correlation alone does not imply low tracking error: confirm the tool does *not* recommend a 3x-levered clone of the strategy, which correlates at exactly 1.00 and is a terrible benchmark (`test_leveraged_clone_is_not_recommended`).
- Confirm a flat risk-free benchmark yields `nan` correlation rather than `0.0`.
- Confirm tracking error matches an independently computed sample standard deviation of active returns, annualised.
- Run `python -m unittest discover -s skills/benchmark-selection-for-strategy-evaluation/scripts` and confirm a 100% pass rate.

## Related Skills
- `backtest-reporting-standardized-tearsheet`
- `benchmark-relative-performance-attribution`
- `strategy-performance-attribution-vs-market-beta`
- `benchmark-portfolio-for-multi-strategy-performance-context`
- `risk-adjusted-performance-attribution-per-strategy`
