---
name: value-at-risk-var-live-monitoring
description: Use when monitoring live portfolio risk to calculate real-time Parametric
  VaR, Historical Simulation VaR, and Conditional VaR (CVaR) and block order placement
  upon VaR limit breaches
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- value-at-risk
- var-monitoring
- expected-shortfall
- cvar
- live-risk
brokers_frameworks:
- SciPy
- PyPFcon
- NumPy
- Pandas
- Custom Live Risk Engine
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a live quantitative trading engine manages open market positions. Computing static Value at Risk (VaR) during historical backtesting alone fails to protect live capital when intraday volatility surges or when active position weightings shift dynamically. Real-time live VaR monitoring computes Parametric VaR, Historical Simulation VaR, and Conditional VaR (CVaR / Expected Shortfall) at $95\%$ and $99\%$ confidence levels on every tick or bar update. Blocking new position entries when $\text{VaR}_{99\%}$ breaches predefined risk budgets (e.g., $\le 5.0\%$ NAV) is mandatory.

## Prerequisites

- Synchronized historical return series for active portfolio assets.
- Live position vector $Q = [q_1, q_2, \dots, q_m]$ and asset prices $P = [p_1, p_2, \dots, p_m]$.
- Maximum allowed portfolio 1-day VaR limit (e.g. 5.0% of portfolio NAV).

## Workflow

1. **Ingest Real-Time Positions & Valuation**:
   - Calculate current dollar position value $V_i = q_i \cdot p_i$ and portfolio weights $w_i = V_i / \text{NAV}$.

2. **Compute Parametric (Variance-Covariance) VaR**:
   - Compute portfolio variance $\sigma_p^2 = w^T \Sigma w$ using covariance matrix $\Sigma$.
   - Calculate $99\%$ 1-day Parametric VaR:
     $$\text{VaR}_{\text{param}, 99\%} = \text{NAV} \cdot \left(Z_{0.99} \cdot \sigma_p - \mu_p\right)$$

3. **Compute Historical Simulation VaR & CVaR (Expected Shortfall)**:
   - Construct historical portfolio return distribution $R_{p, t} = \sum_i w_i \cdot R_{i, t}$.
   - Compute $1\text{st}$ percentile quantile as $\text{VaR}_{\text{hist}, 99\%}$.
   - Compute CVaR as average loss beyond VaR:
     $$\text{CVaR}_{99\%} = E[R_p \mid R_p \le -\text{VaR}_{\text{hist}, 99\%}]$$

4. **Enforce Live Risk Breaker**:
   - If $\text{VaR}_{99\%} \ge \text{VaR}_{\text{limit}}$, trigger `VaRBreachWarning` and block new entry orders.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Static Backtest VaR Reliance**: Assuming backtest VaR remains valid during live market volatility spikes.
- **Normal Distribution Assumption in Tail Risk**: Relying solely on Parametric VaR when asset returns exhibit fat tails (high kurtosis).
- **Ignoring Leverage Effects**: Failing to scale VaR by total leveraged position exposure relative to net account equity.

## Verification

- Submit synthetic 2-asset portfolio and verify `LiveValueAtRiskMonitor` computes Parametric VaR, Historical VaR, and CVaR accurately.
- Submit high volatility return series causing $\text{VaR}_{99\%}$ to exceed $5\%$ NAV and verify `evaluate_live_risk()` blocks new order placement.
- Run unit test suite `python scripts/test_var_monitor.py` and confirm 100% pass rate.

## Related Skills

- `broker-account-margin-call-handling`
- `correlation-aware-exposure-limits`
- `kill-switch-and-drawdown-circuit-breakers`
---
