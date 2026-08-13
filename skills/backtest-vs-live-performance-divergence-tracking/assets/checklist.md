# Pre-Flight / Sign-off Checklist — backtest-vs-live-performance-divergence-tracking

Use this before considering the skill's implementation complete.

## Snapshot Pairing

- [ ] **Paired Metric Snapshots:** Confirm backtest and live metrics are captured over equivalent time horizons, instrument universe and regime.
- [ ] **Observation Counts Recorded:** `observation_periods` populated on both snapshots, and `min_live_observations` set to a figure justified by the strategy's trade frequency.
- [ ] **Sample Adequacy Checked:** `is_sample_adequate` is True, or the verdict is being investigated rather than acted on.
- [ ] **Baseline Provenance Recorded:** Which backtest run produced the baseline, when, and whether it has ever been re-based.

## Convention Hygiene

- [ ] **One Drawdown Sign Convention** across both snapshots. Confirmed against the source system — `backtest-reporting-standardized-tearsheet` emits negatives.
- [ ] **Percentages Not Fractions:** win rate and fill rate in $[0, 100]$; `0.55` would be read as $0.55\%$.
- [ ] **Backtest Baseline Is Non-Degenerate:** no zero slippage assumption, no zero drawdown, positive Sharpe. Any `notes` on a metric means that dimension is currently unmonitored.

## Scoring

- [ ] **Sharpe Decay Scoring:** Confirm Sharpe divergence is computed as relative % decay.
- [ ] **Drawdown Blow-Up Ratio:** Confirm live drawdown is compared as a multiplier of backtest drawdown, on magnitudes.
- [ ] **Thresholds Calibrated, Not Inherited:** the defaults have no published basis; confirm they were set against your own strategy population.
- [ ] **Threshold Ladder Not Inverted:** every warning threshold at or below its critical counterpart (enforced on construction).
- [ ] **Compared Against `comparison_value`,** not `divergence_pct`, in every dashboard and alert rule.

## Classification and Alerting

- [ ] **Severity Classification:** Confirm ACCEPTABLE / WARNING / CRITICAL thresholds are enforced, inclusively.
- [ ] **Undefined Comparisons Escalate:** confirm no metric with a populated `notes` is reported ACCEPTABLE.
- [ ] **Non-Finite Inputs Rejected:** injecting a NaN live metric raises rather than returning a clean report.
- [ ] **Suspension Alert:** Confirm CRITICAL severity triggers strategy suspension recommendation, and that `driving_metrics` is surfaced with it.
- [ ] **Recommendation Is Not Automation:** the actual halt path is a real kill switch, not this report.
- [ ] **Divergence Decomposed Before Response:** execution-driven and alpha-driven divergence routed to different owners.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/backtest-vs-live-performance-divergence-tracking/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Strategy, baseline run ID, observation windows, thresholds used: ___________________________
