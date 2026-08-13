---
name: benchmark-portfolio-for-multi-strategy-performance-context
description: Use when evaluating a multi-strategy quantitative portfolio to isolate
  genuine skill (Alpha) from hidden market exposure (Beta) and calculate tracking
  error against a custom or standard benchmark.
domain: algorithmic-trading
subdomain: portfolio-construction
tags:
- multi-strategy
- benchmarking
- alpha
- beta
- information-ratio
- tracking-error
brokers_frameworks:
- NumPy
- Portfolio Benchmarking
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Multi-strategy quantitative portfolios are typically designed to generate uncorrelated absolute returns. However, poor strategy allocation often results in "Hidden Beta," where the portfolio is actually just riding the S&P 500 upwards. Invoke this skill to mathematically decompose portfolio returns against a benchmark, explicitly calculating Beta, annualized Alpha, Tracking Error, and the Information Ratio to prove that the returns are driven by skill rather than simple market exposure.

The same decomposition answers the capital-allocation question directly: benchmark the full multi-strategy portfolio against the *simple alternative* it is supposed to beat (a static 60/40 blend, an equal-weight sleeve, or cash) and check whether the added complexity actually earns a positive Alpha and a defensible Information Ratio.

## When NOT to Use

- **Fewer than ~1 year of observations.** Annualizing a 3-month sample produces headline Alpha and Tracking Error figures with no statistical support. The helper emits a warning below one year and reports `Active Return t-stat` and `Observations` so the sample size is never invisible.
- **As a substitute for benchmark selection.** This skill *measures* against a benchmark you already chose; it cannot tell you the benchmark is wrong. See `benchmark-selection-for-strategy-evaluation`.
- **Sector/holdings-level attribution.** Beta/Alpha decomposition explains market exposure, not which sector bets drove returns. Use `benchmark-relative-performance-attribution` for Brinson-Fachler allocation and selection effects.
- **Non-linear payoffs.** A single-factor Beta is a poor description of an options-overlay or heavily convex strategy; its residual will be misread as Alpha.

## Prerequisites

- A 1D array of per-period (typically daily) portfolio returns — simple returns, not log returns, net of fees and costs.
- A 1D array of benchmark **total** returns (including income/dividends) over the exact same periods, aligned by date.
- The annual risk-free rate (e.g., 0.04), expressed on the same basis as the return series.
- Both series must be free of NaN/Inf. The helper rejects non-finite input rather than propagating it.

## Workflow

1. **Align and clean both series**: Match portfolio and benchmark returns by date. A missing benchmark day silently shifts every subsequent observation and corrupts Beta — reject the run rather than forward-filling.
2. **Calculate Active Return**: Subtract benchmark returns from portfolio returns.
3. **Calculate Tracking Error**: Compute the standard deviation of the active return, annualized by $\sqrt{252}$ for daily data.
4. **Calculate Beta**: $\beta = \mathrm{Cov}(R_p, R_b) / \mathrm{Var}(R_b)$. If the benchmark is constant (a cash or absolute-return benchmark), Beta is *not identified* by the data — the helper reports `0.0` by convention and records that convention in `Warnings`. Do not read it as a measured zero exposure.
5. **Calculate Alpha (Skill)**: Jensen's Alpha, $\alpha = (\bar{R}_p - R_f) - \beta (\bar{R}_b - R_f)$, using arithmetic annualization ($\bar{R} \times 252$). This is not a compounded return and will not equal a CAGR difference.
6. **Calculate Information Ratio**: Divide the annualized active return by the annualized tracking error. If tracking error is zero the ratio is **undefined**, not zero — the helper returns `NaN` plus an explanatory warning.
7. **Check statistical support before acting**: Read `Active Return t-stat` ($\mathrm{IR} \times \sqrt{\text{years}}$ under an i.i.d. assumption) and `Observations`. A high Information Ratio over 40 trading days is noise, not skill.
8. **Read `Warnings` before reporting any number**: Every degenerate or unidentified quantity is flagged there. An empty list means no caveats applied.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Inappropriate Benchmarking**: Benchmarking a market-neutral statistical arbitrage strategy against a long-only Equity Index. (Use a zero-beta custom benchmark or a cash benchmark).
- **Ignoring Tracking Error**: Focusing entirely on outperforming the benchmark while ignoring that the portfolio's tracking error has exploded to 20%, fundamentally violating the fund's mandate.
- **Reading an undefined metric as zero**: A portfolio that beats the benchmark by exactly 10bps every single day has zero tracking error and an *infinite* Information Ratio. Any implementation that reports `IR = 0.0` there makes the most consistent manager possible look identical to one with no skill. The same trap applies to Beta against a constant benchmark and to correlation against a constant series.
- **Misclassifying a low-volatility benchmark as constant**: A naive `variance > 1e-8` degeneracy guard trips on a genuine short-duration or cash-plus benchmark (daily $\sigma \approx 1$bp, variance $\approx 10^{-10}$), silently returning Beta = 0 alongside a correlation of 1.0. Degeneracy tolerances must sit at the floating-point noise floor, not at a plausible financial magnitude.
- **Expecting zero Alpha from a leveraged clone**: A 2x unfunded leveraged replication of the benchmark shows $\alpha = +R_f$, not 0, because Jensen's Alpha charges the risk-free rate against only one unit of capital. Alpha only collapses to zero once the borrowing cost of the second unit is charged inside the portfolio return series.
- **Annualizing autocorrelated active returns**: $\sqrt{252}$ scaling assumes serially uncorrelated active returns. Smoothed or illiquid marks are positively autocorrelated, so this understates true annual tracking error and inflates the Information Ratio.
- **Price-only benchmark against a total-return portfolio**: Comparing a portfolio return that includes dividends to a price-only index manufactures Alpha equal to the index dividend yield.

## Verification

- Simulate a portfolio that is perfectly correlated to the benchmark with a 2x leverage factor. Beta must calculate to 2.0, correlation to 1.0, and Alpha to $+R_f$ (0.04 at the default risk-free rate) — **not** 0.0.
- Simulate `portfolio = 1.5 * benchmark + 0.0002` per day. Beta must be 1.5 and Alpha must equal $252 \times 0.0002 + 0.5 R_f = 0.0704$.
- Confirm a constant active-return series yields `Tracking Error (Ann) == 0.0`, `Information Ratio` = NaN, and a populated `Warnings` entry.
- Confirm NaN/Inf input, length mismatch, 2D input, and fewer than 2 observations all raise `BenchmarkingError`.
- Run `python scripts/test_multi_strategy_benchmarker.py` and confirm 100% pass rate.

## Related Skills

- `cross-strategy-correlation-monitoring`
- `benchmark-relative-performance-attribution`
- `benchmark-selection-for-strategy-evaluation`
- `risk-adjusted-performance-attribution-per-strategy`
