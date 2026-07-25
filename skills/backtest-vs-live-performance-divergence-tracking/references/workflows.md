# Deep Workflow Reference — backtest-vs-live-performance-divergence-tracking

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Capture Paired Snapshots**:
   - Record backtest baseline ($S_{\text{bt}}, DD_{\text{bt}}, WR_{\text{bt}}, FR_{\text{bt}}, SL_{\text{bt}}$) and live realized metrics.

2. **Compute Per-Metric Divergence**:
   - Sharpe Decay %: $(S_{\text{bt}} - S_{\text{live}}) / S_{\text{bt}} \times 100$
   - Drawdown Blow-Up Ratio: $DD_{\text{live}} / DD_{\text{bt}}$
   - Fill Rate Gap: $FR_{\text{bt}} - FR_{\text{live}}$
   - Slippage Amplification Ratio: $SL_{\text{live}} / SL_{\text{bt}}$

3. **Classify Overall Severity**:
   - `ACCEPTABLE`, `WARNING`, or `CRITICAL` based on worst individual metric.

4. **Alert & Suspend**:
   - `CRITICAL` triggers automatic strategy suspension review.

## Production Implementation Reference

- Reference code: `scripts/divergence_tracker.py` (`BacktestLiveDivergenceTracker`, `DivergenceReport`, `DivergenceSeverity`).
- Automated unit tests: `scripts/test_divergence_tracker.py`.
