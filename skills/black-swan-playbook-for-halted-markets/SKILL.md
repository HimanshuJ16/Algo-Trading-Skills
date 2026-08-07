---
name: black-swan-playbook-for-halted-markets
description: Institutional quant standard playbook for handling exchange trading halts
  (LULD, volatility halts, market-wide circuit breakers). Includes microstructure
  awareness, basis-risk filtered dynamic proxy index hedging, and fair-value auction
  resumption management.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- trading-halt
- circuit-breaker
- black-swan
- proxy-hedging
- luld
- halt-playbook
- institutional
brokers_frameworks:
- Black Swan Halted Market Engine
- Python
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill during Black Swan events, elevated market volatility, or whenever an exchange disseminates a `HALTED_LULD` or `HALTED_CIRCUIT_BREAKER` status for traded symbols. Standard "Gaussian" risk models and naïve algorithmic retries fail catastrophically during microstructure breakdowns. This skill automates the institutional response: 
1. Avoiding trapped capital by cancelling pending limit/stop orders.
2. Expanding risk parameters for extreme volatility regimes.
3. Deploying dynamic proxy hedges (filtered by basis risk) to protect delta while the primary asset is frozen.
4. Intelligently liquidating or rebalancing positions during re-opening auctions at model-derived fair value.

## Prerequisites

- Real-time Exchange Feed Status Notifications (`HALT`, `RESUME`, `RESUME_AUCTION`).
- Quantified correlation map identifying liquid proxy instruments (e.g. ETFs, index futures) and their betas relative to portfolio constituents.
- Basis risk metrics to disable hedges when proxy correlations structurally break down.
- A Fair-Value pricing model for the halted asset to determine optimal auction participation prices.

## Workflow

1. **Detect Trading Halt Event**: Engine receives a Halt signal (`status`, `halt_reason`, `symbol`).
2. **Microstructure Lockdown**: Cancel all open orders for the halted symbol to prevent adverse selection upon un-halt. Expand local VaR thresholds for the fat-tail environment.
3. **Dynamic Proxy Hedging**: 
   - Check if current basis risk exceeds the pre-defined safety limit.
   - If safe, calculate hedge size based on beta: `HedgeQty = abs(OpenPosition * Beta)`.
   - Fire a `PROXY_HEDGE` order in the opposite direction of the held position.
4. **Auction Resumption**: 
   - Upon a `RESUME_AUCTION` signal, compute the asset's estimated fair value.
   - Inject an `AUCTION_RESUME_ORDER` (Limit MOC/LOC) at fair value.
   - Immediately issue a `PROXY_HEDGE_UNWIND` order to remove the temporary delta shield.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Basis Risk**: Deploying a proxy hedge when the proxy instrument itself is decoupling from historical correlations, exacerbating losses instead of muting them.
- **Spamming Orders During Halt**: Continuing to send execution API requests, leading to broker throttling, limits, or outright account suspension.
- **Static Risk Limits**: Using static Value-at-Risk (VaR) or stop-loss limits during Black Swans. High volatility will instantly blow through these, triggering cascading liquidations. Dynamic, volatility-adjusted limits are required.

## Verification

- Inject LULD halt event on an open position. Verify order cancellation, risk limit adjustment, and the deployment of a proxy hedge.
- Inject a high basis-risk scenario; confirm the proxy hedge aborts safely.
- Inject an auction resume event; verify the auction participation order and simultaneous proxy hedge unwind.
- Run `python scripts/test_halted_market_engine.py` and confirm 100% pass rate.

## Related Skills

- `greeks-based-portfolio-hedging-automation`
- `vendor-outage-fallback-data-source-hierarchy`
- `kill-switch-and-drawdown-circuit-breakers`
- `tail-risk-hedging-with-options`
---
