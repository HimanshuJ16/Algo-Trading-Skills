# Deep Workflow Reference — value-at-risk-var-live-monitoring

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Ingest Active Positions & Valuations:**
   - Compute current dollar exposure $V_i = q_i \cdot p_i$ and portfolio weights $w_i = V_i / \text{NAV}$.

2. **Compute Parametric VaR:**
   - Portfolio variance $\sigma_p^2 = w^T \Sigma w$.
   - Parametric VaR: $\text{VaR}_{\text{param}} = \text{NAV} \cdot (Z_{\alpha} \sigma_p - \mu_p)$.

3. **Compute Historical Simulation VaR & CVaR:**
   - Sort historical portfolio returns $R_{p, t} = \sum w_i R_{i, t}$.
   - Identify $1\text{st}$ percentile cutoff as Historical VaR.
   - Calculate CVaR (Expected Shortfall) as mean of tail returns beyond VaR.

4. **Enforce Circuit Breaker:**
   - If $\text{VaR}_{99\%} \ge \text{Limit}$, block entry orders and trigger risk alarm.

## Failure Modes Observed in Production

- **Static Backtest VaR Reliance:** Failing to recalculate VaR dynamically during intraday volatility spikes.
- **Ignoring Leverage Scaling:** Computing VaR on un-leveraged position values without accounting for gross leverage.

## Production Implementation Reference

- Reference code: `scripts/var_monitor.py` (`LiveValueAtRiskMonitor`, `VaRMetrics`, `LiveRiskStatus`).
- Automated unit tests: `scripts/test_var_monitor.py`.
