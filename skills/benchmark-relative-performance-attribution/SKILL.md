---
name: benchmark-relative-performance-attribution
description: >-
  Use when one or several strategies must be judged against a benchmark rather than on raw
  return: alpha, beta, correlation, tracking error, information ratio with its t-statistic,
  a comparable multi-strategy table, and Brinson-Fachler allocation and selection effects.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting-methodology, performance-attribution, alpha-beta, information-ratio, tracking-error, multi-strategy, brinson-attribution
  brokers_frameworks: "PyFolio; Empyrial; QuantStats; Custom Performance Evaluators"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this when evaluating a quantitative strategy against a benchmark index (e.g. `SPY`, `NIFTY 50`, `BTC`). Raw returns alone are not evidence of skill: a strategy returning 20% while carrying beta 1.5 to an index that returned 25% destroyed value. This skill separates the return that came from benchmark exposure ($\beta$) from the return that did not ($\alpha$), sizes the active risk taken to get it ($TE$), scores the trade-off ($IR$) with the $t$-statistic that says whether the score means anything, and — for a single period — attributes active return to sector bets (Brinson-Fachler).

It also answers the **multi-strategy** version of the question. `compare_strategies` runs the same decomposition for every sleeve against one benchmark over one window and returns a comparable table, so "which of these is earning its keep" is a sortable column rather than a set of incomparable one-off reports. Pointed at the *simple alternative* a book is supposed to beat — a static 60/40 blend, an equal-weight sleeve, or cash — it also answers the capital-allocation question directly: does the added complexity earn a positive alpha at a defensible information ratio, or is the portfolio just riding the index? That is the "hidden beta" test for a multi-strategy portfolio sold as uncorrelated absolute return.

`PerformanceAttributionEngine` in `scripts/attribution_engine.py` implements this.

## When NOT to Use

- **To chain attribution across periods.** Brinson effects add across sectors but not across time — returns compound multiplicatively while effects add arithmetically. Summing twelve monthly allocation effects does *not* give the annual allocation effect. Multi-period reporting needs a linking method (Cariño, Menchero, Frongello, GRAP), which this engine deliberately does not implement.
- **To attribute against multiple factors, or to do inference properly.** This is **single-factor CAPM with a single descriptive $t$-statistic**. It has no Fama-French size/value/momentum decomposition, no Newey-West or otherwise autocorrelation-robust standard errors, no confidence intervals on $\beta$, and no regression diagnostics. Its `information_ratio_t_stat` assumes i.i.d. active returns and is a reporting aid, not an inference framework — a strategy that is really a small-cap or value tilt will show that tilt as alpha here, in every row of a comparison table alike. Hand all of that to `strategy-performance-attribution-vs-market-beta`.
- **Against a flat benchmark.** Beta is unidentified when the benchmark has no return variance (a constant risk-free series), so alpha is undefined too. The engine raises rather than returning a fabricated $\beta = 1.0$. Use active-return statistics instead.
- **On a non-linear payoff.** A single-factor beta describes an options-overlay or heavily convex strategy badly, and its residual will be read as alpha. Use a payoff-aware evaluation instead.
- **To choose a benchmark.** This *measures* against a benchmark you already chose; it cannot tell you the benchmark is wrong. Ranking candidates after seeing results is benchmark-selection bias — see `benchmark-selection-for-strategy-evaluation`.
- **On a short sample.** The engine's 5-observation floor is numerical, not statistical. See Verification for how many observations a claim actually needs.

## Prerequisites

- Date-aligned periodic return series for the strategy ($R_p$) and benchmark ($R_b$), equal length, as **simple** (not log) returns, **net** of fees, financing and transaction costs. Equal length is enforced; a one-period shift is not detectable by the engine and silently contaminates every statistic — align on an explicit date index upstream.
- For a multi-strategy comparison: one series per strategy, all aligned to the **same** benchmark over the **same** window. Rows from different windows or different annualization factors are not comparable, and the engine cannot detect that they came from different periods.
- **Total-return** benchmark series. A price-only index understates the benchmark by roughly its dividend yield per year, which the strategy then "outperforms" for free.
- A benchmark **specified in advance** and appropriate to the mandate: a long-only equity index is the wrong yardstick for a market-neutral book (use a zero-beta custom benchmark or cash), and a blend must be documented as a custom benchmark with its components and weights.
- `annualization_factor` matching the data frequency: 252 daily, 52 weekly, 12 monthly, 365 for 24/7 crypto.
- Annual risk-free rate $R_f$ as a decimal (default `0.0`).
- For Brinson: **start-of-period** sector weights for both portfolio and benchmark, over a mutually exclusive and exhaustive partition (both vectors must sum to 1.0), plus the sector return for every sector carrying weight.

## Workflow

1. **Align the series and set the conventions.** Align $R_p$ and $R_b$ on dates upstream. Set `annualization_factor` to the actual data frequency — leaving a monthly series at the 252 default overstates tracking error by $\sqrt{21} \approx 4.6\times$.

2. **Compute Beta and Annualized Alpha** via `evaluate_alpha_beta()`:
   $$\beta = \frac{\text{Cov}(R_p, R_b)}{\text{Var}(R_b)}, \qquad \alpha_{\text{period}} = \left(\bar{R}_p - R_f^{\text{period}}\right) - \beta \cdot \left(\bar{R}_b - R_f^{\text{period}}\right)$$
   Alpha is annualized **arithmetically**: $\alpha = \alpha_{\text{period}} \cdot N$. `pyfolio`/`empyrical` compound it geometrically, $(1 + \alpha_{\text{period}})^N - 1$, so cross-checking against them will show a small gap that widens as alpha grows (5.00% arithmetic is 5.13% geometric at $N = 252$). State the convention in any report.
   If $\text{Var}(R_b) \approx 0$ the engine raises — do not substitute a default beta.

3. **Compute Tracking Error and Information Ratio.** Active return $D_t = R_{p,t} - R_{b,t}$; $TE = \text{Std}(D_t) \cdot \sqrt{N}$ (sample stdev, `ddof=1`); $IR = \frac{\text{Mean}(D_t) \cdot N}{TE}$ — the numerator is annualized too, so $IR = IR_{\text{period}} \cdot \sqrt{N}$. Zero active risk with non-zero active return yields $\pm\infty$, not `0.0`.

4. **Read the t-statistic before reading the IR.** `information_ratio_t_stat` $= IR \cdot \sqrt{\text{years}}$. An IR of 0.5 over one year of daily data has $t \approx 0.5$ — indistinguishable from zero. Do not sign off on a point estimate whose $|t| < 1.96$ without saying so.

4a. **Read `summary.warnings` before quoting any figure.** Every caveat that applies is in that list: a sub-one-year sample, a thin sample, an insignificant $t$, an undefined correlation, an unbounded IR. An empty list means no caveats applied. Nothing degenerate is ever reported as `0.0` — beta against a flat benchmark raises, correlation against a constant series is `nan`, and zero active risk with non-zero active return is $\pm\infty$. Reporting `0.0` for the last would score the most consistent outperformer possible as merely average.

4b. **For several strategies, use `compare_strategies(strategies, benchmark)`.** It runs step 2-4 per strategy against the shared benchmark and returns rows sorted by information ratio (undefined ratios last, name as the tie-break); `render_comparison_table` formats them, carrying a caveat count per row so a four-month IR does not read like a ten-year one. A strategy that fails validation raises with its name rather than being dropped — a table with a silently missing row is worse than no table. Decision point: the comparison is only meaningful because every row shares one benchmark, one window, one `annualization_factor` and one `risk_free_rate`; do not merge rows from separate calls.

5. **Execute Brinson-Fachler sector attribution** via `compute_brinson_attribution()`, for **one period at a time**:
   - Allocation: $A_i = (w_{p,i} - w_{b,i}) \cdot (R_{b,i} - R_b)$
   - Selection: $S_i = w_{b,i} \cdot (R_{p,i} - R_{b,i})$
   - Interaction: $I_i = (w_{p,i} - w_{b,i}) \cdot (R_{p,i} - R_{b,i})$

   Leave `total_benchmark_return` as `None` so $R_b$ is derived from the benchmark weights and sector returns. If you pass it explicitly it is validated against the derived value and a mismatch raises — pasting a compounded annual benchmark return into a single-period call produces plausible-looking allocation effects that do not reconcile.

6. **Reconcile before reporting.** $\sum_i (A_i + S_i + I_i)$ must equal $R_p - R_b$ exactly. The engine asserts this before returning; if you re-implement the formulas, check it yourself. A decomposition that does not reconcile is not an attribution.

7. **Generate the sign-off report.** Common institutional gates are $\alpha > 0$, $IR \ge 0.50$, and positive selection effect — see `references/standards.md` for what those thresholds are and are not.

> Full step-by-step procedure: see `references/workflows.md`.
> Standards, conventions, and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing beta outperformance with alpha.** Claiming a 20% return is skill when a beta-1.5 exposure to an index that returned 25% would have produced more. Active return ($R_p - R_b$) is not alpha; only the beta-adjusted intercept is.
- **Reading unfunded leverage as alpha.** A 2x unfunded replication of the benchmark reports $\alpha = +R_f$, not 0, because Jensen's alpha charges the risk-free rate against one unit of capital while the portfolio earns twice the benchmark's excess return. The "alpha" is the un-charged borrowing cost of the second unit; it collapses to zero only once that cost is inside the portfolio return series.
- **Benchmarking a market-neutral book against a long-only index.** The residual of a badly chosen single factor is not skill. Use a zero-beta custom benchmark or cash, and say which.
- **Ignoring tracking error once alpha is positive.** A book that beats its benchmark while its tracking error has run to 20% has broken its mandate, whatever the alpha says. $TE$ is a constraint, not a diagnostic.
- **Reading an undefined metric as zero.** A portfolio that beats its benchmark by exactly 10bp every day has zero tracking error and an *infinite* information ratio; reporting `IR = 0.0` there makes the most consistent manager possible look identical to one with no skill. The same trap applies to correlation against a constant series and to beta against a flat benchmark.
- **Setting the degeneracy tolerance at a financial magnitude.** A naive `variance > 1e-8` guard trips on a genuine short-duration or cash-plus benchmark (daily $\sigma \approx 1$bp, variance $\approx 10^{-8}$), silently returning $\beta = 0$ next to a correlation of 1.0. Degeneracy tolerances belong at the floating-point noise floor.
- **Annualizing autocorrelated active returns.** The $\sqrt{N}$ scaling of $TE$ assumes serially uncorrelated active returns. Smoothed or illiquid marks are positively autocorrelated, so it understates true annual tracking error and inflates both the IR and its $t$-statistic.
- **Comparing rows from different windows.** A comparison table is only comparable because every row shares one benchmark, window and set of conventions. Stitching in a row measured over a different period reintroduces exactly the incomparability the table exists to remove.
- **Summing single-period Brinson effects across time.** Adding twelve monthly allocation effects to get an annual figure is arithmetically wrong because returns compound. Use a linking method or report per period.
- **End-of-period Brinson weights.** The model is defined on start-of-period weights. End-of-period weights already embed the return being attributed, double-counting the winners.
- **Partial sector coverage.** Attributing only the top five sectors leaves weights summing to less than 1.0, and the effects then do not reconcile to active return — while still looking like a complete table. Add a cash/other bucket instead.
- **A mistyped sector key.** Silently defaulting a missing sector return to 0.0 turns a typo into a large phantom selection effect. The engine raises instead.
- **Treating $IR \ge 0.50$ as proof.** It is a cross-sectional manager percentile from Grinold & Kahn, not a significance test. Over one year of daily data it corresponds to $t \approx 0.5$.
- **Wrong annualization factor.** $TE$ and $IR$ both scale with $\sqrt{N}$, in opposite directions. A monthly series scored at 252 is wrong by $4.6\times$ both ways.
- **Un-synchronized return series.** Equal length is checked; date alignment is not. A one-day shift silently corrupts beta, alpha, and tracking error.
- **NaN in the return series.** A single NaN propagates through covariance into beta, alpha, and `is_alpha_positive` (which becomes `False`, reading as a legitimate fail). The engine rejects non-finite input.
- **Price-only benchmark series.** Hands the strategy the benchmark's dividend yield as free outperformance every year.

## Verification

- Pass $R_p = 1.2 \cdot R_b + 0.05/252$ with $R_f = 0$ and confirm $\beta = 1.20$ and $\alpha = 5.00\%$ **exactly** — the portfolio is an affine function of the benchmark, so both are closed-form, not approximate (`test_documented_verification_case_beta_and_alpha`). With $R_f = 2\%$ the same series gives $\alpha = 5.4\%$, since $\alpha = a + (\beta - 1) R_f$ (`test_nonzero_risk_free_rate_shifts_alpha_by_beta_minus_one`).
- Confirm $TE$ matches an independently computed sample standard deviation of active returns, annualized, and that $IR$ matches $\text{Mean}(D) \cdot N / TE$.
- Confirm `information_ratio_t_stat` equals $\sqrt{n} \cdot \text{Mean}(D) / \text{Std}(D)$ computed from the raw series, and coincides with $IR$ at exactly one year of data.
- Confirm Brinson effects sum to $R_p - R_b$ to within floating-point tolerance, including with an off-benchmark sector ($w_b = 0$).
- Confirm the Brinson-Fachler convention: overweighting a sector that rose but underperformed the benchmark must give a **negative** allocation effect (Brinson-Hood-Beebower would give a positive one).
- Confirm a flat benchmark raises rather than returning $\beta = 1.0$, and that NaN/Inf input raises rather than propagating.
- **Leveraged clone**: $R_p = 2 R_b$ at $R_f = 4\%$ must give $\beta = 2.0$, correlation $= 1.0$ and $\alpha = +0.04$ — **not** 0.0. And $R_p = 1.5 R_b + 0.0002$ per day must give $\beta = 1.5$ and $\alpha = 252 \times 0.0002 + 0.5 R_f = 0.0704$.
- **Degeneracy**: a constant portfolio gives correlation `nan` (never 0.0) with a warning; a genuine cash-like benchmark at a daily $\sigma$ of 1bp must **not** be misclassified as constant — $\beta = 2.0$ and correlation $= 1.0$ for a 2x clone of it.
- **Warnings**: a 60-observation sample flags the sub-one-year caveat; a near-zero IR over two years flags $|t| < 1.96$; a constant $+10$bp active return flags the unbounded IR; and a clean two-year affine clone carries an **empty** warnings list.
- **Multi-strategy comparison**: three affine clones ($\beta = 1.2/0.8/1.0$, $\alpha = 5\%/2\%/-1\%$ at $R_f = 0$) must reproduce those closed forms per row, and each row must equal the single-strategy `evaluate_alpha_beta` result field for field — the comparison must not be a second implementation of the same arithmetic. Rows sort by IR descending with undefined ratios last and the name as tie-break; `sort_by_information_ratio=False` preserves insertion order; an empty mapping, a blank or non-string name, a length mismatch and a NaN series each raise, with the offending strategy named. `render_comparison_table` emits one line per strategy plus a header and rule, carries a caveat count, and never renders `-0.00%`.
- Run `python -m unittest discover -s skills/benchmark-relative-performance-attribution/scripts` and confirm a 100% pass rate (47 tests).

## Related Skills

- `benchmark-selection-for-strategy-evaluation`
- `strategy-performance-attribution-vs-market-beta`
- `risk-adjusted-performance-attribution-per-strategy`
- `cross-strategy-correlation-monitoring`
- `backtest-reporting-standardized-tearsheet`
- `walk-forward-optimization-window-management`
- `survivorship-bias-free-universe-construction`
- `multi-asset-backtest-currency-normalization`
