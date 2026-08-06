---
name: tail-risk-hedging-with-options
description: Implement systematic out-of-the-money put option overlays, negative carry budgeting, and Black-Scholes convex crash payout simulation.
domain: risk-management
subdomain: tail-risk
tags: [options-hedging, tail-risk, otm-puts, black-scholes, convex-payoff, carry-budget]
brokers_frameworks: [numpy, scipy]
version: 1.0.0
author: Quant Team
license: MIT
---

# Tail Risk Hedging With Options

The `tail-risk-hedging-with-options` skill provides institutional-grade downside tail risk protection using systematic out-of-the-money (OTM) put option overlays. It balances negative carry cost drag against convex crash payouts during market panics.

## When to Use

- When structuring portfolio downside protection against Black Swan crash events (-20% to -40% market drawdowns).
- When budgeting annual option premium carry costs (e.g., capping drag at 1–3% of AUM).
- When computing option Greeks ($\Delta, \Gamma, \mathcal{V}$) for tail risk overlays.
- When simulating non-linear payoff profiles across extreme stress market scenarios.

## Prerequisites

- Portfolio AUM, spot underlying asset price, and implied volatility surface parameters.
- Python 3.9+ with math standard libraries.

## Workflow

1. **Set Carry Budget**: Establish annual maximum option premium budget (e.g. 2% of portfolio AUM).
2. **Select Strike & DTE**: Target 10–20% out-of-the-money put strikes with 60–90 days to expiration.
3. **Price Options**: Calculate Black-Scholes put prices, Delta, Gamma, and Vega.
4. **Allocate Contracts**: Allocate maximum whole option contracts within budget limits.
5. **Simulate Stress Payoffs**: Evaluate intrinsic payouts for -20% and -30% market crashes.

## Common Pitfalls

- **High Drag Burn Rate**: Over-hedging with near-the-money puts can consume 5–8% of portfolio returns annually in calm markets.
- **Under-sizing During Volatility Spikes**: Failing to roll options before rapid theta decay diminishes tail coverage.
- **Ignoring Contract Multipliers**: Miscalculating 100-share contract multipliers leads to severe under/over-allocation.

## Verification

Run the test suite:
```bash
python -m unittest test_tail_risk_hedger.py
```

## Related Skills

- `tail-correlation-between-strategies-under-stress`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
- `options-implied-volatility-surface-construction`
