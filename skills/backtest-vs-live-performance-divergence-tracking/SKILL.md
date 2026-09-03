---
name: backtest-vs-live-performance-divergence-tracking
description: >-
  Use after promoting a strategy to live trading, to measure and decompose the gap
  between backtested and realised Sharpe, hit rate and slippage, and alert when the
  divergence exceeds what execution friction explains.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting-methodology, backtest-live-divergence, performance-tracking, strategy-monitoring, slippage-drift, sharpe-decay
  brokers_frameworks: "Divergence Tracking Engine; Python Statistics"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill after promoting a strategy from backtesting to live trading. Every strategy experiences some divergence between its backtested equity curve and realized live performance. Small divergence ($<20\%$ Sharpe decay) is expected due to execution friction. Large unexplained divergence ($>30\%$ Sharpe decay, or max drawdown $2\times$ backtest worst case) signals model overfitting, regime shift, or execution infrastructure failure. This skill provides a structured framework for tracking, decomposing, and alerting on backtest-vs-live divergence.

The decomposition is the point. A strategy whose Sharpe halved because fill rate collapsed and slippage tripled has an execution problem; one whose Sharpe halved with execution metrics intact has an alpha problem. Those need different responses, and the per-metric breakdown separates them.

## When NOT to Use

- **Not real-time monitoring, and not a kill switch.** This compares two periodic snapshots. It cannot detect a runaway algorithm within a session. Firms subject to a real-time monitoring obligation — EU investment firms engaged in algorithmic trading fall under Article 16 of RTS 6 (Commission Delegated Regulation (EU) 2017/589) — do not discharge it with a divergence report. See `mifid-ii-algo-trading-compliance-eu`, `sec-rule-15c3-5-risk-controls-us`, and `kill-switch-and-drawdown-circuit-breakers`. *(Sourcing note: EUR-Lex was not retrievable during review; the article number and title are corroborated from secondary reproductions of RTS 6, not read from the primary text.)*
- **Not a cause attribution.** It reports which metrics moved, not why. Slippage amplification and Sharpe decay moving together is consistent with an execution problem *and* with a volatility regime change that widened spreads and hurt the signal simultaneously.
- **Not a validated threshold set.** No authoritative source prescribes backtest-vs-live divergence limits. Every default here is an implementation default. Calibrate against your own strategy population before wiring the output to a suspension workflow.
- **Not usable on a short live sample.** Comparing a multi-year backtest Sharpe against two weeks of live results measures noise. Supply `observation_periods` and set `min_live_observations` so the report flags it; the flag warns in *both* directions, since a short sample can hide a real problem as easily as invent one.
- **Not a substitute for a paired like-for-like backtest.** The backtest snapshot must cover a comparable regime and instrument universe, otherwise the divergence is an artefact of the comparison, not of the strategy.

## Prerequisites

- Backtested performance metrics: Sharpe ratio, max drawdown, win rate, avg slippage assumption.
- Live performance metrics over equivalent observation window.
- **One drawdown sign convention across both snapshots.** Either $-15.0$ or $15.0$ works; magnitudes are compared. Mixing them raises, because it means the two snapshots came from different sources.
- Win rate and fill rate as percentages in $[0, 100]$, not fractions.
- Optionally `observation_periods` on each snapshot, to enable the sample-adequacy flag.

## Workflow

1. **Capture Paired Metric Snapshots**:
   - Record backtest baseline metrics $M_{\text{bt}}$ and live realized metrics $M_{\text{live}}$ at equivalent time horizons.

2. **Compute Divergence Scores** — five metrics on three different comparison bases. `comparison_value` carries the quantity actually classified and shares the scale of the thresholds; `divergence_pct` is for display only.
   - Sharpe Decay, relative %: $\Delta_{\text{sharpe}} = \frac{S_{\text{bt}} - S_{\text{live}}}{S_{\text{bt}}} \times 100\%$
   - Win Rate Decay, relative %: same form on win rate
   - Drawdown Blow-Up, ratio: $|DD_{\text{live}}| / |DD_{\text{bt}}|$
   - Slippage Amplification, ratio: $SL_{\text{live}} / SL_{\text{bt}}$
   - Fill Rate Gap, percentage points: $\Delta_{\text{fill}} = \text{FillRate}_{\text{bt}} - \text{FillRate}_{\text{live}}$

3. **Handle Comparisons That Cannot Be Formed**: A zero or non-positive baseline — a backtest that assumed no slippage, recorded no drawdown, or produced a non-positive Sharpe — admits no ratio or relative decay. Such a metric is escalated to `WARNING` with an explanatory `notes` string, **never** reported as `ACCEPTABLE`. An unassessed dimension is not a benign one.

4. **Classify Divergence Severity** — thresholds are **inclusive**, and the classified value is rounded first so the number in the report can never contradict the verdict printed beside it.
   - `ACCEPTABLE`: All divergence metrics within tolerance thresholds.
   - `WARNING`: One or more metrics at or beyond the soft threshold (e.g., Sharpe decay $\ge 20\%$), or any comparison that could not be formed.
   - `CRITICAL`: Sharpe decay $\ge 50\%$, live drawdown $\ge 2\times$ backtest, win rate decay $\ge 25\%$, fill rate gap $\ge 15$ points, or slippage $\ge 4\times$ backtest — triggers strategy suspension review.
   - Overall severity is the worst individual metric; `driving_metrics` names which ones sit at that level.

5. **Check Sample Adequacy Before Acting**: If `is_sample_adequate` is False the verdict is noise-dominated. Investigate rather than act — the severity is deliberately not downgraded, because a short sample is not evidence of safety.

6. **Generate Divergence Report & Alerts**:
   - Emit structured divergence audit report with per-metric breakdown. `DivergenceSeverity` is a string enum, so the report serialises directly to JSON for an alerting pipeline.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Comparing Mismatched Time Windows**: Comparing 3-year backtest Sharpe against 2-week live Sharpe, inflating noise-driven divergence.
- **Ignoring Survivorship Bias in Backtest**: Backtest includes delisted winners; live portfolio never held them.
- **Attributing All Divergence to Execution**: Assuming all Sharpe decay is slippage when it may be regime-driven alpha decay.
- **Mixed Drawdown Sign Conventions**: A tracker guarding on `backtest_drawdown > 0` silently skips the comparison when fed the negative convention that `backtest-reporting-standardized-tearsheet` emits. A live drawdown five times the backtest then reports `ACCEPTABLE`.
- **A Zero-Slippage Backtest**: The most common backtest omission is also the one that defeats a ratio-based slippage check. Against a zero baseline there is no amplification factor to compute, and defaulting it to $1\times$ blesses unlimited live execution cost.
- **NaN Passing Every Threshold**: `max(0.0, nan)` is `0.0` and `nan >= threshold` is `False`, so an unguarded NaN in any live metric reports no divergence and no suspension. Reject non-finite inputs at the boundary.
- **Thresholds That Invert the Ladder**: A warning threshold above its critical counterpart makes mild divergence classify `CRITICAL` and severe divergence `WARNING`. Validate on construction.
- **Floating-Point Deciding a Threshold Case**: A Sharpe of $2.0$ decaying to $1.6$ is exactly $20\%$, but computes as $19.999999999999996$. Classifying the unrounded value while displaying the rounded one produces an audit record reading "divergence 20.0, warning threshold 20.0, severity ACCEPTABLE".
- **Reading `divergence_pct` Against a Threshold**: For the two ratio metrics the displayed percentage and the threshold are on different scales — 80.0 versus 1.5. Compare `comparison_value`.

## Verification

- Submit paired metrics with 25% Sharpe decay, verify `WARNING` classification.
- Submit paired metrics with 60% Sharpe decay, verify `CRITICAL` classification.
- Submit the same drawdown pair under both sign conventions and assert identical verdicts; submit mixed conventions and assert it raises.
- Submit a zero backtest slippage against 50 bps live and assert the metric is not `ACCEPTABLE`.
- Submit a NaN live metric and assert it raises rather than classifying.
- Submit exactly 20.0% Sharpe decay and assert `WARNING`, with `divergence_pct` and `comparison_value` both reading 20.0.
- Run `python -m unittest discover -s skills/backtest-vs-live-performance-divergence-tracking/scripts` and confirm 100% pass rate.

## Related Skills

- `transaction-cost-analysis-tca-integration`
- `paper-to-live-promotion-checklist`
- `multi-year-regime-coverage-requirement`
- `backtest-reporting-standardized-tearsheet`
- `kill-switch-and-drawdown-circuit-breakers`
- `strategy-performance-decay-detection-vs-market-wide-decay`
---
