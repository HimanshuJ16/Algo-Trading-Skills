# Deep Workflow Reference — multi-year-regime-coverage-requirement

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Prepare and Validate the Inputs

The audit refuses inputs it cannot interpret rather than degrading silently. Every one of
these raises `ValueError`:

| Input problem | Why it is fatal |
|---|---|
| Non-finite price | Every comparison against `NaN` is `False`, so a corrupt window falls through to `LOW_VOLATILITY_RANGE` and is never noticed. |
| Zero or negative price | Divides by zero in the window return calculation. Back-adjusted futures series can go non-positive — use a ratio-adjusted or unadjusted series for classification. |
| Non-finite return | Makes the compounded equity curve `NaN`, after which `drawdown > max_drawdown` is `False` forever. Max drawdown stays 0.00%, **no veto fires, and corrupt data produces an automatic pass.** |
| Return $\le -100\%$ | Equity hits or crosses zero; every compounded figure after it is meaningless. |
| `len(prices) != len(strategy_returns)` | The n-vs-n-1 off-by-one is the most common alignment error in backtesting. Truncating to the shorter series shifts every regime label by one bar. |
| Fewer than 2 prices | Nothing to classify. |

Set `bars_per_year` to the observations-per-year of your bar frequency **before** anything
else. It is not inferable and it drives two separate gates.

## 1. Market Regime Segmentation

For each bar $i \ge w$ (where $w$ = `window_size`), over the sub-series
$P_{i-w} \dots P_i$:

$$r_j = \frac{P_j - P_{j-1}}{P_{j-1}}, \qquad
\sigma_{\text{ann}} = \sqrt{\frac{1}{w}\sum_j (r_j - \bar r)^2} \times \sqrt{F}, \qquad
\Delta = \frac{P_i - P_{i-w}}{P_{i-w}}$$

Bucketing, in strict order — volatility is checked first, so a violent selloff is a crash
rather than a bear trend:

1. $\sigma_{\text{ann}} >$ `high_vol_annualized_threshold` → `HIGH_VOLATILITY_CRASH`
2. $\Delta >$ `trend_threshold_pct` → `BULL_TREND`
3. $\Delta < -$`trend_threshold_pct` → `BEAR_MARKET`
4. otherwise → `LOW_VOLATILITY_RANGE`

Bars $0 \dots w-1$ have no full trailing window and are `UNCLASSIFIED`. They are excluded
from coverage counting and from every per-regime metric, and reported in
`unclassified_bars`. Labelling them `LOW_VOLATILITY_RANGE` — as a naive warm-up default
does — fabricates a regime that then helps satisfy the coverage requirement.

**The label for bar $i$ uses bar $i$'s own price.** That is intended: the return earned on
a bar that crashed belongs in the crash bucket. It also makes these labels unusable as a
live signal. See `regime-detection-for-strategy-switching` for the live-routing case.

## 2. Audit Multi-Year Coverage

Two independent gates, reported as two independent findings:

- **Duration**: `bars_analyzed / bars_per_year >= min_required_years`, compared on the
  unrounded value. 755 daily bars is 2.9960 years; a 3-year gate needs 756.
- **Regimes**: at least `min_required_regimes` regimes have at least `min_bars_per_regime`
  bars each. Regimes that appear with fewer bars are listed in `regimes_observed` and
  carry full metrics, but `counts_toward_coverage` is `False` and the audit message names
  them so the reviewer can see what was excluded and why.

## 3. De-average Performance Metrics

Per regime bucket:

| Metric | Definition | Notes |
|---|---|---|
| `total_return_pct` | $\left(\prod_i (1 + r_i)\right) - 1$ | Compounded, consistent with the equity curve the drawdown walks. An arithmetic sum of simple returns disagrees with it — for four $-10\%$ bars, $-40.00\%$ versus the correct $-34.39\%$. |
| `win_rate_pct` | bars with $r_i > 0$, as a percentage | Zero-return bars count as losses, not wins. |
| `sharpe_ratio` | $\dfrac{\bar r}{\sigma_r}\sqrt{F}$, population $\sigma$ | `None` when bars $<$ `min_bars_per_regime` or dispersion is floating-point noise. See `references/standards.md` for why $\sqrt{F}$ here is a comparative indicator only. |
| `max_drawdown_pct` | worst peak-to-trough decline **within a single contiguous episode** | The decline actually experienced. **This is the veto metric.** |
| `concatenated_drawdown_pct` | drawdown of a synthetic curve chaining every bar of the regime | Detects death by a thousand cuts across separated episodes. Never a realized figure. |
| `episode_count` | number of contiguous runs of the regime | An `episode_count` of 1 means the regime appeared once; coverage over one episode is weaker evidence than the bar count alone suggests. |

Worked example — a regime with two episodes, each opening with two $-10\%$ bars:

- Within an episode: $1.00 \to 0.90 \to 0.81$, a **19.00%** decline.
- Concatenated across both: $0.9^4 = 0.6561$, a **34.39%** decline that never occurred.
- Compounded total return: **$-34.39\%$**.

At a 25% limit the within-episode figure passes and the concatenated figure would veto.
Vetoing on the concatenated number rejects strategies for a loss no account took.

## 4. Enforce Promotion Thresholds

`is_promotable` requires **both** `is_coverage_sufficient` **and** an empty
`vetoed_regimes`.

- The veto compares the **unrounded** drawdown fraction. Rounding to 2 dp first lets a
  25.0049% decline present as 25.00% and slip under a 25% limit.
- Exactly at the limit is not a breach: the comparison is strictly greater-than.
- A regime too thin to count toward coverage **still vetoes on drawdown**. Sample size
  disqualifies a statistical estimate (Sharpe), not a path fact (drawdown).
- Failures are reported separately: `Insufficient duration`, `Insufficient regimes`, and
  `REGIME VETO` are three distinct findings. `vetoed_regimes` is empty and the message
  says nothing about drawdown when no drawdown breach occurred — an audit report that
  claims a breach that did not happen is worse than one that reports nothing.

## 5. Record the Parameters

The buckets, and therefore every number in the report, depend on `window_size`,
`high_vol_annualized_threshold`, `trend_threshold_pct`, `bars_per_year` and
`min_bars_per_regime`. Archive them with the report. ESMA's supervisory briefing
(ESMA74-1505669079-10311, ¶29) expects testing methodologies and their documentation to be
comprehensive enough for a supervisor to assess compliance from the documentation alone.

## Production Implementation Reference

- Reference code: `scripts/regime_coverage.py` (`MarketRegimeCoverageEngine`,
  `MarketRegime`, `RegimePerformanceMetrics`, `RegimeCoverageAuditReport`).
- Automated unit tests: `scripts/test_regime_coverage.py` (run from `scripts/`).
