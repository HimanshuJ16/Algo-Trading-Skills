---
name: risk-model-backtesting-against-realized-outcomes
description: >-
  Production-grade risk model backtesting engine evaluating forecast Value-at-Risk (VaR) and Expected Shortfall (CVaR) against realized P&L outcomes using Kupiec's POF test, Christoffersen's independence test, and the Basel Traffic Light Framework.
domain: Risk Management & Quantitative Auditing
subdomain: VaR Backtesting & Model Validation
tags: ["var-backtesting", "kupiec-pof-test", "basel-traffic-light", "expected-shortfall", "risk-model-validation", "cvar"]
brokers_frameworks: ["Basel Traffic Light Framework", "Kupiec POF Likelihood Ratio Test", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when auditing or validating internal risk models (Value-at-Risk, Expected Shortfall, historical simulation models) against realized daily trading P&L. Financial regulators (SEC, Basel Committee, FCA) require institutions to perform daily backtesting of 99% VaR models over a 250-day rolling window. If realized trading losses breach forecast VaR limits more often than expected (e.g. $\ge 10$ exceptions in 250 days), the risk model is placed in the Basel Red Zone and rejected, forcing higher regulatory capital add-ons.

## Prerequisites

- Daily risk observations list (`DailyRiskObservation`: `date_iso`, `realized_pnl_usd`, `forecast_var_usd`, `confidence_level`).
- Target confidence level (default 0.99 for 99% 1-day VaR). Minimum 20 daily observations required.

## Workflow

1. **VaR Exception Identification**:
   - Compare realized P&L against forecast VaR limit. Flag exception whenever $\text{Realized PnL} < -\text{Forecast VaR}$.
2. **Kupiec POF Likelihood Ratio Test**:
   - Compute Kupiec's POF LR statistic:
     $$\text{LR} = -2 \ln \left[ \frac{(1-p)^{N-x} p^x}{(1 - x/N)^{N-x} (x/N)^x} \right]$$
3. **Basel Traffic Light Zone Assignment**:
   - Classify 250-observation equivalent exceptions:
     - $0 \dots 4$ exceptions $\implies$ **Green Zone** (Model Validated).
     - $5 \dots 9$ exceptions $\implies$ **Yellow Zone** (Accepted with Capital Add-on).
     - $\ge 10$ exceptions $\implies$ **Red Zone** (Model Rejected).
4. **Audit Report Generation**: Output structured `RiskModelBacktestReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Expected vs Maximum Loss**: Treating 99% VaR as the maximum possible loss rather than an threshold exceeded 1% of the time.
- **Ignoring Exception Clustering**: Failing to test whether VaR exceptions occur in consecutive days during market crises.
- **Small Sample Calibration**: Running backtests on $< 100$ trading days, leading to low statistical power in Kupiec's test.

## Verification

- Instantiate `RiskModelBacktesterEngine`. Run 250 observations with 2 exceptions $\implies$ verify `GREEN` zone, Kupiec LR $< 3.841$, and model accepted. Run 250 observations with 12 exceptions $\implies$ verify `RED` zone, Kupiec LR $> 3.841$, and model rejected.
- Run `python scripts/test_risk_model_backtester.py`.

## Related Skills

- `risk-limit-calibration-against-historical-drawdowns`
- `risk-metric-recalculation-frequency-tuning`
---
