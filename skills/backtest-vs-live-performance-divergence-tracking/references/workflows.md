# Deep Workflow Reference — backtest-vs-live-performance-divergence-tracking

This file holds the full technical procedure referenced by `SKILL.md`. Thresholds,
comparison bases and regulatory scope live in `references/standards.md`.

## Full Procedure

1. **Pair the snapshots like for like.**
   Record backtest baseline ($S_{\text{bt}}, DD_{\text{bt}}, WR_{\text{bt}}, FR_{\text{bt}}, SL_{\text{bt}}$)
   and live realized metrics over comparable windows, instrument universe and regime.
   Divergence between a 2019-2021 backtest and a 2022 live period is partly a statement
   about 2022. Carry `observation_periods` on both.

2. **Normalise conventions before comparing.**
   One drawdown sign convention across both snapshots — this repo's
   `backtest-reporting-standardized-tearsheet` emits negatives. Win rate and fill rate as
   percentages in $[0, 100]$, not fractions. Mixed conventions and out-of-range
   percentages raise rather than being silently normalised, because both indicate the two
   snapshots came from different places.

3. **Configure, and treat the defaults as placeholders.**
   ```python
   tracker = BacktestLiveDivergenceTracker(
       sharpe_warning_pct=20.0, sharpe_critical_pct=50.0,
       drawdown_warning_multiplier=1.5, drawdown_critical_multiplier=2.0,
       min_live_observations=60,     # 0 disables the sample-adequacy flag
   )
   ```
   Inverted thresholds raise. No published standard backs any of these numbers.

4. **Evaluate.**
   ```python
   report = tracker.evaluate_divergence("mean_rev_v3", backtest, live)
   ```
   Non-finite values raise rather than classifying — they would otherwise report
   `ACCEPTABLE` on every metric they touch.

5. **Read the decomposition, not just the headline.**
   The severity alone tells you something is wrong; `driving_metrics` tells you what kind
   of wrong, and that determines the response:
   - Sharpe down, **execution metrics intact** → alpha decay or regime shift. Route to
     `strategy-performance-decay-detection-vs-market-wide-decay`.
   - Sharpe down, **fill rate and slippage also moved** → execution problem. Route to
     `transaction-cost-analysis-tca-integration`.
   - **Drawdown blow-up alone** → risk sizing or a tail the backtest never sampled.
   - **Win rate decay with Sharpe intact** → the payoff profile changed shape; the
     strategy is winning less often but larger.

6. **Compare against the right field.**
   Use `comparison_value`, which shares the scale of `threshold_warning` and
   `threshold_critical`. `divergence_pct` is display-only and is a percentage even for
   the two metrics whose thresholds are multipliers.

7. **Check `is_sample_adequate` before acting.**
   A False here means the verdict is noise-dominated in both directions. Severity is not
   downgraded — investigate rather than either acting or dismissing.

8. **Treat `notes` as a finding, not a footnote.**
   A populated `notes` means a comparison could not be formed: the backtest assumed no
   slippage, recorded no drawdown, or produced a non-positive Sharpe. That is a defect in
   the baseline, and it means that dimension is currently unmonitored.

9. **Alert and escalate.**
   `DivergenceSeverity` is a string enum, so `dataclasses.asdict(report)` serialises to
   JSON directly. `CRITICAL` triggers a suspension **review** — this module recommends,
   it does not act. Wire the actual halt to
   `kill-switch-and-drawdown-circuit-breakers`, which is built for it.

10. **Re-baseline deliberately, or not at all.**
    Resetting the backtest baseline to recent live performance makes future divergence
    unmeasurable. If the original baseline is genuinely stale, record why and version it.

## Known Failure Modes

- **Negative drawdown convention skipping the check.** A guard of
  `if backtest.max_drawdown_pct > 0` defaulted the ratio to $1.0$ when fed the negative
  convention, so a live drawdown of $-50\%$ against a backtest $-10\%$ reported
  `ACCEPTABLE` with `is_suspension_recommended=False`.
- **A zero-slippage backtest blessing unlimited live cost.** The same guard pattern on
  `avg_slippage_bps` meant the most common backtest omission was also the one case the
  amplification check skipped: 0 bps assumed against 50 bps live reported `ACCEPTABLE`.
- **NaN reporting no divergence.** `max(0.0, nan)` is `0.0` and `nan >= threshold` is
  `False`, so an all-NaN live snapshot produced a clean bill of health.
- **A non-positive backtest Sharpe forcing decay to zero.** Backtest Sharpe $0.0$ against
  live $-3.0$ reported `ACCEPTABLE`.
- **Floating point deciding a threshold case.** Exactly $20\%$ decay computed as
  $19.999999999999996$ and classified `ACCEPTABLE` while the report displayed
  `divergence_pct` $20.0$ beside `threshold_warning` $20.0$.
- **Inverted thresholds.** Warning $50$ with critical $20$ made a $30\%$ decay
  `CRITICAL`, silently reversing the ladder.
- **Comparing incommensurable fields.** A dashboard rendering `divergence_pct` $80.0$
  against `threshold_warning` $1.5$ for the same metric.
- **Acting on two weeks of live data.** Suspending a working strategy on a sample too
  short to distinguish it from noise.

## Production Implementation Reference

- Reference code: `scripts/divergence_tracker.py` (`BacktestLiveDivergenceTracker`,
  `PerformanceSnapshot`, `DivergenceMetric`, `DivergenceReport`, `DivergenceSeverity`,
  `DivergenceTrackerError`).
- Automated unit tests: `scripts/test_divergence_tracker.py`.
