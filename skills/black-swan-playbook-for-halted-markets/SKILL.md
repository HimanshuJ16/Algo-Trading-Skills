---
name: black-swan-playbook-for-halted-markets
description: Institutional quant standard playbook for handling US NMS equity trading
  halts (LULD single-name pauses, market-wide circuit breakers). Includes microstructure
  awareness, notional-scaled basis-risk-filtered proxy hedging, market-wide-halt hedge
  suppression, and fair-value auction resumption management.
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
version: "3.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill during Black Swan events, elevated market volatility, or whenever an exchange disseminates a `HALTED_LULD` or `HALTED_CIRCUIT_BREAKER` status for traded symbols. Standard "Gaussian" risk models and naïve algorithmic retries fail catastrophically during microstructure breakdowns. This skill automates the institutional response:
1. Avoiding trapped capital by cancelling pending limit/stop orders.
2. Expanding risk parameters for extreme volatility regimes.
3. Deploying notional-scaled proxy hedges (filtered by basis risk) to protect delta while the primary asset is frozen — but only when a proxy is actually tradable.
4. Intelligently liquidating or rebalancing positions during re-opening auctions at model-derived fair value.

## When NOT to Use

- **During a market-wide circuit breaker, for hedging purposes.** The engine still performs order lockdown and risk-limit expansion, but it deliberately emits no proxy hedge: a US MWCB halts every NMS security (including SPY/QQQ) and CME coordinates a simultaneous halt of all US-based equity index futures and options, so no proxy exists to trade. `HaltedMarketReport.hedge_suppression_reason` records this.
- **Outside US NMS equities**, without re-deriving the mechanics. LULD and MWCB thresholds, durations and reopening procedures are US-specific; other venues (NSE/BSE, Eurex, JPX, ASX) use different price-band and auction regimes.
- **As a fill-aware position manager.** The engine emits *intents*, not executions. Reconciling fills, partial fills and rejects is the execution layer's job.

## Prerequisites

- Real-time Exchange Feed Status Notifications (`HALT`, `RESUME`, `RESUME_AUCTION`, `PRE_OPEN`).
- Quantified correlation map identifying liquid proxy instruments (e.g. ETFs, index futures) and their **return betas** (`r_asset = beta * r_proxy`) relative to portfolio constituents.
- **Current prices for both legs** — the halted asset's last trade / pre-halt reference price and the live proxy price. Without both, hedge size cannot be made notional-correct and the engine refuses to hedge.
- Basis risk metrics to disable hedges when proxy correlations structurally break down.
- A Fair-Value pricing model for the halted asset to determine optimal auction participation prices.

## Workflow

1. **Detect Trading Halt Event**: Engine receives a Halt signal (`status`, `halt_reason`, `symbol`). Structurally invalid events (empty symbol, non-finite position, non-`MarketStatus` status) raise `ValueError` rather than being silently absorbed.
2. **Microstructure Lockdown**: Cancel all open orders for the halted symbol to prevent adverse selection upon un-halt — resting orders survive a US halt in the book and remain eligible for the reopening auction. Orders carrying a foreign `symbol` are excluded from the cancel set. Expand local VaR thresholds for the fat-tail environment.
3. **Classify the halt before hedging**:
   - If the halt is market-wide (`HALTED_CIRCUIT_BREAKER`), suppress hedging entirely and record the reason — the proxy is halted too.
   - If a hedge is already working for this symbol, do nothing further; exchanges re-disseminate halt status and a second message must not fire a second hedge.
4. **Dynamic Proxy Hedging** (single-name pauses only):
   - Reject unusable inputs first — a `NaN` basis-risk reading must fail *closed*, because `NaN > limit` evaluates to `False`.
   - Check that current basis risk does not exceed the configured limit for that proxy pair.
   - Size on beta-adjusted **notional**, not share count: `ProxyUnits = Position × Beta × (AssetPrice / ProxyPrice)`. A negative beta (inverse proxy) flips the hedge direction.
   - Fire a `PROXY_HEDGE` order offsetting the held position's beta-adjusted delta, and record the exact instrument, size and side for the unwind.
5. **Auction Resumption**:
   - Upon a `RESUME_AUCTION` or `PRE_OPEN` signal, compute the asset's estimated fair value.
   - Inject an `AUCTION_RESUME_ORDER` (limit) at fair value. If no usable fair value is available, skip auction participation rather than send an unpriced order into a reopening cross.
   - Issue the `PROXY_HEDGE_UNWIND` **unconditionally** whenever the symbol is resuming — including on a direct transition to `NORMAL` — so the hedge is never orphaned by a missing fair value or a flat position.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Proxy hedging through a market-wide circuit breaker**: An MWCB halt covers all NMS securities and, by coordinated CME halt, all US-based equity index futures and options. A `SELL QQQ` hedge routed during a Level 1/2 halt cannot execute; if the router queues it, it fires blind into the reopen at an unknown price. Classify the halt before hedging.
- **Sizing a beta hedge in share count**: `Position × Beta` is only correct when the asset and proxy trade at the same price. A 1,000-share position in a $450 stock hedged with a $375 ETF at beta 1.5 needs 1,800 proxy units, not 1,500 — a 17% under-hedge from omitting the price ratio.
- **Risk gates that fail open on `NaN`**: `float('nan') > basis_risk_limit` is `False`, so a naive comparison passes a stale or corrupt basis-risk reading straight through and deploys the hedge it was meant to block. Validate for finiteness explicitly.
- **Orphaning the hedge**: Nesting the unwind inside the auction-order branch means that whenever fair value is unavailable or the position is flat, the proxy hedge stays working after the halted name resumes — converting a hedge into naked directional exposure in the proxy.
- **Treating repeated halt messages as new halts**: Exchange status is re-disseminated. Non-idempotent handling fires a second hedge while recording only one, so the eventual unwind is undersized and residual proxy exposure survives the event. The engine's guard is deliberately coarse — it keeps the first hedge and will not resize it if a late fill report changes the position after the hedge was placed; a desk needing that must compute and route the delta adjustment itself.
- **Unwinding on submission rather than on fill**: This engine emits the auction order and the unwind together. If the auction limit order does not fill, the position is left unhedged. Production systems should trigger the unwind from the auction fill notification, not from order entry.
- **Assuming a reopening auction always follows**: A Level 3 (20%) MWCB closes the market for the remainder of the day. There is no reopen to participate in, and the position carries overnight with the hedge decision already made.
- **Ignoring Basis Risk**: Deploying a proxy hedge when the proxy instrument itself is decoupling from historical correlations, exacerbating losses instead of muting them.
- **Spamming Orders During Halt**: Continuing to send execution API requests, leading to broker throttling, limits, or outright account suspension.
- **Static Risk Limits**: Using static Value-at-Risk (VaR) or stop-loss limits during Black Swans. High volatility will instantly blow through these, triggering cascading liquidations. Dynamic, volatility-adjusted limits are required.

## Verification

- Inject a `HALTED_LULD` event on a 1,000-share long at 450.00 with a beta-1.5 proxy at 375.00. Verify order cancellation (with order IDs), risk limit adjustment, and a `SELL 1800 QQQ` proxy hedge — the notional-correct size, not 1,500.
- Inject a high basis-risk scenario, then a `NaN` basis-risk scenario; confirm the proxy hedge aborts in both and `hedge_suppression_reason` is populated.
- Inject a `HALTED_CIRCUIT_BREAKER` event; confirm lockdown occurs but no `PROXY_HEDGE` action is emitted.
- Re-send the same `HALTED_LULD` event; confirm no second hedge order and an unchanged `active_hedges` entry.
- Inject a resume event with no fair value; confirm no auction order but a full-size `PROXY_HEDGE_UNWIND`.
- Run `python -m unittest discover -s skills/black-swan-playbook-for-halted-markets/scripts` and confirm 100% pass rate.

## Related Skills

- `greeks-based-portfolio-hedging-automation`
- `vendor-outage-fallback-data-source-hierarchy`
- `kill-switch-and-drawdown-circuit-breakers`
- `tail-risk-hedging-with-options`
- `execution-algo-behavior-under-halted-instrument`
---
