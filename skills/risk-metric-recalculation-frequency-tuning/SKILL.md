---
name: risk-metric-recalculation-frequency-tuning
description: Use when engineering risk monitoring architecture to tune and schedule
  differential recalculation frequencies across risk metrics (real-time tick checks
  vs periodic VaR/Greeks/stress tests), optimizing compute budget without compromising
  risk protection.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- risk-cadence
- recalculation-frequency
- compute-efficiency
- event-driven-risk
- var-tuning
brokers_frameworks:
- Risk Metric Frequency Tuner Engine
- Python
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building multi-metric live risk monitoring engines. Computing intensive risk metrics (10,000-scenario Monte Carlo VaR, full option surface Greeks, multi-counterparty CVA) on every single incoming price tick causes CPU starvation and delays critical order processing. This skill establishes differential recalculation cadences: real-time tick evaluation for drawdown/position limits, fast cadence for Delta/Gamma, and periodic/event-driven acceleration for heavy VaR and stress tests.

## Prerequisites

- Risk metric definitions (Drawdown, Delta, Vega, VaR, Stress Test).
- Target recalculation interval configurations ($T_{\text{target\_ms}}$) and volatility acceleration thresholds.

## Workflow

1. **Classify Risk Metric Cadence Tier**:
   - Tier 1 (Tick-level, $0$ ms delay): Pre-trade order caps, intraday drawdown kill-switch.
   - Tier 2 (Fast Intraday, $1,000–5,000$ ms): Delta, Gamma, position concentration.
   - Tier 3 (Medium Intraday, $30,000$ ms): 1-Day VaR, Expected Shortfall (ES).
   - Tier 4 (Slow / Scheduled, $300,000$ ms): Stress testing, CVA.

2. **Evaluate Due Recalculation Metrics**:
   Check elapsed time $\Delta t_{\text{elapsed}} = T_{\text{now}} - T_{\text{last\_calc}}$.

3. **Trigger Volatility Acceleration**:
   If P&L velocity $\left|\frac{d\text{PnL}}{dt}\right| > \text{Threshold}$, accelerate Tier 3/4 cadences by $5\times$.

4. **Execute Scheduled Calculations & Audit Efficiency**: Log CPU time saved by tiered scheduling.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Recalculating Monte Carlo VaR Per Tick**: Freezing order processing threads by running 100,000-path simulations on 100 Hz market data feeds.
- **Static Cadence During Fast Crashes**: Keeping VaR recalculation fixed at 5 minutes during a flash crash instead of dynamically accelerating.

## Verification

- Simulate 100 ticks with normal and high-velocity price moves, verify Tier 1 runs every tick, Tier 3 runs every 30s, and high P&L velocity triggers automatic 5x acceleration.
- Run `python scripts/test_risk_frequency_tuner.py` and confirm 100% pass rate.

## Related Skills

- `value-at-risk-var-live-monitoring`
- `real-time-greeks-recalculation-on-market-moves`
- `risk-control-latency-budget`
---
