# Deep Workflow Reference — risk-metric-recalculation-frequency-tuning

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Classify Metric Cadence Tiers**:
   - Tier 1: Per-tick ($0$ ms delay) for kill switches and intraday drawdowns.
   - Tier 2: Fast ($1,000–5,000$ ms) for Delta and Gamma.
   - Tier 3: Medium ($30,000$ ms) for 1-Day VaR and ES.
   - Tier 4: Slow ($300,000$ ms) for stress testing and CVA.
2. **Calculate P&L Velocity**: $\left|\frac{d\text{PnL}}{dt}\right| = \frac{|\text{PnL}_t - \text{PnL}_{t-1}|}{\Delta t}$.
3. **Trigger Volatility Acceleration**: If velocity $\ge \$500/\text{sec}$, switch Tier 2-4 metrics to accelerated intervals.
4. **Execute Due Risk Calculations**: Run calculations only for metrics whose elapsed time exceeds active interval.

## Production Implementation Reference

- Reference code: `scripts/risk_frequency_tuner.py` (`RiskMetricFrequencyTuner`, `RiskMetricScheduleConfig`, `TunerExecutionReport`).
- Automated unit tests: `scripts/test_risk_frequency_tuner.py`.
