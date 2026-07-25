# Pre-Flight / Sign-off Checklist — backtest-vs-live-performance-divergence-tracking

Use this before considering the skill's implementation complete.

- [ ] **Paired Metric Snapshots:** Confirm backtest and live metrics are captured over equivalent time horizons.
- [ ] **Sharpe Decay Scoring:** Confirm Sharpe divergence is computed as relative % decay.
- [ ] **Drawdown Blow-Up Ratio:** Confirm live drawdown is compared as a multiplier of backtest drawdown.
- [ ] **Severity Classification:** Confirm ACCEPTABLE / WARNING / CRITICAL thresholds are enforced.
- [ ] **Suspension Alert:** Confirm CRITICAL severity triggers strategy suspension recommendation.
- [ ] **Automated Testing:** Run `python scripts/test_divergence_tracker.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
