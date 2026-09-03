---
name: multi-year-regime-coverage-requirement
description: Use when validating strategy backtests to segment historical price data
  into distinct market regimes (Bull Trend, Bear Market, High Volatility Crash, Low
  Volatility Range), enforce multi-regime coverage rules (>=3 regimes, each with a
  minimum bar count), and de-average performance metrics so the worst regime is
  visible instead of averaged away.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- regime-classification
- market-regimes
- multi-year-backtest
- robustness-testing
- de-averaged-performance
brokers_frameworks:
- Market Regime Coverage Engine
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill during strategy validation, before promotion to live capital. A strategy with stellar metrics over one or two years of a trending bull market routinely suffers a catastrophic drawdown in the first high-volatility crash or sideways range it meets. This skill buckets historical bars into market regimes, enforces a minimum coverage floor (duration **and** at least `min_required_regimes` regimes, each with enough bars to be more than an artifact), and reports drawdown, return, win rate and Sharpe **per regime** so the worst bucket is visible rather than averaged into an aggregate.

Use it as a gate: an aggregate multi-year Sharpe of 2.5 tells you nothing about whether the strategy lost 40% during the one crash in the sample.

## When NOT to Use

- **As a live regime signal.** Bar *i*'s label is computed from a window that *includes bar i's own price*. That is correct for retrospective attribution and catastrophic as a trading signal — at the open of bar *i* that price does not exist yet. For live routing use `regime-detection-for-strategy-switching`, which confirms regimes with hysteresis on already-closed bars.
- **On intraday bars while leaving `bars_per_year` at 252.** The default is daily. One year of 1-minute bars is 98,280 bars, which at 252 audits as *390 years* of coverage and passes any duration gate. Frequency cannot be inferred from a list of floats — you must set it.
- **On a series with calendar gaps.** Duration is bar count ÷ `bars_per_year`, not a timestamp span. A series missing six months reports the span it would have had if the bars were consecutive.
- **As the account-level drawdown control.** This measures drawdown *inside each regime bucket*. The portfolio-level question belongs to `kill-switch-and-drawdown-circuit-breakers` and `portfolio-level-stop-loss-independent-of-strategy-stops`.
- **As proof of robustness on its own.** Passing is necessary, not sufficient. It says nothing about look-ahead bias (`lookahead-bias-elimination`), overfitting (`walk-forward-validation-setup`), or capacity.

## Prerequisites

- A gap-free price series, strictly positive and finite, at **one** consistent bar frequency.
- `bars_per_year` matching that frequency (252 for daily bars) — it drives both the duration gate and the Sharpe annualization.
- A strategy return series **aligned one-to-one with the prices**: `strategy_returns[i]` is the return realized over the bar ending at `prices[i]`. Same length, no exceptions — the engine raises rather than truncating.
- Returns expressed as *differential* (excess) returns if the risk-free rate is non-zero; otherwise the reported figure is a return-to-variability ratio, not a Sharpe ratio.
- Thresholds you are willing to defend: minimum years, minimum regimes, minimum bars per regime, and the per-regime drawdown limit. The defaults are house heuristics, not standards — see `references/standards.md`.

## Workflow

1. **Segment Historical Data into Market Regimes**:
   - For each bar with a full trailing window, compute annualized volatility $\sigma = \text{stdev}(r_{\text{window}}) \times \sqrt{F}$ and window price change $\Delta = (P_i - P_{i-w}) / P_{i-w}$.
   - Bucket: $\sigma >$ `high_vol_annualized_threshold` → `HIGH_VOLATILITY_CRASH`; else $\Delta > $ `trend_threshold_pct` → `BULL_TREND`; else $\Delta < -$`trend_threshold_pct` → `BEAR_MARKET`; else `LOW_VOLATILITY_RANGE`.
   - **Decision point — warm-up bars are `UNCLASSIFIED`, not "range".** The first $w$ bars have no trailing window. Labelling them `LOW_VOLATILITY_RANGE` invents a regime that then satisfies part of the coverage requirement. They are excluded from coverage and from every metric, and counted in `unclassified_bars` so the exclusion is auditable.

2. **Audit Multi-Year Regime Coverage**:
   - Duration: $\text{bars} / F \ge T_{\text{min}}$, compared **unrounded** — 755 daily bars is 2.9960 years and must not pass a 3-year gate that needs 756.
   - **Decision point — a regime only counts if it has at least `min_bars_per_regime` bars.** Otherwise a 750-bar bull backtest with one incidental bear bar and one crash bar "covers three regimes". Thin regimes are still reported in `regimes_observed` and still carry metrics; they just do not satisfy the coverage floor.

3. **De-average Performance Across Regimes**:
   - Per regime: compounded total return, win rate, annualized Sharpe, and drawdown.
   - **Decision point — Sharpe is `None`, never a number, when it is not measurable.** Below `min_bars_per_regime` observations, or when return dispersion is floating-point noise (a constant return series), there is no risk-adjusted interpretation. Substituting an epsilon denominator produced a reported Sharpe of $2.4 \times 10^{16}$; `None` means "not measurable" and must be rendered as such, not coerced to 0.
   - **Decision point — two drawdown figures, only one of them real.** `max_drawdown_pct` is the worst decline **within a single contiguous episode** of that regime: the decline an account actually experienced, and the veto metric. `concatenated_drawdown_pct` chains every bar of the regime together, skipping the bars in between — useful for spotting death by a thousand cuts across many separate episodes, but it is a decline that never occurred. Never report it as realized.

4. **Enforce Promotion Thresholds**:
   - Veto promotion if within-episode drawdown exceeds `max_allowed_regime_drawdown_pct` in **any** regime, however high the aggregate Sharpe.
   - **Decision point — a thin regime still vetoes.** A 40% loss over five crash bars is a path fact, not a statistical estimate; small sample size disqualifies a Sharpe ratio, not a drawdown.
   - **Decision point — report each failure for what it is.** A coverage failure and a drawdown breach are different findings. `vetoed_regimes` is empty when no drawdown breach occurred, and the message must not claim one.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reusing the classifier as a live signal**: the label for bar *i* is built from a window ending at bar *i*. A backtest that routes strategies on these labels knows the current bar's close before the bar opens and will look spectacular for reasons that have nothing to do with the strategy.
- **Counting a regime that barely appeared**: without a minimum bar count, "covers 3 regimes" is satisfiable by two stray bars in an otherwise single-regime sample. The gate then certifies exactly the backtest it exists to reject.
- **Feeding intraday bars at the daily default**: 98,280 one-minute bars audited at 252 bars/year report 390 years of coverage. The duration gate passes trivially and the annualized Sharpe is overstated by roughly $\sqrt{390}$.
- **Letting corrupt data pass the gate**: a single `NaN` return makes the compounded equity curve `NaN`, and every subsequent `drawdown > max_drawdown` comparison is `False`. Max drawdown stays 0.00%, no veto fires, and the strategy is certified by the absence of data. Reject non-finite prices and returns before auditing — the engine raises.
- **Silently misaligned series**: a return series one element shorter than the price series is the most common off-by-one in backtesting. Truncating to the shorter series shifts every regime label by one bar; the engine raises instead.
- **Testing only in bull regimes**: backtesting momentum strategies exclusively across the 2020–2021 liquidity expansion, masking the 2022 drawdown.
- **Reporting a single aggregate Sharpe**: hiding a $-40\%$ crash drawdown behind an overall $2.5$ Sharpe driven by one favourable regime.
- **Arbitrary window slicing**: hand-picking start/end dates that exclude crash periods instead of classifying algorithmically.
- **Treating the labels as conventional market definitions**: `BEAR_MARKET` here means a 20-bar move below $-3\%$. The SEC's investor education material defines a bear market as a broad index falling 20% or more over at least two months. Do not write "tested through a bear market" in an external document on the strength of this bucket.
- **Comparing regime Sharpe ratios across audits with different thresholds**: the buckets are threshold-dependent. Record the parameters with the report or the numbers are not reproducible.

## Verification

- Instantiate `MarketRegimeCoverageEngine(min_required_years=1.0, min_required_regimes=3, max_allowed_regime_drawdown_pct=25.0)` and feed the 540-bar four-regime fixture from `scripts/test_regime_coverage.py`. Confirm 20 `UNCLASSIFIED` warm-up bars, all four regimes covered, and `total_years` = 540/252 = 2.1429.
- Place two $-10\%$ bars at the start of each of the two `LOW_VOLATILITY_RANGE` episodes. Verify `max_drawdown_pct` = 19.00 (within-episode: $1.0 \to 0.90 \to 0.81$), `concatenated_drawdown_pct` = 34.39 ($0.9^4 = 0.6561$), `total_return_pct` = $-34.39$ (compounded, not the arithmetic sum of $-40.00$), and that no veto fires.
- Place six consecutive $-5\%$ bars inside `BULL_TREND`: $0.95^6 = 0.735092$, a 26.49% decline. Verify `is_coverage_sufficient` is `True` and `is_promotable` is `False`, so the veto is demonstrably the only reason for refusal.
- Boundary checks: a $-25.0049\%$ bar must veto even though it reports as 25.00%; exactly $-25\%$ must not. 755 daily bars must fail a 3-year gate; 756 must pass.
- Negative checks: a `NaN` return, a `NaN` or non-positive price, a return at or below $-100\%$, a length mismatch, and each out-of-range constructor argument must all raise `ValueError`.
- Confirm a constant return series reports `sharpe_ratio is None` for every regime, not a large number.
- Run `python -m unittest discover -s skills/multi-year-regime-coverage-requirement/scripts` and confirm 100% pass rate.

## Related Skills

- `walk-forward-validation-setup`
- `regime-detection-for-strategy-switching`
- `stress-testing-against-historical-crash-scenarios`
- `monte-carlo-strategy-robustness-testing`
- `paper-to-live-promotion-checklist`
