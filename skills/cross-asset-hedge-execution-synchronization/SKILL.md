---
name: cross-asset-hedge-execution-synchronization
description: >-
  Use when a primary leg fill must be hedged immediately in another asset, as in delta
  hedging, convertible arbitrage or ETF basis trading, enforcing latency bounds so
  legging risk does not open between the two.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: execution-algo, hedge-synchronization, legging-risk, delta-hedging, multi-leg, latency-bounds
  brokers_frameworks: "Generic FIX / OMS; Python Dataclasses"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in multi-leg trading algorithms (e.g. Options Delta Hedging, Convertible Bond Arbitrage, ETF Basis Trading, Cross-Asset Pairs) where primary leg fills must be immediately hedged in a secondary asset. Failing to synchronize hedge executions introduces severe **Legging Risk**—where market movements during the execution gap create unhedged directional exposure and slippage. This module calculates hedge quantities from fill events (including partial fills), generates hedge orders upon primary fill events, measures dispatch and fill latency against a configurable SLA, and force-flags any hedge left incomplete past an unhedged-exposure timeout so the primary leg can be emergency-unwound.

## When NOT to Use

- **Single-leg strategies** with no hedge offset — there is no legging risk to synchronize.
- **Passive / slow rebalancing** (e.g. daily portfolio rebalancing against closing prices) where sub-second synchronization adds complexity without benefit.
- **Exchange-native combos**: when the venue offers the spread as a single instrument, prefer it over synthesizing the legs yourself. US options exchanges auction complex orders "as a packaged order with one net price" (Cboe), which removes the leg-to-leg gap this engine exists to measure. Note the limit: complex orders can still *leg* into the single-leg book, and Cboe issues a standing regulatory circular on "Legging Complex and Other Multi Part Orders" warning of risks "inherent in legging multi-part orders related to timing of the executions and potential for price movement". Confirm per venue and order type that your combo actually executes as a package before disabling synchronization.
- **Atomic cross-asset legs are impossible** on most venues — this engine minimizes the unhedged window; it cannot eliminate it. Do not treat the SLA flags as a guarantee of zero exposure.

## Prerequisites

- Primary leg fill notification payload (`strategy_id`, `symbol`, `fill_qty`, `fill_price`, `fill_timestamp_ms`) delivered per fill event, not per order.
- A **stable, unique `fill_id` per economic fill**. It is the deduplication key: the engine derives `hedge_order_id` from it and refuses to hedge the same `fill_id` twice. If your gateway does not supply a durable execution id, derive one before calling — a counter that resets on reconnect will collide and block legitimate hedges.
- Strategy parameters: `hedge_symbol`, `hedge_ratio`, `max_sync_delay_ms` (default 100 ms), `unhedged_timeout_ms` (default 500 ms), and an emergency-unwind callback wired to the primary-leg cancel/flatten path.
- **Threading**: public methods are lock-guarded, so the gateway thread delivering fills and the timer thread driving `enforce_unhedged_timeouts()` may differ. `unwind_callback` is invoked outside the lock, so it runs after the hedge has left `pending_hedges`.
- **Hedge ratio semantics**: `hedge_ratio` is expressed in hedge units per one unit of primary quantity and MUST include the contract multiplier. For US equity options, one contract *usually* represents 100 shares of the underlying (OIC/OCC) — a 0.50-delta call therefore needs `hedge_ratio = 0.50 × 100 = 50.0` shares per contract. Corporate-action-adjusted (non-standard) contracts can carry multipliers other than 100 — verify per instrument.

## Workflow

1. **Fill Event Ingestion**: Receive each primary leg execution fill ($Q_{primary}, P_{primary}, t_{fill}$). Validate `strategy_id` and symbol before generating any order; reject non-finite or zero quantities, and reject a hedge quantity that rounds to zero rather than sending a zero-quantity order.
   - **Decision point — is this fill new?** `generate_hedge_order` deduplicates on `fill_id` and raises if that fill was already hedged (live or finalized). A resent execution report is not a new fill: FIX puts duplicate detection on the application layer, because the session layer cannot distinguish a `PossResend` from a genuine fill. Do not catch this and re-dispatch — reconcile against the OMS to establish whether the venue already holds the hedge.
2. **Hedge Quantity Calculation**:
   - $\text{Hedge Qty} = -1.0 \times Q_{primary} \times \text{Hedge Ratio}$ (ratio includes the contract multiplier — see Prerequisites).
3. **Synchronized Hedge Order Dispatch**:
   - Dispatch the market or aggressive limit order to the hedge venue, then record the dispatch timestamp via `mark_dispatched()` so dispatch latency is measured separately from fill latency.
4. **Incremental Fill Processing**: Call `process_hedge_fill()` for every hedge fill callback, including partials. Quantities accumulate; the order stays pending until the cumulative fill reaches the target, so residual exposure is always tracked. Reject wrong-side fills outright — they indicate position books disagreeing, not a retryable condition.
5. **Latency & Synchronization Audit**:
   - Measure synchronization delay $\Delta t = t_{hedge\_fill} - t_{fill}$.
   - $\Delta t \le \text{max\_sync\_delay\_ms}$ and fully filled → `SYNCHRONIZED_OK`.
   - Completed but late ($\Delta t > \text{max\_sync\_delay\_ms}$) → `SYNC_DELAY_BREACH`: flag for audit and have the execution gateway aggressively reprice any remaining quote (repricing is the OMS's action; this engine supplies the flag).
   - Incomplete within the window → `PARTIALLY_FILLED` with the residual quantity exposed.
6. **Legging Risk Exception Handling**: Run `enforce_unhedged_timeouts(now_ms)` on a periodic timer. Any hedge still incomplete after `unhedged_timeout_ms` from its primary fill is flagged `UNHEDGED_TIMEOUT_UNWIND` and routed to the unwind callback to flatten the primary leg — do not wait for a late fill to arrive before unwinding, and never re-submit the hedge order on a timeout without first reconciling position state.
   - The same breach observed by a **late fill callback** instead of the timer routes to the same callback. Whichever observer sees it first changes only the timing, never whether the primary leg is unwound. This matters most for a late *partial* fill: it finalizes the hedge order, so the timer sweep will never revisit it, and the residual exposure would otherwise leave tracking entirely.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Synchronized Legging**: Routing primary and hedge legs independently without cross-leg atomic state tracking.
- **Hedging a Redelivered Fill Twice**: a gateway reconnect, a `PossResend` execution report, or an OMS replay re-delivers a fill you already hedged. Keying hedge state on `fill_id` and rejecting repeats is the control; regenerating "just in case" discards the accumulated fill state of the live hedge *and* puts a second hedge on the market. SEC Rule 15c3-5(c)(1)(ii) requires pre-trade controls that reject orders "that indicate duplicative orders" — this is that control on the hedge path.
- **Ignoring Partial Fills**: Waiting for the primary order to fill 100% before routing any hedge leg — and, on the hedge side, closing out hedge state on the first partial fill callback, which silently drops the residual unhedged quantity from tracking. Both directions must be incremental.
- **Static Delta Assumption**: Using static option delta for hedging during fast market moves without updating real-time implied volatility / spot delta.
- **Forgetting the Contract Multiplier**: Using delta directly as `hedge_ratio` (e.g. 0.50 instead of 50.0 for a standard 100-share equity option contract) under-hedges by 100×. Adjusted contracts can carry non-standard multipliers — verify per instrument.
- **No Timer for the No-Fill Case**: Reacting only to fill callbacks means a hedge that never fills is never unwound. Timeout enforcement must be driven by a clock, not by the arrival of a (possibly late) fill.
- **Retrying a Timed-Out Hedge Blindly**: A timeout does not tell you whether the venue holds a live order. Re-submitting without cancel/reconcile can double the hedge; unwind or reconcile first.
- **Two Timeout Paths That Disagree**: if the late-fill path only *flags* a breach while the timer sweep *acts* on it, a hedge whose late fill lands just before the next tick is silently never unwound — and a late partial fill takes its residual exposure out of tracking with it. Both observers must route to one unwind path.
- **Running the Sweep on Its Own Thread Without Synchronization**: a timer thread sweeping while the gateway thread accumulates fills races on the same hedge records — lost quantity updates, or the same order finalized twice. Guard the state, and never hold that guard while calling out to an unwind handler you do not control.

## Verification

- Instantiate `CrossAssetHedgeSynchronizer`. Register an options delta hedge strategy (`Primary` = `AAPL_250516_C200`, `Hedge` = `AAPL`, `hedge_ratio` = 50.0 = 0.50 delta × 100-share multiplier, `Max Delay` = 100 ms). Ingest primary fill of +10 option contracts at $t = 1000\text{ ms}$. Verify synchronizer generates hedge order of -500 shares `AAPL`. Submit hedge fill at $t = 1040\text{ ms}$ ($\Delta t = 40\text{ ms}$) and verify `SYNCHRONIZED_OK` status.
- Verify partial-fill tracking: submit -200 shares at $t = 1030\text{ ms}$ (expect `PARTIALLY_FILLED`, residual 300, order still pending), then -300 shares at $t = 1060\text{ ms}$ (expect `SYNCHRONIZED_OK`, residual 0).
- Verify timeout enforcement: generate a hedge order, call `enforce_unhedged_timeouts(now_ms=2000)` with no hedge fill submitted, and confirm a `UNHEDGED_TIMEOUT_UNWIND` status is returned and the unwind callback fired.
- Verify duplicate-fill rejection: after hedging `FILL_X`, calling `generate_hedge_order` again with `fill_id = FILL_X` must raise while the live hedge keeps its accumulated `filled_hedge_qty` and dispatch timestamp; the same must raise after that hedge has completed.
- Verify unified timeout routing: submit a *partial* hedge fill past `unhedged_timeout_ms` and confirm the unwind callback fires, the residual is reported, and the subsequent sweep finds nothing left pending.
- Verify rejection paths: wrong-side fill, duplicate fill for a completed order, `strategy_id`/symbol mismatch, a hedge quantity rounding to zero, and NaN/zero quantities must all raise `ValueError`.
- Run `python -m unittest discover -s skills/cross-asset-hedge-execution-synchronization/scripts`.

## Related Skills

- `cross-venue-latency-arbitrage-defensive-design`
- `tail-risk-hedging-with-options`
- `order-placement-idempotency`
