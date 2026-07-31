---
name: real-time-greeks-recalculation-on-market-moves
description: >-
  Event-driven options portfolio Greeks recalculation engine utilizing Taylor series expansion for micro-ticks and triggering full Black-Scholes revaluation upon price threshold breaches.
domain: Derivatives & Risk Management
subdomain: Real-Time Options Risk Engineering
tags: ["options-greeks", "real-time-risk", "taylor-series", "delta-gamma-approximation", "black-scholes", "event-driven"]
brokers_frameworks: ["Black-Scholes Model", "Python Dataclasses", "Math Normal Distribution"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing real-time risk for options portfolios and algo market-making desks. Re-evaluating full Black-Scholes pricing models across thousands of options contracts on every microsecond underlying tick consumes excessive CPU capacity. This engine uses a hybrid recalculation architecture: it applies fast Taylor series expansions ($\Delta_t \approx \Delta_0 + \Gamma_0 \Delta S$) for small price movements and triggers full Black-Scholes revaluation only when underlying price shifts exceed a configured threshold ($|\Delta S / S_0| > \text{threshold}$).

## Prerequisites

- Option position data (`symbol`, `underlying_symbol`, `option_type`, `strike`, `expiry_years`, `spot`, `iv`, `position_size`).
- Recalculator config (`full_recalc_threshold_pct`: default 0.005 / 0.5%).

## Workflow

1. **Price Shift Evaluation**:
   - Calculate relative price shift $\Delta S_{\text{pct}} = |\frac{S_{\text{new}} - S_0}{S_0}|$.
2. **Hybrid Recalculation Dispatch**:
   - If $\Delta S_{\text{pct}} \le \text{Threshold}$: Compute fast Taylor expansion:
     $$\Delta_{\text{new}} = \Delta_0 + \Gamma_0 \cdot (S_{\text{new}} - S_0)$$
     $$\Delta V \approx \Delta_0 \cdot \Delta S + \frac{1}{2} \Gamma_0 \cdot (\Delta S)^2$$
   - If $\Delta S_{\text{pct}} > \text{Threshold}$: Trigger full Black-Scholes recalculation ($d_1, d_2$, Delta, Gamma, Vega, Theta).
3. **Portfolio Greeks Aggregation**:
   - Sum position-weighted Net Delta, Net Gamma, and Net Vega across all portfolio options contracts.
4. **Audit Report Generation**: Output structured `RealTimeGreeksReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Over-recalculating on Micro-Ticks**: Running full Black-Scholes pricing on every single tick, stalling event loops during market volatility spikes.
- **Ignoring Higher-Order Gamma**: Relying on linear Delta approximation alone during large market moves, miscalculating directional exposure.
- **Unsynchronized Underlying Spot**: Calculating options Greeks using stale or non-synchronized underlying tick quotes.

## Verification

- Instantiate `RealTimeGreeksRecalculatorEngine`. Ingest 1 Call Option ($S=\$100, K=\$100, \text{Delta}_0=0.50, \text{Gamma}_0=0.03$). Process small move $S \to \$100.20$ ($0.2\%$ move) $\implies$ verify Taylor expansion executed ($\Delta_{\text{new}} = 0.506$), full BS skipped (`TAYLOR_EXPANSION_UPDATE`). Process large move $S \to \$102.00$ ($2.0\%$ move) $\implies$ verify full BS recalculation triggered (`FULL_BS_RECALCULATION`).
- Run `python scripts/test_real_time_greeks_recalculator.py`.

## Related Skills

- `options-greeks-real-time-portfolio-aggregation`
- `options-pin-risk-management-at-expiry`
---
