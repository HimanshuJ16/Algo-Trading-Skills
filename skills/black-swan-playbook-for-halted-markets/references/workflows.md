# Black Swan Playbook Workflow

The following outlines the precise state-machine transitions and operational workflows required during a market halt event. Mechanics are US NMS equities; see `standards.md` for the jurisdictional scope.

## Phase 1: Halt Detection & Lockdown
1. **Signal Ingestion:** Listen to exchange data feeds for `HALTED_LULD`, `HALTED_CIRCUIT_BREAKER`, or `NEWS_PENDING` flags. Reject structurally malformed events loudly — a risk control must not silently absorb a bad feed message.
2. **Halt Order Routing:**
   - Immediately suspend all new order generation for the affected symbol.
   - Issue `CANCEL` requests for all active working orders (Limit, Stop, Trailing) **on the halted symbol only**; carry the individual order IDs so the execution layer can target them.
   - Order entry and cancellation remain permitted during a US halt, so this is executable in-halt.
3. **Risk Profile Adjustment:**
   - Alert the central risk engine to widen VaR bounds and dynamic stop-loss thresholds for the affected asset to account for fat-tail volatility.

## Phase 2: Halt Classification (gate before hedging)
1. **Market-wide vs single-name:** If the halt is a market-wide circuit breaker, **abort hedging** and record the reason. An MWCB halts every NMS security and, by coordinated CME halt, all US-based equity index futures and options — there is no tradable proxy. Phases 1 and 4 still apply.
2. **Idempotency:** If a proxy hedge is already working for this symbol, take no further hedging action. Exchange status is re-disseminated; a repeated `HALTED_*` message must not deploy a second hedge.

## Phase 3: Defensive Proxy Hedging (single-name pauses)
1. **Position Assessment:** Determine the net open position delta for the halted asset. If zero, remain idle.
2. **Proxy Mapping:** Retrieve the predefined, highly liquid proxy instrument (e.g., SPY, QQQ) and its return beta. An unmapped symbol is not hedged against an assumed beta — configure an explicit default proxy if a fallback is wanted.
3. **Input Sanity:** Reject non-finite or negative basis-risk readings, and non-positive or missing prices, *before* any threshold comparison. `NaN > limit` is `False`, so an unchecked comparison fails open.
4. **Basis Risk Check:** Evaluate current market basis risk. If the divergence exceeds the safety threshold, abort hedging to prevent compounded losses.
5. **Execution:** Deploy a market or aggressive limit order in the proxy asset offsetting the position's beta-adjusted delta.
   Size = `abs(PositionUnits × Beta × AssetPrice / ProxyPrice)`; the sign of `PositionUnits × Beta` determines the side, so a negative-beta (inverse) proxy is bought to hedge a long.
6. **Record the hedge:** Persist the proxy symbol, size, side and beta actually used. The unwind must reproduce these, not re-derive them from a map that may have changed.

## Phase 4: Auction Resumption
1. **Resume Signal Ingestion:** Detect the `RESUME_AUCTION` or `PRE_OPEN` state. Both are treated as resumption.
2. **Fair Value Calculation:** Run the internal pricing model to estimate the asset's current fair value, factoring in how the proxy asset moved during the halt duration.
3. **Auction Order Entry:** Submit an `AUCTION_RESUME_ORDER` (Limit) priced at the fair value estimate to liquidate or rebalance the position. If no usable fair value exists, skip auction participation — do not send an unpriced order into a reopening cross.
4. **Hedge Unwind:** Submit orders to close the proxy hedge **unconditionally on resumption**, independent of whether an auction order was generated and independent of the position size. A hedge left working after the halted name resumes is naked directional exposure. Where the execution stack supports it, trigger the unwind from the auction *fill* rather than from order entry, so an unfilled auction order does not leave the position unhedged.
   - **Level 3 MWCB exception:** a 20% decline ends the session; there is no reopening auction. Resolve the hedge against the close, not against a reopen that will not happen.

## Phase 5: Normalization
1. **Reconciliation:** Once the market officially un-halts and transitions to `NORMAL`, reconcile filled quantities from the auction. A transition straight to `NORMAL` without an auction message must still clear any hedge left working.
2. **Resume Normal Trading:** Re-enable standard alpha-generating models and reset risk limits to normal regime levels.
