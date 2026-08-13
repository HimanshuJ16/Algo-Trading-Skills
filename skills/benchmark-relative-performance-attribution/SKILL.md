---
name: benchmark-relative-performance-attribution
description: Use when analyzing backtest or live trading performance to decompose
  total returns into Alpha, Beta, Tracking Error, Information Ratio, and Brinson-Fachler
  allocation and selection effects
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- performance-attribution
- alpha-beta
- information-ratio
- brinson-attribution
brokers_frameworks:
- PyFolio
- Empyrial
- QuantStats
- Custom Performance Evaluators
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when evaluating a quantitative strategy against a benchmark index (e.g. `SPY`, `NIFTY 50`, `BTC`). Raw returns alone are not evidence of skill: a strategy returning 20% while carrying beta 1.5 to an index that returned 25% destroyed value. This skill separates the return that came from benchmark exposure ($\beta$) from the return that did not ($\alpha$), sizes the active risk taken to get it ($TE$), scores the trade-off ($IR$), and — for a single period — attributes active return to sector bets (Brinson-Fachler).

`PerformanceAttributionEngine` in `scripts/attribution_engine.py` implements this.

## When NOT to Use

- **To chain attribution across periods.** Brinson effects add across sectors but not across time — returns compound multiplicatively while effects add arithmetically. Summing twelve monthly allocation effects does *not* give the annual allocation effect. Multi-period reporting needs a linking method (Cariño, Menchero, Frongello, GRAP), which this engine deliberately does not implement.
- **To attribute against multiple factors.** This is single-factor CAPM. A strategy that is really a small-cap or value tilt will show that tilt as alpha. Use `strategy-performance-attribution-vs-market-beta` for a Fama-French decomposition.
- **Against a flat benchmark.** Beta is unidentified when the benchmark has no return variance (a constant risk-free series), so alpha is undefined too. The engine raises rather than returning a fabricated $\beta = 1.0$. Use active-return statistics instead.
- **To choose a benchmark.** Ranking candidates after seeing results is benchmark-selection bias — see `benchmark-selection-for-strategy-evaluation`.
- **On a short sample.** The engine's 5-observation floor is numerical, not statistical. See Verification for how many observations a claim actually needs.

## Prerequisites

- Date-aligned periodic return series for the strategy ($R_p$) and benchmark ($R_b$), equal length. Equal length is enforced; a one-period shift is not detectable by the engine and silently contaminates every statistic — align on an explicit date index upstream.
- **Total-return** benchmark series. A price-only index understates the benchmark by roughly its dividend yield per year, which the strategy then "outperforms" for free.
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
- Run `python -m unittest discover -s skills/benchmark-relative-performance-attribution/scripts` and confirm a 100% pass rate.

## Related Skills

- `benchmark-selection-for-strategy-evaluation`
- `strategy-performance-attribution-vs-market-beta`
- `risk-adjusted-performance-attribution-per-strategy`
- `backtest-reporting-standardized-tearsheet`
- `walk-forward-optimization-window-management`
- `survivorship-bias-free-universe-construction`
- `multi-asset-backtest-currency-normalization`
